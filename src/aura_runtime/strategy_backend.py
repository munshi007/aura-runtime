"""Replaceable finite-trace strategy-synthesis backends."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol

from aura_runtime.valuation import ValuationSpace

if TYPE_CHECKING:
    from aura_runtime.ltlf import LTLfMonitor, LTLfStrategyReport


class StrategyBackend(Protocol):
    """Solve a monitor game while preserving Aura's strategy report contract."""

    name: str

    def solve(
        self,
        monitor: LTLfMonitor,
        controllable_propositions: set[str] | frozenset[str],
        *,
        max_states: int | None,
        valuation_filter: Callable[[frozenset[str]], bool] | None,
        valuation_space: ValuationSpace | None,
    ) -> LTLfStrategyReport: ...


class ExplicitProgressionBackend:
    """Trusted on-demand progression-game reference backend."""

    name = "explicit_progression"

    def solve(
        self,
        monitor: LTLfMonitor,
        controllable_propositions: set[str] | frozenset[str],
        *,
        max_states: int | None,
        valuation_filter: Callable[[frozenset[str]], bool] | None,
        valuation_space: ValuationSpace | None,
    ) -> LTLfStrategyReport:
        return monitor._synthesize_strategy_explicit(
            controllable_propositions,
            max_states=max_states,
            valuation_filter=valuation_filter,
            valuation_space=valuation_space,
            strategy_backend=self.name,
        )
