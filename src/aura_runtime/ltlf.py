"""LTLf parsing, formula progression, and exact finite-prefix monitoring."""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from itertools import product
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class LTLfSyntaxError(ValueError):
    """Raised when an LTLf formula is not valid Aura syntax."""


class LTLfComplexityError(ValueError):
    """Raised instead of approximating a monitor whose state space exceeds its bound."""


@dataclass(frozen=True, slots=True)
class Formula:
    op: str
    args: tuple[Formula, ...] = ()
    name: str = ""

    def __str__(self) -> str:
        if self.op == "true":
            return "true"
        if self.op == "false":
            return "false"
        if self.op == "atom":
            return self.name
        if self.op == "not_atom":
            return f"!{self.name}"
        if self.op in {"next", "weak_next", "eventually", "always"}:
            symbols = {
                "next": "X",
                "weak_next": "Xw",
                "eventually": "F",
                "always": "G",
            }
            return f"{symbols[self.op]}({self.args[0]})"
        if self.op in {"need_next", "need_weak_next"}:
            symbol = "@X" if self.op == "need_next" else "@Xw"
            return f"{symbol}({self.args[0]})"
        symbols = {"and": "&", "or": "|", "until": "U", "release": "R"}
        return f"({f' {symbols[self.op]} '.join(str(item) for item in self.args)})"


TRUE = Formula("true")
FALSE = Formula("false")


def _key(formula: Formula) -> str:
    return str(formula)


def _opposite(formula: Formula) -> Formula | None:
    if formula.op == "atom":
        return Formula("not_atom", name=formula.name)
    if formula.op == "not_atom":
        return Formula("atom", name=formula.name)
    return None


def make_and(*values: Formula) -> Formula:
    flattened: list[Formula] = []
    for value in values:
        if value == FALSE:
            return FALSE
        if value == TRUE:
            continue
        flattened.extend(value.args if value.op == "and" else (value,))
    unique = set(flattened)
    if any(_opposite(value) in unique for value in unique):
        return FALSE
    ordered = tuple(sorted(unique, key=_key))
    if not ordered:
        return TRUE
    if len(ordered) == 1:
        return ordered[0]
    return Formula("and", ordered)


def make_or(*values: Formula) -> Formula:
    flattened: list[Formula] = []
    for value in values:
        if value == TRUE:
            return TRUE
        if value == FALSE:
            continue
        flattened.extend(value.args if value.op == "or" else (value,))
    unique = set(flattened)
    if any(_opposite(value) in unique for value in unique):
        return TRUE
    ordered = tuple(sorted(unique, key=_key))
    if not ordered:
        return FALSE
    if len(ordered) == 1:
        return ordered[0]
    return Formula("or", ordered)


def make_eventually(value: Formula) -> Formula:
    if value in {TRUE, FALSE}:
        return value
    return Formula("eventually", (value,))


def make_always(value: Formula) -> Formula:
    if value == TRUE:
        return TRUE
    if value == FALSE:
        return FALSE
    return Formula("always", (value,))


def negate(formula: Formula) -> Formula:
    """Return negation normal form, including finite-trace next duality."""
    if formula == TRUE:
        return FALSE
    if formula == FALSE:
        return TRUE
    if formula.op == "atom":
        return Formula("not_atom", name=formula.name)
    if formula.op == "not_atom":
        return Formula("atom", name=formula.name)
    if formula.op == "and":
        return make_or(*(negate(item) for item in formula.args))
    if formula.op == "or":
        return make_and(*(negate(item) for item in formula.args))
    if formula.op == "next":
        return Formula("weak_next", (negate(formula.args[0]),))
    if formula.op == "weak_next":
        return Formula("next", (negate(formula.args[0]),))
    if formula.op == "eventually":
        return make_always(negate(formula.args[0]))
    if formula.op == "always":
        return make_eventually(negate(formula.args[0]))
    if formula.op == "until":
        return Formula("release", tuple(negate(item) for item in formula.args))
    if formula.op == "release":
        return Formula("until", tuple(negate(item) for item in formula.args))
    raise ValueError(f"cannot negate internal formula operator {formula.op!r}")


_TOKEN = re.compile(r"\s*(<->|->|&&|\|\||[()!&|]|[A-Za-z_][A-Za-z0-9_.-]*)")


def _tokens(text: str) -> list[str]:
    values: list[str] = []
    position = 0
    while position < len(text):
        match = _TOKEN.match(text, position)
        if match is None:
            if text[position:].strip() == "":
                break
            raise LTLfSyntaxError(f"unexpected token at character {position + 1}")
        values.append(match.group(1))
        position = match.end()
    if not values:
        raise LTLfSyntaxError("formula is empty")
    return values


class _Parser:
    def __init__(self, text: str) -> None:
        self.tokens = _tokens(text)
        self.index = 0

    def parse(self) -> Formula:
        formula = self._equivalence()
        if self.index != len(self.tokens):
            raise LTLfSyntaxError(f"unexpected token {self.tokens[self.index]!r}")
        return formula

    def _peek(self, *values: str) -> bool:
        return self.index < len(self.tokens) and self.tokens[self.index] in values

    def _take(self) -> str:
        if self.index >= len(self.tokens):
            raise LTLfSyntaxError("unexpected end of formula")
        value = self.tokens[self.index]
        self.index += 1
        return value

    def _equivalence(self) -> Formula:
        left = self._implication()
        while self._peek("<->"):
            self._take()
            right = self._implication()
            left = make_or(make_and(left, right), make_and(negate(left), negate(right)))
        return left

    def _implication(self) -> Formula:
        left = self._or()
        if self._peek("->"):
            self._take()
            return make_or(negate(left), self._implication())
        return left

    def _or(self) -> Formula:
        values = [self._and()]
        while self._peek("|", "||", "or"):
            self._take()
            values.append(self._and())
        return make_or(*values)

    def _and(self) -> Formula:
        values = [self._temporal()]
        while self._peek("&", "&&", "and"):
            self._take()
            values.append(self._temporal())
        return make_and(*values)

    def _temporal(self) -> Formula:
        left = self._unary()
        if self._peek("U", "R"):
            operator = "until" if self._take() == "U" else "release"
            return Formula(operator, (left, self._temporal()))
        return left

    def _unary(self) -> Formula:
        if self._peek("!", "not"):
            self._take()
            return negate(self._unary())
        if self._peek("X", "Xw", "F", "G"):
            operator = {
                "X": "next",
                "Xw": "weak_next",
                "F": "eventually",
                "G": "always",
            }[self._take()]
            value = self._unary()
            if operator == "eventually":
                return make_eventually(value)
            if operator == "always":
                return make_always(value)
            return Formula(operator, (value,))
        return self._primary()

    def _primary(self) -> Formula:
        token = self._take()
        if token == "(":
            value = self._equivalence()
            if self._take() != ")":
                raise LTLfSyntaxError("expected closing parenthesis")
            return value
        if token == ")":
            raise LTLfSyntaxError("unexpected closing parenthesis")
        if token.lower() == "true":
            return TRUE
        if token.lower() == "false":
            return FALSE
        if token in {"U", "R", "and", "or", "not", "X", "Xw", "F", "G"}:
            raise LTLfSyntaxError(f"operator {token!r} is missing an operand")
        return Formula("atom", name=token)


def parse_ltlf(text: str) -> Formula:
    """Parse Aura's ASCII LTLf syntax into a canonical formula."""
    return _Parser(text).parse()


def atoms(formula: Formula) -> frozenset[str]:
    if formula.op in {"atom", "not_atom"}:
        return frozenset({formula.name})
    return frozenset().union(*(atoms(item) for item in formula.args))


def progress(formula: Formula, true_atoms: frozenset[str]) -> Formula:
    """Compute one deterministic residual state after observing a valuation."""
    if formula in {TRUE, FALSE}:
        return formula
    if formula.op == "atom":
        return TRUE if formula.name in true_atoms else FALSE
    if formula.op == "not_atom":
        return FALSE if formula.name in true_atoms else TRUE
    if formula.op == "and":
        return make_and(*(progress(item, true_atoms) for item in formula.args))
    if formula.op == "or":
        return make_or(*(progress(item, true_atoms) for item in formula.args))
    if formula.op == "next":
        return Formula("need_next", formula.args)
    if formula.op == "weak_next":
        return Formula("need_weak_next", formula.args)
    if formula.op in {"need_next", "need_weak_next"}:
        return progress(formula.args[0], true_atoms)
    if formula.op == "eventually":
        return make_or(progress(formula.args[0], true_atoms), formula)
    if formula.op == "always":
        return make_and(progress(formula.args[0], true_atoms), formula)
    if formula.op == "until":
        left, right = formula.args
        return make_or(
            progress(right, true_atoms),
            make_and(progress(left, true_atoms), formula),
        )
    if formula.op == "release":
        left, right = formula.args
        return make_and(
            progress(right, true_atoms),
            make_or(progress(left, true_atoms), formula),
        )
    raise ValueError(f"unknown formula operator {formula.op!r}")


def accepts_empty(formula: Formula) -> bool:
    """Evaluate a residual formula when the finite trace ends now."""
    if formula == TRUE:
        return True
    if formula == FALSE:
        return False
    if formula.op == "atom":
        return False
    if formula.op == "not_atom":
        return True
    if formula.op == "and":
        return all(accepts_empty(item) for item in formula.args)
    if formula.op == "or":
        return any(accepts_empty(item) for item in formula.args)
    if formula.op in {"next", "need_next", "eventually", "until"}:
        return False
    if formula.op in {"weak_next", "need_weak_next", "always", "release"}:
        return True
    raise ValueError(f"unknown formula operator {formula.op!r}")


@lru_cache(maxsize=4096)
def _extension_possibilities(
    initial: Formula,
    atom_names: tuple[str, ...],
    max_states: int,
) -> tuple[bool, bool, int]:
    valuations = tuple(
        frozenset(name for name, enabled in zip(atom_names, bits, strict=True) if enabled)
        for bits in product((False, True), repeat=len(atom_names))
    )
    queue = deque([initial])
    seen: set[Formula] = set()
    accepts = False
    rejects = False
    while queue:
        state = queue.popleft()
        if state in seen:
            continue
        seen.add(state)
        if len(seen) > max_states:
            raise LTLfComplexityError(
                f"reachable monitor states exceed configured limit {max_states}"
            )
        if accepts_empty(state):
            accepts = True
        else:
            rejects = True
        if accepts and rejects:
            return accepts, rejects, len(seen)
        for valuation in valuations:
            successor = progress(state, valuation)
            if successor not in seen:
                queue.append(successor)
    return accepts, rejects, len(seen)


class PrefixVerdict(StrEnum):
    PERMANENTLY_SATISFIED = "permanently_satisfied"
    CURRENTLY_SATISFIED = "currently_satisfied"
    CURRENTLY_VIOLATED = "currently_violated"
    PERMANENTLY_VIOLATED = "permanently_violated"


class ShieldClassification(StrEnum):
    SAFE = "safe"
    PERMANENTLY_VIOLATING = "permanently_violating"


class ShieldAlternative(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    true_propositions: list[str]
    false_propositions: list[str]
    changed_propositions: list[str]
    distance: int = Field(ge=1)
    resulting_verdict: PrefixVerdict


class LTLfShieldReport(BaseModel):
    """Non-mutating safety assessment of one proposed valuation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    proposed_true_propositions: list[str]
    proposed_false_propositions: list[str]
    classification: ShieldClassification
    resulting_residual: str
    resulting_verdict: PrefixVerdict
    alternatives: list[ShieldAlternative]
    content_included: Literal[False] = False


class LTLfMonitorReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    formula: str
    residual: str
    propositions: list[str]
    observed_event_count: int = Field(ge=0)
    prefix_verdict: PrefixVerdict
    finite_trace_verdict: Literal["pass", "fail", "undetermined"]
    explored_state_count: int = Field(ge=1)
    finalized: bool
    content_included: Literal[False] = False


class LTLfMonitor:
    """An on-demand deterministic automaton generated by formula progression."""

    def __init__(
        self,
        formula: str | Formula,
        *,
        max_atoms: int = 10,
        max_states: int = 4096,
    ) -> None:
        self.formula = parse_ltlf(formula) if isinstance(formula, str) else formula
        self.propositions = tuple(sorted(atoms(self.formula)))
        if len(self.propositions) > max_atoms:
            raise LTLfComplexityError(
                f"formula uses {len(self.propositions)} propositions; limit is {max_atoms}"
            )
        self.max_states = max_states
        self.residual = self.formula
        self.observed_event_count = 0
        self.finalized = False

    def observe(self, true_propositions: set[str] | frozenset[str]) -> None:
        if self.finalized:
            raise ValueError("cannot observe events after the LTLf monitor is finalized")
        unknown = set(true_propositions) - set(self.propositions)
        if unknown:
            raise ValueError(f"unknown propositions: {', '.join(sorted(unknown))}")
        self.residual = progress(self.residual, frozenset(true_propositions))
        self.observed_event_count += 1

    def preview(
        self,
        true_propositions: set[str] | frozenset[str],
        *,
        max_alternatives: int = 8,
    ) -> LTLfShieldReport:
        """Assess a next valuation and nearest safe alternatives without mutation."""
        if self.finalized:
            raise ValueError("cannot preview events after the LTLf monitor is finalized")
        proposed = frozenset(true_propositions)
        unknown = proposed - set(self.propositions)
        if unknown:
            raise ValueError(f"unknown propositions: {', '.join(sorted(unknown))}")
        successor = progress(self.residual, proposed)
        verdict, _ = self._classify(successor)
        violating = verdict == PrefixVerdict.PERMANENTLY_VIOLATED
        alternatives: list[ShieldAlternative] = []
        if violating:
            candidates: list[tuple[int, tuple[str, ...], frozenset[str], PrefixVerdict]] = []
            for bits in product((False, True), repeat=len(self.propositions)):
                candidate = frozenset(
                    name
                    for name, enabled in zip(self.propositions, bits, strict=True)
                    if enabled
                )
                if candidate == proposed:
                    continue
                candidate_verdict, _ = self._classify(progress(self.residual, candidate))
                if candidate_verdict == PrefixVerdict.PERMANENTLY_VIOLATED:
                    continue
                changed = tuple(sorted(proposed ^ candidate))
                candidates.append((len(changed), changed, candidate, candidate_verdict))
            candidates.sort(key=lambda item: (item[0], item[1], tuple(sorted(item[2]))))
            for distance, changed, candidate, candidate_verdict in candidates[:max_alternatives]:
                alternatives.append(
                    ShieldAlternative(
                        true_propositions=sorted(candidate),
                        false_propositions=sorted(set(self.propositions) - candidate),
                        changed_propositions=list(changed),
                        distance=distance,
                        resulting_verdict=candidate_verdict,
                    )
                )
        return LTLfShieldReport(
            proposed_true_propositions=sorted(proposed),
            proposed_false_propositions=sorted(set(self.propositions) - proposed),
            classification=(
                ShieldClassification.PERMANENTLY_VIOLATING
                if violating
                else ShieldClassification.SAFE
            ),
            resulting_residual=str(successor),
            resulting_verdict=verdict,
            alternatives=alternatives,
        )

    def finalize(self) -> LTLfMonitorReport:
        if self.observed_event_count == 0:
            raise ValueError("cannot finalize an empty LTLf trace")
        self.finalized = True
        return self.report()

    def report(self) -> LTLfMonitorReport:
        verdict, state_count = self._classify(self.residual)
        finite_verdict: Literal["pass", "fail", "undetermined"] = "undetermined"
        if self.finalized:
            finite_verdict = "pass" if accepts_empty(self.residual) else "fail"
        return LTLfMonitorReport(
            formula=str(self.formula),
            residual=str(self.residual),
            propositions=list(self.propositions),
            observed_event_count=self.observed_event_count,
            prefix_verdict=verdict,
            finite_trace_verdict=finite_verdict,
            explored_state_count=state_count,
            finalized=self.finalized,
        )

    def _classify(self, residual: Formula) -> tuple[PrefixVerdict, int]:
        accepts, rejects, state_count = _extension_possibilities(
            residual, self.propositions, self.max_states
        )
        if accepts and not rejects:
            verdict = PrefixVerdict.PERMANENTLY_SATISFIED
        elif rejects and not accepts:
            verdict = PrefixVerdict.PERMANENTLY_VIOLATED
        elif accepts_empty(residual):
            verdict = PrefixVerdict.CURRENTLY_SATISFIED
        else:
            verdict = PrefixVerdict.CURRENTLY_VIOLATED
        return verdict, state_count
