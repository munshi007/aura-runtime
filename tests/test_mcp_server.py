import asyncio

from mcp import Client

from aura_runtime.mcp_server import mcp
from aura_runtime.models import AgentEvent, EventKind
from aura_runtime.store import SQLiteEventStore


def test_mcp_status_tool(tmp_path) -> None:
    async def call_status() -> None:
        async with Client(mcp) as client:
            result = await client.call_tool("aura_status", {"db_path": str(tmp_path / "aura.db")})
            integrity = await client.call_tool(
                "aura_trace_integrity",
                {"run_id": "missing", "db_path": str(tmp_path / "aura.db")},
            )
        assert result.structured_content == {
            "status": "ready",
            "run_count": 0,
            "run_ids": [],
        }
        assert integrity.structured_content == {
            "run_id": "missing",
            "record_count": 0,
            "valid": True,
            "head_hash": None,
        }

        store = SQLiteEventStore(tmp_path / "aura.db")
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
                    "db_path": str(tmp_path / "aura.db"),
                },
            )
        assert replay.structured_content["read_only"] is True
        assert replay.structured_content["replayed_finding_count"] == 1

    asyncio.run(call_status())
