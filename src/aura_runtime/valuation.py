"""Replaceable Boolean valuation spaces for temporal reasoning backends."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from itertools import product
from typing import Protocol


class ValuationSpace(Protocol):
    """Provide valid joint proposition valuations without exposing its solver."""

    propositions: tuple[str, ...]
    backend: str

    def valuations(self) -> Iterator[frozenset[str]]:
        """Yield each permitted valuation exactly once."""
        ...


@dataclass(frozen=True)
class ExplicitValuationSpace:
    """Reference implementation used for unconstrained and differential tests."""

    propositions: tuple[str, ...]
    predicate: Callable[[frozenset[str]], bool] | None = None
    backend: str = "explicit"

    def valuations(self) -> Iterator[frozenset[str]]:
        for bits in product((False, True), repeat=len(self.propositions)):
            valuation = frozenset(
                name
                for name, enabled in zip(self.propositions, bits, strict=True)
                if enabled
            )
            if self.predicate is None or self.predicate(valuation):
                yield valuation
