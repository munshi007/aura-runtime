# OTLP conformance export

## Import GenAI traces

Aura also consumes an OTLP/HTTP JSON `ExportTraceServiceRequest` produced by agent
framework instrumentation:

```bash
aura ingest-otlp traces.json --db .aura/aura.db
```

The importer reconstructs two boundary events from each completed duration span:

| `gen_ai.operation.name` | Start event | End event |
| --- | --- | --- |
| `invoke_agent` | `run.started` | `run.completed` |
| `execute_tool` | `tool.call.requested` | `tool.call.completed` or `tool.call.failed` |
| `chat`, `generate_content`, `text_completion` | `model.requested` | `model.completed` |

Event IDs are derived from the trace ID, span ID, and boundary, so importing the same
payload repeatedly produces stable evidence identities. Sequences are assigned per run,
not globally across a batch. Unrecognized spans remain `unknown` evidence instead of
being guessed into a lifecycle.

Import is retrospective observability: it can verify recorded behavior but cannot block
an action that already happened. Use the MCP proxy for pre-execution enforcement.

Aura can export a captured MCP transcript as an OTLP/HTTP JSON trace payload:

```bash
aura export-otlp <run-id> --db .aura/aura.db --output traces.json
```

The payload can be sent to an OpenTelemetry Collector or compatible backend at its
`/v1/traces` endpoint with `Content-Type: application/json`.

## Mapping

Completed MCP requests become duration spans. `tools/call` uses the OpenTelemetry GenAI
`execute_tool` convention with `gen_ai.operation.name`, `gen_ai.tool.name`,
`gen_ai.tool.call.id`, and `gen_ai.tool.type`. MCP method and protocol-version attributes
remain attached, while Aura adds namespaced evidence hashes, enforcement decisions,
conformance verdicts, and issue events.

Subscription notifications link to their causal `subscriptions/listen` span. Aura derives
stable trace and span identifiers from the run and hash-chained evidence, making repeated
exports deterministic without pretending that the Aura run ID is an upstream conversation
ID.

## Privacy boundary

Tool arguments and results are omitted by default. This follows their opt-in status in the
GenAI semantic conventions and prevents secrets or personal data from silently entering a
telemetry backend. Operators can explicitly include them when appropriate:

```bash
aura export-otlp <run-id> --include-content --output traces.json
```

Review the destination's retention, access, and redaction controls before enabling this
option.

Import applies an allowlist independently of the exporter. Operational identity,
correlation, model, provider, tool, token-count, server, and error attributes are kept.
Content-bearing attributes—including `gen_ai.input.messages`, `gen_ai.output.messages`,
`gen_ai.tool.call.arguments`, and `gen_ai.tool.call.result`—are discarded. Their names
are recorded as redaction evidence, but their values never enter an `AgentEvent`.

## References

- [OTLP specification](https://opentelemetry.io/docs/specs/otlp/)
- [OpenTelemetry GenAI execute-tool span](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/reference/reports/execute-tool-span.md)
- [OpenTelemetry GenAI semantic conventions](https://github.com/open-telemetry/semantic-conventions-genai)
