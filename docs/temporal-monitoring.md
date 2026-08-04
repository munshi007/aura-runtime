# Finite-trace temporal monitoring

Aura monitors agent behavior as a finite but incrementally growing trace. AuraSpec `0.1`
supports two temporal directions:

- `require_prior`: the triggering event requires matching evidence in bounded history.
- `require_after`: the triggering event creates a bounded future obligation.

## Bounded response semantics

```yaml
version: "0.1"
policies:
  - id: destructive-call-completes
    description: A destructive call must complete within three observed events
    severity: critical
    on:
      event: tool.call.requested
      tool_matches: [delete_*]
    require_after:
      event: tool.call.completed
      within_events: 3
      correlate:
        parent_event_id: event_id
```

For a trigger at index `i` and `within_events: N`, matching events at indices `i+1`
through `i+N` satisfy the obligation. The monitor reports:

- `pending` while a satisfying continuation is still possible;
- `satisfied` when a matching future event is observed;
- `violated` at index `i+N` when no match exists, or when the finite trace is finalized.

One response may satisfy multiple overlapping obligations, matching the standard response
property `G(trigger -> F response)`. Every violation references the immutable trigger and
observed evidence event IDs. It does not include prompt, argument, or result content.

`correlate` maps paths on the candidate response to paths on its trigger. In the example,
the completed event must name the triggering request as its parent; an unrelated tool
completion cannot satisfy the obligation. Correlation is optional for genuinely global
signals, but should be used whenever the adapter provides causal identity.

## Prefix versus complete trace

Inspect the current prefix without inventing a failure for work that may still finish:

```bash
aura temporal-state run-123 --db .aura/aura.db --policy aura.yaml
```

Declare a stored prefix complete when it has no explicit `run.completed` event:

```bash
aura temporal-state run-123 --db .aura/aura.db --policy aura.yaml --final
```

The MCP tool `aura_temporal_state` exposes the same content-free report to agents and IDEs.
The live MCP flight recorder advances the same monitor for both client requests and server
responses, so violations become conclusive online rather than only during replay.

## Research boundary

This is a deliberately named bounded-response fragment, not a claim of a general LTLf
compiler. LTLf gives temporal logic a finite-trace interpretation, while runtime
verification additionally needs to distinguish an inconclusive prefix from a conclusive
finite trace. Aura's explicit `pending` state preserves that distinction and provides a
stable monitor interface for later formula-to-automata compilation.

## References

- [Linear Temporal Logic and Linear Dynamic Logic on Finite Traces](https://www.cs.rice.edu/~vardi/papers/ijcai13.pdf)
- [Comparing LTL Semantics for Runtime Verification](https://trustworthy.systems/publications/nicta_full_text/3976.pdf)
- [Introduction to Runtime Verification](https://inria.hal.science/hal-01762297/file/book-chapter-introduction-to-RV.pdf)
