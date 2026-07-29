# Focused Phase 5 implementation audit

| Area | Existing status | Change/evidence |
| --- | --- | --- |
| Solver secrecy | Architecture correct: separate named volumes and allowlisted staging | Added a real recursive controlled-canary command solver; evaluator confirms the canary while solver task/workspace/tmp, env, argv, bundle/database/artifact/socket paths remain clean |
| Finalization order | **Defect:** `CANDIDATE_FINALIZED` preceded policy/hidden overlap | Moved finalization and added seven persisted completion events plus exact-order unit and real-run evidence |
| Raw candidate tree | Correct raw blob/symlink hashing and baseline tree guard | Expanded round-trip test with empty, binary, non-UTF-8, deletion/directory removal, file↔symlink, executable mode, and addition states |
| Patch round-trip | Correct full manifest equality | Added transition coverage; mismatch remains structured `CANDIDATE_PATCH_ROUNDTRIP_ERROR` |
| Patch solver | Correct: input validation/read-only staging/non-root apply/export/regeneration | Existing fake-runner and successful real patch lifecycle confirm regenerated digest differs from input representation while content identity resolves |
| No-op | Correct full pipeline | Real no-op exported, rebuilt the locked tree, generated empty patch digest, ran policy and fresh candidate evaluator, and returned unresolved/exit 1 |
| Capability boundary | **Defect:** capability-free admin chmod failed on host-owned staged files | Final modes now come from trusted host staging; container verifies non-writability. Real probe: UID/GID 1000, CapEff 0, NoNewPrivs 1 |
| Result boundary | Core validation correct | Added FIFO, oversized file, and unexpected absolute extra-field attacks; existing missing, malformed, symlink-parent, schema, status, selector, duration, timestamp, and message tests retained |
| Hidden conflict | Exact parser/set intersection correct | Added case-sensitive exact nonmatches and verified conflict stops before candidate evaluator with exit 6/content redaction |
| SQLite durability | Phase commits correct | Added candidate-evaluator and report-write late failure tests; completed evidence survives and commands finalize |
| `task show` paths | **Defect:** init/validate lacked artifact roots; paths were returned without validation | All commands persist roots. Show rejects absolute/traversal/file or root symlinks, never reads contents, and marks missing artifacts |
| Real Docker | Previously blocked | Full example plus 736-second Go lifecycle passed. **Additional defect:** isolated Docker HOME hid Buildx; now only the executable plugin is staged, never host config/credentials |

No provider integration, cache, parallelism, remote engine, alternate runtime, UI,
private credential flow, or distributed execution was added.
