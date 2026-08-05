# MCP evidence API

Aura's MCP server makes runtime-verification evidence queryable by agents, IDEs, and other
MCP clients. It is an inspection plane, not another agent framework: callers receive
deterministic verdicts and causal structure produced from the local evidence store.

## Start the server

```bash
AURA_DB_PATH=.aura/aura.db uv run mcp run src/aura_runtime/mcp_server.py
```

`AURA_DB_PATH` selects the database used by MCP resources. Tools also accept an explicit
`db_path`, which is useful for local development and CI evidence artifacts.

## Evidence primitives

| Primitive | Purpose | Content policy |
| --- | --- | --- |
| `aura_status` | List bounded summaries for every evidence-bearing run | Counts, verdicts, versions, and run IDs only |
| `aura_conformance` | Return the deterministic protocol report and causal graph | Message shape metadata and content hashes; no wire messages |
| `aura_explain_issue` | Return an issue plus its causal neighborhood, bounded to 0-5 hops | Same content-free node model |
| `aura_temporal_state` | Inspect bounded-response obligations | Verdicts and evidence IDs only |
| `aura_ltlf_state` | Inspect exact LTLf prefix or finalized state | Formula residuals, verdicts, and evidence IDs only |
| `aura_shield_action` | Preview an event and enumerate nearest safe valuations | Proposition names and verdicts; no payloads |
| `aura_strategy_check` | Solve controller/environment games for LTLf policies | Winning regions, strategies, and counterstrategies |
| `aura_protocol_chain_integrity` | Verify the MCP flight-recorder hash chain | Counts, validity, and the chain head hash only |
| `aura_object_behavior` | Discover aggregate object lifecycles for run cohorts | Object types, activities, and counts; no object IDs |
| `aura_object_conformance` | Compare trusted and candidate object behavior | Structural additions and removals only |
| `aura_object_contract` | Validate a content-addressed object contract | Contract metadata and hash only |
| `aura_object_state` | Replay pseudonymous lifecycle state and counterexamples | Contract-scoped object pseudonyms; no raw IDs or payloads |
| `aura://runs/{run_id}/conformance` | Application-controlled resource for a full conformance report | Same content-free conformance model |

All inspection tools declare MCP read-only, idempotent, closed-world annotations. Results
are structured so clients can validate and reason over them without parsing prose.
Aura opens evidence databases in SQLite read-only mode, so inspection does not create a
database, journal, or evidence record.

`aura_trace_integrity` remains available as a deprecated compatibility alias for
`aura_protocol_chain_integrity`. New integrations should use the explicit name; the alias
will not be removed before `0.25.0a1`.

## Privacy boundary

These evidence APIs never return captured prompts, MCP `arguments`, tool results, or raw
event `data`. `content_included: false` is explicit on run summaries and issue
neighborhoods. Causal nodes expose only protocol metadata, forwarding decisions, sequence
numbers, and content hashes.

Object state uses deterministic, contract-scoped SHA-256 pseudonyms. This supports stable
correlation within one contract but is pseudonymization, not anonymity. Tool and activity
names remain structural metadata and can be sensitive in some deployments.

The separate `aura export-otlp --include-content` command remains the explicit opt-in path
for exporting tool arguments and results. There is intentionally no equivalent content
flag on the MCP evidence API.

## Why tools and a resource

MCP tools are model-controlled, so they provide bounded discovery and targeted issue
explanations. The resource is application-controlled and provides a stable URI for a
selected run's complete content-free report. This division keeps large evidence out of a
model's context until the client or user chooses it.
