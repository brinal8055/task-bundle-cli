# Phase 4: Baseline and Golden Validation

Phase 4 implements `task validate BUNDLE`. It establishes that a task is
well-formed before any solver is allowed to run: existing behavior must remain
green at baseline, requested fixes must fail in their configured baseline
state, and the trusted golden patch must make every requested selector pass.
Phase 4 does not run solvers or candidate patches.

## Preconditions and identity

Validation requires a current `.task/bundle.lock.json` and the exact locked
image to exist locally. It never rebuilds. Missing state directs the operator to
`task init`; stale state directs the operator to `task init --rebuild`.

The deterministic validation identity covers:

- bundle input digest;
- immutable task image ID;
- runtime-policy digest;
- harness digest;
- selector digest;
- hidden-test patch digest;
- golden-patch digest;
- repeat count.

Matching successful validations are queryable for Phase 5, but Phase 4 always
executes and never silently reuses cached results.

## Fresh evaluator model

Every baseline and golden repeat receives a new container, workspace volume,
and evaluation volume. The task image is addressed by immutable image ID.
`/opt/task/repo` remains unchanged; it is copied into the fresh
`/workspace/repo` volume before evaluator data is introduced.

The bundle root and command artifact directory are never mounted. Docker copy
stages only:

```text
/evaluation/input/
  plan.json
  task-metadata.json
  test.patch
  golden.patch       # golden phase only

/evaluation/harness/
  task-owned scripts, parser, helpers, and fixtures

/evaluation/output/
  task-owned structured output
```

The hidden patch is applied to baseline. Golden applies the golden patch first
and the hidden patch second. Patch headers are validated on the host, then
Docker executes `git apply --check --index --binary` and
`git apply --index --binary` with hooks disabled. Absolute, traversing,
non-normalized, `.git`, submodule, and escaping-symlink changes are rejected.

## Permissions and runtime boundary

Evaluator inputs and harness files are root-owned. Directories and executable
harness files are `0555`; other files are `0444`. Fixed administrative seeding,
permission, and patch operations run as container root with all capabilities
dropped. Before test execution the administrative Git index is removed and only
the workspace and output volumes are made writable. Configured preparation and
runner argv run as the non-root runtime user with no retained capabilities.

Each evaluator has:

- network `none`;
- read-only root filesystem;
- CPU, memory, PID, and timeout limits from the locked runtime policy;
- all capabilities dropped, including for administrative operations;
- `no-new-privileges`;
- configured tmpfs mounts;
- no Docker socket, host credentials, SSH agent, bundle mount, or artifact
  mount.

Preparation is optional. When configured, it runs once per fresh evaluator,
without a shell added by the CLI and without network access. Non-zero and
timeout outcomes are infrastructure errors with preserved stdout/stderr.

## Result contract and selector mapping

The configured runner must create a bounded regular UTF-8 JSON object at a
normalized path below `/evaluation/output`. Schema version `1` uses the strict,
immutable `NormalizedResult` model. It requires timezone-aware ordered
timestamps, known harness/test statuses, non-negative durations, and bounded
messages. Missing, oversized, malformed, non-object, unsupported, or incomplete
results are infrastructure failures; runner exit code alone is never trusted.
The result and every existing parent below the controlled output root must be
real filesystem entries rather than symlinks.

Every requested selector must occur exactly once. Duplicate or missing
requested selectors fail as incomplete infrastructure. Additional unrequested
tests are retained by the task-owned result but cannot satisfy a selector.

Candidate code in Phase 5 can attempt to spoof task-owned output while tests
run. This boundary provides strict schema, path, size, harness-completion, and
selector-completeness checks; it does not claim cryptographic separation
between candidate code and harness output.

Baseline accepts:

- every `PASS_TO_PASS` selector as `passed`;
- every `FAIL_TO_PASS` selector in its explicit `baseline_statuses` list,
  which defaults to only `failed`.

`error` is accepted only when explicitly configured. Skip, xfail, timeout,
missing results, partial collection, and global harness failures are never
valid baseline evidence.

Golden requires every requested selector to be `passed`.

## Repeats and outcomes

The bundle `evaluation.repeat` value is authoritative unless `--repeat`
provides a positive override. All repeats use fresh resources. Every repeat
must satisfy phase semantics, and the per-selector status vector must remain
identical. A changed status is classified as baseline or golden flakiness even
when both statuses would otherwise be accepted.

An invalid baseline stops before golden execution. A valid baseline followed by
an invalid golden preserves evidence from both phases.

CLI exits are:

```text
0   valid
2   configuration, missing lock, or stale lock
3   Docker/evaluator/prepare/runner/result infrastructure failure
4   invalid or flaky baseline/golden behavior
130 interruption
```

## Persistence and artifacts

Phase 4 introduced SQLite schema version 3; schema version 4 retains these
records while adding Phase 5 state. Validation rows store identity, repeat
count, timestamps, per-repeat evaluation/container/storage identity, patch
digests, cleanup state, and per-selector expected/actual statuses. Validation
finalization and command completion share a transaction. If later golden
infrastructure fails, completed baseline evidence is committed under an
incomplete validation row. Handled failures always finalize their command
record.

Artifacts live under `artifacts/<task-id>/<command-id>/` and include the
command, bundle snapshot, lock copy, validation identity, phase plans and task
metadata, patch/prepare/runner logs, raw normalized results, selector
classification, phase summaries, and JSON/Markdown reports. Every artifact is
atomically written, hashed, and registered.

Evaluator containers and volumes are removed after all normal and exceptional
outcomes. `--keep-containers` explicitly retains evaluator containers and
volumes and emits a prominent warning because they contain hidden tests and,
for golden phases, the golden patch. The warning also identifies retained
selectors and evaluation output.

## CLI

```bash
task validate BUNDLE [--repeat N] [--keep-containers] [--json] [--no-colour]
```

## Limitations

- Docker is required for real validation.
- Runtime dependency downloads are unavailable because evaluator networking is
  disabled.
- Solver execution and candidate evaluation are provided by Phase 5. There is
  no parallel validation, cache, private-repository support, or SWE-bench Pro
  integration.
