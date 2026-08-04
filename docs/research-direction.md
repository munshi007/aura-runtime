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

The current alpha implements the evidence boundary, qualified event-to-object links,
privacy-safe OCEL 2.0 exchange, bounded temporal prerequisites, three-valued bounded
future-response monitors, and a general LTLf frontend with exact progression semantics and
four-valued prefix verdicts. It also implements SMT-backed concrete constraints and a first partial-order causal
graph for MCP requests, responses, and subscriptions. It also discovers content-free
object-type lifecycle and interaction profiles and checks exact structural drift between
trusted and candidate cohorts. Trusted profiles can be compiled into content-addressed
contracts that gate proposed MCP actions transactionally without trusting tool annotations.
The anticipatory shield partitions propositions by control ownership, keeps environment
facts fixed while calculating feasible repairs, and reports when no configured runtime
action can enforce a monitorable formula.
The strategy checker constructs the reachable progression automaton on demand and solves
its finite reachability game by least fixpoint. It separates adversarial realizability from
mere cooperative reachability and produces machine-readable controller or environment
counterstrategies.
Strategy and shielding now share a conservative event-alphabet theory, preventing the
solver from winning through Boolean valuations that no single canonical event can produce.
The feasibility predicate is an injected boundary intended for later symbolic-automata
theories rather than a hard-coded dependency of the LTLf parser.
It does not yet claim LDLf, symbolic automata for high-dimensional proposition sets,
object-centric Petri-net discovery, statistical drift thresholds, digital authorship
signatures, or inferred concurrency. Those remain explicit extension milestones.
