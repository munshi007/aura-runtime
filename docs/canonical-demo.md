# Canonical end-to-end demo

The reference support agent demonstrates Aura's complete current product boundary with no
LLM, cloud service, or API key:

```bash
uv run python examples/reference_agent/demo.py --output .aura-demo
```

The runner starts a real MCP server behind Aura's stdio proxy, captures a tool manifest and
wire transcript, creates canonical events and findings, and checks both runs against the
same committed trace contract. The safe control must pass. The dangerous control adds an
unapproved destructive call and must fail with contract-check exit code `2`.

The output directory contains a SQLite evidence database plus JSON and Markdown reports for
each control, and `summary.json` records whether both expected verdicts were observed. It is
safe to upload the whole directory as a CI artifact: the contract reports are content-free,
although the local SQLite flight recorder retains the test tool payloads.

This demo proves deterministic capture and verification, not model quality. A real agent or
LLM can replace the scenario driver without changing the Aura boundary.
