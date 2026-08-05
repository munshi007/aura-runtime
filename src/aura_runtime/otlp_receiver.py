"""Minimal OTLP/HTTP JSON receiver for zero-code agent trace ingestion."""

from __future__ import annotations

import json
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from aura_runtime.adapters.otel import events_from_otlp_json
from aura_runtime.store import SQLiteEventStore

OTLP_JSON = "application/json"


@dataclass(frozen=True)
class IngestResult:
    generated_events: int
    stored_events: int

    @property
    def duplicate_events(self) -> int:
        return self.generated_events - self.stored_events


class OTLPIngestService:
    """Normalize and idempotently persist one OTLP trace export request."""

    def __init__(self, db: str | Path) -> None:
        self.store = SQLiteEventStore(db)

    def ingest(self, payload: dict[str, Any]) -> IngestResult:
        events = events_from_otlp_json(payload)
        stored = sum(self.store.append_event_once(event) for event in events)
        return IngestResult(generated_events=len(events), stored_events=stored)


class AuraOTLPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        service: OTLPIngestService,
        *,
        max_request_bytes: int,
    ) -> None:
        super().__init__(server_address, AuraOTLPHandler)
        self.service = service
        self.max_request_bytes = max_request_bytes


class AuraOTLPHandler(BaseHTTPRequestHandler):
    server: AuraOTLPServer

    def _json_response(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        content = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", OTLP_JSON)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            self._json_response(HTTPStatus.OK, {"status": "ok"})
            return
        self._json_response(HTTPStatus.NOT_FOUND, {"code": 5, "message": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/v1/traces":
            self._json_response(HTTPStatus.NOT_FOUND, {"code": 5, "message": "not found"})
            return
        media_type = self.headers.get_content_type()
        if media_type != OTLP_JSON:
            self._json_response(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                {"code": 3, "message": "Aura currently accepts OTLP/HTTP JSON"},
            )
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if length < 0 or length > self.server.max_request_bytes:
            self._json_response(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {"code": 8, "message": "request body exceeds configured limit"},
            )
            return
        try:
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("request body must be a JSON object")
            self.server.service.ingest(payload)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as error:
            self._json_response(HTTPStatus.BAD_REQUEST, {"code": 3, "message": str(error)})
            return
        self._json_response(HTTPStatus.OK, {})

    def log_message(self, format: str, *args: object) -> None:
        return


def serve_otlp_http(
    *,
    host: str = "127.0.0.1",
    port: int = 4318,
    db: str | Path = ".aura/aura.db",
    max_request_bytes: int = 16 * 1024 * 1024,
) -> None:
    """Serve OTLP/HTTP JSON until interrupted."""

    service = OTLPIngestService(db)
    with AuraOTLPServer(
        (host, port), service, max_request_bytes=max_request_bytes
    ) as server:
        server.serve_forever()
