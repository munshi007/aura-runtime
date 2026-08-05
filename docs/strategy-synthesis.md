# Finite-trace strategy synthesis

One-step shielding answers whether a proposed action is safe now. Strategy synthesis asks
the stronger question: does the agent have a policy that reaches an accepting finite trace
regardless of every future environment response?

## Game model

Aura uses each canonical LTLf residual formula as a game state. Propositions marked
`agent` are controller outputs. Every `environment` or `observed` proposition is treated as
an adversarial input. In each round the controller chooses its valuation and the environment
chooses a response; their union progresses the formula once.

The joint valuation must also be feasible for one canonical Aura event. Aura derives a
conservative event alphabet from proposition selectors and rejects only proven conflicts:
different event kinds, contradictory exact `where` values, disjoint exact tool-name sets,
or identical selectors assigned different truth values. Wildcards and other undecidable
relationships remain feasible. Reports expose total, feasible, and rejected valuation
counts with content-free reason codes.

Aura's event alphabet compiles proven selector conflicts into Z3 and enumerates satisfying
models rather than testing the full agent/environment Cartesian product. The solver then
groups each joint model into its controller choice and permitted environment responses.
An explicit valuation implementation remains available as a reference oracle.

Residuals that accept the empty suffix are final states because the controller may terminate
the finite trace there. Aura computes the least winning fixpoint:

1. Accepting states have rank zero.
2. A state enters the next rank when some agent valuation sends every environment response
   into the existing winning region.
3. Iteration stops when no state can be added.

This is a Moore-style `exists agent, forall environment` game. The report states the turn
semantics explicitly through separate agent and environment valuations.

## Verdicts

| Status | Meaning |
| --- | --- |
| `realizable` | The initial residual is in the adversarial winning region |
| `cooperative_only` | Satisfaction is reachable only with some environment cooperation |
| `unachievable` | No joint valuation sequence reaches an accepting residual |

`cooperative_only` is deliberately not called formal best-effort synthesis. Aura returns a
cooperative strategy candidate, but does not claim the dominance guarantees of best-effort
synthesis research.

For realizable policies, `strategy` contains one controller valuation per ranked winning
residual. For unrealizable policies, `counterstrategy` maps every controller valuation at a
losing state to an environment response that remains losing. These artifacts are
machine-checkable and content-free.

## Contract-bundle compatibility

For two or more LTLf policies, Aura also synthesizes their conjunction from the current
accepted prefix. Two policies may each be realizable while requiring mutually incompatible
actions, so independent success is not sufficient evidence that a deployment can satisfy the
whole contract.

Equivalent event selectors share one synthetic proposition in the joint game. If policies
assign conflicting ownership to that selector, Aura resolves it conservatively as observed
rather than granting the agent control. Reports retain `all_individually_realizable`, expose
the joint strategy and participating policy IDs, and define `all_realizable` using the joint
result.

## Interfaces

Check a policy from its initial state:

```bash
aura strategy-check --policy aura.yaml
```

Check it from a captured accepted prefix:

```bash
aura strategy-check --policy aura.yaml --run run-123 --db .aura/aura.db
```

The read-only MCP tool `aura_strategy_check` accepts the same policy YAML and optional run
ID. Neither interface executes a tool, invokes an agent, calls an LLM, or requires an API
key.

## Bounds

The game is expanded on demand from the current residual. Aura stops with an explicit
`LTLfComplexityError` when the configured state limit is exceeded. Future symbolic and
compositional backends can replace Z3 model enumeration or the explicit progression game
without changing the report contract.

Reports identify both `valuation_backend` and `strategy_backend`, alongside feasible
valuation and reachable-state counts. Run the scaling harness with:

```bash
uv run python benchmarks/strategy_scaling.py
```

The harness emits JSON containing elapsed time and peak traced memory for repeatable local
comparisons. Timing is intentionally excluded from signed runtime reports because it is not
deterministic evidence.
