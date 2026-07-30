# Final cleanup audit

Audit date: `2026-07-30`

The prospective submission tree was materialized from the audited base commit
plus the complete staged patch in a separate detached worktree. No commit was
created before these gates passed.

## Standard and package gates

`scripts/verify-submission.sh` passed in both the development and clean
worktrees:

- frozen development dependency sync: passed;
- pytest: `381 passed, 24 explicitly environment-gated Docker tests skipped`;
- Ruff: passed;
- strict mypy over `src tests`: passed, `96` source files;
- source distribution and wheel build: passed;
- independent security selection: `106 passed`;
- portable report validation: `6` JSON reports passed;
- clean Python 3.12 wheel installation: passed;
- CLI root/init/validate/run/show help and version surfaces: passed;
- `git diff --check`: passed;
- generated bundle-state audit: passed.

The skips were only opt-in real-Docker fixtures; they were executed separately
by the real closure below.

## Real-Docker gates

The development worktree independently passed:

- complete synthetic Docker lifecycle: `1 passed in 1343.20s`;
- TB-001/TB-002 result-forgery and image/source integrity module:
  `20 passed in 68.65s`.

The exact prospective tree then passed `scripts/verify-submission.sh --real`
from its clean worktree:

- repeated standard, security, package, wheel, and report gates: passed;
- combined synthetic lifecycle plus integrity module:
  `21 passed in 1391.40s`;
- committed Go init, validation, unresolved no-op, resolved patch, installed
  command, and staged isolation command: passed;
- supported Ansible init, validation, unresolved no-op, and resolved patch:
  passed;
- `task show --json --events --tests` for all ten command IDs: passed;
- expected unresolved exit `1`: accepted only with persisted successful command
  status and `resolved=false`;
- no command-labeled container or volume remained;
- generated `.task/`, `artifacts/`, and bundle `__pycache__/` state was removed.

Clean-worktree command IDs:

```text
synthetic init       cmd_7f4cb5e6a567493990867ec872ee48d7
synthetic validate   cmd_47e33acb5d97428cb5ec45fc5599774a
synthetic noop       cmd_9d16e71ae6fd4f3db41f042acb940720
synthetic patch      cmd_8ea5ade255804d9cbfdbd034977dda73
synthetic installed  cmd_88457208e31a4163906917ddb989a228
synthetic staged     cmd_24c82c9e1ff946c49ba6de7d62ed6c6d
Ansible init         cmd_7c02ef1e8857427898b7cffdfd24a267
Ansible validate     cmd_d45e21257cc143638148c8c85868ef53
Ansible noop         cmd_2172e7b8ce78401b97ed2f2d4a6aef5f
Ansible resolved     cmd_93281f8d3f934b92bcf95eacb3256d80
```

The verification host used Docker client/server `29.5.2`, API `1.54`, with a
Linux `aarch64` Docker Desktop daemon. Both committed examples target
`linux/amd64`, so these timings include emulation overhead.

## Repository hygiene

- no `.task/`, `artifacts/`, SQLite database, Docker export, generated build
  context, or temporary source tree is part of the staged submission;
- no local absolute path, host container ID, credential, or secret is present
  in portable submission reports;
- the committed support matrix and synthetic reports are validated by tests
  and `scripts/verify-portable-reports.py`;
- OpenLibrary remains only as provenance-verified unsupported-Gitlink evidence;
- the ignored local development environment, caches, and `dist/` output are
  not submission content.
