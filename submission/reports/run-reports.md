# Real-Docker run evidence

| Command | Command ID | Exit | Outcome | Artifact root |
| --- | --- | ---: | --- | --- |
| Example init | `cmd_dbf9acc81ba148be8df650c5be68e3f3` | 0 | initialized | `artifacts/submission-hello-answer/cmd_dbf9acc81ba148be8df650c5be68e3f3` |
| Example validate | `cmd_4f3ba029afa54ce3affac59285699e90` | 0 | valid | `artifacts/submission-hello-answer/cmd_4f3ba029afa54ce3affac59285699e90` |
| Example no-op | `cmd_64cd4c010cb6428f89be90009f7ff3a1` | 1 | unresolved | `artifacts/submission-hello-answer/cmd_64cd4c010cb6428f89be90009f7ff3a1` |
| Example patch | `cmd_c41188f54b734692b854d25991bcb0fb` | 0 | resolved | `artifacts/submission-hello-answer/cmd_c41188f54b734692b854d25991bcb0fb` |
| Isolation command | `cmd_5f0428f258e6423284b5bc38eb6c80ce` | 0 | resolved | `artifacts/submission-hello-answer/cmd_5f0428f258e6423284b5bc38eb6c80ce` |
| OpenLibrary init | executed with isolated state | 3 | `SOURCE_SUBMODULE_UNSUPPORTED` | temporary evidence only |
| OpenLibrary validate | not executed | — | blocked by init | — |
| OpenLibrary no-op | not executed | — | blocked by init | — |
| OpenLibrary patch | not executed | — | blocked by init | — |

The no-op candidate tree equals baseline and its candidate patch is the empty
SHA-256 (`e3b0…b855`). The patch and command solvers independently generated
candidate tree `87c7af548645cc540a43d4679310f9763bbc1939` and regenerated patch digest
`sha256:ce885b0901b3bb66c22ea8679b27f9163e0b2bbef5a4c8867147559918d20d9b`.

The successful command event order was:

```text
SOLVER_COMPLETED
WORKSPACE_EXPORT_VALIDATED
CANDIDATE_TREE_CONSTRUCTED
CANDIDATE_PATCH_GENERATED
CANDIDATE_PATCH_ROUNDTRIP_VERIFIED
PATCH_POLICY_ACCEPTED
CANDIDATE_FINALIZED
CANDIDATE_EVALUATOR_STARTED
```

`task show`'s service path was exercised for every produced init, validate, and
run record with events and per-selector results.
