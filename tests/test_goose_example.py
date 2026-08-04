import asyncio
import os
import sys
from pathlib import Path

import yaml
from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client

from aura_runtime.contract import TraceContract, check_contract
from aura_runtime.store import SQLiteEventStore

ROOT = Path(__file__).parents[1]
GOOSE = ROOT / "examples" / "goose"
REFERENCE = ROOT / "examples" / "reference_agent"


def load_recipe(name: str) -> dict:
    return yaml.safe_load((GOOSE / f"{name}.yaml").read_text(encoding="utf-8"))


def test_goose_recipes_use_aura_as_the_only_mcp_boundary() -> None:
    for name in ("safe", "dangerous"):
        recipe = load_recipe(name)
        assert recipe["version"] == "1.0.0"
        assert len(recipe["extensions"]) == 1
        extension = recipe["extensions"][0]
        assert extension["type"] == "stdio"
        assert extension["cmd"] == "uv"
        assert extension["args"] == ["run", "python", "examples/goose/extension.py"]
        assert extension["available_tools"] == [
            "search_customer",
            "refund_order",
            "delete_customer",
        ]


async def capture(db: Path, run_id: str, *, dangerous: bool) -> None:
    env = os.environ.copy()
    env.update(
        {
            "AURA_GOOSE_DB": str(db),
            "AURA_GOOSE_RUN_ID": run_id,
            "AURA_GOOSE_MODE": "observe",
        }
    )
    server = StdioServerParameters(
        command=sys.executable,
        args=[str(GOOSE / "extension.py")],
        env=env,
    )
    async with Client(stdio_client(server)) as client:
        await client.list_tools()
        await client.call_tool("search_customer", {"customer_id": "cus_123"})
        await client.call_tool("refund_order", {"order_id": "ord_456", "amount": 125})
        if dangerous:
            await client.call_tool("delete_customer", {"customer_id": "cus_123"})


def test_goose_boundary_matches_and_then_breaks_the_trace_contract(tmp_path: Path) -> None:
    contract = TraceContract.from_yaml(REFERENCE / "aura-contract.yaml")

    safe_db = tmp_path / "safe.db"
    asyncio.run(capture(safe_db, "goose-safe", dangerous=False))
    safe_report = check_contract(contract, SQLiteEventStore(safe_db), "goose-safe")
    assert safe_report.verdict == "pass"

    dangerous_db = tmp_path / "dangerous.db"
    asyncio.run(capture(dangerous_db, "goose-dangerous", dangerous=True))
    dangerous_report = check_contract(
        contract,
        SQLiteEventStore(dangerous_db),
        "goose-dangerous",
    )
    assert dangerous_report.verdict == "fail"
    assert dangerous_report.run_diff.first_divergence_index == 4
    assert [finding.policy_id for finding in dangerous_report.introduced_findings] == [
        "destructive-tools-require-approval"
    ]
