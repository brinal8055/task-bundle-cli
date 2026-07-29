from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

from task_bundle.bundle.loader import load_bundle
from task_bundle.database import Database
from task_bundle.errors import ErrorCode, TaskBundleError
from task_bundle.image.docker import DockerCommandResult
from task_bundle.image.lock import LOCK_RELATIVE_PATH, load_bundle_lock
from task_bundle.image.runtime import create_runtime_policy
from task_bundle.image.service import InitOptions, InitService
from task_bundle.models import (
    EvaluationPhase,
    EvaluationPlan,
    HarnessStatus,
    NormalizedResult,
)
from task_bundle.models import TestResult as ResultItem
from task_bundle.models import TestStatus as ResultStatus
from task_bundle.validation.docker import (
    DockerEvaluator,
    EvaluationRequest,
)
from tests.bundle_helpers import create_bundle, read_task, write_task
from tests.image_helpers import FakeDockerRunner, StaticSourceFactory


class EvaluatorDockerRunner(FakeDockerRunner):
    def __init__(
        self,
        result: NormalizedResult,
        *,
        write_result: bool = True,
        fail_cleanup: bool = False,
        unsafe_result_parent: bool = False,
    ) -> None:
        super().__init__()
        self.result = result
        self.write_result = write_result
        self.fail_cleanup = fail_cleanup
        self.unsafe_result_parent = unsafe_result_parent
        self.staged: dict[str, tuple[tuple[str, int], ...]] = {}

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
        if command[0] == "cp" and not command[1].startswith("c" * 64):
            source = Path(command[1].removesuffix("/."))
            self.staged[command[2].split(":", 1)[1]] = tuple(
                sorted(
                    (
                        path.relative_to(source).as_posix(),
                        path.stat().st_mode & 0o777,
                    )
                    for path in source.rglob("*")
                    if path.is_file()
                )
            )
        if (
            command[0] == "cp"
            and command[1].startswith("c" * 64)
            and self.write_result
        ):
            output = Path(command[2])
            output.mkdir(parents=True, exist_ok=True)
            result_root = output
            if self.unsafe_result_parent:
                result_root = output.parent / "escaping-output"
                result_root.mkdir()
                (output / "nested").symlink_to(result_root, target_is_directory=True)
            (result_root / "results.json").write_text(
                self.result.model_dump_json(),
                encoding="utf-8",
            )
        if self.fail_cleanup and command[:2] == ("volume", "rm"):
            return self._result(
                stderr="simulated cleanup failure",
                exit_code=1,
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


def _patch(path: Path, target: str) -> None:
    path.write_text(
        f"diff --git a/{target} b/{target}\n"
        f"--- a/{target}\n"
        f"+++ b/{target}\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n",
        encoding="utf-8",
    )


def test_docker_evaluator_stages_only_phase_inputs_and_uses_restricted_storage(
    tmp_path: Path,
) -> None:
    bundle_path = create_bundle(tmp_path / "bundle")
    _patch(bundle_path / "evaluation/hidden/test.patch", "tests/hidden_test.py")
    _patch(bundle_path / "evaluation/hidden/golden.patch", "calculator.go")
    database = Database(tmp_path / "task.db")
    init_runner = FakeDockerRunner()
    InitService(
        database=database,
        cli_version="test",
        source_factory=StaticSourceFactory(tmp_path / "source"),
        docker_factory=lambda home: init_runner,
    ).run(bundle_path, InitOptions())
    bundle = load_bundle(bundle_path)
    lock = load_bundle_lock(bundle_path / LOCK_RELATIVE_PATH)
    started = datetime.now(UTC)
    normalized = NormalizedResult(
        schema_version="1",
        framework="fake",
        harness_status=HarnessStatus.COMPLETED,
        collection_succeeded=True,
        execution_started=True,
        command=["fake"],
        started_at=started,
        finished_at=started,
        exit_code=0,
        tests=[
            ResultItem(
                requested_selector="tests/test_api.py::test_existing",
                status=ResultStatus.PASSED,
            ),
            ResultItem(
                requested_selector="tests/test_api.py::test_create",
                status=ResultStatus.FAILED,
            ),
        ],
    )
    runner = EvaluatorDockerRunner(normalized)
    plan = EvaluationPlan(
        phase=EvaluationPhase.BASELINE,
        repeat_index=1,
        pass_to_pass=bundle.task.evaluation.pass_to_pass,
        fail_to_pass=bundle.task.evaluation.fail_to_pass,
        timeout_seconds=60,
    )

    execution = DockerEvaluator(runner).run(
        EvaluationRequest(
            bundle=bundle,
            lock=lock,
            runtime_policy=create_runtime_policy(bundle.task.environment.runtime),
            command_id="cmd_" + "a" * 32,
            plan=plan,
        )
    )

    create = next(command for command in runner.commands if command[0] == "create")
    create_text = "\n".join(create)
    assert "--network\nnone" in create_text
    assert "--read-only" in create
    assert "--cap-drop\nALL" in create_text
    assert "--cap-add" not in create
    assert "no-new-privileges" in create
    assert "/var/run/docker.sock" not in create_text
    assert str(bundle.root) not in create_text
    assert "artifacts" not in create_text
    assert "type=volume" in create_text
    assert f"io.task-bundle.task-id={bundle.task.task.id}" in create
    assert f"io.task-bundle.image-id={lock.image_id}" in create
    input_files = dict(runner.staged["/evaluation/input"])
    harness_files = dict(runner.staged["/evaluation/harness"])
    assert set(input_files) == {"plan.json", "task-metadata.json", "test.patch"}
    assert "golden.patch" not in input_files
    assert not any("hidden" in path for path in harness_files)
    assert harness_files["prepare.sh"] == 0o755
    user_exec = [
        command
        for command in runner.commands
        if command[:3] == ("exec", "--user", "1000:1000")
    ]
    assert user_exec
    seed = next(
        command
        for command in runner.commands
        if command[:3] == ("exec", "--user", "0:0")
        and "git -C /workspace/repo init" in command[-1]
    )
    assert "/opt/task/repo" in seed[-1]
    runtime_permissions = next(
        command
        for command in runner.commands
        if command[:3] == ("exec", "--user", "0:0")
        and any("rm -rf /workspace/repo/.git" in item for item in command)
    )
    runtime_permissions_text = "\n".join(runtime_permissions)
    assert "find /workspace -type d -exec chmod 0777" in runtime_permissions_text
    assert "chmod 0777 /evaluation/output" in runtime_permissions_text
    assert execution.cleaned_up
    assert any(command[:2] == ("rm", "--force") for command in runner.commands)
    assert sum(command[:3] == ("volume", "rm", "--force") for command in runner.commands) == 2

    missing_result = EvaluatorDockerRunner(normalized, write_result=False)
    with pytest.raises(TaskBundleError) as missing:
        DockerEvaluator(missing_result).run(
            EvaluationRequest(
                bundle=bundle,
                lock=lock,
                runtime_policy=create_runtime_policy(
                    bundle.task.environment.runtime
                ),
                command_id="cmd_" + "b" * 32,
                plan=plan,
            )
        )
    assert missing.value.code == ErrorCode.TEST_RESULT_MISSING
    assert any(
        command[:2] == ("rm", "--force")
        for command in missing_result.commands
    )
    assert (
        sum(
            command[:3] == ("volume", "rm", "--force")
            for command in missing_result.commands
        )
        == 2
    )

    cleanup_failure = EvaluatorDockerRunner(normalized, fail_cleanup=True)
    with pytest.raises(TaskBundleError) as cleanup:
        DockerEvaluator(cleanup_failure).run(
            EvaluationRequest(
                bundle=bundle,
                lock=lock,
                runtime_policy=create_runtime_policy(
                    bundle.task.environment.runtime
                ),
                command_id="cmd_" + "c" * 32,
                plan=plan,
            )
        )
    assert cleanup.value.code == ErrorCode.VALIDATION_CLEANUP_ERROR


def test_docker_evaluator_rejects_symlinked_result_parent(tmp_path: Path) -> None:
    bundle_path = create_bundle(tmp_path / "bundle")
    mapping = read_task(bundle_path)
    mapping["evaluation"]["runner"]["result_file"] = (
        "/evaluation/output/nested/results.json"
    )
    write_task(bundle_path, mapping)
    _patch(bundle_path / "evaluation/hidden/test.patch", "tests/hidden_test.py")
    _patch(bundle_path / "evaluation/hidden/golden.patch", "calculator.go")
    database = Database(tmp_path / "task.db")
    init_runner = FakeDockerRunner()
    InitService(
        database=database,
        cli_version="test",
        source_factory=StaticSourceFactory(tmp_path / "source"),
        docker_factory=lambda home: init_runner,
    ).run(bundle_path, InitOptions())
    bundle = load_bundle(bundle_path)
    lock = load_bundle_lock(bundle_path / LOCK_RELATIVE_PATH)
    now = datetime.now(UTC)
    result = NormalizedResult(
        schema_version="1",
        framework="fake",
        harness_status=HarnessStatus.COMPLETED,
        collection_succeeded=True,
        execution_started=True,
        command=["fake"],
        started_at=now,
        finished_at=now,
        exit_code=0,
        tests=[
            ResultItem(
                requested_selector="tests/test_api.py::test_existing",
                status=ResultStatus.PASSED,
            ),
            ResultItem(
                requested_selector="tests/test_api.py::test_create",
                status=ResultStatus.FAILED,
            ),
        ],
    )
    plan = EvaluationPlan(
        phase=EvaluationPhase.BASELINE,
        repeat_index=1,
        pass_to_pass=bundle.task.evaluation.pass_to_pass,
        fail_to_pass=bundle.task.evaluation.fail_to_pass,
        timeout_seconds=60,
    )

    with pytest.raises(TaskBundleError) as caught:
        DockerEvaluator(
            EvaluatorDockerRunner(result, unsafe_result_parent=True)
        ).run(
            EvaluationRequest(
                bundle=bundle,
                lock=lock,
                runtime_policy=create_runtime_policy(
                    bundle.task.environment.runtime
                ),
                command_id="cmd_" + "d" * 32,
                plan=plan,
            )
        )

    assert caught.value.code == ErrorCode.TEST_RESULT_SCHEMA_ERROR


def test_candidate_patch_is_applied_before_hidden_patch_without_golden(
    tmp_path: Path,
) -> None:
    bundle_path = create_bundle(tmp_path / "bundle")
    _patch(bundle_path / "evaluation/hidden/test.patch", "tests/hidden_test.py")
    _patch(bundle_path / "evaluation/hidden/golden.patch", "calculator.go")
    database = Database(tmp_path / "task.db")
    init_runner = FakeDockerRunner()
    InitService(
        database=database,
        cli_version="test",
        source_factory=StaticSourceFactory(tmp_path / "source"),
        docker_factory=lambda home: init_runner,
    ).run(bundle_path, InitOptions())
    bundle = load_bundle(bundle_path)
    lock = load_bundle_lock(bundle_path / LOCK_RELATIVE_PATH)
    now = datetime.now(UTC)
    normalized = NormalizedResult(
        schema_version="1",
        framework="fake",
        harness_status=HarnessStatus.COMPLETED,
        collection_succeeded=True,
        execution_started=True,
        command=["fake"],
        started_at=now,
        finished_at=now,
        exit_code=0,
        tests=[
            ResultItem(
                requested_selector="tests/test_api.py::test_existing",
                status=ResultStatus.PASSED,
            ),
            ResultItem(
                requested_selector="tests/test_api.py::test_create",
                status=ResultStatus.PASSED,
            ),
        ],
    )
    candidate_path = tmp_path / "candidate.patch"
    _patch(candidate_path, "README.md")
    runner = EvaluatorDockerRunner(normalized)
    plan = EvaluationPlan(
        phase=EvaluationPhase.CANDIDATE,
        repeat_index=1,
        pass_to_pass=bundle.task.evaluation.pass_to_pass,
        fail_to_pass=bundle.task.evaluation.fail_to_pass,
        timeout_seconds=60,
    )

    DockerEvaluator(runner).run(
        EvaluationRequest(
            bundle=bundle,
            lock=lock,
            runtime_policy=create_runtime_policy(bundle.task.environment.runtime),
            command_id="cmd_" + "e" * 32,
            plan=plan,
            candidate_patch=candidate_path.read_bytes(),
        )
    )

    assert set(dict(runner.staged["/evaluation/input"])) == {
        "candidate.patch",
        "plan.json",
        "task-metadata.json",
        "test.patch",
    }
    patch_commands = [
        command[-1]
        for command in runner.commands
        if command[:3] == ("exec", "--user", "0:0")
        and "apply" in command
        and command[-1].endswith(".patch")
    ]
    assert patch_commands == [
        "/evaluation/input/candidate.patch",
        "/evaluation/input/candidate.patch",
        "/evaluation/input/test.patch",
        "/evaluation/input/test.patch",
    ]
    assert not any("golden.patch" in item for command in runner.commands for item in command)
