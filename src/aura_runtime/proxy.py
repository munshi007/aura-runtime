"""Transparent newline-delimited JSON-RPC proxy for MCP stdio servers."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Sequence
from contextlib import suppress

from aura_runtime.flight import MCPFlightRecorder


async def run_stdio_proxy(command: Sequence[str], recorder: MCPFlightRecorder) -> int:
    if not command:
        raise ValueError("upstream command is required")

    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None

    async def client_to_server() -> None:
        while raw_line := await asyncio.to_thread(sys.stdin.buffer.readline):
            try:
                message = json.loads(raw_line)
            except json.JSONDecodeError:
                process.stdin.write(raw_line)
                await process.stdin.drain()
                continue
            result = recorder.handle_client_message(message)
            if result.forward:
                process.stdin.write(raw_line)
                await process.stdin.drain()
            elif result.response is not None:
                encoded = json.dumps(result.response, separators=(",", ":")).encode() + b"\n"
                sys.stdout.buffer.write(encoded)
                sys.stdout.buffer.flush()
        process.stdin.close()
        await process.stdin.wait_closed()

    async def server_to_client() -> None:
        while raw_line := await process.stdout.readline():
            with suppress(json.JSONDecodeError):
                recorder.handle_server_message(json.loads(raw_line))
            sys.stdout.buffer.write(raw_line)
            sys.stdout.buffer.flush()

    async def forward_stderr() -> None:
        while chunk := await process.stderr.read(8192):
            sys.stderr.buffer.write(chunk)
            sys.stderr.buffer.flush()

    await asyncio.gather(client_to_server(), server_to_client(), forward_stderr())
    return await process.wait()
