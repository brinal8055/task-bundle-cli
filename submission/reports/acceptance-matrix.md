# Acceptance matrix

| Area | Requirement | Status | Evidence |
| --- | --- | --- | --- |
| Correctness | exact public commits | PASS | Hello-World, OpenLibrary, and selected Ansible 40-hex commits |
| Correctness | baseline/golden semantics | PASS | synthetic and selected real CLI validation |
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
| Demonstration | supported SWE-bench Pro init | PASS | `cmd_ead91f…7422` |
| Demonstration | supported SWE-bench Pro validation | PASS | `cmd_95994a…e125` |
| Demonstration | supported SWE-bench Pro no-op | PASS | `cmd_5a1105…3325`, expected exit 1 |
| Demonstration | supported SWE-bench Pro golden candidate | PASS | `cmd_846788…6be4` |
| Assignment | Demo on one SWE-bench Pro task | PASS | complete selected Ansible CLI lifecycle |
| Packaging | lint/type/unit/build/clean wheel | PASS | final verification gates |
| Handoff | private GitHub collaborators added | BLOCKED | manual owner action required |

Final required status summary:

```text
Synthetic lifecycle                 PASS
Synthetic hidden isolation          PASS
Synthetic security suite            PASS
OpenLibrary source import           BLOCKED — unsupported gitlinks
Real supported SWE-bench Pro init   PASS
Real supported validation           PASS
Real supported no-op                PASS
Real supported golden candidate     PASS
```
