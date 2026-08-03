"""MCP tools for inspecting an Aura Runtime evidence store."""

from __future__ import annotations

from mcp.server import MCPServer

from aura_runtime.flight import verify_protocol_chain
from aura_runtime.policy import AuraSpec
from aura_runtime.replay import compare_manifests, compare_runs, replay_run
from aura_runtime.store import SQLiteEventStore

mcp = MCPServer("Aura Runtime")


@mcp.tool()
def aura_status(db_path: str = ".aura/aura.db") -> dict[str, object]:
    """Return the runs currently present in an Aura evidence store."""
    store = SQLiteEventStore(db_path)
    runs = list(store.run_ids())
    return {"status": "ready", "run_count": len(runs), "run_ids": runs}


@mcp.tool()
def aura_findings(run_id: str, db_path: str = ".aura/aura.db") -> dict[str, object]:
    """Return deterministic policy findings for one agent run."""
    store = SQLiteEventStore(db_path)
    findings = [finding.model_dump(mode="json") for finding in store.findings(run_id)]
    return {"run_id": run_id, "finding_count": len(findings), "findings": findings}


@mcp.resource("aura://runs/{run_id}/events")
def aura_events(run_id: str) -> str:
    """Return the canonical event stream for a run as JSON Lines."""
    store = SQLiteEventStore()
    return "\n".join(event.model_dump_json() for event in store.events(run_id))


@mcp.tool()
def aura_trace_integrity(run_id: str, db_path: str = ".aura/aura.db") -> dict[str, object]:
    """Verify the hash chain for an MCP flight-recorder transcript."""
    records = SQLiteEventStore(db_path).protocol_records(run_id)
    return {
        "run_id": run_id,
        "record_count": len(records),
        "valid": verify_protocol_chain(records),
        "head_hash": records[-1].content_hash if records else None,
    }


@mcp.tool()
def aura_replay(
    run_id: str,
    policy_yaml: str,
    db_path: str = ".aura/aura.db",
) -> dict[str, object]:
    """Replay a run read-only against an AuraSpec YAML document."""
    report = replay_run(SQLiteEventStore(db_path), run_id, AuraSpec.from_yaml_text(policy_yaml))
    return report.model_dump(mode="json")


@mcp.tool()
def aura_compare_runs(
    left_run_id: str,
    right_run_id: str,
    db_path: str = ".aura/aura.db",
) -> dict[str, object]:
    """Find the first behavioral divergence between two captured runs."""
    report = compare_runs(SQLiteEventStore(db_path), left_run_id, right_run_id)
    return report.model_dump(mode="json")


@mcp.tool()
def aura_compare_manifests(
    left_run_id: str,
    right_run_id: str,
    db_path: str = ".aura/aura.db",
) -> dict[str, object]:
    """Compare latest MCP tool manifests captured for two runs."""
    report = compare_manifests(SQLiteEventStore(db_path), left_run_id, right_run_id)
    return report.model_dump(mode="json")
