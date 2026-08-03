# Aura Runtime

Aura Runtime is an open runtime-verification layer for AI agents. It turns MCP and
OpenTelemetry activity into a canonical evidence log, evaluates temporal and data
policies deterministically, and exposes findings back to developers and agents.

This is deliberately not another trace dashboard. The core question is:

> Did this agent execution conform to the process and safety contract we expected?

## Why Aura exists

Agent teams can usually see spans, latency, tokens, and tool calls. They still struggle
to answer whether a tool call was legal *at that point in the process*, what prior
evidence justified it, and whether the run can be replayed against a new policy.

Aura's first vertical slice provides:

- an append-only, SQLite-backed canonical agent event log;
- MCP JSON-RPC and OTLP/JSON adapters;
- declarative `AuraSpec` policies with temporal prerequisites;
- Z3-backed constraints over tool arguments and state;
- deterministic findings with evidence event IDs;
- a Typer CLI and an MCP server for querying the runtime.
- a transparent MCP stdio flight recorder with optional enforcement.

## Quick start

```bash
uv sync --extra dev
uv run aura init
uv run aura check examples/events.jsonl --policy examples/policy.yaml
uv run aura report demo-run
```

Wrap any stdio MCP server in observe-only mode:

```bash
uv run aura proxy --policy examples/policy.yaml --mode observe -- \
  uv run mcp run path/to/upstream_server.py
```

Enable deterministic blocking only after reviewing the recorded behavior:

```bash
uv run aura proxy --policy examples/policy.yaml --mode enforce -- \
  uv run mcp run path/to/upstream_server.py
```

The proxy writes no logs to stdout: that stream remains valid newline-delimited MCP
JSON-RPC. Every request, response, forwarding decision, and tool-manifest snapshot is
stored in SQLite. Transcript records form a SHA-256 hash chain so tampering is detectable.

Replay historical evidence against a changed policy without invoking an upstream server:

```bash
uv run aura replay demo-run --policy examples/policy-strict.yaml
uv run aura replay demo-run --policy examples/policy-strict.yaml --fail-on-new
uv run aura diff baseline-run candidate-run
uv run aura manifests diff baseline-run candidate-run
```

Replay reports separate introduced, resolved, and unchanged findings. Run diffs identify
the common prefix and first divergent event while ignoring timestamps, generated IDs, and
run IDs. Manifest diffs detect added, removed, and schema-changed tools.

Run the MCP server with the official MCP SDK CLI:

```bash
uv run mcp run src/aura_runtime/mcp_server.py
```

Run validation:

```bash
uv run ruff check .
uv run pytest
```

## AuraSpec example

```yaml
version: "0.1"
policies:
  - id: destructive-tools-require-approval
    description: A destructive tool call must be preceded by explicit approval.
    severity: critical
    on:
      event: tool.call.requested
      tool_matches: ["delete_*", "drop_*"]
    require_prior:
      event: human.approval
      within_events: 20
      where:
        data.approved: true
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for the system boundary and
[docs/research-direction.md](docs/research-direction.md) for the research thesis.

## Status

`0.3.0a1` is a research-grade foundation. The next milestones are online LTLf monitors,
object-centric process discovery, causal conformance checking, and OCEL 2.0 export.
