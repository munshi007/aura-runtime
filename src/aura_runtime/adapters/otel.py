"""Normalize OTLP/JSON spans into privacy-safe Aura lifecycle events."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from aura_runtime.models import AgentEvent, EventKind

_SAFE_GENAI_ATTRIBUTES = frozenset(
    {
        "gen_ai.operation.name",
        "gen_ai.agent.id",
        "gen_ai.agent.name",
        "gen_ai.conversation.id",
        "gen_ai.provider.name",
        "gen_ai.request.model",
        "gen_ai.response.model",
        "gen_ai.tool.call.id",
        "gen_ai.tool.name",
        "gen_ai.tool.type",
        "gen_ai.usage.input_tokens",
        "gen_ai.usage.output_tokens",
        "error.type",
        "server.address",
        "server.port",
    }
)


def _otel_value(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    for key in (
        "stringValue",
        "boolValue",
        "intValue",
        "doubleValue",
        "bytesValue",
        "arrayValue",
        "kvlistValue",
    ):
        if key in value:
            return value[key]
    return None


def _attributes(items: list[dict[str, Any]] | dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(items, dict):
        return dict(items)
    result: dict[str, Any] = {}
    for item in items or []:
        key = item.get("key")
        if key:
            result[str(key)] = _otel_value(item.get("value"))
    return result


def _timestamp(nanoseconds: str | int | None) -> datetime:
    if nanoseconds is None:
        return datetime.now(UTC)
    return datetime.fromtimestamp(int(nanoseconds) / 1_000_000_000, tz=UTC)


def _event_id(trace_id: str, span_id: str, boundary: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"aura:otel:{trace_id}:{span_id}:{boundary}")


def _safe_data(span: dict[str, Any], attrs: dict[str, Any]) -> dict[str, Any]:
    safe = {key: value for key, value in attrs.items() if key in _SAFE_GENAI_ATTRIBUTES}
    data: dict[str, Any] = {
        "span.name": span.get("name"),
        "span.kind": span.get("kind"),
        "attributes": safe,
        "derived_from_span": True,
    }
    dropped = sorted(key for key in attrs if key.startswith("gen_ai.") and key not in safe)
    if dropped:
        data["dropped_attribute_names"] = dropped
    return data


def _boundaries(span: dict[str, Any], attrs: dict[str, Any]) -> tuple[EventKind, EventKind] | None:
    operation = attrs.get("gen_ai.operation.name")
    if operation == "invoke_agent":
        return EventKind.RUN_STARTED, EventKind.RUN_COMPLETED
    if operation == "execute_tool" or attrs.get("mcp.method.name") == "tools/call":
        failed = (span.get("status") or {}).get("code") in (2, "STATUS_CODE_ERROR")
        end = EventKind.TOOL_CALL_FAILED if failed else EventKind.TOOL_CALL_COMPLETED
        return EventKind.TOOL_CALL_REQUESTED, end
    if operation in {"chat", "generate_content", "text_completion"}:
        return EventKind.MODEL_REQUESTED, EventKind.MODEL_COMPLETED
    return None


def _span_events(
    span: dict[str, Any], *, service: str, explicit_run_id: str | None
) -> list[AgentEvent]:
    attrs = _attributes(span.get("attributes"))
    trace_id = str(span.get("traceId") or "")
    span_id = str(span.get("spanId") or "")
    run_id = explicit_run_id or attrs.get("gen_ai.conversation.id") or trace_id
    if not run_id:
        return []

    common: dict[str, Any] = {
        "run_id": str(run_id),
        "source": service,
        "actor": attrs.get("gen_ai.agent.name"),
        "tool_name": attrs.get("gen_ai.tool.name"),
        "trace_id": trace_id or None,
        "span_id": span_id or None,
        "data": _safe_data(span, attrs),
    }
    boundaries = _boundaries(span, attrs)
    if boundaries is None:
        return [
            AgentEvent(
                event_id=_event_id(trace_id, span_id, "observed"),
                kind=EventKind.UNKNOWN,
                timestamp=_timestamp(span.get("startTimeUnixNano")),
                **common,
            )
        ]

    start_kind, end_kind = boundaries
    start_id = _event_id(trace_id, span_id, "start")
    start = AgentEvent(
        event_id=start_id,
        kind=start_kind,
        timestamp=_timestamp(span.get("startTimeUnixNano")),
        **common,
    )
    end = AgentEvent(
        event_id=_event_id(trace_id, span_id, "end"),
        kind=end_kind,
        timestamp=_timestamp(span.get("endTimeUnixNano") or span.get("startTimeUnixNano")),
        parent_event_id=start_id,
        **common,
    )
    return [start, end]


def events_from_otlp_json(
    payload: dict[str, Any], *, run_id: str | None = None
) -> list[AgentEvent]:
    events: list[AgentEvent] = []
    for resource_span in payload.get("resourceSpans", []):
        resource_attrs = _attributes((resource_span.get("resource") or {}).get("attributes"))
        service = resource_attrs.get("service.name", "opentelemetry")
        for scope_span in resource_span.get("scopeSpans", []):
            for span in scope_span.get("spans", []):
                events.extend(_span_events(span, service=str(service), explicit_run_id=run_id))

    counters: defaultdict[str, int] = defaultdict(int)
    ordered = sorted(events, key=lambda event: (event.timestamp, str(event.event_id)))
    sequenced: list[AgentEvent] = []
    for event in ordered:
        sequence = counters[event.run_id]
        counters[event.run_id] += 1
        sequenced.append(event.model_copy(update={"sequence": sequence}))
    return sequenced
