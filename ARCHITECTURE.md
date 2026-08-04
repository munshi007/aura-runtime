# Aura Runtime architecture

## Product boundary

Aura is a verification runtime, not a general observability backend. Existing telemetry
systems remain the source for performance analysis. Aura consumes their signals, adds
protocol-native events, and preserves enough evidence to replay a run deterministically.

```mermaid
flowchart TD
    A["Agent frameworks"] --> B["MCP and OTLP adapters"]
    B --> C["Canonical evidence log"]
    C --> D["AuraSpec monitor"]
    D --> E["Findings and proofs"]
    C --> F["Replay and process mining"]
    E --> G["CLI, MCP, CI gates"]
    F --> G
```

## Invariants

1. Adapters never define policy semantics; they only normalize evidence.
2. Every finding refers to immutable event IDs.
3. Policy evaluation is deterministic and does not require an LLM.
4. Model-generated explanations may be added later, but never decide compliance.
5. OpenTelemetry is an import/export contract, not Aura's primary event database.

## Modules

| Module | Responsibility |
|---|---|
| `models` | Canonical events, qualified business-object links, and findings |
| `store` | SQLite WAL evidence and finding storage |
| `policy` | AuraSpec schema, selectors, and YAML loading |
| `verifier` | Online past/future temporal monitors and Z3-backed data constraints |
| `adapters.mcp` | MCP JSON-RPC normalization and correlation |
| `adapters.otel` | OTLP/JSON span normalization |
| `flight` | Hash-chained MCP transcript and policy decisions |
| `proxy` | Transparent MCP stdio process boundary |
| `replay` | Read-only policy replay and behavioral comparison |
| `contract` | CI regression verdicts over committed baselines |
| `mcp_server` | Agent-accessible status and finding tools |
| `ocel_export` | Privacy-safe OCEL 2.0 object-centric exchange |
| `cli` | Local ingestion, verification, and reports |

## Near-term design

The alpha evaluates bounded-history prerequisites and bounded future-response obligations
online. Future obligations remain pending while a response is still possible, become
satisfied when a matching event arrives, and become violated at the earliest deadline or
when the trace is explicitly finalized. The interface is intentionally compatible with
compiling richer temporal clauses into finite-state monitors. SQLite is the local default;
a storage protocol will allow Postgres or an immutable object store without changing
verification semantics.

## Flight-recorder decision boundary

`observe` mode records findings but forwards every valid MCP message. `enforce` mode
intercepts a violating `tools/call` request and returns a namespaced JSON-RPC error without
starting the upstream side effect. Policy effects distinguish unconditional denial from
approval-required decisions. The transcript records both the blocked request and the
synthetic response, preserving an auditable explanation of what did not execute.

## Replay identity

Replay does not compare regenerated finding IDs or timestamps. A finding's deterministic
identity is its policy, evidence event, severity, message, and verification engine. Run
comparison similarly removes run IDs, event IDs, timestamps, sequence counters, and trace
identifiers before alignment. Protocol-scoped request, session, and tool-call IDs are also
removed from event payloads. Tool manifests are compared by canonical JSON content.

These normalization rules are deliberately narrow: tool arguments and results remain part
of behavioral identity, so a changed value produces a divergence. Replay only reads the
evidence store and never launches a process, calls a tool, or appends new findings.

## Trace contracts

A trace contract binds an AuraSpec policy, baseline event fixture, baseline MCP manifest,
and allow/deny rules. Candidate evidence remains in its own SQLite store. Contract checking
normalizes execution-scoped identity, evaluates both event streams under the same policy,
and produces one verdict from three independent signals: new findings, event divergence,
and tool-manifest drift.

The contract engine itself is read-only. Scenario execution is a separate CI step through
the flight recorder, which keeps capture and judgment independently testable.
