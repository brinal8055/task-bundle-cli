# Task Bundle CLI

Task Bundle CLI safely, reproducibly, and observably evaluates coding-agent
solutions against hidden benchmark tests across arbitrary coding repositories.

The implementation follows the canonical
[technical design](docs/technical-design.md).

The completed secure bundle core is documented in
[Phase 1: Secure Bundle Core](docs/phase-1-secure-bundle-core.md).
Trusted source acquisition is documented in
[Phase 2: Trusted Git Source Materialisation](docs/phase-2-trusted-git-source.md).
Task image construction is documented in
[Phase 3: Task Image Construction and `task init`](docs/phase-3-task-image-init.md).

## Roadmap

- Phase 0 — Foundation (complete)
- Phase 1 — Secure bundle core (complete)
- Phase 2 — Trusted Git source materialisation (complete after focused audit)
- Phase 3 — Task image construction and task init (complete)
- Phase 4 — Baseline and golden validation (next; not implemented)
- Phase 5 — Solver and candidate evaluation (not implemented)
- Phase 6 — Security verification and real benchmark (not implemented)

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

`task init` supports JSON output, colour control, strict lock freshness, and
opt-in build-context retention. Later lifecycle commands remain intentionally
unimplemented until their corresponding phases.
