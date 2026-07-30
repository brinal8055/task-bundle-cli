# Submission map

This directory is the compact reviewer surface for Task Bundle CLI. Start with
the repository [README](../README.md), run the
[synthetic Go bundle](../bundles/synthetic-go-calculator), and use the portable
reports here to inspect outcomes without committing generated runtime trees.

## Evaluation criteria

| Evaluation criterion | Repository evidence |
| --- | --- |
| Baseline vs candidate correctness | `src/task_bundle/validation/`, `src/task_bundle/run/`, selector-level synthetic reports |
| Clear UX | Typer help, structured errors/exits, JSON output, Markdown reports, `task show` |
| Reproducibility | exact commits and raw Git trees, bundle/image/runtime identities, complete image-source verification |
| Safety and isolation | separate solver/evaluator/parser lifecycles, hidden-input timing, bounded non-root Docker policies |
| Code quality | strict Pydantic models, focused services, fake-runner tests, adversarial real-Docker regressions, Ruff and strict mypy |
| Creativity beyond minimum | `task show`, installed and staged command solvers, trusted parser isolation, complete image-source equality, portable support matrix |

The trusted-result and complete image-source checks are correctness and
security requirements, not optional creativity.

## Reviewer path

```bash
uv sync --frozen --extra dev
uv run task init bundles/synthetic-go-calculator
uv run task validate bundles/synthetic-go-calculator
uv run task run bundles/synthetic-go-calculator --solver noop
uv run task run bundles/synthetic-go-calculator \
  --solver patch \
  --patch submission/candidates/synthetic-go/golden.patch
uv run task show <command-id> --events --tests
```

The no-op exits `1` after a successful unresolved evaluation. The golden patch
exits `0` and resolves. The committed candidate inputs are deterministic and
contain no hidden evaluation material.

## Contents

- [`support-matrix.json`](support-matrix.json) states implemented and
  intentionally unsupported capabilities.
- [`candidates/synthetic-go`](candidates/synthetic-go) contains reviewer-runnable
  patch inputs.
- [`solvers/synthetic-go`](solvers/synthetic-go) contains staged solver,
  isolation-check, and timeout contexts.
- [`reports/synthetic`](reports/synthetic) contains compact unresolved and
  resolved reports with baseline and candidate selector outcomes.
- [`reports/real-task-*.json`](reports) and the adjacent Markdown evidence
  record the supported Ansible SWE-bench Pro lifecycle.
- [`reports/openlibrary-blocker.md`](reports/openlibrary-blocker.md) preserves
  the intentional Gitlink/submodule rejection.

Generated `.task/`, `artifacts/`, SQLite databases, Docker contexts, and
container exports are intentionally absent from the submission.
