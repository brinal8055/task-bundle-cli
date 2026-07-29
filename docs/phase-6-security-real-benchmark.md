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

The successful real demonstration is immutable SWE-bench Pro row `407` in
`bundles/swebench-pro-ansible-d9f186`. The exact issue text, requirements,
interface, hidden test patch, golden patch, selectors, instance ID, dataset
revision, source commit, Git tree SHA, and canonical source-record digest are
preserved. Its exact Ansible tree has zero Gitlinks. The digest-pinned official
image supplies commit-compatible dependencies, while task-owned preparation
and parsing ensure pytest imports candidate code from `/workspace/repo/lib`.

The final clean-wheel lifecycle completed with init command `cmd_ead91f…7422`,
validation `cmd_95994a…e125`, unresolved no-op `cmd_5a1105…3325`, and resolved
patch candidate `cmd_846788…6be4`. Baseline P2P passed/F2P failed; golden and resolved
candidate P2P/F2P both passed. The patch solver exported the complete non-root
workspace, reconstructed raw trees, regenerated a binary patch, verified exact
round-trip and policy, finalized the candidate, and only then created its
evaluator.

That full 5,084-entry export exposed one generic ordering defect: depth-first
filesystem traversal was locally sorted but not globally lexicographic.
`build_filesystem_manifest` now sorts its completed entry list before
constructing and digesting the strict manifest, with a nested-path regression
test. No trust boundary or source rule changed.

The earlier OpenLibrary import remains committed. Its recorded two Gitlinks
cause `SOURCE_SUBMODULE_UNSUPPORTED`; Phase 6 does not add submodule support or
alter the raw source tree. Its official digest-pinned image separately proves
the selector semantics. It remains boundary evidence rather than the
successful Task Bundle lifecycle.

The smaller `submission/example-bundle` provides a quick clean-machine
lifecycle. `submission/solvers/verify-isolation-and-solve.py` recursively
inspects its visible task/workspace/tmp trees plus environment and argv for
controlled hidden canaries before producing a deterministic public solution.

Run all non-Docker gates with `scripts/verify-submission.sh`. Set
`TASK_BUNDLE_RUN_REAL_DOCKER=1` when invoking `scripts/verify-security.py` to
include the complete synthetic Docker lifecycle test. Run
`scripts/verify-submission.sh --real` for synthetic, security, and selected
real CLI closure.
