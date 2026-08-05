"""Structural feasibility for Aura event-selector proposition valuations."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from itertools import combinations, product
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from aura_runtime.policy import LTLfPolicy


class EventAlphabetReport(BaseModel):
    """Content-free summary of inferred valuation feasibility."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    propositions: list[str]
    total_valuation_count: int = Field(ge=1)
    feasible_valuation_count: int = Field(ge=1)
    rejected_valuation_count: int = Field(ge=0)
    rejection_reasons: dict[str, int]
    content_included: Literal[False] = False


class EventAlphabet:
    """Infer a conservative Boolean alphabet from proposition event selectors.

    The checker proves common structural impossibilities. Unknown relationships remain
    feasible, so inference cannot remove a real behavior merely because a glob or payload
    constraint is too complex to decide here.
    """

    backend = "z3"

    def __init__(
        self,
        policy: LTLfPolicy,
        propositions: tuple[str, ...] | None = None,
    ) -> None:
        self.policy = policy
        selected = tuple(
            sorted(policy.propositions if propositions is None else propositions)
        )
        unknown = set(selected) - set(policy.propositions)
        if unknown:
            raise ValueError(f"unknown propositions: {', '.join(sorted(unknown))}")
        self.propositions = selected

    def rejection_reason(self, true_propositions: frozenset[str]) -> str | None:
        unknown = true_propositions - set(self.propositions)
        if unknown:
            raise ValueError(f"unknown propositions: {', '.join(sorted(unknown))}")
        false_propositions = set(self.propositions) - true_propositions
        true_selectors = [self.policy.propositions[name] for name in true_propositions]
        event_kinds = {selector.event for selector in true_selectors}
        if len(event_kinds) > 1:
            return "conflicting_event_kinds"

        where_values: dict[str, object] = {}
        for selector in true_selectors:
            for path, value in selector.where.items():
                if path in where_values and where_values[path] != value:
                    return "conflicting_where_values"
                where_values[path] = value

        tool_pattern_sets = [
            set(selector.tool_matches)
            for selector in true_selectors
            if selector.tool_matches
        ]
        exact_only = tool_pattern_sets and all(
            all(not any(symbol in pattern for symbol in "*?[") for pattern in patterns)
            for patterns in tool_pattern_sets
        )
        if exact_only and not set.intersection(*tool_pattern_sets):
            return "conflicting_exact_tools"

        fingerprints = {
            name: self.policy.propositions[name].model_dump_json()
            for name in self.propositions
        }
        for true_name in true_propositions:
            if any(
                fingerprints[true_name] == fingerprints[false_name]
                for false_name in false_propositions
            ):
                return "identical_selectors_disagree"
        return None

    def is_feasible(self, true_propositions: frozenset[str]) -> bool:
        return self.rejection_reason(true_propositions) is None

    def valuations(self) -> Iterator[frozenset[str]]:
        """Enumerate feasible valuations from a symbolic selector theory."""
        import z3

        symbols = {name: z3.Bool(name) for name in self.propositions}
        solver = z3.Solver()
        selectors = self.policy.propositions
        fingerprints = {
            name: selectors[name].model_dump_json() for name in self.propositions
        }
        for left, right in combinations(self.propositions, 2):
            left_selector = selectors[left]
            right_selector = selectors[right]
            incompatible = left_selector.event != right_selector.event or any(
                path in right_selector.where and right_selector.where[path] != value
                for path, value in left_selector.where.items()
            )
            if incompatible:
                solver.add(z3.Or(z3.Not(symbols[left]), z3.Not(symbols[right])))
            if fingerprints[left] == fingerprints[right]:
                solver.add(symbols[left] == symbols[right])

        exact_tool_sets = {
            name: set(selector.tool_matches)
            for name, selector in selectors.items()
            if name in symbols
            and selector.tool_matches
            and all(
                not any(symbol in pattern for symbol in "*?[")
                for pattern in selector.tool_matches
            )
        }
        wildcard_tool_symbols = [
            symbols[name]
            for name, selector in selectors.items()
            if name in symbols
            and any(
                any(symbol in pattern for symbol in "*?[")
                for pattern in selector.tool_matches
            )
        ]
        for left, right in combinations(exact_tool_sets, 2):
            if exact_tool_sets[left].isdisjoint(exact_tool_sets[right]):
                solver.add(
                    z3.Or(
                        z3.Not(symbols[left]),
                        z3.Not(symbols[right]),
                        *wildcard_tool_symbols,
                    )
                )

        while solver.check() == z3.sat:
            model = solver.model()
            valuation = frozenset(
                name
                for name, symbol in symbols.items()
                if z3.is_true(model.eval(symbol, model_completion=True))
            )
            solver.add(
                z3.Or(
                    *[
                        symbol != z3.BoolVal(name in valuation)
                        for name, symbol in symbols.items()
                    ]
                )
            )
            if self.is_feasible(valuation):
                yield valuation

    def report(self) -> EventAlphabetReport:
        reasons: Counter[str] = Counter()
        feasible = 0
        for bits in product((False, True), repeat=len(self.propositions)):
            valuation = frozenset(
                name
                for name, enabled in zip(self.propositions, bits, strict=True)
                if enabled
            )
            reason = self.rejection_reason(valuation)
            if reason is None:
                feasible += 1
            else:
                reasons[reason] += 1
        total = 2 ** len(self.propositions)
        return EventAlphabetReport(
            propositions=list(self.propositions),
            total_valuation_count=total,
            feasible_valuation_count=feasible,
            rejected_valuation_count=total - feasible,
            rejection_reasons=dict(sorted(reasons.items())),
        )
