import shutil
from collections.abc import Sequence
from pathlib import Path

import pytest

from task_bundle.bundle.loader import load_bundle
from task_bundle.database import Database
from task_bundle.errors import ErrorCode, ErrorContext, TaskBundleError
from task_bundle.image.docker import DockerCommandResult
from task_bundle.image.lock import LOCK_RELATIVE_PATH, load_bundle_lock
from task_bundle.image.runtime import create_runtime_policy
from task_bundle.image.service import InitOptions, InitService
from task_bundle.run.docker import DockerSolver, SolverRequest, validate_patch_input
from task_bundle.run.models import RunOptions, SolverStatus, SolverType
from tests.bundle_helpers import create_bundle
from tests.image_helpers import FakeDockerRunner, StaticSourceFactory


class SolverDockerRunner(FakeDockerRunner):
    def __init__(
        self,
        baseline: Path,
        candidate: Path,
        *,
        solver_exit_code: int = 0,
    ) -> None:
        super().__init__()
        self.baseline = baseline
        self.candidate = candidate
        self.solver_exit_code = solver_exit_code
        self.staged_paths: tuple[str, ...] = ()

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        timeout_seconds: int,
        error_code: ErrorCode,
        phase: str,
        description: str,
        check: bool = True,
        redact: Sequence[str] = (),
        timeout_code: ErrorCode | None = None,
    ) -> DockerCommandResult:
        command = tuple(args)
        if command[0] == "cp" and ":" not in command[1]:
            source = Path(command[1].removesuffix("/."))
            self.staged_paths = tuple(
                sorted(
                    path.relative_to(source).as_posix()
                    for path in source.rglob("*")
                    if not path.is_dir()
                )
            )
        if command[0] == "cp" and ":" in command[1]:
            source = self.baseline if "/opt/task/repo" in command[1] else self.candidate
            destination = Path(command[2])
            for child in source.iterdir():
                target = destination / child.name
                if child.is_symlink():
                    target.symlink_to(child.readlink())
                elif child.is_dir():
                    shutil.copytree(child, target, symlinks=True)
                else:
                    shutil.copy2(child, target)
        if command[:3] == ("exec", "--user", "1000:1000"):
            self.commands.append(command)
            return self._result(
                stdout="solver stdout\n",
                stderr="solver stderr\n" if self.solver_exit_code else "",
                exit_code=self.solver_exit_code,
                check=check,
                error_code=error_code,
                phase=phase,
                description=description,
            )
        return super().run(
            command,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            error_code=error_code,
            phase=phase,
            description=description,
            check=check,
            redact=redact,
            timeout_code=timeout_code,
        )


class TimeoutSolverDockerRunner(SolverDockerRunner):
    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        timeout_seconds: int,
        error_code: ErrorCode,
        phase: str,
        description: str,
        check: bool = True,
        redact: Sequence[str] = (),
        timeout_code: ErrorCode | None = None,
    ) -> DockerCommandResult:
        command = tuple(args)
        if command[:3] == ("exec", "--user", "1000:1000"):
            self.commands.append(command)
            raise TaskBundleError(
                ErrorCode.SOLVER_TIMEOUT,
                "Solver timed out.",
                ErrorContext(
                    phase="solver",
                    expected="Solver completion",
                    actual="timeout",
                    corrective_action="Inspect solver logs.",
                    details={"stdout": "partial\n", "stderr": ""},
                ),
            )
        return super().run(
            command,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            error_code=error_code,
            phase=phase,
            description=description,
            check=check,
            redact=redact,
            timeout_code=timeout_code,
        )


def _request(
    tmp_path: Path,
    options: RunOptions,
    runner: SolverDockerRunner,
) -> SolverRequest:
    bundle_path = create_bundle(tmp_path / "bundle")
    database = Database(tmp_path / "task.db")
    init_runner = FakeDockerRunner()
    InitService(
        database=database,
        cli_version="test",
        source_factory=StaticSourceFactory(runner.baseline),
        docker_factory=lambda home: init_runner,
    ).run(bundle_path, InitOptions())
    bundle = load_bundle(bundle_path)
    return SolverRequest(
        bundle=bundle,
        lock=load_bundle_lock(bundle_path / LOCK_RELATIVE_PATH),
        runtime_policy=create_runtime_policy(bundle.task.environment.runtime),
        command_id="cmd_" + "a" * 32,
        options=options,
        context_root=None,
        context_manifest=None,
        patch_input=None,
        export_root=tmp_path / "export",
    )


def test_command_solver_is_non_root_isolated_and_stages_only_public_context(
    tmp_path: Path,
) -> None:
    baseline = tmp_path / "source"
    baseline.mkdir()
    (baseline / "README.md").write_text("source\n")
    candidate = tmp_path / "candidate"
    shutil.copytree(baseline, candidate)
    (candidate / "answer.txt").write_text("42\n")
    runner = SolverDockerRunner(baseline, candidate)
    request = _request(
        tmp_path,
        RunOptions(
            solver=SolverType.COMMAND,
            command=("python", "/task/solver/solve.py", "--flag"),
        ),
        runner,
    )
    request.export_root.mkdir()

    outcome = DockerSolver(runner).run(request)

    assert outcome.execution.status == SolverStatus.SUCCEEDED
    assert outcome.candidate_manifest is not None
    assert {entry.path for entry in outcome.candidate_manifest.entries} == {
        "README.md",
        "answer.txt",
    }
    create = next(command for command in runner.commands if command[0] == "create")
    create_text = "\n".join(create)
    assert "--network\nnone" in create_text
    assert "--read-only" in create
    assert "--cap-drop\nALL" in create_text
    assert "--cap-add" not in create
    assert "no-new-privileges" in create
    assert "/var/run/docker.sock" not in create_text
    assert str(request.bundle.root) not in create_text
    assert "artifacts" not in create_text
    solver_exec = next(
        command
        for command in runner.commands
        if command[:3] == ("exec", "--user", "1000:1000")
    )
    assert solver_exec[-3:] == (
        "python",
        "/task/solver/solve.py",
        "--flag",
    )
    assert "TASK_DESCRIPTION_FILE=/task/public/description.md" in solver_exec
    assert not any("hidden" in item or "selector" in item for item in solver_exec)
    assert set(runner.staged_paths) == {
        "public/description.md",
        "public/interface.md",
        "public/requirements.md",
    }
    seed = next(
        command
        for command in runner.commands
        if command[:3] == ("exec", "--user", "0:0")
        and any("find /workspace -type d -exec chmod 0777" in item for item in command)
    )
    seed_text = "\n".join(seed)
    assert "find /workspace -type f -exec chmod a+rw" in seed_text
    assert "test ! -w /task/public/description.md" in seed_text
    assert any(command[:2] == ("rm", "--force") for command in runner.commands)
    assert sum(command[:3] == ("volume", "rm", "--force") for command in runner.commands) == 2


def test_solver_nonzero_exit_does_not_export_candidate(tmp_path: Path) -> None:
    baseline = tmp_path / "source"
    baseline.mkdir()
    (baseline / "README.md").write_text("source\n")
    candidate = tmp_path / "candidate"
    shutil.copytree(baseline, candidate)
    runner = SolverDockerRunner(baseline, candidate, solver_exit_code=7)
    request = _request(
        tmp_path,
        RunOptions(solver=SolverType.COMMAND, command=("false",)),
        runner,
    )
    request.export_root.mkdir()

    outcome = DockerSolver(runner).run(request)

    assert outcome.execution.status == SolverStatus.FAILED
    assert outcome.execution.exit_code == 7
    assert outcome.candidate_manifest is None
    assert not any(
        command[0] == "cp" and ":" in command[1] for command in runner.commands
    )


def test_patch_solver_applies_input_as_non_root_and_exports_result(
    tmp_path: Path,
) -> None:
    baseline = tmp_path / "source"
    baseline.mkdir()
    (baseline / "README.md").write_text("old\n")
    candidate = tmp_path / "candidate"
    shutil.copytree(baseline, candidate)
    (candidate / "README.md").write_text("new\n")
    patch = (
        b"diff --git a/README.md b/README.md\n"
        b"--- a/README.md\n"
        b"+++ b/README.md\n"
        b"@@ -1 +1 @@\n-old\n+new\n"
    )
    runner = SolverDockerRunner(baseline, candidate)
    request = _request(
        tmp_path,
        RunOptions(solver=SolverType.PATCH, patch=tmp_path / "candidate.patch"),
        runner,
    )
    request = SolverRequest(
        bundle=request.bundle,
        lock=request.lock,
        runtime_policy=request.runtime_policy,
        command_id=request.command_id,
        options=request.options,
        context_root=None,
        context_manifest=None,
        patch_input=patch,
        export_root=request.export_root,
    )
    request.export_root.mkdir()

    outcome = DockerSolver(runner).run(request)

    assert outcome.execution.status == SolverStatus.SUCCEEDED
    solver_exec = next(
        command
        for command in runner.commands
        if command[:3] == ("exec", "--user", "1000:1000")
    )
    assert solver_exec[-8:] == (
        "git",
        "-c",
        "core.hooksPath=/dev/null",
        "-C",
        "/workspace/repo",
        "apply",
        "--binary",
        "/task/input/candidate.patch",
    )
    assert "input/candidate.patch" in runner.staged_paths


def test_solver_timeout_cleans_resources_and_records_partial_output_details(
    tmp_path: Path,
) -> None:
    baseline = tmp_path / "source"
    baseline.mkdir()
    (baseline / "README.md").write_text("source\n")
    candidate = tmp_path / "candidate"
    shutil.copytree(baseline, candidate)
    runner = TimeoutSolverDockerRunner(baseline, candidate)
    request = _request(
        tmp_path,
        RunOptions(solver=SolverType.COMMAND, command=("sleep", "999")),
        runner,
    )
    request.export_root.mkdir()

    with pytest.raises(TaskBundleError) as caught:
        DockerSolver(runner).run(request)

    assert caught.value.code == ErrorCode.SOLVER_TIMEOUT
    assert caught.value.context.details is not None
    assert caught.value.context.details["container_id"] == "c" * 64
    assert caught.value.context.details["cleaned_up"] is True
    assert caught.value.context.details["stdout"] == "partial\n"
    assert any(command[:2] == ("rm", "--force") for command in runner.commands)
    assert sum(command[:3] == ("volume", "rm", "--force") for command in runner.commands) == 2


def test_patch_input_rejects_symlink_oversize_and_malformed_patch(
    tmp_path: Path,
) -> None:
    regular = tmp_path / "candidate.patch"
    regular.write_bytes(b"not a patch\n")
    link = tmp_path / "candidate-link.patch"
    link.symlink_to(regular.name)

    with pytest.raises(TaskBundleError) as symlink:
        validate_patch_input(link, 1024)
    with pytest.raises(TaskBundleError) as oversized:
        validate_patch_input(regular, 2)
    with pytest.raises(TaskBundleError) as malformed:
        validate_patch_input(regular, 1024)

    assert symlink.value.code == ErrorCode.PATCH_POLICY_ERROR
    assert oversized.value.code == ErrorCode.PATCH_POLICY_ERROR
    assert malformed.value.code == ErrorCode.PATCH_POLICY_ERROR
