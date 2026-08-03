"""Deterministic online verification for AuraSpec policies."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from aura_runtime.models import AgentEvent, Finding
from aura_runtime.policy import AuraSpec, DataConstraint, Policy, value_at_path


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
        history: list[AgentEvent] = []
        findings: list[Finding] = []
        for event in events:
            if history and event.run_id != history[0].run_id:
                raise ValueError("verify() accepts events from exactly one run")
            findings.extend(self.verify_event(event, history))
            history.append(event)
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
                candidate for candidate in candidates if policy.require_prior.matches(candidate)
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
