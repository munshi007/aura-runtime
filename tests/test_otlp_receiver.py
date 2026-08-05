import json
import threading
from http import HTTPStatus
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from aura_runtime.otlp_receiver import AuraOTLPServer, OTLPIngestService
from aura_runtime.store import SQLiteEventStore


def payload() -> dict[str, object]:
    return {
        "resourceSpans": [
            {
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": "trace-live",
                                "spanId": "span-live",
                                "name": "execute_tool lookup",
                                "startTimeUnixNano": "1785758400000000000",
                                "endTimeUnixNano": "1785758401000000000",
                                "attributes": {
                                    "gen_ai.operation.name": "execute_tool",
                                    "gen_ai.tool.name": "lookup",
                                },
                            }
                        ]
                    }
                ]
            }
        ]
    }


def test_ingest_service_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "aura.db"
    service = OTLPIngestService(db)

    first = service.ingest(payload())
    second = service.ingest(payload())

    assert first.generated_events == first.stored_events == 2
    assert second.generated_events == 2
    assert second.stored_events == 0
    assert second.duplicate_events == 2
    assert len(SQLiteEventStore(db).events("trace-live")) == 2


def test_otlp_http_json_endpoint(tmp_path: Path) -> None:
    server = AuraOTLPServer(
        ("127.0.0.1", 0),
        OTLPIngestService(tmp_path / "aura.db"),
        max_request_bytes=1024 * 1024,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        health = urlopen(f"{base}/healthz")  # noqa: S310
        assert health.status == HTTPStatus.OK
        assert json.load(health) == {"status": "ok"}

        request = Request(
            f"{base}/v1/traces",
            data=json.dumps(payload()).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        response = urlopen(request)  # noqa: S310
        assert response.status == HTTPStatus.OK
        assert json.load(response) == {}
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_otlp_http_rejects_wrong_media_type_and_oversized_body(tmp_path: Path) -> None:
    server = AuraOTLPServer(
        ("127.0.0.1", 0),
        OTLPIngestService(tmp_path / "aura.db"),
        max_request_bytes=2,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/v1/traces"
    try:
        wrong_type = Request(url, data=b"{}", headers={"Content-Type": "text/plain"})
        try:
            urlopen(wrong_type)  # noqa: S310
        except HTTPError as error:
            assert error.code == HTTPStatus.UNSUPPORTED_MEDIA_TYPE

        oversized = Request(url, data=b"{} ", headers={"Content-Type": "application/json"})
        try:
            urlopen(oversized)  # noqa: S310
        except HTTPError as error:
            assert error.code == HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
