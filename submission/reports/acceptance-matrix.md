# Acceptance matrix

| Area | Requirement | Status | Evidence |
| --- | --- | --- | --- |
| Correctness | exact public commits | PASS | Hello-World and OpenLibrary 40-hex commits |
| Correctness | baseline/golden semantics | PASS | example lifecycle and official OpenLibrary selector checks |
| Correctness | fresh preflight | PASS | every recorded run |
| Correctness | candidate finalization | PASS | persisted ordered events |
| Correctness | resolved/unresolved evidence | PASS | example no-op and patch/command reports |
| Isolation | hidden absent from solver | PASS | recursive controlled-canary solver |
| Isolation | separate containers/storage | PASS | fake-runner assertions plus real lifecycle |
| Isolation | non-root/cap-drop/no-new-privileges | PASS | command construction and real capability-safe fix |
| Isolation | network none/no socket/resource limits | PASS | command assertions and real lifecycle |
| Isolation | cleanup | PASS | no labeled example containers or volumes |
| Reproducibility | bundle/source/build/image/runtime/patch identities | PASS | lock, validation, and run reports |
| Reproducibility | solver-context digest | PASS | isolation run `b8d3…7e79` |
| Observability | command IDs/SQLite/events/tests/logs/reports | PASS | `task show` service queries and artifacts |
| Demonstration | synthetic init/validate/unresolved/resolved | PASS | real example plus full Go lifecycle test |
| Demonstration | command solver/hidden isolation | PASS | `cmd_5f0428…` |
| Demonstration | OpenLibrary record and selector semantics | PASS | immutable import and official image runs |
| Demonstration | OpenLibrary Task Bundle init | BLOCKED | exact base commit has two Gitlinks |
| Demonstration | OpenLibrary Task Bundle validate | BLOCKED | init prerequisite blocked |
| Demonstration | OpenLibrary Task Bundle unresolved | BLOCKED | validation prerequisite blocked |
| Demonstration | OpenLibrary Task Bundle resolved | BLOCKED | validation prerequisite blocked |
| Packaging | lint/type/unit/build/clean wheel | PASS | final verification gates |
| Handoff | private GitHub collaborators added | BLOCKED | manual owner action required |
