#!/bin/sh
set -eu

verify_root=$(mktemp -d)
trap 'rm -rf "$verify_root"' EXIT HUP INT TERM
export UV_CACHE_DIR="$verify_root/uv-cache"

uv sync --frozen --extra dev
uv run ruff check .
uv run mypy
uv run pytest -q
uv build
uv run python scripts/verify-security.py
git diff --check

python3.12 -m venv "$verify_root/venv"
"$verify_root/venv/bin/pip" install --disable-pip-version-check \
  dist/task_bundle_cli-0.1.0-py3-none-any.whl
"$verify_root/venv/bin/task" --help >/dev/null
"$verify_root/venv/bin/task" --version >/dev/null
"$verify_root/venv/bin/task" init --help >/dev/null
"$verify_root/venv/bin/task" validate --help >/dev/null
"$verify_root/venv/bin/task" run --help >/dev/null
"$verify_root/venv/bin/task" show --help >/dev/null

if find bundles submission/example-bundle \
  \( -name .task -o -name artifacts -o -name '*.db' -o -name __pycache__ \) \
  -print | grep .; then
  echo "generated runtime state is present in committed examples" >&2
  exit 1
fi
