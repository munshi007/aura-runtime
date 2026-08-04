"""Privacy-safe MCP tools for inspecting an Aura Runtime evidence store."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from aura_runtime.conformance import (
    CausalEdge,
    CausalNode,
    ConformanceIssue,
    ConformanceReport,
    analyze_protocol_records,
)
from aura_runtime.contract import TraceContract, check_contract
from aura_runtime.flight import verify_protocol_chain
from aura_runtime.object_process import (
    ObjectBehaviorProfile,
    ObjectConformanceReport,
    compare_object_behavior,
    discover_object_behavior,
)
from aura_runtime.policy import AuraSpec
from aura_runtime.replay import compare_manifests, compare_runs, replay_run
from aura_runtime.store import SQLiteEventStore
from aura_runtime.verifier import OnlineTemporalMonitor, TemporalMonitorReport

mcp = MCPServer("Aura Runtime")

READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


class RunSummary(BaseModel):
    """Content-free evidence summary for one run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    event_count: int = Field(ge=0)
    finding_count: int = Field(ge=0)
    protocol_record_count: int = Field(ge=0)
    manifest_count: int = Field(ge=0)
    conformance_verdict: Literal["pass", "fail", "not_recorded"]
    transcript_integrity: bool | None
    conformance_issue_count: int = Field(ge=0)
    protocol_versions: list[str]
    content_included: Literal[False] = False


class RunList(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["ready"] = "ready"
    run_count: int = Field(ge=0)
    returned_count: int = Field(ge=0)
    runs: list[RunSummary]


class CausalNeighborhood(BaseModel):
    """Bounded, content-free evidence around one conformance issue."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    issue_index: int = Field(ge=0)
    issue: ConformanceIssue
    nodes: list[CausalNode]
    edges: list[CausalEdge]
    content_included: Literal[False] = False


def _default_db_path() -> str:
    return os.environ.get("AURA_DB_PATH", ".aura/aura.db")


def _store(db_path: str) -> SQLiteEventStore:
    """Open an existing evidence database without creating files or journals."""
    return SQLiteEventStore(db_path, read_only=True)


def _summary(store: SQLiteEventStore, run_id: str) -> RunSummary:
    records = store.protocol_records(run_id)
    report = analyze_protocol_records(records) if records else None
    return RunSummary(
        run_id=run_id,
        event_count=len(store.events(run_id)),
        finding_count=len(store.findings(run_id)),
        protocol_record_count=len(records),
        manifest_count=len(store.manifests(run_id)),
        conformance_verdict=report.verdict if report else "not_recorded",
        transcript_integrity=report.transcript_integrity if report else None,
        conformance_issue_count=len(report.issues) if report else 0,
        protocol_versions=report.protocol_versions if report else [],
    )


def _conformance(store: SQLiteEventStore, run_id: str) -> ConformanceReport:
    try:
        return analyze_protocol_records(store.protocol_records(run_id))
    except ValueError as error:
        raise ValueError(f"run {run_id!r} has no MCP protocol transcript") from error


@mcp.tool(
    title="List Aura evidence runs",
    annotations=READ_ONLY,
    structured_output=True,
)
def aura_status(db_path: str = ".aura/aura.db", limit: int = 50) -> RunList:
    """List privacy-safe run summaries; no prompts, arguments, or results are returned."""
    if not 1 <= limit <= 200:
        raise ValueError("limit must be between 1 and 200")
    if not Path(db_path).is_file():
        return RunList(run_count=0, returned_count=0, runs=[])
    store = _store(db_path)
    runs = list(store.run_ids())
    selected = runs[:limit]
    return RunList(
        run_count=len(runs),
        returned_count=len(selected),
        runs=[_summary(store, run_id) for run_id in selected],
    )


@mcp.tool(annotations=READ_ONLY, structured_output=True)
def aura_findings(run_id: str, db_path: str = ".aura/aura.db") -> dict[str, object]:
    """Return deterministic policy findings for one agent run."""
    store = _store(db_path)
    findings = [finding.model_dump(mode="json") for finding in store.findings(run_id)]
    return {"run_id": run_id, "finding_count": len(findings), "findings": findings}


@mcp.resource(
    "aura://runs/{run_id}/conformance",
    title="Aura MCP conformance report",
    description="Content-free causal and protocol conformance evidence for one run.",
    mime_type="application/json",
)
def aura_conformance_resource(run_id: str) -> str:
    """Read a conformance report from AURA_DB_PATH (default .aura/aura.db)."""
    report = _conformance(_store(_default_db_path()), run_id)
    return json.dumps(report.model_dump(mode="json"), sort_keys=True)


@mcp.tool(
    title="Inspect Aura conformance",
    annotations=READ_ONLY,
    structured_output=True,
)
def aura_conformance(
    run_id: str,
    db_path: str = ".aura/aura.db",
) -> ConformanceReport:
    """Return a content-free MCP causal graph and deterministic conformance issues."""
    return _conformance(_store(db_path), run_id)


@mcp.tool(
    title="Explain Aura conformance issue",
    annotations=READ_ONLY,
    structured_output=True,
)
def aura_explain_issue(
    run_id: str,
    issue_index: int,
    db_path: str = ".aura/aura.db",
    max_hops: int = 1,
) -> CausalNeighborhood:
    """Return a bounded causal neighborhood for one issue; wire content is never included."""
    if not 0 <= max_hops <= 5:
        raise ValueError("max_hops must be between 0 and 5")
    report = _conformance(_store(db_path), run_id)
    if not 0 <= issue_index < len(report.issues):
        raise ValueError(
            f"issue_index must be between 0 and {len(report.issues) - 1} for run {run_id!r}"
        )

    issue = report.issues[issue_index]
    selected = {f"record:{sequence}" for sequence in issue.record_sequences}
    for _ in range(max_hops):
        selected.update(
            endpoint
            for edge in report.edges
            if edge.source in selected or edge.target in selected
            for endpoint in (edge.source, edge.target)
        )
    return CausalNeighborhood(
        run_id=run_id,
        issue_index=issue_index,
        issue=issue,
        nodes=[node for node in report.nodes if node.node_id in selected],
        edges=[
            edge
            for edge in report.edges
            if edge.source in selected and edge.target in selected
        ],
    )


@mcp.tool(
    title="Inspect Aura temporal state",
    annotations=READ_ONLY,
    structured_output=True,
)
def aura_temporal_state(
    run_id: str,
    policy_yaml: str,
    db_path: str = ".aura/aura.db",
    final: bool = False,
) -> TemporalMonitorReport:
    """Inspect three-valued bounded-response state without returning captured content."""
    events = _store(db_path).events(run_id)
    if not events:
        raise ValueError(f"run {run_id!r} has no captured events")
    monitor = OnlineTemporalMonitor(AuraSpec.from_yaml_text(policy_yaml))
    for event in events:
        monitor.observe(event)
    if final:
        monitor.finalize()
    return monitor.report()


@mcp.tool(annotations=READ_ONLY, structured_output=True)
def aura_trace_integrity(run_id: str, db_path: str = ".aura/aura.db") -> dict[str, object]:
    """Verify the hash chain for an MCP flight-recorder transcript."""
    records = _store(db_path).protocol_records(run_id)
    return {
        "run_id": run_id,
        "record_count": len(records),
        "valid": verify_protocol_chain(records),
        "head_hash": records[-1].content_hash if records else None,
    }


@mcp.tool(annotations=READ_ONLY, structured_output=True)
def aura_replay(
    run_id: str,
    policy_yaml: str,
    db_path: str = ".aura/aura.db",
) -> dict[str, object]:
    """Replay a run read-only against an AuraSpec YAML document."""
    report = replay_run(_store(db_path), run_id, AuraSpec.from_yaml_text(policy_yaml))
    return report.model_dump(mode="json")


@mcp.tool(annotations=READ_ONLY, structured_output=True)
def aura_compare_runs(
    left_run_id: str,
    right_run_id: str,
    db_path: str = ".aura/aura.db",
) -> dict[str, object]:
    """Find the first behavioral divergence between two captured runs."""
    report = compare_runs(_store(db_path), left_run_id, right_run_id)
    return report.model_dump(mode="json")


@mcp.tool(
    title="Discover Aura object behavior",
    annotations=READ_ONLY,
    structured_output=True,
)
def aura_object_behavior(
    run_ids: list[str],
    db_path: str = ".aura/aura.db",
) -> ObjectBehaviorProfile:
    """Discover aggregate lifecycles and interactions without returning object IDs or content."""
    store = _store(db_path)
    events = [event for run_id in dict.fromkeys(run_ids) for event in store.events(run_id)]
    return discover_object_behavior(events)


@mcp.tool(
    title="Compare Aura object behavior",
    annotations=READ_ONLY,
    structured_output=True,
)
def aura_object_conformance(
    baseline_run_ids: list[str],
    candidate_run_ids: list[str],
    db_path: str = ".aura/aura.db",
) -> ObjectConformanceReport:
    """Report exact structural drift between trusted and candidate object behavior."""
    store = _store(db_path)
    baseline_events = [
        event for run_id in dict.fromkeys(baseline_run_ids) for event in store.events(run_id)
    ]
    candidate_events = [
        event for run_id in dict.fromkeys(candidate_run_ids) for event in store.events(run_id)
    ]
    return compare_object_behavior(
        discover_object_behavior(baseline_events),
        discover_object_behavior(candidate_events),
    )


@mcp.tool(annotations=READ_ONLY, structured_output=True)
def aura_compare_manifests(
    left_run_id: str,
    right_run_id: str,
    db_path: str = ".aura/aura.db",
) -> dict[str, object]:
    """Compare latest MCP tool manifests captured for two runs."""
    report = compare_manifests(_store(db_path), left_run_id, right_run_id)
    return report.model_dump(mode="json")


@mcp.tool(annotations=READ_ONLY, structured_output=True)
def aura_check_contract(
    contract_path: str,
    candidate_run_id: str,
    db_path: str = ".aura/aura.db",
) -> dict[str, object]:
    """Check a captured run against a local Aura trace contract."""
    report = check_contract(
        TraceContract.from_yaml(contract_path),
        _store(db_path),
        candidate_run_id,
    )
    return report.model_dump(mode="json")
