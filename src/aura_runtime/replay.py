"""Read-only replay and behavioral comparison over captured Aura evidence."""

from __future__ import annotations

import json
from difflib import SequenceMatcher
from hashlib import sha256
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field

from aura_runtime.flight import verify_protocol_chain
from aura_runtime.models import AgentEvent, Finding, Severity, ToolManifestSnapshot
from aura_runtime.policy import AuraSpec
from aura_runtime.store import SQLiteEventStore
from aura_runtime.verifier import RuntimeVerifier


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return sha256(_canonical(value).encode()).hexdigest()


EPHEMERAL_DATA_KEYS = {
    "gen_ai.tool.call.id",
    "mcp.request_id",
    "mcp.session.id",
}


def _behavior_data(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _behavior_data(item)
            for key, item in value.items()
            if key not in EPHEMERAL_DATA_KEYS
        }
    if isinstance(value, list):
        return [_behavior_data(item) for item in value]
    return value


class FindingSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_id: str
    event_id: UUID
    severity: Severity
    message: str
    engine: str

    @classmethod
    def from_finding(cls, finding: Finding) -> FindingSnapshot:
        return cls(
            policy_id=finding.policy_id,
            event_id=finding.event_id,
            severity=finding.severity,
            message=finding.message,
            engine=finding.engine,
        )

    def identity(self) -> str:
        return _canonical(self.model_dump(mode="json"))


class ReplayReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    policy_hash: str
    event_count: int
    recorded_finding_count: int
    replayed_finding_count: int
    introduced: list[FindingSnapshot] = Field(default_factory=list)
    resolved: list[FindingSnapshot] = Field(default_factory=list)
    unchanged: list[FindingSnapshot] = Field(default_factory=list)
    transcript_integrity: bool
    transcript_head_hash: str | None
    read_only: Literal[True] = True


class EventSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    index: int
    kind: str
    tool_name: str | None
    actor: str | None
    data_hash: str


class SequenceEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation: Literal["replace", "delete", "insert"]
    left_range: tuple[int, int]
    right_range: tuple[int, int]


class RunDiffReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    left_run_id: str
    right_run_id: str
    left_event_count: int
    right_event_count: int
    identical: bool
    common_prefix_count: int
    first_divergence_index: int | None
    left_event: EventSnapshot | None
    right_event: EventSnapshot | None
    edits: list[SequenceEdit] = Field(default_factory=list)


class ManifestDiffReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    left_run_id: str
    right_run_id: str
    left_manifest_hash: str | None
    right_manifest_hash: str | None
    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    changed: list[str] = Field(default_factory=list)
    unchanged: list[str] = Field(default_factory=list)

    @computed_field
    @property
    def has_drift(self) -> bool:
        return bool(self.added or self.removed or self.changed)


def replay_run(store: SQLiteEventStore, run_id: str, spec: AuraSpec) -> ReplayReport:
    """Re-evaluate captured evidence without mutating storage or invoking tools."""
    events = store.events(run_id)
    if not events:
        raise ValueError(f"run {run_id!r} has no captured events")

    recorded = [FindingSnapshot.from_finding(item) for item in store.findings(run_id)]
    replayed = [FindingSnapshot.from_finding(item) for item in RuntimeVerifier(spec).verify(events)]
    recorded_by_id = {item.identity(): item for item in recorded}
    replayed_by_id = {item.identity(): item for item in replayed}
    recorded_ids = set(recorded_by_id)
    replayed_ids = set(replayed_by_id)
    records = store.protocol_records(run_id)

    return ReplayReport(
        run_id=run_id,
        policy_hash=_digest(spec.model_dump(mode="json")),
        event_count=len(events),
        recorded_finding_count=len(recorded),
        replayed_finding_count=len(replayed),
        introduced=[replayed_by_id[key] for key in sorted(replayed_ids - recorded_ids)],
        resolved=[recorded_by_id[key] for key in sorted(recorded_ids - replayed_ids)],
        unchanged=[replayed_by_id[key] for key in sorted(replayed_ids & recorded_ids)],
        transcript_integrity=verify_protocol_chain(records),
        transcript_head_hash=records[-1].content_hash if records else None,
    )


def _event_identity(event: AgentEvent) -> str:
    return _canonical(
        {
            "kind": event.kind.value,
            "source": event.source,
            "actor": event.actor,
            "tool_name": event.tool_name,
            "data": _behavior_data(event.data),
        }
    )


def _event_snapshot(event: AgentEvent, index: int) -> EventSnapshot:
    return EventSnapshot(
        index=index,
        kind=event.kind.value,
        tool_name=event.tool_name,
        actor=event.actor,
        data_hash=_digest(_behavior_data(event.data)),
    )


def compare_runs(store: SQLiteEventStore, left_run_id: str, right_run_id: str) -> RunDiffReport:
    left = store.events(left_run_id)
    right = store.events(right_run_id)
    if not left:
        raise ValueError(f"run {left_run_id!r} has no captured events")
    if not right:
        raise ValueError(f"run {right_run_id!r} has no captured events")

    left_ids = [_event_identity(event) for event in left]
    right_ids = [_event_identity(event) for event in right]
    prefix = 0
    while prefix < min(len(left_ids), len(right_ids)) and left_ids[prefix] == right_ids[prefix]:
        prefix += 1

    edits = [
        SequenceEdit(operation=tag, left_range=(i1, i2), right_range=(j1, j2))
        for tag, i1, i2, j1, j2 in SequenceMatcher(
            None, left_ids, right_ids, autojunk=False
        ).get_opcodes()
        if tag != "equal"
    ]
    identical = left_ids == right_ids
    divergence = None if identical else prefix
    return RunDiffReport(
        left_run_id=left_run_id,
        right_run_id=right_run_id,
        left_event_count=len(left),
        right_event_count=len(right),
        identical=identical,
        common_prefix_count=prefix,
        first_divergence_index=divergence,
        left_event=_event_snapshot(left[prefix], prefix) if prefix < len(left) else None,
        right_event=_event_snapshot(right[prefix], prefix) if prefix < len(right) else None,
        edits=edits,
    )


def _latest_manifest(store: SQLiteEventStore, run_id: str) -> ToolManifestSnapshot | None:
    snapshots = store.manifests(run_id)
    return snapshots[-1] if snapshots else None


def compare_manifests(
    store: SQLiteEventStore, left_run_id: str, right_run_id: str
) -> ManifestDiffReport:
    left_snapshot = _latest_manifest(store, left_run_id)
    right_snapshot = _latest_manifest(store, right_run_id)
    left_tools = (
        {tool.get("name", ""): tool for tool in left_snapshot.tools} if left_snapshot else {}
    )
    right_tools = (
        {tool.get("name", ""): tool for tool in right_snapshot.tools} if right_snapshot else {}
    )
    left_names = set(left_tools)
    right_names = set(right_tools)
    common = left_names & right_names

    return ManifestDiffReport(
        left_run_id=left_run_id,
        right_run_id=right_run_id,
        left_manifest_hash=left_snapshot.content_hash if left_snapshot else None,
        right_manifest_hash=right_snapshot.content_hash if right_snapshot else None,
        added=sorted(right_names - left_names),
        removed=sorted(left_names - right_names),
        changed=sorted(
            name for name in common if _canonical(left_tools[name]) != _canonical(right_tools[name])
        ),
        unchanged=sorted(
            name for name in common if _canonical(left_tools[name]) == _canonical(right_tools[name])
        ),
    )
