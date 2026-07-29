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
Baseline and golden validation is documented in
[Phase 4: Baseline and Golden Validation](docs/phase-4-baseline-golden-validation.md).
Solver and candidate evaluation is documented in
[Phase 5: Solver and Candidate Evaluation](docs/phase-5-solver-candidate-evaluation.md).

## Roadmap

- Phase 0 — Foundation (provisionally complete)
- Phase 1 — Secure bundle core (complete)
- Phase 2 — Trusted Git source materialisation (complete after focused audit)
- Phase 3 — Task image construction and task init (complete after focused audit)
- Phase 4 — Baseline and golden validation (complete)
- Phase 5 — Solver and candidate evaluation (complete)
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
opt-in build-context retention. `task validate` runs isolated baseline and
golden evaluation with structured results and repeat-aware flakiness checks.
`task run` supports noop, patch-file, and structured command solvers with
trusted candidate extraction and fresh hidden evaluation. `task show` queries
persisted init, validation, and run lifecycles.
