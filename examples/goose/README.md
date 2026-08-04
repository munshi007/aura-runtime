# Goose integration

This example uses [goose](https://github.com/aaif-goose/goose) as a real external
agent-under-test. Aura is inserted as a transparent stdio MCP boundary between goose and
the deterministic reference support server:

```text
goose -> Aura MCP flight recorder -> reference MCP server
```

Goose is not patched, imported, or vendored. Its recipe launches a normal stdio MCP
extension; `extension.py` places Aura around that server and keeps stdout exclusively for
MCP JSON-RPC traffic.

## Prerequisites

1. Install and configure goose with a model provider by following the
   [official quickstart](https://goose-docs.ai/docs/quickstart/).
2. From the Aura repository root, install Aura's development environment:

   ```bash
   uv sync --extra dev
   ```

No model credential is read or stored by Aura.

## Run the experiments

Capture the known-good workflow and require it to match the committed trace contract:

```bash
uv run python examples/goose/run.py safe
```

Run the same workflow with an additional unapproved deletion. The harness expects Aura's
contract checker to exit `2`, report the new policy finding, and identify the first
behavioral divergence:

```bash
uv run python examples/goose/run.py dangerous
```

Each invocation creates a new timestamped directory below `.aura/goose/` containing the
SQLite evidence database plus JSON and Markdown contract reports. It never overwrites a
previous capture.

The model still introduces nondeterminism: if it ignores the exact recipe instructions,
Aura reports that behavior as a contract divergence. That is part of the experiment, not
a reason to hide or normalize the run.

## Observe versus enforce

The harness uses observe mode so the unsafe call reaches the reference server while Aura
records the violation. To exercise online enforcement manually, set these variables and
run the dangerous recipe:

```bash
export AURA_GOOSE_DB=.aura/goose/enforced.db
export AURA_GOOSE_RUN_ID=goose-enforced
export AURA_GOOSE_MODE=enforce
goose run --recipe examples/goose/dangerous.yaml
uv run aura report goose-enforced --db .aura/goose/enforced.db
```

In enforce mode, Aura returns an MCP error requiring approval and does not forward
`delete_customer` to the upstream server.

## What this proves

Aura observes the framework-independent tool plane: MCP initialization, tool manifests,
tool arguments and results, failures, ordering, policy decisions, and protocol integrity.
It does not claim access to goose's private model reasoning. Model-call and control-loop
telemetry remain an optional OpenTelemetry integration above this boundary.
