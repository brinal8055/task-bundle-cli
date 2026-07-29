# Real-Docker run evidence

| Command | Command ID | Exit | Outcome | Artifact root |
| --- | --- | ---: | --- | --- |
| Example init | `cmd_dbf9acc81ba148be8df650c5be68e3f3` | 0 | initialized | `artifacts/submission-hello-answer/cmd_dbf9acc81ba148be8df650c5be68e3f3` |
| Example validate | `cmd_4f3ba029afa54ce3affac59285699e90` | 0 | valid | `artifacts/submission-hello-answer/cmd_4f3ba029afa54ce3affac59285699e90` |
| Example no-op | `cmd_64cd4c010cb6428f89be90009f7ff3a1` | 1 | unresolved | `artifacts/submission-hello-answer/cmd_64cd4c010cb6428f89be90009f7ff3a1` |
| Example patch | `cmd_c41188f54b734692b854d25991bcb0fb` | 0 | resolved | `artifacts/submission-hello-answer/cmd_c41188f54b734692b854d25991bcb0fb` |
| Isolation command | `cmd_5f0428f258e6423284b5bc38eb6c80ce` | 0 | resolved | `artifacts/submission-hello-answer/cmd_5f0428f258e6423284b5bc38eb6c80ce` |
| OpenLibrary init | executed with isolated state | 3 | `SOURCE_SUBMODULE_UNSUPPORTED` | temporary evidence only |
| Selected real init | `cmd_ead91f81c1534e468029b0c977327422` | 0 | initialized | `artifacts/swebench-pro-ansible-d9f186/cmd_ead91f81c1534e468029b0c977327422` |
| Selected real validate | `cmd_95994ac450a94272bf1d163c09ede125` | 0 | valid | `artifacts/swebench-pro-ansible-d9f186/cmd_95994ac450a94272bf1d163c09ede125` |
| Selected real no-op | `cmd_5a11053ab6644e41a84da0befa433325` | 1 | unresolved | `artifacts/swebench-pro-ansible-d9f186/cmd_5a11053ab6644e41a84da0befa433325` |
| Selected real patch | `cmd_8467886813444d9d8c5f2341027d6be4` | 0 | resolved | `artifacts/swebench-pro-ansible-d9f186/cmd_8467886813444d9d8c5f2341027d6be4` |

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

The selected Ansible no-op kept tree
`64a85753dada2a0a05dcf13093dabbdae13cc7de` and regenerated the empty patch.
The normal patch solver produced candidate tree
`90c572d5e8ef884c8beb4d40afe9639d6020f9b4` and regenerated patch digest
`sha256:f565e33798cbbf6159a25680a8b0392d6e467af2c03d5363d22104bc2fc2b1e7`.
