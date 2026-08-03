# Trace Contract CI

Trace contracts are behavioral snapshot tests for agents. They combine a known-good event
sequence, the MCP tool manifest visible during that run, and an AuraSpec policy.

```yaml
version: "0.1"
name: reference-support-agent
policy: policy.yaml
baseline:
  events: baseline/events.jsonl
  manifest: baseline/tools.json
rules:
  new_findings: deny
  behavioral_divergence: deny
  tool_manifest_drift: deny
```

Paths are resolved relative to the contract file, making the contract portable in a
repository. Candidate evidence is addressed by run ID in an Aura SQLite database.

## Signals

- **New findings** catch actions that violate the common policy only in the candidate.
- **Behavioral divergence** catches changed tool order, arguments, and results.
- **Tool-manifest drift** catches added, removed, or changed MCP tool definitions.

Each signal can be allowed or denied independently. A denied signal produces verdict
`fail` and CLI exit code `2`. Configuration or missing-evidence errors remain ordinary
command failures, so CI can distinguish regression from infrastructure problems.

## Reference scenario

`examples/reference_agent` contains a deterministic MCP customer-support server. The safe
scenario searches a customer and stages a refund. The dangerous negative control adds an
unapproved deletion. GitHub Actions verifies that the safe scenario passes and that the
dangerous scenario fails at the first new event with a critical policy finding.

The SDK client launches Aura's stdio proxy, and Aura launches the server. This intentionally
tests the same subprocess and newline-delimited JSON-RPC boundary used by real MCP hosts.
