# Contributing

## Development

```bash
uv sync --extra dev
uv run ruff check .
uv run pytest
uv build
```

Use a focused branch and PR. Add tests for behavior changes and update the relevant docs.
Keep verdicts deterministic: an LLM may explain a result but must never decide compliance.
Preserve the content-free evidence boundary and avoid fixtures containing real credentials,
prompts, tool arguments, or results.

PRs should explain the trust boundary affected, compatibility impact, tests performed, and
any known limitation. Do not disclose vulnerabilities in issues or PRs; follow
[SECURITY.md](SECURITY.md).
