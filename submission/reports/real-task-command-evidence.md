# Real task command evidence

Selected bundle: `bundles/swebench-pro-ansible-d9f186`

| Command | Command ID | Exit | Outcome | Artifact root |
| --- | --- | ---: | --- | --- |
| init | `cmd_d326a7f751fe4f30b908509ef66ab691` | 0 | initialized | `artifacts/swebench-pro-ansible-d9f186/cmd_d326a7f751fe4f30b908509ef66ab691` |
| validate | `cmd_ce2d13ef592844bb8e3bb840165f4dd0` | 0 | valid | `artifacts/swebench-pro-ansible-d9f186/cmd_ce2d13ef592844bb8e3bb840165f4dd0` |
| no-op | `cmd_1428ff1ef6a24430ad36e89b186586d9` | 1 | unresolved | `artifacts/swebench-pro-ansible-d9f186/cmd_1428ff1ef6a24430ad36e89b186586d9` |
| golden candidate | `cmd_a0931c336d024b668f2eb6f9477398d7` | 0 | resolved | `artifacts/swebench-pro-ansible-d9f186/cmd_a0931c336d024b668f2eb6f9477398d7` |

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
All evaluator test execution used adapter contract version `2`; candidate
processes were stopped and verified gone before the non-root trusted parser
consumed host-captured execution records.
