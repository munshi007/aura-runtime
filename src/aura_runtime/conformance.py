"""Incremental MCP causal reconstruction and protocol conformance checks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from aura_runtime.models import GateAction, ProtocolRecord

PROTOCOL_VERSION_KEY = "io.modelcontextprotocol/protocolVersion"
CLIENT_CAPABILITIES_KEY = "io.modelcontextprotocol/clientCapabilities"
SUBSCRIPTION_ID_KEY = "io.modelcontextprotocol/subscriptionId"
MODERN_PROTOCOL_START = date(2026, 7, 28)
LEGACY_PROTOCOL_END = date(2025, 11, 25)


class ProtocolEra(StrEnum):
    LEGACY = "legacy"
    MODERN = "modern"
    UNKNOWN = "unknown"


class MessageKind(StrEnum):
    REQUEST = "request"
    RESPONSE = "response"
    NOTIFICATION = "notification"
    INVALID = "invalid"


class ConformanceSeverity(StrEnum):
    WARNING = "warning"
    ERROR = "error"


class CausalNode(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str
    record_sequence: int = Field(ge=0)
    direction: str
    kind: MessageKind
    method: str | None = None
    request_id: str | int | None = None
    protocol_version: str | None = None
    protocol_era: ProtocolEra
    forwarded: bool
    action: GateAction
    content_hash: str


class CausalEdge(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str
    target: str
    relation: Literal["responds_to", "subscription_of"]


class ConformanceIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    severity: ConformanceSeverity
    message: str
    record_sequences: list[int] = Field(default_factory=list)


class ConformanceReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    verdict: Literal["pass", "fail"]
    transcript_integrity: bool
    protocol_versions: list[str]
    nodes: list[CausalNode]
    edges: list[CausalEdge]
    issues: list[ConformanceIssue]
    open_requests: list[str]


@dataclass(frozen=True)
class _PendingRequest:
    node_id: str
    sequence: int
    direction: str
    request_id: str | int
    method: str
    protocol_version: str | None
    protocol_era: ProtocolEra


def _typed_request_id(value: str | int) -> str:
    return f"{type(value).__name__}:{value}"


def _message_kind(message: dict[str, Any]) -> MessageKind:
    method = message.get("method")
    has_id = "id" in message
    if isinstance(method, str):
        return MessageKind.REQUEST if has_id else MessageKind.NOTIFICATION
    if has_id:
        return MessageKind.RESPONSE
    return MessageKind.INVALID


def _request_meta(message: dict[str, Any]) -> dict[str, Any]:
    params = message.get("params")
    if not isinstance(params, dict):
        return {}
    meta = params.get("_meta")
    return meta if isinstance(meta, dict) else {}


def _parse_protocol_date(version: str | None) -> date | None:
    if version is None:
        return None
    try:
        return date.fromisoformat(version)
    except ValueError:
        return None


def _era(version: str | None) -> ProtocolEra:
    parsed = _parse_protocol_date(version)
    if parsed is None:
        return ProtocolEra.UNKNOWN
    if parsed >= MODERN_PROTOCOL_START:
        return ProtocolEra.MODERN
    if parsed <= LEGACY_PROTOCOL_END:
        return ProtocolEra.LEGACY
    return ProtocolEra.UNKNOWN


def _opposite(direction: str) -> str:
    if direction == "client_to_server":
        return "server_to_client"
    if direction == "server_to_client":
        return "client_to_server"
    return ""


class MCPConformanceMonitor:
    """Consume hash-chained records and emit violations at the earliest known point."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.nodes: list[CausalNode] = []
        self.edges: list[CausalEdge] = []
        self.issues: list[ConformanceIssue] = []
        self._pending: dict[tuple[str, str], _PendingRequest] = {}
        self._subscriptions: dict[str, _PendingRequest] = {}
        self._legacy_version: str | None = None
        self._expected_sequence = 0
        self._previous_hash = ""
        self._integrity = True

    def observe(self, record: ProtocolRecord) -> list[ConformanceIssue]:
        """Observe one record and return only issues made conclusive by this record."""
        before = len(self.issues)
        self._check_chain(record)
        message = record.message
        kind = _message_kind(message)
        method = message.get("method") if isinstance(message.get("method"), str) else None
        request_id = message.get("id")
        if not isinstance(request_id, (str, int)) or isinstance(request_id, bool):
            request_id = None

        version, era = self._message_protocol(message, kind)
        node = CausalNode(
            node_id=f"record:{record.sequence}",
            record_sequence=record.sequence,
            direction=record.direction,
            kind=kind,
            method=method,
            request_id=request_id,
            protocol_version=version,
            protocol_era=era,
            forwarded=record.forwarded,
            action=record.action,
            content_hash=record.content_hash,
        )
        self.nodes.append(node)

        self._check_jsonrpc(record, kind, request_id)
        if kind == MessageKind.REQUEST and method is not None and request_id is not None:
            self._observe_request(record, node, method, request_id, version, era)
        elif kind == MessageKind.RESPONSE and request_id is not None:
            self._observe_response(record, node, request_id)
        elif kind == MessageKind.NOTIFICATION:
            self._observe_notification(record, node)
        elif kind == MessageKind.INVALID:
            self._issue(
                "invalid_message_shape",
                ConformanceSeverity.ERROR,
                "message is neither a JSON-RPC request, response, nor notification",
                record.sequence,
            )
        return self.issues[before:]

    def report(self) -> ConformanceReport:
        versions = sorted(
            {node.protocol_version for node in self.nodes if node.protocol_version is not None}
        )
        open_requests = sorted(
            f"{request.direction}:{_typed_request_id(request.request_id)}:{request.method}"
            for request in self._pending.values()
        )
        return ConformanceReport(
            run_id=self.run_id,
            verdict=(
                "fail"
                if any(issue.severity == ConformanceSeverity.ERROR for issue in self.issues)
                else "pass"
            ),
            transcript_integrity=self._integrity,
            protocol_versions=versions,
            nodes=self.nodes,
            edges=self.edges,
            issues=self.issues,
            open_requests=open_requests,
        )

    def _check_chain(self, record: ProtocolRecord) -> None:
        if record.run_id != self.run_id:
            self._integrity = False
            self._issue(
                "run_id_mismatch",
                ConformanceSeverity.ERROR,
                "record belongs to a different run",
                record.sequence,
            )
        if record.direction not in ("client_to_server", "server_to_client"):
            self._issue(
                "invalid_direction",
                ConformanceSeverity.ERROR,
                "MCP record direction must identify the client or server sender",
                record.sequence,
            )
        if (
            record.sequence != self._expected_sequence
            or record.previous_hash != self._previous_hash
        ):
            self._integrity = False
            self._issue(
                "transcript_chain_broken",
                ConformanceSeverity.ERROR,
                "record sequence or previous hash does not continue the transcript chain",
                record.sequence,
            )
        rebuilt = ProtocolRecord.create(
            run_id=record.run_id,
            sequence=record.sequence,
            direction=record.direction,
            message=record.message,
            forwarded=record.forwarded,
            action=record.action,
            previous_hash=record.previous_hash,
        )
        if record.content_hash != rebuilt.content_hash:
            self._integrity = False
            self._issue(
                "record_hash_invalid",
                ConformanceSeverity.ERROR,
                "record content does not match its content hash",
                record.sequence,
            )
        self._expected_sequence = record.sequence + 1
        self._previous_hash = record.content_hash

    def _message_protocol(
        self, message: dict[str, Any], kind: MessageKind
    ) -> tuple[str | None, ProtocolEra]:
        if kind != MessageKind.REQUEST:
            return None, ProtocolEra.UNKNOWN
        method = message.get("method")
        params = message.get("params")
        if method == "initialize" and isinstance(params, dict):
            candidate = params.get("protocolVersion")
            if isinstance(candidate, str):
                self._legacy_version = candidate
                return candidate, _era(candidate)
        candidate = _request_meta(message).get(PROTOCOL_VERSION_KEY)
        if isinstance(candidate, str):
            return candidate, _era(candidate)
        return self._legacy_version, _era(self._legacy_version)

    def _check_jsonrpc(
        self,
        record: ProtocolRecord,
        kind: MessageKind,
        request_id: str | int | None,
    ) -> None:
        if record.message.get("jsonrpc") != "2.0":
            self._issue(
                "invalid_jsonrpc_version",
                ConformanceSeverity.ERROR,
                "MCP messages must declare jsonrpc '2.0'",
                record.sequence,
            )
        if kind in (MessageKind.REQUEST, MessageKind.RESPONSE) and request_id is None:
            self._issue(
                "invalid_request_id",
                ConformanceSeverity.ERROR,
                "request and response IDs must be non-null strings or integers",
                record.sequence,
            )
        if kind == MessageKind.RESPONSE:
            has_result = "result" in record.message
            has_error = "error" in record.message
            if has_result == has_error:
                self._issue(
                    "invalid_response_shape",
                    ConformanceSeverity.ERROR,
                    "a response must contain exactly one of result or error",
                    record.sequence,
                )

    def _observe_request(
        self,
        record: ProtocolRecord,
        node: CausalNode,
        method: str,
        request_id: str | int,
        version: str | None,
        era: ProtocolEra,
    ) -> None:
        key = (record.direction, _typed_request_id(request_id))
        if key in self._pending:
            self._issue(
                "duplicate_outstanding_request_id",
                ConformanceSeverity.ERROR,
                "request ID is already outstanding in the same direction",
                self._pending[key].sequence,
                record.sequence,
            )
            return
        pending = _PendingRequest(
            node_id=node.node_id,
            sequence=record.sequence,
            direction=record.direction,
            request_id=request_id,
            method=method,
            protocol_version=version,
            protocol_era=era,
        )
        self._pending[key] = pending
        if method == "subscriptions/listen":
            self._subscriptions[_typed_request_id(request_id)] = pending

        if era == ProtocolEra.MODERN:
            meta = _request_meta(record.message)
            if not isinstance(meta.get(CLIENT_CAPABILITIES_KEY), dict):
                self._issue(
                    "modern_request_missing_capabilities",
                    ConformanceSeverity.ERROR,
                    "modern MCP requests must carry client capabilities in params._meta",
                    record.sequence,
                )
            if record.direction != "client_to_server":
                self._issue(
                    "modern_server_initiated_request",
                    ConformanceSeverity.ERROR,
                    "modern MCP uses MRTR instead of server-initiated requests",
                    record.sequence,
                )
        elif era == ProtocolEra.UNKNOWN:
            self._issue(
                "protocol_version_unknown",
                ConformanceSeverity.WARNING,
                "request cannot be assigned to modern or legacy MCP semantics",
                record.sequence,
            )

    def _observe_response(
        self, record: ProtocolRecord, node: CausalNode, request_id: str | int
    ) -> None:
        key = (_opposite(record.direction), _typed_request_id(request_id))
        request = self._pending.pop(key, None)
        if request is None:
            self._issue(
                "orphan_response",
                ConformanceSeverity.ERROR,
                "response has no outstanding request in the opposite direction",
                record.sequence,
            )
            return
        self.edges.append(
            CausalEdge(source=node.node_id, target=request.node_id, relation="responds_to")
        )
        self.nodes[-1] = node.model_copy(
            update={
                "protocol_version": request.protocol_version,
                "protocol_era": request.protocol_era,
            }
        )
        if request.method == "subscriptions/listen":
            self._subscriptions.pop(_typed_request_id(request_id), None)

    def _observe_notification(self, record: ProtocolRecord, node: CausalNode) -> None:
        meta = _request_meta(record.message)
        subscription_id = meta.get(SUBSCRIPTION_ID_KEY)
        if subscription_id is None:
            return
        if not isinstance(subscription_id, (str, int)) or isinstance(subscription_id, bool):
            self._issue(
                "invalid_subscription_id",
                ConformanceSeverity.ERROR,
                "subscription ID must be a string or integer",
                record.sequence,
            )
            return
        subscription = self._subscriptions.get(_typed_request_id(subscription_id))
        if subscription is None:
            self._issue(
                "orphan_subscription_notification",
                ConformanceSeverity.ERROR,
                "notification references no open subscriptions/listen request",
                record.sequence,
            )
            return
        if record.direction != "server_to_client":
            self._issue(
                "subscription_notification_direction_mismatch",
                ConformanceSeverity.ERROR,
                "subscription notifications must be sent from server to client",
                record.sequence,
            )
        self.nodes[-1] = node.model_copy(
            update={
                "protocol_version": subscription.protocol_version,
                "protocol_era": subscription.protocol_era,
            }
        )
        self.edges.append(
            CausalEdge(
                source=node.node_id,
                target=subscription.node_id,
                relation="subscription_of",
            )
        )

    def _issue(
        self,
        code: str,
        severity: ConformanceSeverity,
        message: str,
        *record_sequences: int,
    ) -> None:
        self.issues.append(
            ConformanceIssue(
                code=code,
                severity=severity,
                message=message,
                record_sequences=list(record_sequences),
            )
        )


def analyze_protocol_records(records: list[ProtocolRecord]) -> ConformanceReport:
    if not records:
        raise ValueError("protocol transcript is empty")
    run_ids = {record.run_id for record in records}
    if len(run_ids) != 1:
        raise ValueError("protocol transcript contains multiple run IDs")
    monitor = MCPConformanceMonitor(next(iter(run_ids)))
    for record in records:
        monitor.observe(record)
    return monitor.report()
