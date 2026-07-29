# Confirmed real-Docker defect and correction

The first real validation, command
`cmd_6e73798d105c4d2ea5cbdbf3bf9e4901`, failed with:

```text
EVALUATOR_PERMISSION_ERROR
chmod: changing permissions of '/evaluation/input/plan.json': Operation not permitted
```

Cause: all capabilities were correctly dropped, but the administrative
permission step attempted to chmod host-staged, Docker-copied files owned by a
different UID. Root without `CAP_FOWNER` cannot do that.

Correction: input/harness and solver-task files are now assigned their final
non-writable modes in trusted host staging before `docker cp`. The
capability-free container verifies readability/non-writability and changes
permissions only on root-owned workspace/output directories. No capability was
restored. The next validation succeeded, followed by successful no-op, patch,
and command-solver lifecycles.

The first complete Go lifecycle build also exposed that the isolated Docker CLI
home could not discover Docker Desktop's Buildx plugin and fell back to the
deprecated legacy builder. The Docker wrapper now stages only the executable
Buildx plugin into its isolated CLI home; it does not copy host Docker
configuration or credentials. A regression verifies that `config.json` is not
staged. The real lifecycle then built with BuildKit.

The first isolation-solver attempt also treated permission-denied `/root/.ssh`
metadata as an execution error. The regression solver now treats
`PermissionError` as positive evidence that the host path is inaccessible.

Finally, the Go-only synthetic parser initially used `go run` under `/tmp`,
which is non-executable in the restricted evaluator. Its test harness now
places Go's temporary executable in evaluator-owned output storage. This was a
fixture correction, not a runtime-policy relaxation.
