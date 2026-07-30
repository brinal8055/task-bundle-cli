# Task Bundle CLI

Task Bundle CLI builds and evaluates reproducible coding-agent tasks from an
exact public Git commit, a Docker environment, and task-owned hidden tests. A
solver produces a candidate without access to hidden inputs; a fresh evaluator
then tests the finalized candidate, and a separate trusted parser normalizes
the captured results. The CLI provides `task init`, `task validate`, `task run`,
and `task show`.

## Key guarantees

- The public repository is materialized at one exact commit and raw Git tree.
- `task init` verifies the complete `/opt/task/repo` image filesystem against
  the source manifest and Git tree before writing a lock.
- `task validate` proves the expected broken baseline and known-good golden
  behavior.
- Every run performs a fresh baseline preflight before starting the solver.
- Hidden tests are withheld until the solver is stopped and the candidate tree
  and patch are finalized.
- Docker captures selector argv, stdout, stderr, exit status, timeout, and
  truncation state; candidate-created result files are never accepted.
- A separate non-root parser emits complete, schema-validated per-test results.
- SQLite history and command-local JSON, Markdown, logs, patches, and captured
  executions make each lifecycle inspectable.
- Solver and evaluator containers run without network, Docker socket, Linux
  capabilities, or root privileges, under bounded resource policies.

These are practical local integrity boundaries, not cryptographic protection
against arbitrary candidate code interfering with its own test process.

## Prerequisites

- Python 3.12
- [`uv`](https://docs.astral.sh/uv/)
- Git
- A current Docker Engine or Docker Desktop daemon capable of Linux containers

The committed examples target `linux/amd64`. They run natively on compatible
Linux hosts; Docker Desktop may emulate them on Apple Silicon, which is
substantially slower. Bundle Dockerfiles pin their base images by digest. The
`TASK_BUNDLE_REAL_DOCKER_*` variables shown below configure test fixtures only;
normal bundle commands do not need them.

## Installation

From a clean clone:

```bash
uv sync --frozen --extra dev
uv run task --help
uv run task --version
```

To verify the distributable wheel:

```bash
uv build
python3.12 -m venv /tmp/task-bundle-wheel
/tmp/task-bundle-wheel/bin/pip install dist/task_bundle_cli-0.1.0-py3-none-any.whl
/tmp/task-bundle-wheel/bin/task --help
```

## Quick start: synthetic Go

The small Go bundle exercises the complete cross-language lifecycle:

```bash
uv run task init bundles/synthetic-go-calculator
uv run task validate bundles/synthetic-go-calculator

uv run task run bundles/synthetic-go-calculator --solver noop

uv run task run bundles/synthetic-go-calculator \
  --solver patch \
  --patch submission/candidates/synthetic-go/golden.patch

uv run task show <command-id> --events --tests
```

Expected exits are `0`, `0`, `1`, `0`, and `0`. Exit `1` is the successful
command result for an evaluated but unresolved candidate; infrastructure and
configuration failures use different exits and structured error codes.

Two command-solver delivery modes are also committed:

```bash
# Command already installed in the task image
uv run task run bundles/synthetic-go-calculator \
  --solver command -- solve-task

# Read-only staged solver context
uv run task run bundles/synthetic-go-calculator \
  --solver command \
  --solver-context submission/solvers/synthetic-go/solve \
  -- /bin/sh /task/solver/solve.sh
```

Additional partial and regression patches are documented in the
[bundle README](bundles/synthetic-go-calculator/README.md).

## Commands

- `task init BUNDLE` validates closed bundle inputs, acquires the exact Git
  source, builds and inspects the task image, and writes `.task/bundle.lock.json`.
- `task validate BUNDLE` evaluates baseline and golden states and persists the
  validation identity required by `task run`.
- `task run BUNDLE --solver {noop|patch|command}` repeats baseline preflight,
  runs the isolated solver, reconstructs and checks its candidate patch, then
  evaluates it against fresh hidden inputs.
- `task show COMMAND_ID [--events] [--tests]` reads persisted command,
  lifecycle, and selector records without opening artifact contents.

Every command supports `--json` and `--no-colour` where applicable. Use each
command's `--help` for retention, rebuild, platform, repeat, and solver options.

## Example bundles

- [`bundles/synthetic-go-calculator`](bundles/synthetic-go-calculator) is the
  fast, reviewer-runnable cross-language demonstration.
- [`bundles/swebench-pro-ansible-d9f186`](bundles/swebench-pro-ansible-d9f186)
  is the supported real SWE-bench Pro demonstration: init and validation pass,
  no-op is unresolved, and the golden candidate resolves.
- [`bundles/swebench-pro-openlibrary`](bundles/swebench-pro-openlibrary) is a
  preserved unsupported-source example. Its provenance is verified, but source
  policy correctly rejects the exact tree because Gitlinks/submodules are not
  supported. It is not an expected successful lifecycle.

## Artifacts and persistence

Generated state is intentionally ignored by Git:

```text
BUNDLE/.task/
  bundle.lock.json
  source.manifest.json
  source.snapshot.json

BUNDLE/artifacts/TASK_ID/COMMAND_ID/
  report.json
  report.md
  baseline/ and candidate/
    captured-executions.json
    results.json
    stdout/stderr logs
  solver/
    candidate.patch
    candidate-tree.json
    patch-policy.json

~/.task-bundle/task.db
```

The exact artifact set varies by command phase. `task show` retrieves persisted
command status, ordered events, selector results, and safe artifact references.
Portable curated examples live under [`submission/reports`](submission/reports).

## Security model

Bundle authors, Dockerfiles, hidden inputs, harnesses, and trusted parsers are
trusted. Repository content, solvers, candidate code, solver context, patch
inputs, Docker runtime state, and persisted artifact paths are not. Hidden-test
secrecy is enforced before candidate finalization; direct result-file spoofing
and post-exit races are prevented by host capture, candidate shutdown, and
separate parsing.

Docker remains a local process-isolation boundary, not a hostile multi-tenant
sandbox. Candidate code can interfere with the language test process in which
it executes, so cryptographic in-process result integrity is not claimed. See
the complete [security model](SECURITY.md).

## Development and verification

```bash
uv run pytest -rs
uv run ruff check .
uv run mypy src tests
uv build
uv run python scripts/verify-portable-reports.py
scripts/verify-submission.sh
```

Real-Docker integrity fixtures are opt-in and require locally available,
digest-pinned test bases:

```bash
TASK_BUNDLE_RUN_REAL_DOCKER=1 \
TASK_BUNDLE_REAL_DOCKER_GO_BASE='golang@sha256:3d699e4d15d0f8f13c9195c0632a16702b8cbdece2955af1c23b37ae5d55a253' \
TASK_BUNDLE_REAL_DOCKER_PYTHON_BASE='jefzda/sweap-images@sha256:f9e1f9d428d55a8f26b27d89f29819b79a82b847fd252903c68221b2812ccd04' \
TASK_BUNDLE_REAL_DOCKER_PLATFORM='linux/amd64' \
  uv run python scripts/verify-security.py
```

Run `scripts/verify-submission.sh --real` for the committed synthetic and
supported Ansible closure when the required images and network access are
available. It treats no-op exit `1` as expected and audits resource cleanup.

## Design and reviewer material

Start with the reviewer-focused [design notes](DESIGN.md) and
[submission map](submission/README.md). The full implementation contract is in
[docs/technical-design.md](docs/technical-design.md); phase documents under
[`docs/`](docs) record the security and lifecycle decisions in greater depth.
