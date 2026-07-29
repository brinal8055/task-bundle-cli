#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "submission/example-bundle"
REAL = ROOT / "bundles/swebench-pro-ansible-d9f186"


def _invoke(task: str, expected_exit: int, *arguments: str) -> dict[str, Any]:
    argv = list(arguments)
    option_index = argv.index("--") if "--" in argv else len(argv)
    argv[option_index:option_index] = ["--json", "--no-colour"]
    completed = subprocess.run(
        [task, *argv],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != expected_exit:
        raise RuntimeError(
            f"task {' '.join(arguments)} exited {completed.returncode}, "
            f"expected {expected_exit}: {completed.stdout}{completed.stderr}"
        )
    payload = json.loads(completed.stdout)
    if not isinstance(payload, dict):
        raise RuntimeError("task JSON output must be an object")
    return payload


def _show(task: str, command_id: str) -> None:
    shown = _invoke(task, 0, "show", command_id, "--events", "--tests")
    command = shown.get("command")
    if not isinstance(command, dict) or command.get("id") != command_id:
        raise RuntimeError(f"task show did not return {command_id}")
    if command.get("command_status") != "succeeded":
        raise RuntimeError(f"task show returned an unsuccessful command: {command_id}")


def _cleanup_generated() -> None:
    for bundle in (EXAMPLE, REAL):
        for name in (".task", "artifacts"):
            path = bundle / name
            if path.exists():
                shutil.rmtree(path)


def _assert_docker_cleanup(command_ids: list[str]) -> None:
    for task_id in ("submission-hello-answer", "swebench-pro-ansible-d9f186"):
        completed = subprocess.run(
            [
                "docker",
                "ps",
                "-aq",
                "--filter",
                f"label=io.task-bundle.task-id={task_id}",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        if completed.stdout.strip():
            raise RuntimeError(f"retained Task Bundle container for {task_id}")
    for command_id in command_ids:
        completed = subprocess.run(
            [
                "docker",
                "volume",
                "ls",
                "-q",
                "--filter",
                f"label=io.task-bundle.command-id={command_id}",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        if completed.stdout.strip():
            raise RuntimeError(f"retained Task Bundle volume for {command_id}")


def main() -> int:
    task = sys.argv[1] if len(sys.argv) == 2 else "task"
    command_ids: list[str] = []
    _cleanup_generated()
    try:
        example_init = _invoke(task, 0, "init", str(EXAMPLE))
        example_validate = _invoke(task, 0, "validate", str(EXAMPLE))
        example_noop = _invoke(task, 1, "run", str(EXAMPLE), "--solver", "noop")
        example_patch = _invoke(
            task,
            0,
            "run",
            str(EXAMPLE),
            "--solver",
            "patch",
            "--patch",
            str(EXAMPLE / "candidates/golden.patch"),
        )
        example_isolation = _invoke(
            task,
            0,
            "run",
            str(EXAMPLE),
            "--solver",
            "command",
            "--solver-context",
            str(ROOT / "submission/solvers"),
            "--",
            "python",
            "/task/solver/verify-isolation-and-solve.py",
        )
        real_init = _invoke(task, 0, "init", str(REAL))
        real_validate = _invoke(task, 0, "validate", str(REAL))
        real_noop = _invoke(task, 1, "run", str(REAL), "--solver", "noop")
        with tempfile.TemporaryDirectory(prefix="task-bundle-real-candidate-") as temporary:
            candidate = Path(temporary) / "golden.patch"
            shutil.copyfile(REAL / "evaluation/hidden/golden.patch", candidate)
            real_resolved = _invoke(
                task,
                0,
                "run",
                str(REAL),
                "--solver",
                "patch",
                "--patch",
                str(candidate),
            )

        results = (
            example_init,
            example_validate,
            example_noop,
            example_patch,
            example_isolation,
            real_init,
            real_validate,
            real_noop,
            real_resolved,
        )
        command_ids = [str(result["command_id"]) for result in results]
        if example_noop.get("resolved") is not False:
            raise RuntimeError("synthetic no-op did not remain unresolved")
        if example_patch.get("resolved") is not True:
            raise RuntimeError("synthetic patch did not resolve")
        if example_isolation.get("resolved") is not True:
            raise RuntimeError("synthetic isolation solver did not resolve")
        if real_validate.get("validation_status") != "valid":
            raise RuntimeError("real validation did not pass")
        if real_noop.get("command_status") != "succeeded":
            raise RuntimeError("real no-op was not a completed command")
        if real_noop.get("resolved") is not False:
            raise RuntimeError("real no-op did not remain unresolved")
        if real_resolved.get("resolved") is not True:
            raise RuntimeError("real golden candidate did not resolve")
        for command_id in command_ids:
            _show(task, command_id)
        _assert_docker_cleanup(command_ids)
        print("verified command IDs:")
        for command_id in command_ids:
            print(command_id)
    finally:
        _cleanup_generated()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
