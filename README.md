# Task Bundle CLI

Task Bundle CLI safely, reproducibly, and observably evaluates coding-agent
solutions against hidden benchmark tests across arbitrary coding repositories.

The implementation follows the canonical
[technical design](docs/technical-design.md).

The completed secure bundle core is documented in
[Phase 1: Secure Bundle Core](docs/phase-1-secure-bundle-core.md).

## Development

Requires Python 3.12 and [`uv`](https://docs.astral.sh/uv/).

```bash
uv sync --frozen --extra dev
uv run task --help
uv run pytest
uv run ruff check .
uv run mypy
uv build
```

Machine-readable and colour-control output flags are deferred to the reporting
phase described in the technical design.
