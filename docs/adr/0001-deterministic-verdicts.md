# ADR 0001: Deterministic verdicts

Status: Accepted

## Context

Agent traces require explanations, but model-generated judgments are not reproducible and
cannot provide a dependable policy gate.

## Decision

Aura computes compliance only with explicit selectors, temporal monitors, finite-trace
logic, constraints, and exact bounded solvers. LLMs may consume or explain a verdict but
never create, change, or break a tie in one.

## Consequences

Verdicts are replayable and testable. Unsupported or bounded-out cases must be reported as
unknown/inconclusive instead of guessed, and natural-language-only policies are insufficient.
