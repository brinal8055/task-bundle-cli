# Real task command evidence

Selected bundle: `bundles/swebench-pro-ansible-d9f186`

| Command | Command ID | Exit | Outcome | Artifact root |
| --- | --- | ---: | --- | --- |
| init | `cmd_ead91f81c1534e468029b0c977327422` | 0 | initialized | `artifacts/swebench-pro-ansible-d9f186/cmd_ead91f81c1534e468029b0c977327422` |
| validate | `cmd_95994ac450a94272bf1d163c09ede125` | 0 | valid | `artifacts/swebench-pro-ansible-d9f186/cmd_95994ac450a94272bf1d163c09ede125` |
| no-op | `cmd_5a11053ab6644e41a84da0befa433325` | 1 | unresolved | `artifacts/swebench-pro-ansible-d9f186/cmd_5a11053ab6644e41a84da0befa433325` |
| golden candidate | `cmd_8467886813444d9d8c5f2341027d6be4` | 0 | resolved | `artifacts/swebench-pro-ansible-d9f186/cmd_8467886813444d9d8c5f2341027d6be4` |

All four records were successfully queried with:

```bash
task show <command-id> --json --events --tests
```

The no-op exit `1` is the expected process result for a completed unresolved
candidate; its persisted command status is `succeeded`. The resolved run used
a trusted temporary copy outside the bundle as patch-solver input. The solver
operated on that input, exported the non-root workspace, reconstructed raw
trees, regenerated a `3,957`-byte binary patch, verified exact round-trip and
policy, finalized the candidate, and only then created the fresh evaluator.
