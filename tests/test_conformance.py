from pathlib import Path

from typer.testing import CliRunner

from aura_runtime.cli import app
from aura_runtime.conformance import (
    CLIENT_CAPABILITIES_KEY,
    PROTOCOL_VERSION_KEY,
    SUBSCRIPTION_ID_KEY,
    MCPConformanceMonitor,
    analyze_protocol_records,
)
from aura_runtime.models import GateAction, ProtocolRecord
from aura_runtime.store import SQLiteEventStore


def record(
    sequence: int,
    direction: str,
    message: dict,
    *,
    previous_hash: str = "",
) -> ProtocolRecord:
    return ProtocolRecord.create(
        run_id="run-1",
        sequence=sequence,
        direction=direction,
        message=message,
        forwarded=True,
        action=GateAction.ALLOW,
        previous_hash=previous_hash,
    )


def chain(messages: list[tuple[str, dict]]) -> list[ProtocolRecord]:
    records: list[ProtocolRecord] = []
    previous_hash = ""
    for sequence, (direction, message) in enumerate(messages):
        item = record(sequence, direction, message, previous_hash=previous_hash)
        records.append(item)
        previous_hash = item.content_hash
    return records


def modern_meta() -> dict:
    return {
        PROTOCOL_VERSION_KEY: "2026-07-28",
        CLIENT_CAPABILITIES_KEY: {"tools": {}},
    }


def test_modern_request_response_builds_causal_edge() -> None:
    records = chain(
        [
            (
                "client_to_server",
                {
                    "jsonrpc": "2.0",
                    "id": "call-1",
                    "method": "tools/call",
                    "params": {
                        "_meta": modern_meta(),
                        "name": "search",
                        "arguments": {"query": "Aura"},
                    },
                },
            ),
            (
                "server_to_client",
                {"jsonrpc": "2.0", "id": "call-1", "result": {"content": []}},
            ),
        ]
    )

    report = analyze_protocol_records(records)

    assert report.verdict == "pass"
    assert report.transcript_integrity is True
    assert report.protocol_versions == ["2026-07-28"]
    assert report.open_requests == []
    assert report.edges[0].relation == "responds_to"
    assert report.edges[0].source == "record:1"
    assert report.edges[0].target == "record:0"
    assert report.nodes[0].protocol_era == "modern"
    assert report.nodes[1].protocol_era == "modern"
    assert report.nodes[1].protocol_version == "2026-07-28"


def test_legacy_initialize_version_is_inherited_by_later_requests() -> None:
    records = chain(
        [
            (
                "client_to_server",
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"protocolVersion": "2025-11-25"},
                },
            ),
            (
                "server_to_client",
                {"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}},
            ),
            (
                "client_to_server",
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/list",
                    "params": {},
                },
            ),
        ]
    )

    report = analyze_protocol_records(records)

    assert report.verdict == "pass"
    assert report.protocol_versions == ["2025-11-25"]
    assert report.nodes[2].protocol_era == "legacy"
    assert report.open_requests == ["client_to_server:int:2:tools/list"]


def test_monitor_reports_duplicate_and_orphan_at_earliest_record() -> None:
    requests = chain(
        [
            (
                "client_to_server",
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "tools/list",
                    "params": {"_meta": modern_meta()},
                },
            ),
            (
                "client_to_server",
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "tools/call",
                    "params": {"_meta": modern_meta(), "name": "search"},
                },
            ),
            (
                "server_to_client",
                {"jsonrpc": "2.0", "id": 99, "result": {}},
            ),
        ]
    )
    monitor = MCPConformanceMonitor("run-1")

    assert monitor.observe(requests[0]) == []
    duplicate = monitor.observe(requests[1])
    orphan = monitor.observe(requests[2])

    assert [issue.code for issue in duplicate] == ["duplicate_outstanding_request_id"]
    assert duplicate[0].record_sequences == [0, 1]
    assert [issue.code for issue in orphan] == ["orphan_response"]
    assert monitor.report().verdict == "fail"


def test_modern_metadata_and_server_request_invariants() -> None:
    records = chain(
        [
            (
                "client_to_server",
                {
                    "jsonrpc": "2.0",
                    "id": "missing-capabilities",
                    "method": "tools/list",
                    "params": {"_meta": {PROTOCOL_VERSION_KEY: "2026-07-28"}},
                },
            ),
            (
                "server_to_client",
                {
                    "jsonrpc": "2.0",
                    "id": "server-request",
                    "method": "sampling/createMessage",
                    "params": {"_meta": modern_meta()},
                },
            ),
        ]
    )

    report = analyze_protocol_records(records)

    assert {issue.code for issue in report.issues} == {
        "modern_request_missing_capabilities",
        "modern_server_initiated_request",
    }


def test_subscription_notifications_link_to_open_listener() -> None:
    messages = [
        (
            "client_to_server",
            {
                "jsonrpc": "2.0",
                "id": "subscription-1",
                "method": "subscriptions/listen",
                "params": {"_meta": modern_meta(), "filter": {"toolsListChanged": True}},
            },
        ),
        (
            "server_to_client",
            {
                "jsonrpc": "2.0",
                "method": "notifications/tools/list_changed",
                "params": {"_meta": {SUBSCRIPTION_ID_KEY: "subscription-1"}},
            },
        ),
    ]

    report = analyze_protocol_records(chain(messages))

    assert report.verdict == "pass"
    assert report.edges[0].relation == "subscription_of"
    assert report.edges[0].target == "record:0"
    assert report.nodes[1].protocol_era == "modern"


def test_broken_hash_and_invalid_response_are_deterministic() -> None:
    valid = record(
        0,
        "server_to_client",
        {"jsonrpc": "1.0", "id": 3, "result": {}, "error": {"code": -1}},
    )
    tampered = valid.model_copy(update={"content_hash": "0" * 64})

    report = analyze_protocol_records([tampered])

    assert report.transcript_integrity is False
    assert [issue.code for issue in report.issues] == [
        "record_hash_invalid",
        "invalid_jsonrpc_version",
        "invalid_response_shape",
        "orphan_response",
    ]


def test_conformance_cli_reports_stored_transcript(tmp_path: Path) -> None:
    store = SQLiteEventStore(tmp_path / "aura.db")
    for item in chain(
        [
            (
                "client_to_server",
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"protocolVersion": "2025-11-25"},
                },
            ),
            (
                "server_to_client",
                {"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}},
            ),
        ]
    ):
        store.append_protocol_record(item)

    result = CliRunner().invoke(
        app, ["conformance", "run-1", "--db", str(tmp_path / "aura.db")]
    )

    assert result.exit_code == 0, result.output
    assert '"verdict": "pass"' in result.output
    assert '"responds_to"' in result.output
