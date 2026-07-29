# Task Bundle CLI design

## Purpose and trust boundary

Task Bundle CLI turns a digest-covered task bundle and an exact public Git
commit into a reproducible task image, proves baseline/golden semantics, runs a
restricted solver, reconstructs its exported filesystem as raw Git objects, and
evaluates only the finalized candidate against fresh hidden inputs.

The bundle author and task-owned evaluator are trusted. Candidate code, solver
context, patch inputs, repository content, normalized result output, Docker
state, persisted database paths, and user/system Git configuration are not.
Docker provides process isolation, not a hostile multi-tenant sandbox.

## Data flow

```text
bundle + public Git commit
  -> canonical snapshot + raw source tree
  -> digest-labeled image + lock
  -> baseline/golden validation identity
  -> baseline preflight
  -> isolated solver workspace
  -> bounded filesystem export
  -> raw baseline/candidate Git trees
  -> regenerated binary patch + exact round-trip
  -> patch/hidden-path policy
  -> fresh candidate evaluator
  -> normalized selector classification
  -> SQLite events + immutable command artifacts
```

Solver and evaluator storage are separate named volumes. The solver receives
only `/workspace`, the public description/requirements/interface, an optional
bounded solver context, or a validated patch input. It never receives the
bundle directory, hidden/golden patches, selectors, task configuration,
validation evidence, evaluator harness, database, artifact directory, or
Docker socket.

## Candidate finalization order

The persisted completion order is:

1. `SOLVER_COMPLETED`
2. `WORKSPACE_EXPORT_VALIDATED`
3. `CANDIDATE_TREE_CONSTRUCTED`
4. `CANDIDATE_PATCH_GENERATED`
5. `CANDIDATE_PATCH_ROUNDTRIP_VERIFIED`
6. `PATCH_POLICY_ACCEPTED`
7. `CANDIDATE_FINALIZED`
8. `CANDIDATE_EVALUATOR_STARTED`

No candidate evaluator is created before the finalization event. A policy or
hidden-path conflict therefore cannot leak back into a still-running solver.

## Reproducibility identity

Bundle identity covers canonical typed configuration, all allowlisted bundle
files, executable bits, public text, environment definition, task-owned
harness, hidden/golden patches, selectors, and provenance. Source identity
covers the exact commit, raw Git tree, normalized source manifest, and Git
implementation evidence. Image identity is a Docker content ID plus required
labels and platform. Validation reuse additionally binds runtime policy,
harness, selectors, hidden/golden digests, and repeat strength.

Candidate construction hashes exported bytes and symlink targets directly into
a private bare object database. It does not use a solver worktree index, Git
attributes, filters, LFS, EOL conversion, `ident`, hooks, credentials, or
ambient Git configuration. The locked baseline tree must reconstruct exactly.
The generated `--binary` patch must rebuild the entire candidate manifest:
path, type, bytes, executable mode, symlink target, additions, and removals.

## Failure and persistence semantics

Configuration failures exit `2`, infrastructure failures `3`, validation
failures `4`, solver failures `5`, and patch/policy failures `6`. A completed
unresolved candidate is a successful command with process exit `1`; a resolved
candidate exits `0`. Phase evidence commits independently, so later evaluator
or report failures do not erase completed baseline, solver, candidate, or test
records. Handled commands never remain `running`.

`task show` never reads artifact contents. It validates persisted paths as
normalized, command-root-contained paths, rejects absolute/traversing/symlink
paths, and marks missing files without a traceback.

## Non-goals

There is no provider-backed LLM integration, cache, parallel scheduler, remote
Docker, Podman/Kubernetes backend, web UI, private-repository credential flow,
or distributed execution.
