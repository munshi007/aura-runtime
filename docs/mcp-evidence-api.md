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
| `aura://runs/{run_id}/conformance` | Application-controlled resource for a full conformance report | Same content-free conformance model |

All inspection tools declare MCP read-only, idempotent, closed-world annotations. Results
are structured so clients can validate and reason over them without parsing prose.
Aura opens evidence databases in SQLite read-only mode, so inspection does not create a
database, journal, or evidence record.

## Privacy boundary

These evidence APIs never return captured prompts, MCP `arguments`, tool results, or raw
event `data`. `content_included: false` is explicit on run summaries and issue
neighborhoods. Causal nodes expose only protocol metadata, forwarding decisions, sequence
numbers, and content hashes.

The separate `aura export-otlp --include-content` command remains the explicit opt-in path
for exporting tool arguments and results. There is intentionally no equivalent content
flag on the MCP evidence API.

## Why tools and a resource

MCP tools are model-controlled, so they provide bounded discovery and targeted issue
explanations. The resource is application-controlled and provides a stable URI for a
selected run's complete content-free report. This division keeps large evidence out of a
model's context until the client or user chooses it.
