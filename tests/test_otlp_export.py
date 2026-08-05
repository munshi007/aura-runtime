from datetime import UTC, datetime, timedelta
from pathlib import Path

from typer.testing import CliRunner

from aura_runtime.adapters.otel import events_from_otlp_json
from aura_runtime.cli import app
from aura_runtime.conformance import (
    CLIENT_CAPABILITIES_KEY,
    PROTOCOL_VERSION_KEY,
    SUBSCRIPTION_ID_KEY,
)
from aura_runtime.models import EventKind, GateAction, ProtocolRecord
from aura_runtime.otlp_export import otlp_attributes, protocol_records_to_otlp_json
from aura_runtime.store import SQLiteEventStore


def transcript(*, error: bool = False) -> list[ProtocolRecord]:
    started = datetime(2026, 8, 4, 9, 0, tzinfo=UTC)
    request = ProtocolRecord.create(
        run_id="run-1",
        sequence=0,
        direction="client_to_server",
        message={
            "jsonrpc": "2.0",
            "id": "call-1",
            "method": "tools/call",
            "params": {
                "_meta": {
                    PROTOCOL_VERSION_KEY: "2026-07-28",
                    CLIENT_CAPABILITIES_KEY: {"tools": {}},
                },
                "name": "lookup",
                "arguments": {"secret": "do-not-export"},
            },
        },
        forwarded=True,
        action=GateAction.ALLOW,
    ).model_copy(update={"timestamp": started})
    response_message = (
        {"jsonrpc": "2.0", "id": "call-1", "error": {"code": -32001, "message": "no"}}
        if error
        else {"jsonrpc": "2.0", "id": "call-1", "result": {"value": 42}}
    )
    response = ProtocolRecord.create(
        run_id="run-1",
        sequence=1,
        direction="server_to_client",
        message=response_message,
        forwarded=True,
        action=GateAction.ALLOW,
        previous_hash=request.content_hash,
    ).model_copy(update={"timestamp": started + timedelta(milliseconds=25)})
    return [request, response]


def spans(payload: dict) -> list[dict]:
    return payload["resourceSpans"][0]["scopeSpans"][0]["spans"]


def test_otlp_export_is_deterministic_standard_and_private_by_default() -> None:
    first = protocol_records_to_otlp_json(transcript())
    second = protocol_records_to_otlp_json(transcript())
    span = spans(first)[0]
    attrs = otlp_attributes(first)[0]

    assert first == second
    assert len(span["traceId"]) == 32
    assert len(span["spanId"]) == 16
    assert span["kind"] == 3
    assert span["status"] == {"code": 1}
    assert span["name"] == "execute_tool lookup"
    assert attrs["gen_ai.operation.name"] == "execute_tool"
    assert attrs["gen_ai.tool.name"] == "lookup"
    assert attrs["gen_ai.tool.call.id"] == "call-1"
    assert attrs["aura.mcp.request.id"] == "str:call-1"
    assert attrs["mcp.method.name"] == "tools/call"
    assert attrs["mcp.protocol.version"] == "2026-07-28"
    assert "gen_ai.tool.call.arguments" not in attrs
    assert "gen_ai.tool.call.result" not in attrs
    assert "do-not-export" not in str(first)


def test_content_is_exported_only_with_explicit_opt_in() -> None:
    payload = protocol_records_to_otlp_json(transcript(), include_content=True)
    attrs = otlp_attributes(payload)[0]

    assert attrs["gen_ai.tool.call.arguments"] == '{"secret":"do-not-export"}'
    assert attrs["gen_ai.tool.call.result"] == '{"value":42}'


def test_mcp_error_sets_otel_error_status_and_round_trips() -> None:
    payload = protocol_records_to_otlp_json(transcript(error=True))
    span = spans(payload)[0]
    attrs = otlp_attributes(payload)[0]

    assert span["status"] == {"code": 2}
    assert attrs["error.type"] == "-32001"
    events = events_from_otlp_json(payload)
    assert len(events) == 2
    assert events[0].kind == EventKind.TOOL_CALL_REQUESTED
    assert events[1].kind == EventKind.TOOL_CALL_FAILED
    assert events[1].parent_event_id == events[0].event_id
    assert events[0].tool_name == "lookup"


def test_cli_writes_otlp_json(tmp_path: Path) -> None:
    db = tmp_path / "aura.db"
    store = SQLiteEventStore(db)
    for record in transcript():
        store.append_protocol_record(record)
    output = tmp_path / "traces.json"

    result = CliRunner().invoke(
        app,
        ["export-otlp", "run-1", "--db", str(db), "--output", str(output)],
    )

    assert result.exit_code == 0, result.output
    assert '"resourceSpans"' in output.read_text(encoding="utf-8")


def test_subscription_causality_is_exported_as_otel_link() -> None:
    started = datetime(2026, 8, 4, 9, 0, tzinfo=UTC)
    request = ProtocolRecord.create(
        run_id="run-1",
        sequence=0,
        direction="client_to_server",
        message={
            "jsonrpc": "2.0",
            "id": "sub-1",
            "method": "subscriptions/listen",
            "params": {
                "_meta": {
                    PROTOCOL_VERSION_KEY: "2026-07-28",
                    CLIENT_CAPABILITIES_KEY: {},
                }
            },
        },
        forwarded=True,
        action=GateAction.ALLOW,
    ).model_copy(update={"timestamp": started})
    notification = ProtocolRecord.create(
        run_id="run-1",
        sequence=1,
        direction="server_to_client",
        message={
            "jsonrpc": "2.0",
            "method": "notifications/tools/list_changed",
            "params": {"_meta": {SUBSCRIPTION_ID_KEY: "sub-1"}},
        },
        forwarded=True,
        action=GateAction.ALLOW,
        previous_hash=request.content_hash,
    ).model_copy(update={"timestamp": started + timedelta(seconds=1)})

    exported = spans(protocol_records_to_otlp_json([request, notification]))

    assert len(exported) == 2
    assert exported[1]["links"][0]["spanId"] == exported[0]["spanId"]
    link_attrs = {
        item["key"]: next(iter(item["value"].values()))
        for item in exported[1]["links"][0]["attributes"]
    }
    assert link_attrs["aura.causal.relation"] == "subscription_of"


def test_conformance_failure_becomes_error_status_and_structured_event() -> None:
    item = ProtocolRecord.create(
        run_id="run-1",
        sequence=0,
        direction="client_to_server",
        message={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {"_meta": {PROTOCOL_VERSION_KEY: "2026-07-28"}},
        },
        forwarded=True,
        action=GateAction.ALLOW,
    )

    payload = protocol_records_to_otlp_json([item])
    span = spans(payload)[0]
    resource_attrs = {
        attribute["key"]: next(iter(attribute["value"].values()))
        for attribute in payload["resourceSpans"][0]["resource"]["attributes"]
    }

    assert resource_attrs["aura.conformance.verdict"] == "fail"
    assert span["status"] == {"code": 2}
    assert span["events"][0]["name"] == "aura.conformance.issue"
    event_attrs = {
        attribute["key"]: next(iter(attribute["value"].values()))
        for attribute in span["events"][0]["attributes"]
    }
    assert event_attrs["aura.conformance.code"] == "modern_request_missing_capabilities"
