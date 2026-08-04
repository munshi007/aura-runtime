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

Both tools are read-only. Their responses contain object types, activity/tool names, and
aggregate counts. They never include object identifiers, event payloads, arguments, or
results. Tool names are structural metadata and may themselves be sensitive in some
environments, so access to the MCP server should still be controlled.

## Determinism and limits

Events are ordered by timestamp, run ID, sequence, and event ID. Run boundaries terminate
observed lifecycles, so aggregation never invents a transition between separate executions.
Duplicate references to the same object on one event are collapsed. An activity shared by
two object types creates an interaction edge for that type pair.

The profile is intentionally an interpretable directly-follows abstraction, not a claim of
full object-centric Petri-net discovery or arbitrary concurrency inference. Missing paths
can also reflect incomplete baseline coverage; teams should build trusted baselines from
representative runs.
