import os
import textwrap
from pathlib import Path
from typing import Any

import pytest

from task_bundle.bundle.loader import load_bundle
from task_bundle.database import Database
from task_bundle.errors import ErrorCode, TaskBundleError
from task_bundle.image.docker import SystemDockerRunner
from task_bundle.image.service import InitOptions, InitService
from task_bundle.validation.models import ValidationStatus
from task_bundle.validation.service import ValidationOptions, ValidationService
from tests.bundle_helpers import write_task
from tests.image_helpers import StaticSourceFactory
from tests.synthetic_validation import create_synthetic_validation_bundle


def test_real_docker_synthetic_validation_when_configured(tmp_path: Path) -> None:
    base_image = os.environ.get("TASK_BUNDLE_REAL_DOCKER_GO_BASE")
    if not base_image:
        pytest.skip(
            "set TASK_BUNDLE_REAL_DOCKER_GO_BASE to a local digest-pinned Go image"
        )
    platform = os.environ.get("TASK_BUNDLE_REAL_DOCKER_PLATFORM", "linux/amd64")
    bundle, source = create_synthetic_validation_bundle(
        tmp_path,
        base_image=base_image,
        platform=platform,
    )
    database = Database(tmp_path / "task.db")
    runners: list[SystemDockerRunner] = []

    def docker_factory(home: Path) -> SystemDockerRunner:
        runner = SystemDockerRunner.create(home)
        runners.append(runner)
        return runner

    init_service = InitService(
        database=database,
        cli_version="test",
        source_factory=StaticSourceFactory(source),
        docker_factory=docker_factory,
    )
    image_reference: str | None = None
    try:
        initialized = init_service.run(bundle, InitOptions(no_cache=True))
        image_reference = initialized.image_reference
        result = ValidationService(
            database=database,
            cli_version="test",
            docker_factory=docker_factory,
        ).run(bundle, ValidationOptions(repeat=2))

        assert result.validation_status == ValidationStatus.VALID
        assert len(result.evaluations) == 4
        assert len({item.container_id for item in result.evaluations}) == 4
        assert all(item.cleaned_up for item in result.evaluations)
    except TaskBundleError as error:
        pytest.fail(f"{error.code}: {error.context.actual}")
    finally:
        if image_reference is not None and runners:
            runners[-1].run(
                ("image", "rm", "--force", image_reference),
                cwd=tmp_path,
                timeout_seconds=60,
                error_code=ErrorCode.CLEANUP_ERROR,
                phase="test-cleanup",
                description="remove synthetic validation image",
                check=False,
            )


def test_real_docker_python_validation_boundary_when_configured(tmp_path: Path) -> None:
    base_image = os.environ.get("TASK_BUNDLE_REAL_DOCKER_PYTHON_BASE")
    if not base_image:
        pytest.skip(
            "set TASK_BUNDLE_REAL_DOCKER_PYTHON_BASE to a local digest-pinned "
            "Python image containing Git"
        )
    platform = os.environ.get("TASK_BUNDLE_REAL_DOCKER_PLATFORM", "linux/amd64")
    bundle, source = _create_python_validation_bundle(
        tmp_path,
        base_image=base_image,
        platform=platform,
    )
    database = Database(tmp_path / "python-task.db")
    runners: list[SystemDockerRunner] = []

    def docker_factory(home: Path) -> SystemDockerRunner:
        runner = SystemDockerRunner.create(home)
        runners.append(runner)
        return runner

    image_reference: str | None = None
    try:
        initialized = InitService(
            database=database,
            cli_version="test",
            source_factory=StaticSourceFactory(source),
            docker_factory=docker_factory,
        ).run(bundle, InitOptions(no_cache=True))
        image_reference = initialized.image_reference
        result = ValidationService(
            database=database,
            cli_version="test",
            docker_factory=docker_factory,
        ).run(bundle, ValidationOptions(repeat=2))
        assert result.validation_status == ValidationStatus.VALID
        assert len(result.evaluations) == 4
        assert all(item.cleaned_up for item in result.evaluations)
    except TaskBundleError as error:
        pytest.fail(f"{error.code}: {error.context.actual}")
    finally:
        if image_reference is not None and runners:
            runners[-1].run(
                ("image", "rm", "--force", image_reference),
                cwd=tmp_path,
                timeout_seconds=60,
                error_code=ErrorCode.CLEANUP_ERROR,
                phase="test-cleanup",
                description="remove Python validation image",
                check=False,
            )


def _create_python_validation_bundle(
    root: Path,
    *,
    base_image: str,
    platform: str,
) -> tuple[Path, Path]:
    bundle = root / "python-bundle"
    source = root / "python-source"
    for directory in (
        source,
        bundle / "public",
        bundle / "environment/context",
        bundle / "evaluation/hidden",
    ):
        directory.mkdir(parents=True, exist_ok=True)
    (source / "calculator.py").write_text(
        "def add(a: int, b: int) -> int:\n"
        "    return a - b\n\n"
        "def subtract(a: int, b: int) -> int:\n"
        "    return a - b\n",
    )
    (bundle / "public/description.md").write_text("Correct calculator addition.\n")
    (bundle / "environment/Dockerfile").write_text(
        f"FROM {base_image}\n"
        "COPY repo/ /opt/task/repo/\n"
        "WORKDIR /workspace/repo\n"
        "ENTRYPOINT []\n"
        "CMD []\n",
    )
    (bundle / "evaluation/hidden/test.patch").write_text(
        "diff --git a/hidden_cases.py b/hidden_cases.py\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/hidden_cases.py\n"
        "@@ -0,0 +1,2 @@\n"
        "+POSITIVE = (2, 3, 5)\n"
        "+NEGATIVE = (-2, -3, -5)\n",
    )
    (bundle / "evaluation/hidden/golden.patch").write_text(
        "diff --git a/calculator.py b/calculator.py\n"
        "--- a/calculator.py\n"
        "+++ b/calculator.py\n"
        "@@ -1,5 +1,5 @@\n"
        " def add(a: int, b: int) -> int:\n"
        "-    return a - b\n"
        "+    return a + b\n"
        " \n"
        " def subtract(a: int, b: int) -> int:\n"
        "     return a - b\n",
    )
    runner = bundle / "evaluation/adapter.py"
    runner.write_text(_python_runner())
    runner.chmod(0o755)
    mapping: dict[str, Any] = {
        "schema_version": "1",
        "task": {"id": "synthetic-python-calculator", "title": "Python calculator"},
        "repository": {
            "url": "https://example.invalid/python-calculator.git",
            "commit": "b" * 40,
            "submodules": False,
        },
        "public": {"description": "public/description.md"},
        "environment": {
            "type": "dockerfile",
            "dockerfile": "environment/Dockerfile",
            "context": "environment/context",
            "platform": platform,
            "build": {"network": False},
            "runtime": {
                "working_directory": "/workspace/repo",
                "user": "1000:1000",
                "network": "none",
                "timeout_seconds": 300,
                "cpus": 2,
                "memory_mb": 2048,
                "pids_limit": 128,
                "read_only_root": True,
                "tmpfs": ["/tmp:size=512m"],
            },
        },
        "evaluation": {
            "test_patch": "evaluation/hidden/test.patch",
            "golden_patch": "evaluation/hidden/golden.patch",
            "runner": {
                "build_plan": [
                    "/usr/local/bin/python3",
                    "/evaluation/harness/adapter.py",
                    "build-plan",
                ],
                "parse_result": [
                    "/usr/local/bin/python3",
                    "/evaluation/harness/adapter.py",
                    "parse-result",
                ],
                "adapter_contract_version": "2",
                "result_schema_version": "1",
            },
            "pass_to_pass": [{"selector": "TestSubtract"}],
            "fail_to_pass": [
                {"selector": "TestAddPositive", "baseline_statuses": ["failed"]},
                {"selector": "TestAddNegative", "baseline_statuses": ["failed"]},
            ],
            "repeat": 2,
        },
    }
    write_task(bundle, mapping)
    load_bundle(bundle)
    return bundle, source


def _python_runner() -> str:
    return textwrap.dedent(
        """\
        import json
        import sys
        from datetime import UTC, datetime
        from pathlib import Path

        PLAN = Path("/evaluation/input/plan.json")
        CAPTURED = Path("/evaluation/trusted/executions.json")


        def selectors(plan):
            return [
                *(item["selector"] for item in plan["pass_to_pass"]),
                *(item["selector"] for item in plan["fail_to_pass"]),
            ]


        def build_plan():
            plan = json.loads(PLAN.read_text())
            executions = [
                {
                    "execution_id": f"selector-{index:03d}",
                    "requested_selectors": [selector],
                    "argv": [
                        sys.executable,
                        "/evaluation/harness/adapter.py",
                        "run-selector",
                        selector,
                    ],
                    "timeout_seconds": plan["timeout_seconds"],
                }
                for index, selector in enumerate(selectors(plan), start=1)
            ]
            print(json.dumps({"schema_version": "2", "executions": executions}))
            return 0


        def run_selector(selector):
            sys.path.insert(0, "/workspace/repo")
            from calculator import add, subtract
            from hidden_cases import NEGATIVE, POSITIVE

            checks = {
                "TestSubtract": subtract(5, 3) == 2,
                "TestAddPositive": add(*POSITIVE[:2]) == POSITIVE[2],
                "TestAddNegative": add(*NEGATIVE[:2]) == NEGATIVE[2],
            }
            return 0 if checks.get(selector, False) else 1


        def parse_result():
            captured = json.loads(CAPTURED.read_text())["executions"]
            tests = []
            collection_succeeded = True
            for execution in captured:
                selectors = execution["requested_selectors"]
                if len(selectors) != 1:
                    raise ValueError("synthetic adapter requires one selector per execution")
                if execution["stdout_truncated"] or execution["stderr_truncated"]:
                    raise ValueError("captured test output was truncated")
                selector = selectors[0]
                if execution["timed_out"]:
                    status = "timeout"
                elif execution["exit_code"] == 0:
                    status = "passed"
                elif execution["exit_code"] == 1:
                    status = "failed"
                else:
                    status = "error"
                    collection_succeeded = False
                tests.append(
                    {
                        "requested_selector": selector,
                        "observed_id": selector,
                        "status": status,
                        "duration_ms": execution["duration_ms"],
                        "message": (execution["stderr"] or execution["stdout"])[:16384]
                        or None,
                    }
                )
            result = {
                "schema_version": "1",
                "framework": "synthetic-python-exit-code",
                "harness_status": (
                    "completed" if collection_succeeded else "collection_failed"
                ),
                "collection_succeeded": collection_succeeded,
                "execution_started": bool(captured),
                "command": ["per-selector", "synthetic-python"],
                "started_at": captured[0]["started_at"],
                "finished_at": captured[-1]["finished_at"],
                "exit_code": max(
                    (
                        item["exit_code"]
                        for item in captured
                        if item["exit_code"] is not None
                    ),
                    default=0,
                ),
                "tests": tests,
            }
            print(json.dumps(result))
            return 0


        mode = sys.argv[1]
        if mode == "build-plan":
            raise SystemExit(build_plan())
        if mode == "run-selector":
            raise SystemExit(run_selector(sys.argv[2]))
        if mode == "parse-result":
            raise SystemExit(parse_result())
        raise SystemExit(2)
        """
    )
