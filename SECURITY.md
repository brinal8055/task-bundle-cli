# Security model

## Report security issues

Do not publish suspected vulnerabilities with hidden benchmark material.
Provide the affected commit, command ID, error code, Docker/OS version, and a
minimal synthetic reproduction to the private repository maintainers.

## Guarantees

- Public source is materialized from one exact HTTPS Git commit with raw-object
  validation and no submodules.
- Bundles are closed allowlists with canonical digest-covered inputs.
- Solver and evaluator containers have no network, a read-only root, all Linux
  capabilities dropped, `no-new-privileges`, resource limits, no privileged
  mode, and no Docker socket.
- Untrusted execution uses the configured non-root UID/GID.
- Hidden evaluation inputs are staged only after candidate finalization into
  evaluator-only storage.
- Solver exports are bounded, reject special files/hardlinks/unsafe symlinks
  and case collisions, and exclude administrative Git metadata.
- Candidate patches are regenerated from trusted raw trees and exactly
  round-tripped before policy or evaluation.
- Task-owned adapter contract version `2` creates strict structured execution
  units. Docker captures argv, stdout, stderr, exit status, timeout, and
  truncation state outside candidate filesystem control.
- Candidate processes are stopped and verified absent before a separate
  non-root trusted parser receives read-only captured execution records.
- Candidate-created normalized result files are never accepted. Trusted parser
  output must be bounded, schema-valid, and map every requested selector
  exactly once with a verified observed ID.
- Task images are independently exported and compared against the complete
  normalized source manifest and raw Git tree. Docker volumes at, above, or
  below `/opt/task/repo` are rejected before any verification/runtime container.
- Command artifacts are written atomically and database artifact paths are
  treated as untrusted by `task show`.

## Important limits

The task author and task-owned adapter are trusted. Candidate code executing
inside a language test process may interfere with that process, so
cryptographic result integrity is not claimed. The boundary prevents direct
final-result writes and post-test races. Docker Desktop/Linux container
isolation is assumed. Retaining containers intentionally retains sensitive
evaluator state and emits a warning. Runtime network is disabled, but image
builds and public Git materialization require network on a clean machine.

The CLI is a single-host evaluator. It does not manage private credentials,
remote daemons, multi-tenant scheduling, or distributed trust.

## Reviewer checks

Run:

```bash
scripts/verify-submission.sh
python scripts/verify-security.py
TASK_BUNDLE_RUN_REAL_DOCKER=1 python scripts/verify-security.py
scripts/verify-submission.sh --real
```

The last two commands require a working local Docker daemon and configured
digest-pinned bases. `--real` additionally runs the supported Ansible
init/validate/no-op/resolved lifecycle and treats the no-op exit `1` as the
expected unresolved result.
