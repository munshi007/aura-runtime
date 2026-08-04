# Zero-code Goose onboarding

Aura can enroll all configured Goose stdio MCP extensions without changing agent or MCP
server code:

```bash
aura connect goose --dry-run
aura connect goose --mode observe
aura doctor goose
```

Aura uses Goose's documented configuration location. Override detection for testing or a
nonstandard installation with `--config /path/to/config.yaml`.

## What connect changes

Given this Goose extension:

```yaml
extensions:
  github:
    type: stdio
    cmd: npx
    args: [-y, "@modelcontextprotocol/server-github"]
```

`aura connect goose` retains the extension's name, enabled state, environment settings,
timeout, and all other fields. It changes only `cmd` and `args` so the process becomes:

```text
Goose -> aura proxy -> original MCP command
```

Built-in and remote extensions are reported but not modified. Remote Streamable HTTP
enrollment requires a different gateway boundary and is intentionally outside this first
connector.

## Safety and reversibility

Before changing `config.yaml`, Aura creates a timestamped byte-for-byte backup beside it.
The YAML round-trip preserves comments and quoting. Enrollment state records only the
original and expected `cmd` and `args` values that Aura owns; it does not duplicate the
extension's environment values.

Repeated `connect` calls are idempotent. `disconnect` restores only the extensions Aura
enrolled. If an enrolled command changed afterward, Aura refuses to overwrite it and
reports the drift. Unrelated changes such as provider, timeout, or enabled-state edits are
preserved:

```bash
aura disconnect goose --dry-run
aura disconnect goose
```

Disconnect creates another backup and archives the enrollment state, so recovery does not
depend on an irreversible deletion.

## Policies and evidence

Observe mode records violations but forwards calls:

```bash
aura connect goose --mode observe --policy ./aura.yaml
```

Enforce mode can deny or require approval before the original MCP server receives a call:

```bash
aura connect goose --mode enforce --policy ./aura.yaml
```

If `--db` is omitted, Aura uses its platform data directory. All enrolled extension
processes inherit their original environment configuration; Aura does not copy provider or
MCP credentials into its enrollment state.

## Observability boundary

This zero-code connector covers the MCP tool plane: manifests, calls, arguments, results,
failures, policy decisions, and transcript integrity. It does not expose Goose's private
model reasoning. OpenTelemetry remains the optional path for model and control-loop spans.
