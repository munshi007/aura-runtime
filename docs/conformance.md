# MCP causal conformance

Aura reconstructs a partial causal graph from the hash-chained MCP wire transcript and
checks protocol invariants without calling a model or re-executing a tool:

```bash
aura conformance <run-id> --db .aura/aura.db
```

The JSON report contains normalized message nodes, causal edges, protocol versions,
outstanding requests, transcript integrity, and deterministic issues. An error-level issue
produces a failing verdict and exit code 2; warnings remain visible without failing the run.

## Dual-era protocol normalization

Aura treats MCP `2026-07-28` and later as modern, stateless protocol versions. The version
and client capabilities are read from every request's `params._meta`. Protocol versions
through `2025-11-25` are treated as legacy: Aura learns their version from `initialize` and
applies it to subsequent requests on that transcript.

This preserves evidence from existing agents while making the semantic difference explicit.
Unknown or absent version context is reported rather than silently assigned.

## Causal edges

The first graph relations are deliberately small and protocol-grounded:

- `responds_to`: a response references an outstanding request with the same typed ID in
  the opposite direction.
- `subscription_of`: a notification carries the ID of an open `subscriptions/listen`
  request.

String and integer request IDs remain distinct. Aura does not turn timestamp order into
causality, so concurrent operations are not misrepresented as a single workflow.

## Incremental invariants

The monitor reports violations as soon as the available record makes them conclusive:

- broken sequence, previous-hash, or content-hash integrity;
- malformed JSON-RPC message and response shapes;
- duplicate outstanding request IDs;
- responses without a matching request;
- missing modern per-request client capabilities;
- server-initiated requests under modern MRTR semantics;
- notification references to nonexistent subscriptions.

Open requests are reported as state, not failure: a transcript may have been captured while
an operation or subscription was still active.

## Protocol references

- [MCP 2026-07-28 base protocol](https://modelcontextprotocol.io/specification/2026-07-28/basic)
- [MCP versioning and legacy compatibility](https://modelcontextprotocol.io/specification/2026-07-28/basic/versioning)
- [MCP schema reference](https://modelcontextprotocol.io/specification/2026-07-28/schema)
