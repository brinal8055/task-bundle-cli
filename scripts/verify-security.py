#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import sys


def run(*args: str, environment: dict[str, str] | None = None) -> None:
    subprocess.run(args, check=True, env=environment)


def main() -> int:
    selected = [
        "tests/test_bundle_paths.py",
        "tests/test_source_git.py",
        "tests/test_source_materialize.py",
        "tests/test_image_docker.py",
        "tests/test_validation_docker.py",
        "tests/test_validation_result.py",
        "tests/test_run_docker.py",
        "tests/test_run_filesystem.py",
        "tests/test_run_candidate.py",
        "tests/test_run_service.py",
    ]
    run(sys.executable, "-m", "pytest", "-q", *selected)
    if os.environ.get("TASK_BUNDLE_RUN_REAL_DOCKER") == "1":
        environment = dict(os.environ)
        run(
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/test_real_docker_run.py",
            environment=environment,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
