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
- Normalized results must be a bounded regular file under the evaluator output
  root and must map every requested selector exactly once.
- Command artifacts are written atomically and database artifact paths are
  treated as untrusted by `task show`.

## Important limits

The task author and task-owned evaluator are trusted. Candidate code and the
runner share an evaluator environment, so cryptographic result isolation is
not claimed. Docker Desktop/Linux container isolation is assumed. Retaining
containers intentionally retains sensitive evaluator state and emits a
warning. Runtime network is disabled, but image builds and public Git
materialization require network on a clean machine.

The CLI is a single-host evaluator. It does not manage private credentials,
remote daemons, multi-tenant scheduling, or distributed trust.

## Reviewer checks

Run:

```bash
scripts/verify-submission.sh
python scripts/verify-security.py
TASK_BUNDLE_RUN_REAL_DOCKER=1 python scripts/verify-security.py
```

The final command requires a working local Docker daemon and the configured
digest-pinned test base.
