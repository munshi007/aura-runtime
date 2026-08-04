# Object-centric behavior discovery

Aura can discover an aggregate behavioral model from canonical events that carry
qualified business-object references. The model is an object-centric directly-follows
profile: it records activities, starts, ends, and transitions for every object type, plus
activities where two object types interact.

This avoids forcing agent evidence into one global sequence or one artificial case ID.
For example, the same tool call can advance both a `ticket` and a `customer` lifecycle.

## Discover a profile

```bash
aura objects discover --run trusted-1 --run trusted-2 \
  --db .aura/aura.db --output baseline.json
```

Each repeated `--run` contributes to one aggregate cohort. Activities use the canonical
event kind and tool name, such as `tool.call.requested:update_customer`.

## Compare trusted and candidate behavior

```bash
aura objects compare \
  --baseline-run trusted-1 --baseline-run trusted-2 \
  --candidate-run candidate-1 \
  --db .aura/aura.db --output drift.json
```

The command exits `2` if an object type, activity, transition, or cross-object interaction
was added or removed. Frequency counts remain visible but do not fail conformance, because
small cohorts make frequency thresholds unstable. A later statistical layer can operate on
the same deterministic evidence without changing the structural contract.

## MCP tools

- `aura_object_behavior` discovers a profile for a list of run IDs.
- `aura_object_conformance` compares baseline and candidate run cohorts.
- `aura_object_contract` validates a content-addressed contract.
- `aura_object_state` replays a run into pseudonymous lifecycle state.

Both tools are read-only. Their responses contain object types, activity/tool names, and
aggregate counts. They never include object identifiers, event payloads, arguments, or
results. Tool names are structural metadata and may themselves be sensitive in some
environments, so access to the MCP server should still be controlled.

## Compile and enforce a contract

Turn representative trusted runs into a versioned structural contract:

```bash
aura objects contract create \
  --baseline-run trusted-1 --baseline-run trusted-2 \
  --db .aura/aura.db --output aura-object-contract.json
```

The contract contains allowed starts, ends, directly-following activities, and typed
cross-object interactions. Its SHA-256 `contract_hash` covers the canonical contract and
its enforcement effect. This detects accidental or malicious content changes but is not a
digital signature and does not establish who authored the contract.

Object references must be extracted at the trusted MCP boundary. Define the bindings in
the AuraSpec used for both baseline capture and enforcement:

```yaml
object_bindings:
  - on:
      event: tool.call.requested
      tool_matches: [read_ticket, notify_customer, close_ticket]
    object_type: ticket
    id_path: data.arguments.ticket_id
    qualifier: subject
```

Start in observe mode and inspect violations before enabling blocking:

```bash
aura proxy --policy aura.yaml \
  --object-contract aura-object-contract.json --mode observe -- your-mcp-server

aura proxy --policy aura.yaml \
  --object-contract aura-object-contract.json --mode enforce -- your-mcp-server
```

In enforce mode, Aura assesses the proposed event before forwarding it. A rejected request
is retained as attempted evidence but does not advance accepted object state. In observe
mode it is forwarded and therefore does advance state, while the violation remains stored.
If a binding selector matches but its identifier path is absent or invalid, enforcement
fails closed with a `missing_object_binding` counterexample.

Completed runs can be checked offline; prefixes can be inspected without declaring an end:

```bash
aura objects contract check aura-object-contract.json --run candidate-1
aura objects state candidate-1 --contract aura-object-contract.json
```

## Determinism and limits

Events are ordered by timestamp, run ID, sequence, and event ID. Run boundaries terminate
observed lifecycles, so aggregation never invents a transition between separate executions.
Duplicate references to the same object on one event are collapsed. An activity shared by
two object types creates an interaction edge for that type pair.

The profile is intentionally an interpretable directly-follows abstraction, not a claim of
full object-centric Petri-net discovery or arbitrary concurrency inference. Missing paths
can also reflect incomplete baseline coverage; teams should build trusted baselines from
representative runs. Structural contracts reject unseen behavior exactly; they do not infer
that a small baseline is complete.
