# End-to-end demonstration

## Clean setup

Prerequisites: Git, Docker with a local Linux daemon, Python 3.12, and `uv`.

```bash
git clone <private-repository-url>
cd task-bundle-cli
uv sync --frozen --extra dev
uv run task --help
```

## Fast runnable example

```bash
uv run task init submission/example-bundle
uv run task validate submission/example-bundle
uv run task run submission/example-bundle --solver noop
uv run task run submission/example-bundle \
  --solver patch \
  --patch submission/example-bundle/candidates/golden.patch
uv run task run submission/example-bundle \
  --solver command \
  --solver-context submission/solvers \
  -- python /task/solver/verify-isolation-and-solve.py
uv run task show <command-id> --events --tests
```

Expected outcomes are, in order: init succeeds; baseline/golden validation
succeeds; no-op is `UNRESOLVED` and exits `1`; golden patch is `RESOLVED` and
exits `0`; the isolation command solver is `RESOLVED` and exits `0`.

## Real SWE-bench Pro Ansible task

The primary real demonstration uses frozen dataset row `407`, whose exact
Ansible commit contains no Gitlinks. The official image is digest-pinned and
provides dependencies only; the CLI still imports the public source itself.

```bash
uv run task init bundles/swebench-pro-ansible-d9f186
uv run task validate bundles/swebench-pro-ansible-d9f186
uv run task run bundles/swebench-pro-ansible-d9f186 --solver noop

cp bundles/swebench-pro-ansible-d9f186/evaluation/hidden/golden.patch \
  /tmp/ansible-d9f186-candidate.patch
uv run task run bundles/swebench-pro-ansible-d9f186 \
  --solver patch \
  --patch /tmp/ansible-d9f186-candidate.patch
uv run task show <command-id> --events --tests
```

Expected exits are init `0`, validate `0`, no-op `1`, and resolved patch `0`.
The no-op is a successfully evaluated unresolved command. The trusted copy of
the upstream golden patch traverses the ordinary patch-solver, non-root
workspace, export, raw-tree construction, binary-patch regeneration, exact
round-trip, policy, hidden-conflict, and fresh-evaluator pipeline.

The bundle targets `linux/amd64`. Apple Silicon Docker Desktop may use
emulation; full Ansible tree export makes each candidate run slower than the
small example.

## Preserved OpenLibrary blocker

`bundles/swebench-pro-openlibrary` remains the faithful prior import. Its exact
base tree is recorded as containing `vendor/infogami` and `vendor/js/wmd`
Gitlinks, so `task init` exits `3` with
`SOURCE_SUBMODULE_UNSUPPORTED`. Its provenance and official-image selector
semantics remain verified, but it is not represented as the successful real
CLI demonstration and no submodule support was added.

## Evidence and cleanup

Each command prints a stable command ID and artifact root. Inspect:

```bash
uv run task show <command-id> --events --tests --json
docker ps -a --filter label=io.task-bundle.task-id
docker volume ls --filter label=io.task-bundle.command-id=<command-id>
```

Normal runs leave no labeled containers or command volumes. A locked task image
may remain for repeatable future runs. Do not use `--keep-containers` when
checking cleanup.
