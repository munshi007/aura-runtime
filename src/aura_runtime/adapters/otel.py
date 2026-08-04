"""Normalize OTLP/JSON spans into Aura events."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from aura_runtime.models import AgentEvent, EventKind


def _attributes(items: list[dict[str, Any]] | None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for item in items or []:
        value = item.get("value", {})
        typed_value = next(iter(value.values()), None) if isinstance(value, dict) else value
        result[item.get("key", "")] = typed_value
    return result


def _timestamp(nanoseconds: str | int | None) -> datetime:
    if nanoseconds is None:
        return datetime.now(UTC)
    return datetime.fromtimestamp(int(nanoseconds) / 1_000_000_000, tz=UTC)


def _kind(span: dict[str, Any], attrs: dict[str, Any]) -> EventKind:
    operation = attrs.get("gen_ai.operation.name")
    mcp_method = attrs.get("mcp.method.name")
    status = (span.get("status") or {}).get("code")
    if operation == "execute_tool" or mcp_method == "tools/call":
        if status in (2, "STATUS_CODE_ERROR"):
            return EventKind.TOOL_CALL_FAILED
        return EventKind.TOOL_CALL_COMPLETED
    if operation in {"chat", "generate_content", "text_completion"}:
        return EventKind.MODEL_COMPLETED
    return EventKind.UNKNOWN


def events_from_otlp_json(
    payload: dict[str, Any], *, run_id: str | None = None
) -> list[AgentEvent]:
    events: list[AgentEvent] = []
    sequence = 0
    for resource_span in payload.get("resourceSpans", []):
        resource_attrs = _attributes((resource_span.get("resource") or {}).get("attributes"))
        service = resource_attrs.get("service.name", "opentelemetry")
        for scope_span in resource_span.get("scopeSpans", []):
            for span in scope_span.get("spans", []):
                attrs = _attributes(span.get("attributes"))
                current_run_id = (
                    run_id or attrs.get("gen_ai.conversation.id") or span.get("traceId")
                )
                if not current_run_id:
                    continue
                events.append(
                    AgentEvent(
                        run_id=str(current_run_id),
                        kind=_kind(span, attrs),
                        timestamp=_timestamp(span.get("startTimeUnixNano")),
                        source=str(service),
                        actor=attrs.get("gen_ai.agent.name"),
                        tool_name=attrs.get("gen_ai.tool.name"),
                        sequence=sequence,
                        trace_id=span.get("traceId"),
                        span_id=span.get("spanId"),
                        data={"span.name": span.get("name"), "attributes": attrs},
                    )
                )
                sequence += 1
    return events
