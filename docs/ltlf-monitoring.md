# General LTLf runtime monitoring

Aura can monitor finite-trace temporal logic (LTLf) directly over canonical agent events.
This adds general temporal composition without turning Aura into an agent framework or
requiring application code to instantiate a monitor.

## Policy model

Each atomic proposition is a named Aura event selector. The formula composes those names:

```yaml
version: "0.1"
ltlf_policies:
  - id: approved-before-delete
    description: No delete occurs until approval
    severity: critical
    effect: deny
    formula: "(!delete) U approval"
    propositions:
      delete:
        event: tool.call.requested
        tool_matches: [delete_*]
      approval:
        event: human.approval
        where:
          data.approved: true
```

Supported syntax is `!`, `&`, `|`, `->`, `<->`, strong next `X`, weak next `Xw`,
eventually `F`, always `G`, until `U`, and release `R`. Word forms `not`, `and`, and `or`
are also accepted.

## Prefix semantics

Aura progresses each formula after every event and classifies the residual state exactly:

| Verdict | Meaning |
| --- | --- |
| `permanently_satisfied` | Every possible finite continuation satisfies the formula |
| `currently_satisfied` | The current prefix passes if ended now, but a continuation can violate it |
| `currently_violated` | The current prefix fails if ended now, but a continuation can repair it |
| `permanently_violated` | No possible finite continuation can satisfy the formula |

An online finding is emitted only for `permanently_violated`. Finalization turns the
current finite trace into an ordinary `pass` or `fail`, emitting a finding for a failed
obligation. This distinction prevents an unresolved `F approval` from being reported as a
violation before the trace actually ends.

Strong and weak next differ at the boundary: `X p` requires another event, while `Xw p`
is satisfied when no next event exists.

## Use it

The MCP flight recorder evaluates LTLf policies automatically. In enforce mode, a formula
that becomes permanently violated can deny or require approval before the intercepted tool
call reaches the upstream server.

Inspect stored evidence from the CLI:

```bash
aura ltlf-state <run-id> --policy aura.yaml
aura ltlf-state <run-id> --policy aura.yaml --final
```

MCP clients can call the read-only `aura_ltlf_state` tool with the run ID and policy YAML.
Both surfaces return content-free monitor state; they do not return tool arguments or
results.

## Exactness and bounds

The implementation explores reachable residual formulas over all proposition valuations.
It refuses formulas beyond the configured atom or state bounds instead of silently using
an approximation. This is intentionally an explicit research boundary: future work can
replace enumeration with BDD-backed symbolic transitions while retaining the same verdict
contract.
