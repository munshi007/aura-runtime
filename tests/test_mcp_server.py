import asyncio

from mcp import Client

from aura_runtime.conformance import CLIENT_CAPABILITIES_KEY, PROTOCOL_VERSION_KEY
from aura_runtime.mcp_server import mcp
from aura_runtime.models import AgentEvent, EventKind, GateAction, ProtocolRecord
from aura_runtime.store import SQLiteEventStore


def protocol_chain() -> list[ProtocolRecord]:
    messages = [
        (
            "client_to_server",
            {
                "jsonrpc": "2.0",
                "id": "call-1",
                "method": "tools/call",
                "params": {
                    "_meta": {
                        PROTOCOL_VERSION_KEY: "2026-07-28",
                        CLIENT_CAPABILITIES_KEY: {},
                    },
                    "name": "delete_record",
                    "arguments": {"secret": "must-not-leak"},
                },
            },
        ),
        (
            "server_to_client",
            {"jsonrpc": "2.0", "id": "orphan", "result": {"secret": "also-private"}},
        ),
    ]
    records: list[ProtocolRecord] = []
    previous_hash = ""
    for sequence, (direction, message) in enumerate(messages):
        item = ProtocolRecord.create(
            run_id="protocol-only",
            sequence=sequence,
            direction=direction,
            message=message,
            forwarded=True,
            action=GateAction.ALLOW,
            previous_hash=previous_hash,
        )
        records.append(item)
        previous_hash = item.content_hash
    return records


def test_mcp_status_tool(tmp_path) -> None:
    db_path = tmp_path / "aura.db"
    SQLiteEventStore(db_path).initialize()

    async def call_status() -> None:
        async with Client(mcp) as client:
            result = await client.call_tool("aura_status", {"db_path": str(db_path)})
            integrity = await client.call_tool(
                "aura_trace_integrity",
                {"run_id": "missing", "db_path": str(db_path)},
            )
        assert result.structured_content == {
            "status": "ready",
            "run_count": 0,
            "returned_count": 0,
            "runs": [],
        }
        assert integrity.structured_content == {
            "run_id": "missing",
            "record_count": 0,
            "valid": True,
            "head_hash": None,
        }

        store = SQLiteEventStore(db_path)
        store.append_event(
            AgentEvent(
                run_id="replay-run",
                kind=EventKind.TOOL_CALL_REQUESTED,
                tool_name="transfer_funds",
                data={"arguments": {"amount": 11}},
            )
        )
        policy_yaml = """
version: "0.1"
policies:
  - id: limit
    description: Transfer limit
    on:
      event: tool.call.requested
      tool_matches: [transfer_funds]
    constraints:
      - path: data.arguments.amount
        op: "<="
        value: 10
"""
        async with Client(mcp) as client:
            replay = await client.call_tool(
                "aura_replay",
                {
                    "run_id": "replay-run",
                    "policy_yaml": policy_yaml,
                    "db_path": str(db_path),
                },
            )
            temporal = await client.call_tool(
                "aura_temporal_state",
                {
                    "run_id": "replay-run",
                    "policy_yaml": """
version: "0.1"
policies:
  - id: transfer-completes
    description: Transfer must complete
    on:
      event: tool.call.requested
      tool_matches: [transfer_funds]
    require_after:
      event: tool.call.completed
      within_events: 2
""",
                    "db_path": str(db_path),
                },
            )
        assert replay.structured_content["read_only"] is True
        assert replay.structured_content["replayed_finding_count"] == 1
        assert temporal.structured_content["pending_obligation_count"] == 1
        assert temporal.structured_content["content_included"] is False

    asyncio.run(call_status())


def test_mcp_status_does_not_create_a_missing_database(tmp_path) -> None:
    db_path = tmp_path / "missing" / "aura.db"

    async def call_status() -> None:
        async with Client(mcp) as client:
            result = await client.call_tool("aura_status", {"db_path": str(db_path)})
        assert result.structured_content["run_count"] == 0

    asyncio.run(call_status())
    assert not db_path.exists()


def test_mcp_conformance_tools_are_bounded_and_privacy_safe(tmp_path) -> None:
    db_path = tmp_path / "aura.db"
    store = SQLiteEventStore(db_path)
    for item in protocol_chain():
        store.append_protocol_record(item)

    async def inspect_evidence() -> None:
        async with Client(mcp) as client:
            status = await client.call_tool(
                "aura_status", {"db_path": str(db_path), "limit": 10}
            )
            report = await client.call_tool(
                "aura_conformance", {"run_id": "protocol-only", "db_path": str(db_path)}
            )
            explanation = await client.call_tool(
                "aura_explain_issue",
                {
                    "run_id": "protocol-only",
                    "issue_index": 0,
                    "db_path": str(db_path),
                    "max_hops": 1,
                },
            )

        assert status.structured_content["run_count"] == 1
        summary = status.structured_content["runs"][0]
        assert summary["run_id"] == "protocol-only"
        assert summary["protocol_record_count"] == 2
        assert summary["conformance_verdict"] == "fail"
        assert summary["content_included"] is False
        assert report.structured_content["verdict"] == "fail"
        assert explanation.structured_content["issue"]["code"] == "orphan_response"
        assert {node["record_sequence"] for node in explanation.structured_content["nodes"]} == {
            1
        }

        serialized = str(status.structured_content) + str(report.structured_content)
        serialized += str(explanation.structured_content)
        assert "must-not-leak" not in serialized
        assert "also-private" not in serialized

    asyncio.run(inspect_evidence())


def test_mcp_evidence_tools_declare_read_only_annotations() -> None:
    async def inspect_tools() -> None:
        async with Client(mcp) as client:
            result = await client.list_tools()
        evidence = {
            tool.name: tool
            for tool in result.tools
            if tool.name
            in {
                "aura_status",
                "aura_conformance",
                "aura_explain_issue",
                "aura_temporal_state",
                "aura_object_contract",
                "aura_object_state",
            }
        }
        assert set(evidence) == {
            "aura_status",
            "aura_conformance",
            "aura_explain_issue",
            "aura_temporal_state",
            "aura_object_contract",
            "aura_object_state",
        }
        assert all(tool.annotations.read_only_hint is True for tool in evidence.values())
        assert all(tool.annotations.open_world_hint is False for tool in evidence.values())

    asyncio.run(inspect_tools())
