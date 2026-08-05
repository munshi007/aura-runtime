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
- exact LTLf progression monitors with four-valued prefix verdicts;
- event-feasible LTLf shielding and finite-trace strategy synthesis;
- joint synthesis that detects incompatible policy bundles;
- belief-state synthesis for agents operating with hidden environment facts;
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

Run a real external agent through the same boundary using the bundled
[Goose integration](examples/goose/README.md):

```bash
uv run python examples/goose/run.py safe
uv run python examples/goose/run.py dangerous
```

The Goose recipes exercise a known-good tool sequence and an unapproved destructive
regression without modifying or vendoring Goose. Aura retains the MCP evidence and checks
both runs against the committed trace contract.

Enroll an existing Goose installation without editing its agent or MCP server code:

```bash
aura connect goose --dry-run
aura connect goose --mode observe --policy ./aura.yaml
aura doctor goose
```

Aura backs up Goose's YAML configuration, wraps only stdio extensions, preserves their
environment and operational settings, and can restore them with `aura disconnect goose`.
See [zero-code Goose onboarding](docs/goose-onboarding.md) for the safety model and exact
observability boundary.

Reconstruct the MCP causal graph and check dual-era protocol invariants from captured wire
evidence:

```bash
aura conformance <run-id> --db .aura/aura.db
```

The incremental monitor supports legacy initialization-based MCP and modern per-request
metadata without collapsing concurrent messages into timestamp order. See
[MCP causal conformance](docs/conformance.md).

Export the same evidence into an existing OpenTelemetry pipeline without exporting tool
content by default:

```bash
aura export-otlp <run-id> --db .aura/aura.db --output traces.json
```

See [OTLP conformance export](docs/otlp-export.md) for the semantic mapping and explicit
content opt-in.

Import standard OpenTelemetry GenAI agent traces with no framework-specific code:

```bash
aura ingest-otlp traces.json --db .aura/aura.db
```

`invoke_agent`, `execute_tool`, and model-operation spans become Aura lifecycle events.
Prompt messages, tool arguments, and tool results are discarded during import; no LLM or
API key is required.

Replay reports separate introduced, resolved, and unchanged findings. Run diffs identify
the common prefix and first divergent event while ignoring timestamps, generated IDs, and
run IDs. Manifest diffs detect added, removed, and schema-changed tools.

## Trace Contract CI

Commit a known-good behavioral baseline and check every candidate agent run in CI:

```bash
uv run aura contract check examples/reference_agent/aura-contract.yaml \
  --db candidate.db \
  --candidate-run candidate \
  --json-output aura-report.json \
  --markdown-output aura-report.md
```

The command exits `2` when contract rules reject new findings, behavioral divergence, or
tool-manifest drift. The bundled reference agent is exercised through the real MCP stdio
proxy in GitHub Actions; its JSON report, Markdown summary, and evidence database are
uploaded as workflow artifacts. No model or API key is required.

Run the MCP server with the official MCP SDK CLI:

```bash
uv run mcp run src/aura_runtime/mcp_server.py
```

An agent or IDE can then call the `aura_*` inspection tools to inspect protocol,
temporal, and object-centric evidence without receiving prompts, tool arguments, or tool
results. Set `AURA_DB_PATH` to select the store used by the
`aura://runs/{run_id}/conformance` resource. See [MCP evidence API](docs/mcp-evidence-api.md)
for the trust boundary and response shapes.

AuraSpec can also express bounded future obligations. The online monitor distinguishes a
still-possible `pending` prefix from a conclusive `satisfied` or `violated` obligation:

```yaml
on:
  event: tool.call.requested
  tool_matches: [delete_*]
require_after:
  event: human.approval
  within_events: 3
  where:
    data.approved: true
```

Inspect a captured prefix with `aura temporal-state <run-id> --policy aura.yaml`; add
`--final` only when the prefix should be interpreted as a complete finite trace. See
[finite-trace temporal monitoring](docs/temporal-monitoring.md).

For general finite-trace properties, bind named propositions to the same event selectors:

```yaml
ltlf_policies:
  - id: no-unapproved-delete
    description: Deletion never occurs before approval
    formula: "(!delete) U approval"
    propositions:
      delete:
        event: tool.call.requested
        tool_matches: [delete_*]
      approval:
        event: human.approval
        where:
          data.approved: true
    proposition_control:
      delete: agent
      approval: environment
```

Aura supports Boolean operators plus strong/weak next (`X`, `Xw`), eventually (`F`),
always (`G`), until (`U`), and release (`R`). Inspect a prefix with
`aura ltlf-state <run-id> --policy aura.yaml`; use `--final` to obtain the finite-trace
pass/fail verdict. See [general LTLf monitoring](docs/ltlf-monitoring.md).

Canonical events can link actions to qualified business objects such as customers,
documents, tickets, or repositories. Export one or many runs as an OCEL 2.0 object-centric
event log without exporting event payloads or raw identifiers by default:

```bash
aura export-ocel --db .aura/aura.db --run run-1 --run run-2 \
  --output agent-evidence.jsonocel
```

See [OCEL 2.0 export](docs/ocel-export.md) for object annotation and privacy semantics.

Discover aggregate object lifecycles and fail CI on structural drift without exporting raw
object identifiers or event content:

```bash
aura objects discover --run trusted-1 --run trusted-2 --output baseline.json
aura objects compare --baseline-run trusted-1 --candidate-run candidate-1 \
  --output object-drift.json
```

The MCP tools `aura_object_behavior` and `aura_object_conformance` expose the same read-only,
content-free analysis to agents and IDEs. See
[object-centric behavior discovery](docs/object-centric-discovery.md).

Compile representative behavior into a content-addressed contract and enforce it before
MCP tool calls reach the upstream server:

```bash
aura objects contract create --baseline-run trusted-1 \
  --output aura-object-contract.json
aura proxy --policy aura.yaml --object-contract aura-object-contract.json \
  --mode enforce -- your-mcp-server
```

Blocked attempts remain in the evidence log but cannot advance the accepted lifecycle
or LTLf state. Object identifiers stay pseudonymous in reports. Unsafe LTLf proposals
include deterministic nearest-safe proposition valuations in the MCP error response; no
LLM, model API, or API key participates in the verdict.

Check whether a policy is realizable against every environment behavior before deploying
it:

```bash
aura strategy-check --policy aura.yaml
aura strategy-check --policy aura.yaml --run captured-run
```

Aura constructs the reachable residual-formula game, computes the exact winning region,
and returns either a controller strategy or an adversarial counterstrategy. See
[finite-trace strategy synthesis](docs/strategy-synthesis.md).

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

`0.20.0a1` is a research-grade foundation. The next milestones are signed evidence
bundles, symbolic proposition handling for larger formulas, and richer conformance beyond
directly-follows structure.
