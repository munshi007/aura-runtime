# Changelog

This project follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and uses
[Semantic Versioning](https://semver.org/).

## Unreleased

### Added

- Project security, contribution, support, threat-model, and architecture-decision policies.
- Explicit `aura_protocol_chain_integrity` MCP tool.

### Deprecated

- `aura_trace_integrity` is retained as a compatibility alias until at least `0.25.0a1`.

## 0.23.0a1 - Unreleased alpha

### Added

- Canonical MCP and OpenTelemetry evidence ingestion with a local OTLP/HTTP receiver.
- Deterministic AuraSpec, bounded temporal, and general LTLf verification.
- MCP stdio observation and enforcement, replay, trace contracts, and causal integrity.
- Object-centric discovery and contracts, shielding, and finite-trace strategy synthesis.
- Privacy-safe CLI, MCP, OCEL, and OTLP evidence interfaces.
