# ADR 0002: Content-free evidence by default

Status: Accepted

## Context

Prompts, tool arguments, and results routinely contain secrets and personal information;
most verification needs structure rather than content.

## Decision

Normal OTLP import discards content and MCP evidence tools return structural metadata,
hashes, pseudonyms, evidence IDs, and verdicts. Content export requires an explicit,
separate opt-in.

## Consequences

The default blast radius is smaller and reports are safer to share. Some content-dependent
policies cannot be evaluated unless the deployer intentionally retains the required fields.
