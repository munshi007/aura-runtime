"""Belief-state LTLf synthesis under partial environment observability."""

from __future__ import annotations

from collections import deque
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from aura_runtime.ltlf import (
    Formula,
    LTLfComplexityError,
    LTLfMonitor,
    accepts_empty,
    progress,
)
from aura_runtime.valuation import ValuationSpace

Belief = frozenset[Formula]


class BeliefStrategyMove(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    belief_residuals: list[str]
    true_agent_propositions: list[str]
    rank: int = Field(ge=1)


class BeliefCounterMove(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    belief_residuals: list[str]
    true_agent_propositions: list[str]
    true_observable_propositions: list[str]
    successor_belief_residuals: list[str]


class PartialObservationReport(BaseModel):
    """Exact belief-game result that never reveals hidden valuation content."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    formula: str
    status: Literal["realizable", "unrealizable"]
    agent_propositions: list[str]
    observable_environment_propositions: list[str]
    hidden_environment_propositions: list[str]
    reachable_belief_count: int = Field(ge=1)
    winning_belief_count: int = Field(ge=0)
    total_valuation_count: int = Field(ge=1)
    feasible_valuation_count: int = Field(ge=1)
    valuation_backend: str
    strategy_backend: Literal["belief_progression"] = "belief_progression"
    strategy: list[BeliefStrategyMove]
    counterstrategy: list[BeliefCounterMove]
    content_included: Literal[False] = False


def synthesize_partial_observation(
    monitor: LTLfMonitor,
    controllable_propositions: set[str] | frozenset[str],
    observable_environment_propositions: set[str] | frozenset[str],
    valuation_space: ValuationSpace,
    *,
    max_states: int | None = None,
) -> PartialObservationReport:
    """Solve an on-demand belief game with hidden inputs universally quantified."""
    all_propositions = set(monitor.propositions)
    controllable = frozenset(controllable_propositions)
    observable = frozenset(observable_environment_propositions)
    if controllable & observable:
        raise ValueError("agent and observable environment propositions must be disjoint")
    unknown = (controllable | observable) - all_propositions
    if unknown:
        raise ValueError(f"unknown propositions: {', '.join(sorted(unknown))}")
    if tuple(sorted(valuation_space.propositions)) != monitor.propositions:
        raise ValueError("valuation space propositions do not match the formula")
    hidden = frozenset(all_propositions - controllable - observable)
    joint_values = tuple(valuation_space.valuations())
    if not joint_values:
        raise ValueError("valuation space must contain at least one valuation")

    response_sets: dict[
        frozenset[str], dict[frozenset[str], set[frozenset[str]]]
    ] = {}
    for valuation in joint_values:
        if not valuation <= all_propositions:
            raise ValueError("valuation space produced unknown propositions")
        agent_value = valuation & controllable
        observed_value = valuation & observable
        hidden_value = valuation & hidden
        response_sets.setdefault(agent_value, {}).setdefault(observed_value, set()).add(
            hidden_value
        )
    responses = {
        agent: {
            observed: tuple(sorted(values, key=_valuation_key))
            for observed, values in observed_values.items()
        }
        for agent, observed_values in response_sets.items()
    }
    agent_values = tuple(sorted(responses, key=_valuation_key))

    initial: Belief = frozenset({monitor.residual})
    queue = deque([initial])
    beliefs: set[Belief] = set()
    transitions: dict[tuple[Belief, frozenset[str], frozenset[str]], Belief] = {}
    state_limit = max_states or monitor.max_states
    while queue:
        belief = queue.popleft()
        if belief in beliefs:
            continue
        beliefs.add(belief)
        if len(beliefs) > state_limit:
            raise LTLfComplexityError(
                f"partial-observation belief states exceed configured limit {state_limit}"
            )
        for agent in agent_values:
            for observed, hidden_values in responses[agent].items():
                successor = frozenset(
                    progress(residual, agent | observed | hidden_value)
                    for residual in belief
                    for hidden_value in hidden_values
                )
                transitions[(belief, agent, observed)] = successor
                if successor not in beliefs:
                    queue.append(successor)

    accepting = {belief for belief in beliefs if all(map(accepts_empty, belief))}
    winning = set(accepting)
    rank = {belief: 0 for belief in accepting}
    depth = 0
    while True:
        depth += 1
        added = {
            belief
            for belief in beliefs - winning
            if any(
                all(
                    transitions[(belief, agent, observed)] in winning
                    for observed in responses[agent]
                )
                for agent in agent_values
            )
        }
        if not added:
            break
        winning.update(added)
        rank.update({belief: depth for belief in added})

    strategy = []
    for belief in sorted(winning, key=_belief_key):
        if rank[belief] == 0:
            continue
        target_rank = rank[belief] - 1
        choice = next(
            agent
            for agent in agent_values
            if all(
                rank.get(transitions[(belief, agent, observed)], 10**9) <= target_rank
                for observed in responses[agent]
            )
        )
        strategy.append(
            BeliefStrategyMove(
                belief_residuals=_belief_strings(belief),
                true_agent_propositions=sorted(choice),
                rank=rank[belief],
            )
        )

    counterstrategy = []
    losing = beliefs - winning
    if initial in losing:
        for agent in agent_values:
            observed = next(
                value
                for value in responses[agent]
                if transitions[(initial, agent, value)] in losing
            )
            successor = transitions[(initial, agent, observed)]
            counterstrategy.append(
                BeliefCounterMove(
                    belief_residuals=_belief_strings(initial),
                    true_agent_propositions=sorted(agent),
                    true_observable_propositions=sorted(observed),
                    successor_belief_residuals=_belief_strings(successor),
                )
            )
    return PartialObservationReport(
        formula=str(monitor.formula),
        status="realizable" if initial in winning else "unrealizable",
        agent_propositions=sorted(controllable),
        observable_environment_propositions=sorted(observable),
        hidden_environment_propositions=sorted(hidden),
        reachable_belief_count=len(beliefs),
        winning_belief_count=len(winning),
        total_valuation_count=2 ** len(monitor.propositions),
        feasible_valuation_count=len(joint_values),
        valuation_backend=valuation_space.backend,
        strategy=strategy,
        counterstrategy=counterstrategy,
    )


def _valuation_key(value: frozenset[str]) -> tuple[str, ...]:
    return tuple(sorted(value))


def _belief_strings(belief: Belief) -> list[str]:
    return sorted(map(str, belief))


def _belief_key(belief: Belief) -> tuple[str, ...]:
    return tuple(_belief_strings(belief))
