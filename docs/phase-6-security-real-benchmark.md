# Phase 6 — Security verification and real benchmark

Phase 6 is submission hardening, not product expansion.

The focused Phase 5 audit confirmed raw-tree reconstruction, complete manifest
round-trip, patch-input regeneration, no-op pipeline parity, non-root
execution, capability removal, result validation, exact hidden-path identity,
phase-wise SQLite durability, and cleanup behavior. It corrected two defects:

1. `CANDIDATE_FINALIZED` was recorded before policy and hidden overlap checks.
   The event now follows all candidate finalization checks and precedes
   evaluator creation.
2. init/validate artifact roots were not persisted. Every command type now
   records its root, and `task show` validates database paths without opening
   artifact contents.

Adversarial coverage includes hidden-input canaries, mount/environment/argv
inspection, network/capability restrictions, unsafe solver exports, raw-tree
file and symlink transitions, binary/non-UTF-8/mode-only states, baseline-tree
mismatch, patch round-trip mismatch, malformed/oversized/symlink/FIFO result
files, selector completeness, exact conflict semantics, late-phase durability,
corrupted artifact paths, and resource cleanup.

The real demonstration imports the exact OpenLibrary record documented in
`bundles/swebench-pro-openlibrary`. The original task text, hidden test patch,
golden patch, selectors, instance ID, immutable dataset revision, source commit,
and canonical source-record digest are preserved. Repository-specific pytest
normalization remains inside the task bundle.

The imported base commit contains two Gitlinks. The CLI correctly rejects it
with `SOURCE_SUBMODULE_UNSUPPORTED`; Phase 6 does not add submodule support or
alter the raw source tree to manufacture a successful init. The official
digest-pinned benchmark image is used separately to verify the exact
baseline/golden selectors. This limitation is recorded rather than presented
as a successful Task Bundle lifecycle.

The smaller `submission/example-bundle` provides a quick clean-machine
lifecycle. `submission/solvers/verify-isolation-and-solve.py` recursively
inspects its visible task/workspace/tmp trees plus environment and argv for
controlled hidden canaries before producing a deterministic public solution.

Run all non-Docker gates with `scripts/verify-submission.sh`. Set
`TASK_BUNDLE_RUN_REAL_DOCKER=1` when invoking
`scripts/verify-security.py` to include the real Docker lifecycle test.
