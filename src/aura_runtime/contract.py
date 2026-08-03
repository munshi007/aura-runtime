"""Trace-contract regression checks for agent behavior in CI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from aura_runtime.models import AgentEvent, Finding, Severity
from aura_runtime.policy import AuraLoader, AuraSpec
from aura_runtime.replay import (
    ManifestDiffReport,
    RunDiffReport,
    compare_event_sequences,
    compare_manifest_tools,
)
from aura_runtime.store import SQLiteEventStore
from aura_runtime.verifier import RuntimeVerifier


class BaselineFiles(BaseModel):
    model_config = ConfigDict(extra="forbid")

    events: Path
    manifest: Path


class ContractRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_findings: Literal["allow", "deny"] = "deny"
    behavioral_divergence: Literal["allow", "deny"] = "deny"
    tool_manifest_drift: Literal["allow", "deny"] = "deny"


class TraceContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal["0.1"]
    name: str = Field(min_length=1)
    policy: Path
    baseline: BaselineFiles
    rules: ContractRules = Field(default_factory=ContractRules)

    @classmethod
    def from_yaml(cls, path: str | Path) -> TraceContract:
        contract_path = Path(path).resolve()
        raw = yaml.load(contract_path.read_text(encoding="utf-8"), Loader=AuraLoader)
        contract = cls.model_validate(raw)
        base = contract_path.parent
        return contract.model_copy(
            update={
                "policy": (base / contract.policy).resolve(),
                "baseline": contract.baseline.model_copy(
                    update={
                        "events": (base / contract.baseline.events).resolve(),
                        "manifest": (base / contract.baseline.manifest).resolve(),
                    }
                ),
            }
        )


class ContractFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_id: str
    event_index: int
    event_kind: str
    tool_name: str | None
    severity: Severity
    message: str
    engine: str

    def identity(self) -> str:
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


class ContractReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_name: str
    candidate_run_id: str
    verdict: Literal["pass", "fail"]
    reasons: list[str] = Field(default_factory=list)
    introduced_findings: list[ContractFinding] = Field(default_factory=list)
    resolved_findings: list[ContractFinding] = Field(default_factory=list)
    unchanged_findings: list[ContractFinding] = Field(default_factory=list)
    run_diff: RunDiffReport
    manifest_diff: ManifestDiffReport
    read_only: Literal[True] = True

    def to_markdown(self) -> str:
        icon = "✅" if self.verdict == "pass" else "❌"
        lines = [
            f"# Aura Trace Contract: {self.contract_name}",
            "",
            f"## {icon} Verdict: {self.verdict.upper()}",
            "",
            f"Candidate run: `{self.candidate_run_id}`",
            "",
            "| Signal | Result |",
            "|---|---:|",
            f"| Introduced findings | {len(self.introduced_findings)} |",
            f"| Resolved findings | {len(self.resolved_findings)} |",
            f"| Behavioral divergence | {'yes' if not self.run_diff.identical else 'no'} |",
            f"| Tool manifest drift | {'yes' if self.manifest_diff.has_drift else 'no'} |",
            "",
        ]
        if self.reasons:
            lines.extend(["## Blocking reasons", ""])
            lines.extend(f"- {reason}" for reason in self.reasons)
            lines.append("")
        if self.introduced_findings:
            lines.extend(["## Introduced findings", ""])
            lines.extend(
                f"- `{finding.policy_id}` at event {finding.event_index}: {finding.message}"
                for finding in self.introduced_findings
            )
            lines.append("")
        if self.run_diff.first_divergence_index is not None:
            lines.extend(
                [
                    "## First divergence",
                    "",
                    f"Event index: `{self.run_diff.first_divergence_index}`",
                    "",
                ]
            )
        return "\n".join(lines)


def _load_events(path: Path) -> list[AgentEvent]:
    return [
        AgentEvent.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _load_manifest(path: Path) -> list[dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    tools = value.get("tools") if isinstance(value, dict) else value
    if not isinstance(tools, list) or not all(isinstance(tool, dict) for tool in tools):
        raise ValueError("baseline manifest must be a list or an object containing tools")
    return tools


def _finding_views(findings: list[Finding], events: list[AgentEvent]) -> list[ContractFinding]:
    event_indexes = {event.event_id: index for index, event in enumerate(events)}
    return [
        ContractFinding(
            policy_id=finding.policy_id,
            event_index=event_indexes[finding.event_id],
            event_kind=events[event_indexes[finding.event_id]].kind.value,
            tool_name=events[event_indexes[finding.event_id]].tool_name,
            severity=finding.severity,
            message=finding.message,
            engine=finding.engine,
        )
        for finding in findings
    ]


def check_contract(
    contract: TraceContract,
    store: SQLiteEventStore,
    candidate_run_id: str,
) -> ContractReport:
    baseline_events = _load_events(contract.baseline.events)
    candidate_events = store.events(candidate_run_id)
    if not candidate_events:
        raise ValueError(f"run {candidate_run_id!r} has no captured events")
    baseline_tools = _load_manifest(contract.baseline.manifest)
    candidate_manifests = store.manifests(candidate_run_id)
    candidate_tools = candidate_manifests[-1].tools if candidate_manifests else []
    spec = AuraSpec.from_yaml(contract.policy)

    baseline_findings = _finding_views(
        RuntimeVerifier(spec).verify(baseline_events), baseline_events
    )
    candidate_findings = _finding_views(
        RuntimeVerifier(spec).verify(candidate_events), candidate_events
    )
    baseline_by_id = {finding.identity(): finding for finding in baseline_findings}
    candidate_by_id = {finding.identity(): finding for finding in candidate_findings}
    baseline_ids = set(baseline_by_id)
    candidate_ids = set(candidate_by_id)
    introduced = [candidate_by_id[key] for key in sorted(candidate_ids - baseline_ids)]
    resolved = [baseline_by_id[key] for key in sorted(baseline_ids - candidate_ids)]
    unchanged = [candidate_by_id[key] for key in sorted(candidate_ids & baseline_ids)]
    run_diff = compare_event_sequences(
        baseline_events, candidate_events, "baseline", candidate_run_id
    )
    manifest_diff = compare_manifest_tools(
        baseline_tools, candidate_tools, "baseline", candidate_run_id
    )

    reasons: list[str] = []
    if introduced and contract.rules.new_findings == "deny":
        reasons.append(f"{len(introduced)} new policy finding(s)")
    if not run_diff.identical and contract.rules.behavioral_divergence == "deny":
        reasons.append(f"behavior diverged at event {run_diff.first_divergence_index}")
    if manifest_diff.has_drift and contract.rules.tool_manifest_drift == "deny":
        reasons.append("tool manifest drift detected")

    return ContractReport(
        contract_name=contract.name,
        candidate_run_id=candidate_run_id,
        verdict="fail" if reasons else "pass",
        reasons=reasons,
        introduced_findings=introduced,
        resolved_findings=resolved,
        unchanged_findings=unchanged,
        run_diff=run_diff,
        manifest_diff=manifest_diff,
    )
