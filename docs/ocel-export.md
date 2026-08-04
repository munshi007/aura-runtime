# OCEL 2.0 object-centric export

Traditional trace analysis forces every event into one case or run. Agentic workflows often
touch several independent business objects—such as a customer, ticket, document, payment,
and repository—in the same action. Aura events therefore support qualified object links:

```python
from aura_runtime import AgentEvent, EventKind, ObjectRef

event = AgentEvent(
    run_id="run-123",
    kind=EventKind.TOOL_CALL_REQUESTED,
    tool_name="update_customer",
    objects=[
        ObjectRef(object_type="customer", object_id="cus-7", qualifier="subject"),
        ObjectRef(object_type="ticket", object_id="tic-9", qualifier="source"),
    ],
)
```

For enrolled MCP agents, AuraSpec can extract these links from tool arguments without
application code:

```yaml
object_bindings:
  - on:
      event: tool.call.requested
      tool_matches: [update_customer]
    object_type: customer
    id_path: data.arguments.customer_id
    qualifier: subject
```

The flight recorder applies bindings before storing or verifying the canonical event.
Missing, empty, boolean, or non-scalar IDs do not create object links.

The same object may appear in events from different runs. OCEL keeps that shared identity,
allowing downstream object-centric process mining to analyze the workflow without choosing
one artificial case notion.

## Export

Export selected runs:

```bash
aura export-ocel --db .aura/aura.db \
  --run run-123 --run run-456 \
  --output evidence.jsonocel
```

Omit `--run` to export every captured event in deterministic timestamp order. Aura emits
the four OCEL 2.0 top-level collections: `eventTypes`, `objectTypes`, `events`, and
`objects`. Every event links to its Aura run through a reserved `aura.agent_run` object
type and an `execution` relationship, ensuring that events without business-object
annotations still have an object-centric case.

## Privacy boundary

The default export is structural:

- event and object attribute arrays are empty;
- prompts, tool arguments, results, actors, and span attributes are not exported;
- run IDs and business-object IDs are deterministically pseudonymized with SHA-256;
- object types, event types, and relationship qualifiers remain readable.

Original identifiers require explicit opt-in:

```bash
aura export-ocel --db .aura/aura.db --include-identifiers \
  --output evidence-with-identifiers.jsonocel
```

This flag does not export event `data`; it only preserves run and object identifiers.
Default pseudonymization is not anonymization: it is intentionally deterministic for
cross-run linkage, so low-entropy identifiers may still be susceptible to guessing. Treat
the exported file as controlled evidence.

## Standard mapping

| Aura | OCEL 2.0 JSON |
| --- | --- |
| `AgentEvent.kind` | Event type and event `type` |
| `AgentEvent.timestamp` | Event `time` |
| `AgentEvent.event_id` | Event `id` |
| `ObjectRef.object_type` | Object type |
| `ObjectRef.object_id` | Object `id` (pseudonymized by default) |
| `ObjectRef.qualifier` | Event-to-object relationship qualifier |
| `AgentEvent.run_id` | `aura.agent_run` object with `execution` qualifier |

## References

- [OCEL 2.0 specification](https://www.ocel-standard.org/specification/overview/)
- [OCEL 2.0 JSON format and minimal example](https://www.ocel-standard.org/specification/formats/json/)
- [Official OCEL 2.0 JSON Schema](https://www.ocel-standard.org/2.1/ocel20-schema-json.json)
- [OCEL 2.0 resources paper](https://arxiv.org/abs/2403.01982)
