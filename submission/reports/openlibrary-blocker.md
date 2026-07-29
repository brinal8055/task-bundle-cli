# OpenLibrary source-policy blocker

The exact command was executed with isolated SQLite state and the real public
repository:

```text
SOURCE_SUBMODULE_UNSUPPORTED
Gitlinks: vendor/infogami, vendor/js/wmd
```

This occurs before Docker image construction. The dataset record, exact public
text, selectors, hidden test patch, golden patch, immutable revision, instance
ID, and canonical record digest are all available and committed.

No workaround was applied. Removing Gitlinks changes the locked source tree;
flattening their contents adds submodule support and changes candidate-tree
semantics. Both conflict with the Phase 6 instruction to avoid feature
expansion and document “no submodules” as a final limitation.

The exact task semantics were still checked independently in the official
digest-pinned SWE-bench Pro image: baseline P2P passed/F2P failed, and golden
P2P/F2P both passed.

Final classification:

```text
IMPORTED
PROVENANCE VERIFIED
OFFICIAL IMAGE SEMANTICS VERIFIED
CLI EXECUTION BLOCKED BY UNSUPPORTED GITLINKS
```

The successful real CLI demonstration is the separate supported Ansible
bundle. OpenLibrary is retained only as honest source-policy boundary evidence.
