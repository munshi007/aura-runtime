"""Versioned object contracts and transactional online lifecycle monitoring."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aura_runtime.models import AgentEvent, Finding, GateAction, Severity
from aura_runtime.object_process import ObjectBehaviorProfile, event_activity


class ContractTransition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str
    target: str


class ObjectTypeContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    object_type: str
    allowed_starts: list[str] = Field(min_length=1)
    allowed_ends: list[str] = Field(min_length=1)
    allowed_transitions: list[ContractTransition]


class InteractionContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    left_object_type: str
    right_object_type: str
    activity: str


class ObjectContract(BaseModel):
    """Content-addressed structural contract learned from trusted object lifecycles."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["0.1"] = "0.1"
    effect: Literal["deny", "require_approval"] = "deny"
    object_types: list[ObjectTypeContract] = Field(min_length=1)
    interactions: list[InteractionContract] = Field(default_factory=list)
    contract_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    def canonical_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"contract_hash"})

    def computed_hash(self) -> str:
        canonical = json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":"))
        return sha256(canonical.encode()).hexdigest()

    @model_validator(mode="after")
    def hash_and_structure_are_valid(self) -> ObjectContract:
        type_names = [item.object_type for item in self.object_types]
        if len(type_names) != len(set(type_names)):
            raise ValueError("object contract contains duplicate object types")
        if self.contract_hash != self.computed_hash():
            raise ValueError("object contract hash does not match its canonical content")
        return self

    @classmethod
    def from_profile(
        cls,
        profile: ObjectBehaviorProfile,
        *,
        effect: Literal["deny", "require_approval"] = "deny",
    ) -> ObjectContract:
        payload = {
            "schema_version": "0.1",
            "effect": effect,
            "object_types": [
                {
                    "object_type": item.object_type,
                    "allowed_starts": sorted({entry.activity for entry in item.starts}),
                    "allowed_ends": sorted({entry.activity for entry in item.ends}),
                    "allowed_transitions": [
                        {"source": edge.source, "target": edge.target}
                        for edge in sorted(
                            item.transitions, key=lambda edge: (edge.source, edge.target)
                        )
                    ],
                }
                for item in sorted(profile.object_types, key=lambda item: item.object_type)
            ],
            "interactions": [
                {
                    "left_object_type": item.left_object_type,
                    "right_object_type": item.right_object_type,
                    "activity": item.activity,
                }
                for item in sorted(
                    profile.interactions,
                    key=lambda item: (
                        item.left_object_type,
                        item.right_object_type,
                        item.activity,
                    ),
                )
            ],
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return cls.model_validate(
            {**payload, "contract_hash": sha256(canonical.encode()).hexdigest()}
        )

    @classmethod
    def from_json(cls, path: str | Path) -> ObjectContract:
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))


ViolationCode = Literal[
    "missing_object_binding",
    "unknown_object_type",
    "invalid_start",
    "invalid_transition",
    "invalid_interaction",
    "invalid_end",
]


class ObjectContractViolation(BaseModel):
    """A content-free counterexample produced by the object monitor."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: ViolationCode
    object_type: str | None = None
    object_key: str | None = None
    activity: str
    previous_activity: str | None = None
    expected_activities: list[str] = Field(default_factory=list)
    evidence_event_ids: list[UUID] = Field(default_factory=list)
    left_object_type: str | None = None
    right_object_type: str | None = None
    message: str


class ObjectLifecycleState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    object_type: str
    object_key: str
    status: Literal["active", "completed"]
    last_activity: str
    last_event_id: UUID
    expected_activities: list[str]


class ObjectMonitorReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_hash: str
    state_count: int = Field(ge=0)
    violation_count: int = Field(ge=0)
    states: list[ObjectLifecycleState]
    violations: list[ObjectContractViolation]
    content_included: Literal[False] = False


class _AcceptedState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    last_activity: str
    last_event_id: UUID


class ObjectContractMonitor:
    """Evaluate proposed events first, then commit only accepted/executed transitions."""

    def __init__(self, contract: ObjectContract) -> None:
        self.contract = contract
        self._types = {item.object_type: item for item in contract.object_types}
        self._transitions = {
            item.object_type: {
                (transition.source, transition.target)
                for transition in item.allowed_transitions
            }
            for item in contract.object_types
        }
        self._interactions = {
            (item.left_object_type, item.right_object_type, item.activity)
            for item in contract.interactions
        }
        self._states: dict[tuple[str, str], _AcceptedState] = {}
        self._violations: list[ObjectContractViolation] = []

    def _object_key(self, object_type: str, object_id: str) -> str:
        digest = sha256(
            f"{self.contract.contract_hash}\0{object_type}\0{object_id}".encode()
        ).hexdigest()[:24]
        return f"aura:{object_type}:{digest}"

    def expected_after(self, object_type: str, activity: str) -> list[str]:
        contract = self._types.get(object_type)
        if contract is None:
            return []
        return sorted(
            transition.target
            for transition in contract.allowed_transitions
            if transition.source == activity
        )

    def assess(
        self,
        event: AgentEvent,
        *,
        expected_object_types: list[str] | None = None,
    ) -> list[ObjectContractViolation]:
        """Return violations for a proposed event without changing accepted state."""
        activity = event_activity(event)
        identities = sorted({(ref.object_type, ref.object_id) for ref in event.objects})
        violations: list[ObjectContractViolation] = []

        actual_types = {object_type for object_type, _ in identities}
        for object_type in sorted(set(expected_object_types or []) - actual_types):
            violations.append(
                ObjectContractViolation(
                    code="missing_object_binding",
                    object_type=object_type,
                    activity=activity,
                    message=(
                        f"binding for object type {object_type!r} matched activity "
                        f"{activity!r} but did not produce an identifier"
                    ),
                )
            )

        for object_type, object_id in identities:
            object_key = self._object_key(object_type, object_id)
            type_contract = self._types.get(object_type)
            state = self._states.get((object_type, object_id))
            if type_contract is None:
                violations.append(
                    ObjectContractViolation(
                        code="unknown_object_type",
                        object_type=object_type,
                        object_key=object_key,
                        activity=activity,
                        message=f"object type {object_type!r} is absent from the contract",
                    )
                )
            elif state is None and activity not in type_contract.allowed_starts:
                violations.append(
                    ObjectContractViolation(
                        code="invalid_start",
                        object_type=object_type,
                        object_key=object_key,
                        activity=activity,
                        expected_activities=type_contract.allowed_starts,
                        message=(
                            f"activity {activity!r} is not an allowed start "
                            f"for {object_type!r}"
                        ),
                    )
                )
            elif state is not None and (
                state.last_activity,
                activity,
            ) not in self._transitions[object_type]:
                violations.append(
                    ObjectContractViolation(
                        code="invalid_transition",
                        object_type=object_type,
                        object_key=object_key,
                        activity=activity,
                        previous_activity=state.last_activity,
                        expected_activities=self.expected_after(
                            object_type, state.last_activity
                        ),
                        evidence_event_ids=[state.last_event_id],
                        message=(
                            f"transition {state.last_activity!r} -> {activity!r} "
                            f"is not allowed for {object_type!r}"
                        ),
                    )
                )

        object_types = sorted({object_type for object_type, _ in identities})
        for index, left in enumerate(object_types):
            for right in object_types[index + 1 :]:
                if (left, right, activity) not in self._interactions:
                    violations.append(
                        ObjectContractViolation(
                            code="invalid_interaction",
                            activity=activity,
                            left_object_type=left,
                            right_object_type=right,
                            message=(
                                f"activity {activity!r} is not an allowed interaction "
                                f"between {left!r} and {right!r}"
                            ),
                        )
                    )
        return violations

    def commit(self, event: AgentEvent) -> None:
        """Advance all referenced object states after the action is accepted or executed."""
        activity = event_activity(event)
        for reference in event.objects:
            identity = (reference.object_type, reference.object_id)
            self._states[identity] = _AcceptedState(
                last_activity=activity,
                last_event_id=event.event_id,
            )

    def observe(
        self,
        event: AgentEvent,
        *,
        commit: bool,
        expected_object_types: list[str] | None = None,
    ) -> list[Finding]:
        violations = self.assess(
            event, expected_object_types=expected_object_types
        )
        self._violations.extend(violations)
        if commit:
            self.commit(event)
        return [self._finding(event, violation) for violation in violations]

    def finalize(self) -> list[ObjectContractViolation]:
        violations: list[ObjectContractViolation] = []
        for (object_type, object_id), state in sorted(self._states.items()):
            contract = self._types.get(object_type)
            if contract is None or state.last_activity in contract.allowed_ends:
                continue
            violation = ObjectContractViolation(
                code="invalid_end",
                object_type=object_type,
                object_key=self._object_key(object_type, object_id),
                activity=state.last_activity,
                expected_activities=contract.allowed_ends,
                evidence_event_ids=[state.last_event_id],
                message=f"lifecycle for {object_type!r} ended in a non-terminal activity",
            )
            violations.append(violation)
        self._violations.extend(violations)
        return violations

    def report(self) -> ObjectMonitorReport:
        states = []
        for (object_type, object_id), state in sorted(
            self._states.items(), key=lambda item: self._object_key(*item[0])
        ):
            contract = self._types.get(object_type)
            completed = contract is not None and state.last_activity in contract.allowed_ends
            states.append(
                ObjectLifecycleState(
                    object_type=object_type,
                    object_key=self._object_key(object_type, object_id),
                    status="completed" if completed else "active",
                    last_activity=state.last_activity,
                    last_event_id=state.last_event_id,
                    expected_activities=self.expected_after(object_type, state.last_activity),
                )
            )
        return ObjectMonitorReport(
            contract_hash=self.contract.contract_hash,
            state_count=len(states),
            violation_count=len(self._violations),
            states=states,
            violations=self._violations,
        )

    def _finding(self, event: AgentEvent, violation: ObjectContractViolation) -> Finding:
        return Finding(
            run_id=event.run_id,
            policy_id=f"object-contract.{self.contract.contract_hash[:16]}",
            severity=Severity.CRITICAL,
            message=violation.message,
            event_id=event.event_id,
            evidence_event_ids=violation.evidence_event_ids,
            engine="object-contract-v0.1",
        )

    @property
    def action(self) -> GateAction:
        return (
            GateAction.DENY
            if self.contract.effect == "deny"
            else GateAction.REQUIRE_APPROVAL
        )
