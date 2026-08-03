import asyncio

from mcp import Client

from aura_runtime.mcp_server import mcp


def test_mcp_status_tool(tmp_path) -> None:
    async def call_status() -> None:
        async with Client(mcp) as client:
            result = await client.call_tool("aura_status", {"db_path": str(tmp_path / "aura.db")})
        assert result.structured_content == {
            "status": "ready",
            "run_count": 0,
            "run_ids": [],
        }

    asyncio.run(call_status())
