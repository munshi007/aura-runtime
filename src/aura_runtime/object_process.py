"""Deterministic object-centric behavior discovery and structural conformance."""

from __future__ import annotations

from collections import Counter, defaultdict
from itertools import combinations
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from aura_runtime.models import AgentEvent


class ActivityCount(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    activity: str
    count: int = Field(ge=1)


class TransitionCount(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str
    target: str
    count: int = Field(ge=1)


class ObjectTypeBehavior(BaseModel):
    """An aggregate directly-follows graph for one object type."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    object_type: str
    object_count: int = Field(ge=1)
    event_count: int = Field(ge=1)
    activities: list[ActivityCount]
    starts: list[ActivityCount]
    ends: list[ActivityCount]
    transitions: list[TransitionCount]


class InteractionCount(BaseModel):
    """An activity observed while two different object types interacted."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    left_object_type: str
    right_object_type: str
    activity: str
    count: int = Field(ge=1)


class ObjectBehaviorProfile(BaseModel):
    """Content-free object-centric behavioral profile."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["0.1"] = "0.1"
    run_count: int = Field(ge=1)
    event_count: int = Field(ge=1)
    object_count: int = Field(ge=1)
    object_types: list[ObjectTypeBehavior]
    interactions: list[InteractionCount]
    content_included: Literal[False] = False


DriftKind = Literal[
    "object_type_added",
    "object_type_removed",
    "activity_added",
    "activity_removed",
    "transition_added",
    "transition_removed",
    "interaction_added",
    "interaction_removed",
]


class StructuralDrift(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: DriftKind
    object_type: str | None = None
    source: str | None = None
    target: str | None = None
    activity: str | None = None
    left_object_type: str | None = None
    right_object_type: str | None = None


class ObjectConformanceReport(BaseModel):
    """Exact structural differences between two object-centric profiles."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    verdict: Literal["pass", "fail"]
    drift_count: int = Field(ge=0)
    drifts: list[StructuralDrift]
    baseline: ObjectBehaviorProfile
    candidate: ObjectBehaviorProfile
    content_included: Literal[False] = False


def event_activity(event: AgentEvent) -> str:
    """Return the content-free activity identity shared by discovery and monitoring."""
    if event.tool_name:
        return f"{event.kind.value}:{event.tool_name}"
    return event.kind.value


def _ordered(events: list[AgentEvent]) -> list[AgentEvent]:
    return sorted(
        events,
        key=lambda event: (
            event.timestamp,
            event.run_id,
            event.sequence is None,
            event.sequence if event.sequence is not None else 0,
            str(event.event_id),
        ),
    )


def _activity_counts(values: Counter[str]) -> list[ActivityCount]:
    return [ActivityCount(activity=activity, count=values[activity]) for activity in sorted(values)]


def discover_object_behavior(events: list[AgentEvent]) -> ObjectBehaviorProfile:
    """Discover aggregate object lifecycles without retaining object identifiers or content."""
    if not events:
        raise ValueError("cannot discover behavior from an empty event collection")

    traces: dict[tuple[str, str, str], list[AgentEvent]] = defaultdict(list)
    interactions: Counter[tuple[str, str, str]] = Counter()
    for event in _ordered(events):
        identities = {(ref.object_type, ref.object_id) for ref in event.objects}
        for object_type, object_id in identities:
            # A run boundary ends an observed lifecycle. Connecting separate runs would
            # invent a directly-follows edge that was never present in either execution.
            traces[(event.run_id, object_type, object_id)].append(event)
        object_types = sorted({object_type for object_type, _ in identities})
        for left, right in combinations(object_types, 2):
            interactions[(left, right, event_activity(event))] += 1

    if not traces:
        raise ValueError("events do not contain business-object references")

    by_type: dict[str, list[list[AgentEvent]]] = defaultdict(list)
    for (_, object_type, _), trace in traces.items():
        by_type[object_type].append(trace)

    behaviors: list[ObjectTypeBehavior] = []
    for object_type in sorted(by_type):
        activity_counts: Counter[str] = Counter()
        start_counts: Counter[str] = Counter()
        end_counts: Counter[str] = Counter()
        transition_counts: Counter[tuple[str, str]] = Counter()
        typed_traces = by_type[object_type]
        for trace in typed_traces:
            activities = [event_activity(event) for event in trace]
            activity_counts.update(activities)
            start_counts[activities[0]] += 1
            end_counts[activities[-1]] += 1
            transition_counts.update(zip(activities, activities[1:], strict=False))
        behaviors.append(
            ObjectTypeBehavior(
                object_type=object_type,
                object_count=len(typed_traces),
                event_count=sum(activity_counts.values()),
                activities=_activity_counts(activity_counts),
                starts=_activity_counts(start_counts),
                ends=_activity_counts(end_counts),
                transitions=[
                    TransitionCount(source=source, target=target, count=count)
                    for (source, target), count in sorted(transition_counts.items())
                ],
            )
        )

    return ObjectBehaviorProfile(
        run_count=len({event.run_id for event in events}),
        event_count=len(events),
        object_count=len(traces),
        object_types=behaviors,
        interactions=[
            InteractionCount(
                left_object_type=left,
                right_object_type=right,
                activity=activity,
                count=count,
            )
            for (left, right, activity), count in sorted(interactions.items())
        ],
    )


def compare_object_behavior(
    baseline: ObjectBehaviorProfile,
    candidate: ObjectBehaviorProfile,
) -> ObjectConformanceReport:
    """Compare structural support; frequency changes remain evidence, not violations."""
    drifts: list[StructuralDrift] = []
    baseline_types = {item.object_type: item for item in baseline.object_types}
    candidate_types = {item.object_type: item for item in candidate.object_types}

    for object_type in sorted(candidate_types.keys() - baseline_types.keys()):
        drifts.append(StructuralDrift(kind="object_type_added", object_type=object_type))
    for object_type in sorted(baseline_types.keys() - candidate_types.keys()):
        drifts.append(StructuralDrift(kind="object_type_removed", object_type=object_type))

    for object_type in sorted(baseline_types.keys() & candidate_types.keys()):
        left = baseline_types[object_type]
        right = candidate_types[object_type]
        left_activities = {item.activity for item in left.activities}
        right_activities = {item.activity for item in right.activities}
        for activity in sorted(right_activities - left_activities):
            drifts.append(
                StructuralDrift(
                    kind="activity_added", object_type=object_type, activity=activity
                )
            )
        for activity in sorted(left_activities - right_activities):
            drifts.append(
                StructuralDrift(
                    kind="activity_removed", object_type=object_type, activity=activity
                )
            )

        left_transitions = {(item.source, item.target) for item in left.transitions}
        right_transitions = {(item.source, item.target) for item in right.transitions}
        for source, target in sorted(right_transitions - left_transitions):
            drifts.append(
                StructuralDrift(
                    kind="transition_added",
                    object_type=object_type,
                    source=source,
                    target=target,
                )
            )
        for source, target in sorted(left_transitions - right_transitions):
            drifts.append(
                StructuralDrift(
                    kind="transition_removed",
                    object_type=object_type,
                    source=source,
                    target=target,
                )
            )

    def interaction_keys(profile: ObjectBehaviorProfile) -> set[tuple[str, str, str]]:
        return {
            (item.left_object_type, item.right_object_type, item.activity)
            for item in profile.interactions
        }

    baseline_interactions = interaction_keys(baseline)
    candidate_interactions = interaction_keys(candidate)
    for kind, values in (
        ("interaction_added", candidate_interactions - baseline_interactions),
        ("interaction_removed", baseline_interactions - candidate_interactions),
    ):
        for left, right, activity in sorted(values):
            drifts.append(
                StructuralDrift(
                    kind=kind,
                    left_object_type=left,
                    right_object_type=right,
                    activity=activity,
                )
            )

    return ObjectConformanceReport(
        verdict="fail" if drifts else "pass",
        drift_count=len(drifts),
        drifts=drifts,
        baseline=baseline,
        candidate=candidate,
    )
