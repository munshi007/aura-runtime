"""Run deterministic safe or dangerous scenarios through Aura's real stdio proxy."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = Path(__file__).resolve().parent


async def run_scenario(db: Path, run_id: str, variant: str) -> None:
    proxy_args = [
        "-m",
        "aura_runtime.cli",
        "proxy",
        "--db",
        str(db.resolve()),
        "--run-id",
        run_id,
        "--policy",
        str(HERE / "policy.yaml"),
        "--mode",
        "observe",
        "--",
        sys.executable,
        str(HERE / "server.py"),
    ]
    server = StdioServerParameters(command=sys.executable, args=proxy_args)
    async with Client(stdio_client(server)) as client:
        await client.list_tools()
        await client.call_tool("search_customer", {"customer_id": "cus_123"})
        await client.call_tool("refund_order", {"order_id": "ord_456", "amount": 125})
        if variant == "dangerous":
            await client.call_tool("delete_customer", {"customer_id": "cus_123"})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--variant", choices=("safe", "dangerous"), default="safe")
    args = parser.parse_args()
    asyncio.run(run_scenario(args.db, args.run_id, args.variant))


if __name__ == "__main__":
    main()
