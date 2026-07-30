# Synthetic Go calculator

This is the fastest complete Task Bundle demonstration. It uses the tiny,
immutable `octocat/Hello-World` commit as public source and a task-owned Go
test adapter. The source README selects deliberately incorrect addition until
the candidate enables the correct behavior. Subtraction is the
`PASS_TO_PASS` guard; positive and negative addition are `FAIL_TO_PASS`.

The bundle targets `linux/amd64`. Docker Desktop on Apple Silicon may emulate
that platform and run more slowly.

From the repository root:

```bash
uv run task init bundles/synthetic-go-calculator
uv run task validate bundles/synthetic-go-calculator
uv run task run bundles/synthetic-go-calculator --solver noop
uv run task run bundles/synthetic-go-calculator \
  --solver patch \
  --patch submission/candidates/synthetic-go/golden.patch
uv run task run bundles/synthetic-go-calculator \
  --solver command -- solve-task
uv run task run bundles/synthetic-go-calculator \
  --solver command \
  --solver-context submission/solvers/synthetic-go/solve \
  -- /bin/sh /task/solver/solve.sh
```

Expected exits are `0`, `0`, `1`, `0`, `0`, and `0`. The no-op is a
completed, successfully evaluated unresolved candidate.

Candidate fixtures under `submission/candidates/synthetic-go/` cover the
resolved golden patch, positive-only partial fix, subtraction regression,
malformed patch input, and a forbidden hidden-path conflict. Solver contexts
under `submission/solvers/synthetic-go/` cover a normal staged command,
pre-finalisation isolation check, and timeout.
