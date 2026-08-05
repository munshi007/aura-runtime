# Trace integrity

Policy verification is only as trustworthy as its evidence. A normal trace viewer can
render disconnected or malformed spans without explaining that conclusions drawn from
the trace may be incomplete. Aura checks that boundary explicitly:

```bash
aura check-otlp traces.json
```

The command emits a content-free JSON report and exits `2` unless
`verification_ready` is true.

## Verdicts

| Verdict | Meaning |
| --- | --- |
| `pass` | The batch is a causally closed, single-root trace forest with valid identities. |
| `inconclusive` | Evidence may be partial or affected by clock skew; absence-based claims are unsafe. |
| `fail` | The batch contains structurally invalid evidence. |

Checks include W3C-compatible non-zero trace and span identities, duplicate span
identities, self-parenting, missing parents, root count, negative duration, and child
timing outside the parent interval. Missing parents and timing anomalies are warnings:
batching, sampling, and distributed clock skew can produce them without proving that the
agent itself misbehaved. They still prevent a strong verification-ready claim.

The report records only identifiers, reason codes, and structural facts. It does not copy
prompts, messages, tool arguments, or results.

## References

- [OpenTelemetry tracing API](https://opentelemetry.io/docs/specs/otel/trace/api/)
- [W3C Trace Context](https://www.w3.org/TR/trace-context/)
