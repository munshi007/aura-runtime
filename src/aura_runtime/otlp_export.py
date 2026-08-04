"""Privacy-safe OTLP/JSON export for Aura MCP causal evidence."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from aura_runtime import __version__
from aura_runtime.conformance import (
    SUBSCRIPTION_ID_KEY,
    ConformanceIssue,
    ConformanceReport,
    ConformanceSeverity,
    MessageKind,
    analyze_protocol_records,
)
from aura_runtime.models import ProtocolRecord


def _id(seed: str, length: int) -> str:
    return sha256(seed.encode()).hexdigest()[:length]


def _unix_nano(value: datetime) -> str:
    utc = value.astimezone(UTC)
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = utc - epoch
    return str((delta.days * 86_400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1_000)


def _any_value(value: str | int | bool) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    return {"stringValue": value}


def _attributes(values: dict[str, str | int | bool | None]) -> list[dict[str, Any]]:
    return [
        {"key": key, "value": _any_value(value)}
        for key, value in sorted(values.items())
        if value is not None
    ]


def _attribute_map(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        item["key"]: next(iter(item["value"].values()))
        for item in items
        if item.get("key") and item.get("value")
    }


def _request_id(value: str | int | None) -> str | None:
    if value is None:
        return None
    return f"{type(value).__name__}:{value}"


def _tool_name(record: ProtocolRecord) -> str | None:
    params = record.message.get("params")
    if not isinstance(params, dict):
        return None
    name = params.get("name")
    return name if isinstance(name, str) else None


def _subscription_id(record: ProtocolRecord) -> str | None:
    params = record.message.get("params")
    meta = params.get("_meta") if isinstance(params, dict) else None
    value = meta.get(SUBSCRIPTION_ID_KEY) if isinstance(meta, dict) else None
    if isinstance(value, (str, int)) and not isinstance(value, bool):
        return _request_id(value)
    return None


def _issues_by_sequence(
    report: ConformanceReport,
) -> dict[int, list[ConformanceIssue]]:
    result: dict[int, list[ConformanceIssue]] = defaultdict(list)
    for issue in report.issues:
        for sequence in issue.record_sequences:
            result[sequence].append(issue)
    return result


def _issue_events(
    issues: list[ConformanceIssue], records: dict[int, ProtocolRecord]
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for issue in issues:
        sequence = issue.record_sequences[-1]
        timestamp = records.get(sequence)
        events.append(
            {
                "timeUnixNano": _unix_nano(timestamp.timestamp) if timestamp else "0",
                "name": "aura.conformance.issue",
                "attributes": _attributes(
                    {
                        "aura.conformance.code": issue.code,
                        "aura.conformance.severity": issue.severity.value,
                        "aura.conformance.message": issue.message,
                    }
                ),
            }
        )
    return events


def _content_attributes(
    request: ProtocolRecord, response: ProtocolRecord | None
) -> dict[str, str]:
    values: dict[str, str] = {}
    params = request.message.get("params")
    if isinstance(params, dict) and "arguments" in params:
        values["gen_ai.tool.call.arguments"] = json.dumps(
            params["arguments"], sort_keys=True, separators=(",", ":")
        )
    if response is not None:
        key = "error" if "error" in response.message else "result"
        if key in response.message:
            values["gen_ai.tool.call.result"] = json.dumps(
                response.message[key], sort_keys=True, separators=(",", ":")
            )
    return values


def protocol_records_to_otlp_json(
    records: list[ProtocolRecord], *, include_content: bool = False
) -> dict[str, Any]:
    """Return an OTLP/HTTP JSON ExportTraceServiceRequest payload."""
    report = analyze_protocol_records(records)
    by_sequence = {record.sequence: record for record in records}
    response_for_request = {
        int(edge.target.split(":", 1)[1]): int(edge.source.split(":", 1)[1])
        for edge in report.edges
        if edge.relation == "responds_to"
    }
    link_targets: dict[int, list[int]] = defaultdict(list)
    for edge in report.edges:
        if edge.relation == "subscription_of":
            link_targets[int(edge.source.split(":", 1)[1])].append(
                int(edge.target.split(":", 1)[1])
            )
    issues = _issues_by_sequence(report)
    trace_id = _id(f"aura-trace:{report.run_id}", 32)
    span_ids = {
        sequence: _id(f"aura-span:{report.run_id}:{sequence}:{record.content_hash}", 16)
        for sequence, record in by_sequence.items()
    }
    spans: list[dict[str, Any]] = []

    for node in report.nodes:
        record = by_sequence[node.record_sequence]
        if (
            node.kind == MessageKind.RESPONSE
            and node.record_sequence in response_for_request.values()
        ):
            continue

        response_sequence = response_for_request.get(node.record_sequence)
        response = by_sequence.get(response_sequence) if response_sequence is not None else None
        end = (
            response.timestamp
            if response and response.timestamp >= record.timestamp
            else record.timestamp
        )
        tool_name = _tool_name(record)
        is_tool = node.method == "tools/call"
        related_sequences = {node.record_sequence}
        if response_sequence is not None:
            related_sequences.add(response_sequence)
        span_issues: list[ConformanceIssue] = []
        seen_issues: set[tuple[str, tuple[int, ...]]] = set()
        for sequence in related_sequences:
            for item in issues.get(sequence, []):
                identity = (item.code, tuple(item.record_sequences))
                if identity not in seen_issues:
                    seen_issues.add(identity)
                    span_issues.append(item)
        has_error = bool(response and "error" in response.message) or any(
            issue.severity == ConformanceSeverity.ERROR for issue in span_issues
        )
        attrs: dict[str, str | int | bool | None] = {
            "aura.action": record.action.value,
            "aura.conformance.issue.count": len(span_issues),
            "aura.forwarded": record.forwarded,
            "aura.protocol.era": node.protocol_era.value,
            "aura.record.content_hash": record.content_hash,
            "aura.record.sequence": record.sequence,
            "gen_ai.operation.name": "execute_tool" if is_tool else None,
            "gen_ai.tool.call.id": str(node.request_id) if is_tool else None,
            "gen_ai.tool.name": tool_name if is_tool else None,
            "gen_ai.tool.type": "extension" if is_tool else None,
            "mcp.method.name": node.method,
            "mcp.protocol.version": node.protocol_version,
            "aura.mcp.request.id": _request_id(node.request_id),
            "aura.mcp.subscription.id": _subscription_id(record),
        }
        if response and "error" in response.message:
            error = response.message.get("error")
            code = error.get("code") if isinstance(error, dict) else "mcp_error"
            attrs["error.type"] = str(code)
        if include_content and is_tool:
            attrs.update(_content_attributes(record, response))

        links = [
            {
                "traceId": trace_id,
                "spanId": span_ids[target],
                "attributes": _attributes({"aura.causal.relation": "subscription_of"}),
            }
            for target in link_targets.get(node.record_sequence, [])
        ]
        span = {
            "traceId": trace_id,
            "spanId": span_ids[node.record_sequence],
            "name": (
                f"execute_tool {tool_name}"
                if is_tool and tool_name
                else f"mcp {node.method or node.kind.value}"
            ),
            "kind": 3 if node.kind == MessageKind.REQUEST else 1,
            "startTimeUnixNano": _unix_nano(record.timestamp),
            "endTimeUnixNano": _unix_nano(end),
            "attributes": _attributes(attrs),
            "events": _issue_events(span_issues, by_sequence),
            "status": {"code": 2 if has_error else 1},
        }
        if links:
            span["links"] = links
        spans.append(span)

    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": _attributes(
                        {
                            "aura.conformance.verdict": report.verdict,
                            "aura.run.id": report.run_id,
                            "aura.transcript.integrity": report.transcript_integrity,
                            "service.name": "aura-runtime",
                            "service.version": __version__,
                        }
                    )
                },
                "scopeSpans": [
                    {
                        "scope": {"name": "aura-runtime", "version": __version__},
                        "spans": spans,
                    }
                ],
            }
        ]
    }


def otlp_attributes(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Return flattened span attributes for tests and lightweight consumers."""
    return [
        _attribute_map(span.get("attributes", []))
        for resource in payload.get("resourceSpans", [])
        for scope in resource.get("scopeSpans", [])
        for span in scope.get("spans", [])
    ]
