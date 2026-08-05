# ADR 0003: Explicit enforcement boundaries

Status: Accepted

## Context

A runtime cannot safely claim to block actions it merely observes after execution.

## Decision

Aura calls behavior "enforcement" only when its MCP stdio proxy decides before forwarding
`tools/call`. OTLP ingestion and evidence APIs are observational. Observe mode always
forwards valid messages and records findings.

## Consequences

The guarantee is narrow and auditable. Other transports and calls that bypass the proxy
remain outside the boundary, while OTLP findings can only gate subsequent workflows.
