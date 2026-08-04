# Research direction

Aura's thesis is that agent observability should evolve from viewing traces to checking
executions against executable behavioral contracts.

## Differentiating research layers

1. **Temporal runtime verification** — compile bounded and LTLf-style contracts into
   monitors that emit violations at the earliest conclusive event.
2. **Object-centric process mining** — discover workflows around shared objects such as
   a customer, ticket, invoice, or repository rather than forcing one flat trace.
3. **Conformance checking** — compare observed executions to discovered or declared
   process models and quantify deviations.
4. **Constraint proofs** — use SMT solving for cross-field business invariants and retain
   a machine-checkable explanation of satisfiability or contradiction.
5. **Counterfactual replay** — rerun captured evidence under a changed policy or tool
   manifest without re-executing destructive effects.
6. **Partial-order causality** — preserve trace ancestry and object links so concurrent
   actions are not misrepresented as one arbitrary total order.

The current alpha implements the evidence boundary, bounded temporal prerequisites,
SMT-backed concrete constraints, and a first partial-order causal graph for MCP requests,
responses, and subscriptions. Richer object links and discovered concurrency relations
remain explicit extension milestones, not claims of completed functionality.
