"""Deterministic online verification for AuraSpec policies."""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from aura_runtime.alphabet import EventAlphabet, EventAlphabetReport
from aura_runtime.ltlf import (
    Formula,
    LTLfMonitor,
    LTLfMonitorReport,
    LTLfShieldReport,
    LTLfStrategyReport,
    PrefixVerdict,
    make_and,
    rename_atoms,
)
from aura_runtime.models import AgentEvent, EventKind, Finding
from aura_runtime.partial_observation import (
    PartialObservationReport,
    synthesize_partial_observation,
)
from aura_runtime.policy import (
    AuraSpec,
    DataConstraint,
    EventSelector,
    LTLfPolicy,
    Policy,
    value_at_path,
)


class TemporalVerdict(StrEnum):
    PENDING = "pending"
    SATISFIED = "satisfied"
    VIOLATED = "violated"


class PendingObligation(BaseModel):
    """Content-free snapshot of one bounded future obligation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_id: str
    trigger_event_id: UUID
    trigger_index: int = Field(ge=0)
    deadline_index: int = Field(ge=1)
    remaining_events: int = Field(ge=1)
    verdict: TemporalVerdict = TemporalVerdict.PENDING


class TemporalMonitorReport(BaseModel):
    """Three-valued state of a finite-trace monitor at the current prefix."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str | None
    observed_event_count: int = Field(ge=0)
    pending_obligation_count: int = Field(ge=0)
    satisfied_obligation_count: int = Field(ge=0)
    violated_obligation_count: int = Field(ge=0)
    pending: list[PendingObligation]
    findings: list[Finding]
    finalized: bool
    content_included: Literal[False] = False


class _Obligation:
    def __init__(self, policy: Policy, trigger: AgentEvent, trigger_index: int) -> None:
        assert policy.require_after is not None
        assert policy.require_after.within_events is not None
        self.policy = policy
        self.trigger = trigger
        self.trigger_index = trigger_index
        self.deadline_index = trigger_index + policy.require_after.within_events
        self.evidence_event_ids = [trigger.event_id]


class ConstraintEvaluator:
    """Check concrete constraints with Z3 and a safe fallback for minimal installs."""

    def evaluate(self, actual: Any, constraint: DataConstraint) -> tuple[bool, str]:
        try:
            import z3
        except ImportError:
            return self._python_evaluate(actual, constraint), "python-fallback"

        if actual is None or type(actual) is not type(constraint.value):
            return False, "z3"

        if isinstance(actual, bool):
            symbol = z3.Bool("value")
            expected = z3.BoolVal(constraint.value)
            concrete = z3.BoolVal(actual)
        elif isinstance(actual, int):
            symbol = z3.Int("value")
            expected = z3.IntVal(constraint.value)
            concrete = z3.IntVal(actual)
        elif isinstance(actual, float):
            symbol = z3.Real("value")
            expected = z3.RealVal(str(constraint.value))
            concrete = z3.RealVal(str(actual))
        elif isinstance(actual, str):
            symbol = z3.String("value")
            expected = z3.StringVal(constraint.value)
            concrete = z3.StringVal(actual)
        else:
            return False, "z3"

        solver = z3.Solver()
        solver.add(symbol == concrete)
        solver.add(self._z3_expression(symbol, constraint.op, expected))
        return solver.check() == z3.sat, "z3"

    @staticmethod
    def _z3_expression(symbol: Any, op: str, expected: Any) -> Any:
        operations = {
            "==": lambda: symbol == expected,
            "!=": lambda: symbol != expected,
            "<": lambda: symbol < expected,
            "<=": lambda: symbol <= expected,
            ">": lambda: symbol > expected,
            ">=": lambda: symbol >= expected,
        }
        return operations[op]()

    @staticmethod
    def _python_evaluate(actual: Any, constraint: DataConstraint) -> bool:
        if actual is None or type(actual) is not type(constraint.value):
            return False
        operations = {
            "==": lambda: actual == constraint.value,
            "!=": lambda: actual != constraint.value,
            "<": lambda: actual < constraint.value,
            "<=": lambda: actual <= constraint.value,
            ">": lambda: actual > constraint.value,
            ">=": lambda: actual >= constraint.value,
        }
        try:
            return bool(operations[constraint.op]())
        except TypeError:
            return False


class RuntimeVerifier:
    def __init__(self, spec: AuraSpec, *, constraint_evaluator: ConstraintEvaluator | None = None):
        self.spec = spec
        self.constraint_evaluator = constraint_evaluator or ConstraintEvaluator()

    def verify(self, events: Iterable[AgentEvent]) -> list[Finding]:
        """Verify a complete finite trace, concluding pending future obligations at EOF."""
        monitor = OnlineTemporalMonitor(
            self.spec,
            constraint_evaluator=self.constraint_evaluator,
        )
        ltlf_monitor = OnlineLTLfMonitor(self.spec)
        findings: list[Finding] = []
        for event in events:
            findings.extend(monitor.observe(event))
            findings.extend(ltlf_monitor.observe(event))
        findings.extend(monitor.finalize())
        findings.extend(ltlf_monitor.finalize())
        return findings

    def verify_event(self, event: AgentEvent, history: list[AgentEvent]) -> list[Finding]:
        """Evaluate one prospective event against prior evidence only."""
        if history and event.run_id != history[0].run_id:
            raise ValueError("event and history must belong to the same run")
        findings: list[Finding] = []
        for policy in self.spec.policies:
            if policy.on.matches(event):
                findings.extend(self._evaluate_policy(policy, event, history))
        return findings

    def _evaluate_policy(
        self, policy: Policy, event: AgentEvent, history: list[AgentEvent]
    ) -> list[Finding]:
        findings: list[Finding] = []

        if policy.require_prior is not None:
            window = policy.require_prior.within_events or len(history)
            candidates = history[-window:]
            evidence = [
                candidate
                for candidate in candidates
                if policy.require_prior.matches(candidate, reference=event)
            ]
            if not evidence:
                findings.append(
                    Finding(
                        run_id=event.run_id,
                        policy_id=policy.id,
                        severity=policy.severity,
                        message=f"Missing required prior event: {policy.require_prior.event.value}",
                        event_id=event.event_id,
                        evidence_event_ids=[candidate.event_id for candidate in candidates],
                        engine="bounded-temporal-monitor",
                    )
                )

        for constraint in policy.constraints:
            actual = value_at_path(event, constraint.path)
            passed, engine = self.constraint_evaluator.evaluate(actual, constraint)
            if not passed:
                message = constraint.message or (
                    f"Constraint failed: {constraint.path} {constraint.op} {constraint.value!r}; "
                    f"observed {actual!r}"
                )
                findings.append(
                    Finding(
                        run_id=event.run_id,
                        policy_id=policy.id,
                        severity=policy.severity,
                        message=message,
                        event_id=event.event_id,
                        evidence_event_ids=[event.event_id],
                        engine=engine,
                    )
                )

        return findings


class OnlineTemporalMonitor:
    """Incrementally monitor bounded future obligations over one agent run."""

    def __init__(
        self,
        spec: AuraSpec,
        *,
        constraint_evaluator: ConstraintEvaluator | None = None,
    ) -> None:
        self.spec = spec
        self._immediate = RuntimeVerifier(
            spec,
            constraint_evaluator=constraint_evaluator,
        )
        self._history: list[AgentEvent] = []
        self._pending: list[_Obligation] = []
        self._findings: list[Finding] = []
        self._satisfied = 0
        self._violated = 0
        self._finalized = False

    def observe(self, event: AgentEvent) -> list[Finding]:
        """Advance the monitor by one event and return newly conclusive violations."""
        if self._finalized:
            raise ValueError("cannot observe events after the monitor is finalized")
        if self._history and event.run_id != self._history[0].run_id:
            raise ValueError("monitor accepts events from exactly one run")

        index = len(self._history)
        temporal_findings = self._advance_obligations(event, index)
        findings = temporal_findings + self._immediate.verify_event(event, self._history)
        for policy in self.spec.policies:
            if policy.require_after is not None and policy.on.matches(event):
                self._pending.append(_Obligation(policy, event, index))
        self._history.append(event)
        self._findings.extend(temporal_findings)

        if event.kind == EventKind.RUN_COMPLETED:
            findings.extend(self.finalize(conclusion_event=event))
        return findings

    def finalize(self, *, conclusion_event: AgentEvent | None = None) -> list[Finding]:
        """Declare the current prefix complete and violate unresolved obligations."""
        if self._finalized:
            return []
        findings = [
            self._violation(
                obligation,
                conclusion_event or obligation.trigger,
                "finite trace ended before the required future event",
            )
            for obligation in self._pending
        ]
        self._violated += len(findings)
        self._pending.clear()
        self._findings.extend(findings)
        self._finalized = True
        return findings

    def report(self) -> TemporalMonitorReport:
        run_id = self._history[0].run_id if self._history else None
        index = len(self._history)
        return TemporalMonitorReport(
            run_id=run_id,
            observed_event_count=index,
            pending_obligation_count=len(self._pending),
            satisfied_obligation_count=self._satisfied,
            violated_obligation_count=self._violated,
            pending=[
                PendingObligation(
                    policy_id=item.policy.id,
                    trigger_event_id=item.trigger.event_id,
                    trigger_index=item.trigger_index,
                    deadline_index=item.deadline_index,
                    remaining_events=item.deadline_index - index + 1,
                )
                for item in self._pending
            ],
            findings=self._findings,
            finalized=self._finalized,
        )

    def _advance_obligations(self, event: AgentEvent, index: int) -> list[Finding]:
        findings: list[Finding] = []
        still_pending: list[_Obligation] = []
        for obligation in self._pending:
            obligation.evidence_event_ids.append(event.event_id)
            selector = obligation.policy.require_after
            assert selector is not None
            if selector.matches(event, reference=obligation.trigger):
                self._satisfied += 1
            elif index >= obligation.deadline_index:
                findings.append(
                    self._violation(
                        obligation,
                        event,
                        f"required event was not observed within "
                        f"{selector.within_events} subsequent events",
                    )
                )
                self._violated += 1
            else:
                still_pending.append(obligation)
        self._pending = still_pending
        return findings

    @staticmethod
    def _violation(
        obligation: _Obligation,
        conclusion_event: AgentEvent,
        reason: str,
    ) -> Finding:
        selector = obligation.policy.require_after
        assert selector is not None
        evidence = list(dict.fromkeys(obligation.evidence_event_ids))
        if conclusion_event.event_id not in evidence:
            evidence.append(conclusion_event.event_id)
        return Finding(
            run_id=conclusion_event.run_id,
            policy_id=obligation.policy.id,
            severity=obligation.policy.severity,
            message=f"Missing required future event {selector.event.value}: {reason}",
            event_id=conclusion_event.event_id,
            evidence_event_ids=evidence,
            engine="bounded-response-monitor",
        )


class LTLfPolicyState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_id: str
    description: str
    monitor: LTLfMonitorReport
    finding_emitted: bool


class LTLfRuntimeReport(BaseModel):
    """Content-free formula states for one finite agent-run prefix."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str | None
    observed_event_count: int = Field(ge=0)
    policies: list[LTLfPolicyState]
    findings: list[Finding]
    finalized: bool
    content_included: Literal[False] = False


class PolicyShieldDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_id: str
    description: str
    effect: Literal["deny", "require_approval"]
    intervention: Literal[
        "allow", "suppress", "delay", "substitute", "request_approval", "no_safe_action"
    ]
    follow_up: Literal["request_approval", "wait_for_environment"] | None = None
    assessment: LTLfShieldReport


class ShieldActionReport(BaseModel):
    """Deterministic assessment of a proposed canonical event."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    event_id: UUID
    safe: bool
    policies: list[PolicyShieldDecision]
    content_included: Literal[False] = False


class PolicyStrategyReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_id: str
    description: str
    alphabet: EventAlphabetReport
    strategy: LTLfStrategyReport
    partial_observation: PartialObservationReport | None


class JointStrategyReport(BaseModel):
    """One strategy game for the conjunction of every configured LTLf policy."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_ids: list[str]
    alphabet: EventAlphabetReport
    strategy: LTLfStrategyReport
    partial_observation: PartialObservationReport | None


class RuntimeStrategyReport(BaseModel):
    """Game solutions for all LTLf policies at one accepted run prefix."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str | None
    observed_event_count: int = Field(ge=0)
    policies: list[PolicyStrategyReport]
    joint: JointStrategyReport | None
    all_full_information_realizable: bool
    all_individually_realizable: bool
    all_realizable: bool
    content_included: Literal[False] = False


class OnlineLTLfMonitor:
    """Bind Aura event selectors to exact progression-based LTLf monitors."""

    def __init__(self, spec: AuraSpec) -> None:
        self.spec = spec
        self._monitors = {
            policy.id: LTLfMonitor(policy.formula) for policy in spec.ltlf_policies
        }
        self._history: list[AgentEvent] = []
        self._findings: list[Finding] = []
        self._emitted: set[str] = set()
        self._finalized = False

    def preview(self, event: AgentEvent) -> ShieldActionReport:
        """Assess one event without changing accepted monitor state."""
        if self._finalized:
            raise ValueError("cannot preview events after the LTLf monitor is finalized")
        if self._history and event.run_id != self._history[0].run_id:
            raise ValueError("monitor accepts events from exactly one run")
        policies = []
        for policy in self.spec.ltlf_policies:
            monitor = self._monitors[policy.id]
            alphabet = EventAlphabet(policy, monitor.propositions)
            true_propositions = {
                name
                for name in monitor.propositions
                if policy.propositions[name].matches(event)
            }
            assessment = monitor.preview(
                true_propositions,
                controllable_propositions={
                    name
                    for name in monitor.propositions
                    if policy.control_of(name) == "agent"
                },
                environment_propositions={
                    name
                    for name in monitor.propositions
                    if policy.control_of(name) == "environment"
                },
                valuation_filter=alphabet.is_feasible,
            )
            environment_requirements = {
                name
                for requirement in assessment.environment_requirements
                for name in requirement
            }
            approval_required = any(
                policy.propositions[name].event == EventKind.HUMAN_APPROVAL
                for name in environment_requirements
            )
            intervention: Literal[
                "allow",
                "suppress",
                "delay",
                "substitute",
                "request_approval",
                "no_safe_action",
            ]
            if assessment.classification != "permanently_violating":
                intervention = "allow"
            elif assessment.alternatives:
                removed = any(
                    set(item.changed_propositions) & true_propositions
                    for item in assessment.alternatives
                )
                delayed = removed and any(
                    item.resulting_verdict == "currently_violated"
                    for item in assessment.alternatives
                )
                intervention = "delay" if delayed else "suppress" if removed else "substitute"
            elif approval_required:
                intervention = "request_approval"
            else:
                intervention = "no_safe_action"
            policies.append(
                PolicyShieldDecision(
                    policy_id=policy.id,
                    description=policy.description,
                    effect=policy.effect,
                    intervention=intervention,
                    follow_up=(
                        "request_approval"
                        if approval_required
                        else "wait_for_environment"
                        if environment_requirements
                        else None
                    ),
                    assessment=assessment,
                )
            )
        return ShieldActionReport(
            run_id=event.run_id,
            event_id=event.event_id,
            safe=all(
                item.assessment.classification != "permanently_violating"
                for item in policies
            ),
            policies=policies,
        )

    def prospective_findings(
        self, event: AgentEvent, report: ShieldActionReport | None = None
    ) -> list[Finding]:
        """Build findings for a rejected proposal without committing it."""
        assessment = report or self.preview(event)
        policies = {policy.id: policy for policy in self.spec.ltlf_policies}
        evidence = [item.event_id for item in [*self._history, event]]
        return [
            self._violation(policies[item.policy_id], event, evidence, final=False)
            for item in assessment.policies
            if item.assessment.classification == "permanently_violating"
            and item.policy_id not in self._emitted
        ]

    def strategy_report(self) -> RuntimeStrategyReport:
        """Solve controller/environment games from the current accepted prefix."""
        policies = []
        for policy in self.spec.ltlf_policies:
            monitor = self._monitors[policy.id]
            alphabet = EventAlphabet(policy, monitor.propositions)
            agent_propositions = {
                name
                for name in monitor.propositions
                if policy.control_of(name) == "agent"
            }
            hidden_propositions = {
                name
                for name in monitor.propositions
                if policy.visibility_of(name) == "hidden"
            }
            policies.append(
                PolicyStrategyReport(
                    policy_id=policy.id,
                    description=policy.description,
                    alphabet=alphabet.report(),
                    strategy=monitor.synthesize_strategy(
                        agent_propositions,
                        valuation_space=alphabet,
                    ),
                    partial_observation=(
                        synthesize_partial_observation(
                            monitor,
                            agent_propositions,
                            set(monitor.propositions)
                            - agent_propositions
                            - hidden_propositions,
                            alphabet,
                        )
                        if hidden_propositions
                        else None
                    ),
                )
            )
        full_information_realizable = all(
            item.strategy.status == "realizable" for item in policies
        )
        individually_realizable = full_information_realizable and all(
            item.partial_observation is None
            or item.partial_observation.status == "realizable"
            for item in policies
        )
        joint = self._joint_strategy_report()
        return RuntimeStrategyReport(
            run_id=self._history[0].run_id if self._history else None,
            observed_event_count=len(self._history),
            policies=policies,
            joint=joint,
            all_full_information_realizable=full_information_realizable,
            all_individually_realizable=individually_realizable,
            all_realizable=(
                joint.strategy.status == "realizable"
                and (
                    joint.partial_observation is None
                    or joint.partial_observation.status == "realizable"
                )
                if joint is not None
                else individually_realizable
            ),
        )

    def _joint_strategy_report(self) -> JointStrategyReport | None:
        """Compose multiple accepted residuals into one compatibility game."""
        if len(self.spec.ltlf_policies) < 2:
            return None
        selector_names: dict[str, str] = {}
        selectors: dict[str, EventSelector] = {}
        controls: dict[str, list[str]] = {}
        visibilities: dict[str, list[str]] = {}
        residuals: list[Formula] = []
        for policy in self.spec.ltlf_policies:
            monitor = self._monitors[policy.id]
            names: dict[str, str] = {}
            for proposition in monitor.propositions:
                selector = policy.propositions[proposition]
                fingerprint = selector.model_dump_json()
                synthetic = selector_names.setdefault(
                    fingerprint, f"p{len(selector_names)}"
                )
                selectors[synthetic] = selector
                controls.setdefault(synthetic, []).append(policy.control_of(proposition))
                visibilities.setdefault(synthetic, []).append(
                    policy.visibility_of(proposition)
                )
                names[proposition] = synthetic
            residuals.append(rename_atoms(monitor.residual, names))
        joint_formula = make_and(*residuals)
        proposition_control = {
            name: "agent" if set(owners) == {"agent"} else "observed"
            for name, owners in controls.items()
        }
        proposition_visibility = {
            name: "hidden" if "hidden" in values else "observable"
            for name, values in visibilities.items()
        }
        policy = LTLfPolicy(
            id="joint",
            description="Conjunction of configured LTLf policies",
            formula=str(joint_formula),
            propositions=selectors,
            proposition_control=proposition_control,
            proposition_visibility=proposition_visibility,
        )
        monitor = LTLfMonitor(joint_formula)
        alphabet = EventAlphabet(policy, monitor.propositions)
        agent_propositions = {
            name
            for name in monitor.propositions
            if policy.control_of(name) == "agent"
        }
        hidden_propositions = {
            name
            for name in monitor.propositions
            if policy.visibility_of(name) == "hidden"
        }
        return JointStrategyReport(
            policy_ids=[item.id for item in self.spec.ltlf_policies],
            alphabet=alphabet.report(),
            strategy=monitor.synthesize_strategy(
                agent_propositions,
                valuation_space=alphabet,
            ),
            partial_observation=(
                synthesize_partial_observation(
                    monitor,
                    agent_propositions,
                    set(monitor.propositions)
                    - agent_propositions
                    - hidden_propositions,
                    alphabet,
                )
                if hidden_propositions
                else None
            ),
        )

    def observe(self, event: AgentEvent) -> list[Finding]:
        if self._finalized:
            raise ValueError("cannot observe events after the LTLf monitor is finalized")
        if self._history and event.run_id != self._history[0].run_id:
            raise ValueError("monitor accepts events from exactly one run")
        findings: list[Finding] = []
        evidence = [item.event_id for item in [*self._history, event]]
        for policy in self.spec.ltlf_policies:
            monitor = self._monitors[policy.id]
            true_propositions = {
                name
                for name in monitor.propositions
                if policy.propositions[name].matches(event)
            }
            monitor.observe(true_propositions)
            if (
                monitor.report().prefix_verdict == PrefixVerdict.PERMANENTLY_VIOLATED
                and policy.id not in self._emitted
            ):
                findings.append(self._violation(policy, event, evidence, final=False))
                self._emitted.add(policy.id)
        self._history.append(event)
        self._findings.extend(findings)
        if event.kind == EventKind.RUN_COMPLETED:
            findings.extend(self.finalize(conclusion_event=event))
        return findings

    def finalize(self, *, conclusion_event: AgentEvent | None = None) -> list[Finding]:
        if self._finalized:
            return []
        if not self._history:
            self._finalized = True
            return []
        event = conclusion_event or self._history[-1]
        evidence = [item.event_id for item in self._history]
        findings: list[Finding] = []
        for policy in self.spec.ltlf_policies:
            report = self._monitors[policy.id].finalize()
            if report.finite_trace_verdict == "fail" and policy.id not in self._emitted:
                findings.append(self._violation(policy, event, evidence, final=True))
                self._emitted.add(policy.id)
        self._findings.extend(findings)
        self._finalized = True
        return findings

    def report(self) -> LTLfRuntimeReport:
        return LTLfRuntimeReport(
            run_id=self._history[0].run_id if self._history else None,
            observed_event_count=len(self._history),
            policies=[
                LTLfPolicyState(
                    policy_id=policy.id,
                    description=policy.description,
                    monitor=self._monitors[policy.id].report(),
                    finding_emitted=policy.id in self._emitted,
                )
                for policy in self.spec.ltlf_policies
            ],
            findings=self._findings,
            finalized=self._finalized,
        )

    @staticmethod
    def _violation(
        policy: LTLfPolicy,
        event: AgentEvent,
        evidence_event_ids: list[UUID],
        *,
        final: bool,
    ) -> Finding:
        reason = (
            "finite trace does not satisfy the formula"
            if final
            else "no future extension can satisfy the formula"
        )
        return Finding(
            run_id=event.run_id,
            policy_id=policy.id,
            severity=policy.severity,
            message=f"LTLf formula violated: {reason}",
            event_id=event.event_id,
            evidence_event_ids=list(dict.fromkeys(evidence_event_ids)),
            engine="ltlf-progression-monitor-v0.1",
        )
