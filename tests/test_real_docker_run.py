import os
import shutil
import subprocess
from pathlib import Path

import pytest

from task_bundle.database import Database
from task_bundle.errors import ErrorCode, TaskBundleError
from task_bundle.image.docker import SystemDockerRunner
from task_bundle.image.service import InitOptions, InitService
from task_bundle.run.models import RunOptions, SolverType
from task_bundle.run.records import RunStore
from task_bundle.run.service import RunService
from task_bundle.validation.service import ValidationOptions, ValidationService
from tests.image_helpers import StaticSourceFactory
from tests.synthetic_validation import create_synthetic_validation_bundle


def test_real_docker_complete_solver_lifecycle_when_configured(
    tmp_path: Path,
) -> None:
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
    tree_sha = _source_tree_sha(source, tmp_path / "tree-repository")
    database = Database(tmp_path / "task.db")
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
            source_factory=StaticSourceFactory(source, tree_sha=tree_sha),
            docker_factory=docker_factory,
        ).run(bundle, InitOptions(no_cache=True))
        image_reference = initialized.image_reference
        ValidationService(
            database=database,
            cli_version="test",
            docker_factory=docker_factory,
        ).run(bundle, ValidationOptions(repeat=2))
        service = RunService(
            database=database,
            cli_version="test",
            docker_factory=docker_factory,
        )

        noop = service.run(bundle, RunOptions(solver=SolverType.NOOP))
        assert not noop.resolved

        golden = service.run(
            bundle,
            RunOptions(
                solver=SolverType.PATCH,
                patch=bundle / "candidates/golden.patch",
            ),
        )
        assert golden.resolved

        partial = service.run(
            bundle,
            RunOptions(
                solver=SolverType.PATCH,
                patch=bundle / "candidates/partial.patch",
            ),
        )
        assert not partial.resolved

        regression = service.run(
            bundle,
            RunOptions(
                solver=SolverType.PATCH,
                patch=bundle / "candidates/regression.patch",
            ),
        )
        assert not regression.resolved

        command = service.run(
            bundle,
            RunOptions(
                solver=SolverType.COMMAND,
                solver_context=tmp_path / "command-solver",
                command=("/bin/sh", "/task/solver/solve.sh"),
            ),
        )
        assert command.resolved

        isolation = service.run(
            bundle,
            RunOptions(
                solver=SolverType.COMMAND,
                solver_context=tmp_path / "hidden-isolation-solver",
                command=("/bin/sh", "/task/solver/solve.sh"),
            ),
        )
        assert isolation.resolved

        with pytest.raises(TaskBundleError) as conflict:
            service.run(
                bundle,
                RunOptions(
                    solver=SolverType.PATCH,
                    patch=bundle / "candidates/hidden-conflict.patch",
                ),
            )
        assert conflict.value.code == ErrorCode.PATCH_CONFLICT

        with pytest.raises(TaskBundleError) as malformed:
            service.run(
                bundle,
                RunOptions(
                    solver=SolverType.PATCH,
                    patch=bundle / "candidates/malformed.patch",
                ),
            )
        assert malformed.value.exit_code == 6

        with database.connect() as connection:
            all_run_ids = [
                row["id"]
                for row in connection.execute(
                    "SELECT id FROM commands WHERE command_type = 'run' ORDER BY started_at"
                ).fetchall()
            ]
        assert len(all_run_ids) == 8
        for command_id in all_run_ids:
            shown = RunStore(database).show(
                command_id,
                include_events=True,
                include_tests=True,
            )
            assert shown.command["command_status"] != "running"

        runner = runners[-1]
        containers = runner.run(
            (
                "ps",
                "-aq",
                "--filter",
                "label=io.task-bundle.task-id=synthetic-go-calculator",
            ),
            cwd=tmp_path,
            timeout_seconds=30,
            error_code=ErrorCode.CLEANUP_ERROR,
            phase="test-cleanup",
            description="list retained synthetic containers",
        )
        assert not containers.stdout.strip()
        for command_id in all_run_ids:
            volumes = runner.run(
                (
                    "volume",
                    "ls",
                    "-q",
                    "--filter",
                    f"label=io.task-bundle.command-id={command_id}",
                ),
                cwd=tmp_path,
                timeout_seconds=30,
                error_code=ErrorCode.CLEANUP_ERROR,
                phase="test-cleanup",
                description="list retained synthetic volumes",
            )
            assert not volumes.stdout.strip()
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
                description="remove synthetic solver image",
                check=False,
            )


def _source_tree_sha(source: Path, repository: Path) -> str:
    shutil.copytree(source, repository)
    for args in (
        ("init", "-q"),
        ("add", "-A"),
    ):
        subprocess.run(
            ["git", *args],
            cwd=repository,
            check=True,
            capture_output=True,
            shell=False,
        )
    return subprocess.run(
        ["git", "write-tree"],
        cwd=repository,
        check=True,
        capture_output=True,
        shell=False,
        text=True,
    ).stdout.strip()
