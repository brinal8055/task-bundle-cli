# Phase 5: Solver and Candidate Evaluation

Phase 5 implements `task run` and `task show`. A run reuses a matching
successful Phase 4 validation, performs a fresh baseline guardrail, executes one
restricted solver, finalizes a portable candidate patch, and evaluates that
patch in a new hidden-test evaluator.

## Preconditions and validation reuse

`task run` requires a current bundle lock, the exact locked image ID, and the
persisted locked source snapshot and manifest. It never rebuilds or silently
runs validation.

A reusable validation must match the bundle digest, image ID, runtime-policy
digest, harness digest, selector digest, hidden-test patch digest, and golden
patch digest. Its successful repeat count must be at least the bundle's current
configured repeat count. A stronger validation may authorize a weaker
requirement; a diagnostic run with fewer repeats may not.

Every run then performs one fresh baseline evaluator execution. Existing P2P
tests must pass and F2P tests must retain their configured baseline statuses.
`BASELINE_GUARDRAIL_FAILURE` stops before solver creation and preserves the
preflight evaluation and selector evidence.

## Solver types

```bash
task run BUNDLE --solver noop
task run BUNDLE --solver patch --patch candidate.patch
task run BUNDLE --solver command [--solver-context DIR] -- COMMAND ARG...
```

- `noop` changes nothing but still goes through export, patch extraction, policy,
  and candidate evaluation.
- `patch` validates a bounded regular non-symlink host patch, stages it read-only,
  and applies it as the non-root solver user. The supplied patch is never reused
  as the final candidate without workspace extraction.
- `command` preserves structured argv and executes without a shell added by the
  CLI. Non-zero exit and timeout are solver failures.

Solver-specific options are mutually exclusive and validated before Docker work.

## Solver isolation and public context

The solver receives a fresh locked image, a private workspace volume, public
task context, an optional read-only solver context, and an optional patch input.
It does not receive the bundle root, `.task`, artifacts, SQLite database,
selectors, validation reports, harness files, hidden patch, or golden patch.

The container has network `none`, a read-only root filesystem, configured CPU,
memory, PID and wall-time limits, `no-new-privileges`, no Docker socket or host
credentials, and no host bind mounts. Fixed seeding and permission operations use
the administrative root keeper with all capabilities dropped; solver argv and
patch application execute as the configured non-root numeric UID/GID with no
retained capabilities. Solver-visible task storage is root-owned and read-only.
Only `/workspace/repo` and configured temporary storage are writable by
candidate code.

Public context is limited to configured `description.md`, `requirements.md`, and
`interface.md`. Environment variables are added only for files that exist.

## Solver-context staging

An optional command-solver context must be a real directory outside the bundle
and artifacts. Symlinks, `.git`, sockets, devices, FIFOs, hard links, path
traversal, control characters, case collisions, excessive files, and excessive
bytes are rejected. A controlled copy is staged root-owned and read-only.

Its deterministic manifest records normalized path, entry type, executable mode,
size, and SHA-256. The resulting digest is independent of the host path.

## Workspace export and trusted trees

After successful solver execution the container is stopped, freezing the named
workspace volume. Docker copies pristine `/opt/task/repo` and candidate
`/workspace/repo` into command-owned temporary directories. The exported
filesystem is untrusted and is walked without following symlinks.

Only regular files and safe internal relative symlinks are supported. Export
validation rejects `.git`, unsafe or non-normalized paths, absolute/escaping
symlinks, special files, ambiguous hard links, case collisions, and configured
file/count/byte limit violations. Deterministic manifests preserve file bytes,
executable modes, and symlink targets.

Candidate code is never trusted to supply Git metadata. A temporary trusted
object repository hashes raw regular-file bytes and symlink targets, writes
exact `100644`, `100755`, and `120000` index entries, and calls `git write-tree`.
The reconstructed baseline tree must equal the locked Git tree SHA. Because raw
plumbing is used, `.gitattributes`, clean filters, LFS, ident expansion, and EOL
normalization cannot alter candidate content.

## Candidate patch and policy

Trusted tree-to-tree diffing uses binary patches, full blob indexes, and disabled
rename detection. Additions, modifications, deletions, executable changes,
symlinks, non-UTF-8 files, and binary files are portable. Empty noop diffs are
valid.

Before evaluation, the generated patch is applied to another pristine baseline
copy and the rebuilt manifest must exactly match the solver export. A mismatch
is `CANDIDATE_PATCH_ROUNDTRIP_ERROR`.

Policy enforces patch bytes, changed-file count, candidate entry count, candidate
bytes, individual file bounds, normalized Git paths, supported modes, safe
symlinks, and absence of `.git` or gitlinks. Candidate changed paths are
intersected with paths parsed from the trusted hidden patch. `PATCH_CONFLICT`
reports only the overlapping relative paths and never hidden content.

Candidate finalization and policy complete before candidate evaluator staging.

## Candidate evaluator and result trust

The existing Phase 4 evaluator is reused with fresh container, workspace volume,
and evaluation volume:

```text
locked image
→ pristine workspace
→ finalized candidate patch
→ hidden-test patch
→ preparation
→ selected tests
```

The golden patch is absent. All P2P and F2P selectors must map exactly once and
pass for `resolved`. A complete harness with ordinary test failures is
`unresolved`, not infrastructure failure.

Candidate/test execution runs as the configured non-root UID under task-owned
adapter contract version `2`. Docker captures structured argv, stdout/stderr,
exit, timeout, timestamps, duration, and truncation state. The candidate
container is stopped and verified to have no PID and no restart policy before
captured records are staged read-only. A separate non-root trusted parser with
no candidate workspace emits the only accepted normalized result through
bounded stdout; candidate-created result files and raw framework artifacts are
never accepted or used as fallback.

Validated captured records and completed execution logs are persisted before
the parser starts. If parsing fails, this pre-parser evidence remains available,
but no normalized result or selector classification is created.

Candidate code executing inside the test process may still interfere with that
process. This is a direct final-result and post-exit race boundary, not
cryptographic in-process test integrity.

## Persistence, artifacts, and `task show`

SQLite schema version 4 adds run evaluation status, resolved state, artifact
root, full solver execution state, validation reference, container identity,
tree SHAs, patch policy, workspace export, timeout, and cleanup fields.
Preflight and candidate evaluations reuse evaluation/test-result rows. Evidence
is committed phase-by-phase so later failures do not erase completed work.

Run artifacts live under `artifacts/<task-id>/<command-id>/` and contain command,
snapshot, lock, validation reference, baseline evidence, public solver metadata,
context manifest, solver logs, exported manifest, candidate tree, changed paths,
policy, candidate patch, candidate evidence, and JSON/Markdown reports. Hidden
and golden patch content is not copied into normal run artifacts. Artifacts are
atomic, hashed, and registered.

```bash
task show COMMAND_ID [--json] [--events] [--tests] [--no-colour]
```

`task show` queries init, validate, and run commands from structured database
rows. It does not follow database-provided paths to read arbitrary host files.

## Status and exits

```text
0    candidate evaluated and resolved
1    candidate evaluated successfully but unresolved
2    CLI, bundle, lock, context, or validation precondition error
3    Docker, preflight, or candidate-evaluator infrastructure error
4    fresh baseline guardrail failure
5    solver execution failure or timeout
6    patch input, export, extraction, policy, round-trip, or conflict error
130  interruption
```

Unresolved runs have command status `succeeded`. Expected failures are finalized
and rendered without tracebacks. `--keep-containers` is opt-in; normal operation
removes solver/evaluator containers and volumes. Retained candidate evaluators
are prominently identified as containing hidden tests, selectors, and output.

## Limitations

Phase 5 has no provider-backed LLM, real SWE-bench Pro task/importer, private
repository support, networked solvers, candidate dependency downloads, caching,
parallelism, Podman, remote Docker, Kubernetes, or remote artifact storage.
Docker is required for real runs.
