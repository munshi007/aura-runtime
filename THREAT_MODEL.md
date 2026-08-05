# Aura Runtime threat model

## Scope and assets

This model covers Aura's MCP stdio proxy, OTLP/HTTP JSON receiver, adapters, SQLite evidence
store, deterministic monitors, CLI, and read-only MCP evidence API. Protected assets are
agent-side effects, policy integrity, evidence confidentiality and integrity, verdict
correctness, service availability, and stable correlation identifiers.

The agent, upstream MCP server, OTLP exporter, policy author, local host, dependencies, and
evidence consumer cross distinct trust boundaries. Inputs from all of them are untrusted.
A privileged host administrator is outside the protection boundary.

## Security invariants

1. An LLM never determines a compliance verdict.
2. Enforcement blocks before forwarding an in-scope MCP tool call.
3. Every finding identifies immutable evidence events.
4. Inspection APIs do not disclose prompts, arguments, or results by default.
5. Missing or causally incomplete evidence cannot be reported as verified.
6. Hash-chain validity means internally consistent ordering, not producer authenticity.

## Threats and current controls

| Threat | Current control | Residual risk |
| --- | --- | --- |
| Malformed or adversarial MCP JSON | SDK parsing, schema checks, namespaced errors | Parser and dependency defects |
| Forged, missing, duplicate, or reordered OTLP spans | Bounds, idempotency, causal closure, single-root and clock checks | A conforming malicious producer can fabricate a trace |
| Prompt or tool-content disclosure | Content is discarded on normal OTLP import and excluded from evidence APIs | Structural names and opted-in exports can be sensitive |
| Malicious or computationally explosive policy | Pydantic validation and explicit solver/state limits | Resource exhaustion within configured bounds |
| Enforcement bypass | Explicit stdio proxy boundary and observe/enforce modes | Calls made through another transport are not blocked |
| Receiver denial of service | Localhost default and bounded HTTP bodies | No authentication, rate limiting, or tenant isolation |
| Replay or exporter redelivery | Deterministic import identity and idempotent receiver behavior | Distinct fabricated identifiers evade deduplication |
| SQLite or transcript tampering | WAL storage and per-run SHA-256 transcript chains | Privileged local users can replace data and recompute chains |
| Upstream crash or partial response | Requests, forwarding decisions, and synthetic blocks are recorded | Abrupt process termination can leave incomplete evidence |
| Dependency or build compromise | Locked dependencies and CI checks | Releases are not yet signed or reproducibly attested |

## Failure behavior

| Condition | Behavior |
| --- | --- |
| Policy violation in `observe` mode | Record and forward |
| Policy violation in `enforce` mode | Record and block before stdio forwarding |
| Invalid policy or MCP message | Reject; do not invent a compliance verdict |
| Missing or inconsistent OTLP evidence | `inconclusive` or `fail`, never verification-ready |
| Unsupported/ambiguous solver state | Refuse an exact positive verdict |
| Evidence-store failure | Surface the error; durable evidence is not claimed |

OTLP checks are retrospective. They can gate a later deployment or workflow but cannot
block the action represented by a span that has already arrived.

## Known gaps and non-goals

Aura currently provides no TLS, authentication, authorization, multi-tenant isolation,
remote key management, signed evidence bundles, sandbox for upstream MCP servers, or
protection from a compromised host. It does not validate the truth of business facts and
does not replace endpoint security, an observability backend, or human approval controls.

Review this model whenever a network listener, storage backend, enforcement transport,
evidence signing mechanism, content-bearing API, or trust-bearing integration is added.
