#!/bin/sh
set -eu

run_real=0
if [ "${1-}" = "--real" ]; then
  run_real=1
  shift
fi
if [ "$#" -ne 0 ]; then
  echo "usage: scripts/verify-submission.sh [--real]" >&2
  exit 2
fi

verify_root=$(mktemp -d)
trap 'rm -rf "$verify_root"' EXIT HUP INT TERM
export UV_CACHE_DIR="$verify_root/uv-cache"

uv sync --frozen --extra dev
uv run ruff check .
uv run mypy src tests
uv run pytest -q
uv build
uv run python scripts/verify-security.py
uv run python scripts/verify-portable-reports.py
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

if [ "$run_real" -eq 1 ]; then
  TASK_BUNDLE_RUN_REAL_DOCKER=1 \
  TASK_BUNDLE_REAL_DOCKER_GO_BASE="${TASK_BUNDLE_REAL_DOCKER_GO_BASE:-golang@sha256:3d699e4d15d0f8f13c9195c0632a16702b8cbdece2955af1c23b37ae5d55a253}" \
  TASK_BUNDLE_REAL_DOCKER_PYTHON_BASE="${TASK_BUNDLE_REAL_DOCKER_PYTHON_BASE:-jefzda/sweap-images@sha256:f9e1f9d428d55a8f26b27d89f29819b79a82b847fd252903c68221b2812ccd04}" \
  TASK_BUNDLE_REAL_DOCKER_PLATFORM="${TASK_BUNDLE_REAL_DOCKER_PLATFORM:-linux/amd64}" \
    uv run python scripts/verify-security.py
  "$verify_root/venv/bin/python" scripts/verify-submission-real.py \
    "$verify_root/venv/bin/task"
fi

find bundles -type d -name __pycache__ -prune -exec rm -rf {} +

if find bundles \
  \( -name .task -o -name artifacts -o -name '*.db' -o -name __pycache__ \) \
  -print | grep .; then
  echo "generated runtime state is present in committed examples" >&2
  exit 1
fi
