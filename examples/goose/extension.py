"""Expose Aura's reference MCP server to Goose through the flight recorder."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from aura_runtime.flight import EnforcementMode, MCPFlightRecorder
from aura_runtime.policy import AuraSpec
from aura_runtime.proxy import run_stdio_proxy
from aura_runtime.store import SQLiteEventStore

ROOT = Path(__file__).resolve().parents[2]
REFERENCE_AGENT = ROOT / "examples" / "reference_agent"


def main() -> None:
    db = Path(os.environ.get("AURA_GOOSE_DB", ".aura/goose/manual.db"))
    run_id = os.environ.get("AURA_GOOSE_RUN_ID", "goose-manual")
    mode = EnforcementMode(os.environ.get("AURA_GOOSE_MODE", "observe"))
    recorder = MCPFlightRecorder(
        run_id=run_id,
        store=SQLiteEventStore(db),
        spec=AuraSpec.from_yaml(REFERENCE_AGENT / "policy.yaml"),
        mode=mode,
    )
    exit_code = asyncio.run(
        run_stdio_proxy(
            [sys.executable, str(REFERENCE_AGENT / "server.py")],
            recorder,
        )
    )
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
