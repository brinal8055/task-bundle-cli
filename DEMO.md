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

## Real SWE-bench Pro OpenLibrary task

The faithful import currently demonstrates the product's intentional
submodule boundary: the exact commit contains two Gitlinks, so `task init`
returns `SOURCE_SUBMODULE_UNSUPPORTED`. The remaining commands below are the
intended flow once submodule support is separately designed; they are not
claimed as successful Phase 6 evidence.

```bash
uv run task init bundles/swebench-pro-openlibrary
uv run task validate bundles/swebench-pro-openlibrary
uv run task run bundles/swebench-pro-openlibrary --solver noop

cp bundles/swebench-pro-openlibrary/evaluation/hidden/golden.patch \
  /tmp/openlibrary-candidate.patch
uv run task run bundles/swebench-pro-openlibrary \
  --solver patch \
  --patch /tmp/openlibrary-candidate.patch
```

After a future, explicitly reviewed submodule phase, the no-op must complete as
unresolved with exit `1`. The trusted copy of the
upstream golden patch must traverse the ordinary patch-solver/export/raw-tree/
round-trip/policy/fresh-evaluator pipeline and resolve with exit `0`.

The bundle targets `linux/amd64`. Apple Silicon Docker Desktop may use
emulation and the OpenLibrary image is intentionally much larger than the fast
example.

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
