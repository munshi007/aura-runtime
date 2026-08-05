# Security policy

Aura Runtime is a research alpha, not a certified security control.

## Supported versions

Security fixes are made on the latest `0.23.x` alpha and `main`. Earlier alpha lines are
unsupported. This policy will be revised when stable releases exist.

## Reporting a vulnerability

Do not open a public issue. Use GitHub's private vulnerability reporting form at
https://github.com/munshi007/aura-runtime/security/advisories/new. Include affected
versions, impact, reproduction steps, and any suggested mitigation. Maintainers will
acknowledge reports when possible, assess severity, and coordinate disclosure; this
community project does not promise a response SLA.

## Deployment boundary

- The OTLP receiver binds to localhost by default and has no built-in TLS or authentication.
- MCP enforcement protects only `tools/call` traffic routed through Aura's stdio proxy.
- OTLP verification is retrospective and cannot undo an action.
- Hash chains detect mutation but do not authenticate the producer.
- A privileged local attacker can alter the SQLite database and runtime.
- Evidence APIs exclude prompt, argument, and result content by default.

See [THREAT_MODEL.md](THREAT_MODEL.md) for the complete model and known gaps.
