# Submission demonstration

From a clean checkout with Git, Docker, Python 3.12, and `uv`:

```bash
uv sync --frozen --extra dev
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

Expected exits:

```text
init       0
validate   0
noop       1
resolved   0
```

The selected task is immutable SWE-bench Pro test row `407` at dataset
revision `7ab5114912baf22bb098818e604c02fe7ad2c11f`. The no-op is a completed
unresolved evaluation, not an infrastructure failure. The patch command must
use a trusted copy outside `evaluation/hidden`; direct hidden-patch evaluation
is not the candidate flow.

For the fast synthetic and isolation demonstration, follow the root
`DEMO.md`. OpenLibrary remains a blocked no-submodule boundary case documented
in `reports/openlibrary-blocker.md`.
