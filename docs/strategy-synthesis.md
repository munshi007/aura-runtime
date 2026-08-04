# Finite-trace strategy synthesis

One-step shielding answers whether a proposed action is safe now. Strategy synthesis asks
the stronger question: does the agent have a policy that reaches an accepting finite trace
regardless of every future environment response?

## Game model

Aura uses each canonical LTLf residual formula as a game state. Propositions marked
`agent` are controller outputs. Every `environment` or `observed` proposition is treated as
an adversarial input. In each round the controller chooses its valuation and the environment
chooses a response; their union progresses the formula once.

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
compositional backends can replace explicit valuation enumeration without changing the
report contract.
