import json
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
    CapturedEvaluationEvidence,
    DockerEvaluator,
    EvaluationRequest,
)
from tests.bundle_helpers import create_bundle
from tests.image_helpers import FakeDockerRunner, StaticSourceFactory


class EvaluatorDockerRunner(FakeDockerRunner):
    def __init__(
        self,
        result: NormalizedResult,
        *,
        write_result: bool = True,
        parser_exit_code: int = 0,
        fail_cleanup: bool = False,
        execution_plan_payload: dict[str, object] | None = None,
        fail_description: str | None = None,
        fail_occurrence: int = 1,
        interrupt_description: str | None = None,
    ) -> None:
        super().__init__()
        self.result = result
        self.write_result = write_result
        self.parser_exit_code = parser_exit_code
        self.fail_cleanup = fail_cleanup
        self.execution_plan_payload = execution_plan_payload
        self.fail_description = fail_description
        self.fail_occurrence = fail_occurrence
        self.interrupt_description = interrupt_description
        self.description_counts: dict[str, int] = {}
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
        self.description_counts[description] = (
            self.description_counts.get(description, 0) + 1
        )
        if description == self.interrupt_description:
            raise KeyboardInterrupt
        if (
            description == self.fail_description
            and self.description_counts[description] == self.fail_occurrence
        ):
            return self._result(
                stderr="injected boundary failure",
                exit_code=7,
                check=check,
                error_code=error_code,
                phase=phase,
                description=description,
            )
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
            command[:3] == ("exec", "--user", "0:0")
            and command[-1] == "/evaluation/harness/run-tests.sh"
        ):
            if self.execution_plan_payload is not None:
                return self._result(
                    stdout=json.dumps(self.execution_plan_payload)
                )
            selectors = [item.requested_selector for item in self.result.tests]
            executions = [{
                "execution_id": "group-001",
                "requested_selectors": selectors,
                "argv": ["/test-adapter", *selectors],
                "timeout_seconds": 60,
            }]
            return self._result(
                stdout=json.dumps(
                    {"schema_version": "2", "executions": executions}
                )
            )
        if (
            command[:3] == ("exec", "--user", "1000:1000")
            and "/test-adapter" in command
        ):
            candidate_selectors = command[command.index("/test-adapter") + 1 :]
            items = [
                result
                for result in self.result.tests
                if result.requested_selector in candidate_selectors
            ]
            return self._result(
                exit_code=(
                    0
                    if all(item.status == ResultStatus.PASSED for item in items)
                    else 1
                ),
                check=check,
                error_code=error_code,
                phase=phase,
                description=description,
            )
        if command[0] == "inspect":
            self.commands.append(command)
            return self._result(stdout="false 0 no\n")
        if command[:2] == ("start", "--attach"):
            return self._result(
                stdout=self.result.model_dump_json() if self.write_result else "",
                stderr=(
                    "simulated trusted parser failure"
                    if self.parser_exit_code
                    else ""
                ),
                exit_code=self.parser_exit_code,
                check=check,
                error_code=error_code,
                phase=phase,
                description=description,
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
    assert "/evaluation/output" not in runtime_permissions_text
    parser_create = next(
        command
        for command in runner.commands
        if command[0] == "create" and "io.task-bundle.role=trusted-parser" in command
    )
    assert f"type=volume,source={execution.evaluation_storage_id}," in "\n".join(
        parser_create
    )
    assert "target=/evaluation,readonly" in "\n".join(parser_create)
    assert "--user\n65532:65532" in "\n".join(parser_create)
    assert any(command[0] == "stop" for command in runner.commands)
    assert any(
        command[0] == "inspect"
        and any("{{.HostConfig.RestartPolicy.Name}}" in item for item in command)
        for command in runner.commands
    )
    assert execution.cleaned_up
    assert any(command[:2] == ("rm", "--force") for command in runner.commands)
    assert sum(command[:2] == ("rm", "--force") for command in runner.commands) == 2
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
    assert missing.value.code == ErrorCode.TEST_PARSE_ERROR
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

    failed_parser = EvaluatorDockerRunner(normalized, parser_exit_code=2)
    persisted_evidence: list[CapturedEvaluationEvidence] = []
    with pytest.raises(TaskBundleError) as parser_failure:
        DockerEvaluator(failed_parser).run(
            EvaluationRequest(
                bundle=bundle,
                lock=lock,
                runtime_policy=create_runtime_policy(
                    bundle.task.environment.runtime
                ),
                command_id="cmd_" + "d" * 32,
                plan=plan,
                evidence_sink=persisted_evidence.append,
            )
        )
    assert parser_failure.value.code == ErrorCode.TEST_PARSE_ERROR
    assert len(persisted_evidence) == 1
    assert len(persisted_evidence[0].captured_executions.executions) == 1
    assert (
        persisted_evidence[0]
        .captured_executions.executions[0]
        .candidate_processes_terminated
    )
    assert (
        sum(
            command[:2] == ("rm", "--force")
            for command in failed_parser.commands
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


@pytest.mark.parametrize(
    ("payload", "error_code"),
    [
        (
            {
                "schema_version": "1",
                "executions": [
                    {
                        "requested_selector": "tests/test_api.py::test_existing",
                        "argv": ["pytest"],
                        "timeout_seconds": 60,
                    }
                ],
            },
            ErrorCode.ADAPTER_CONTRACT_UNSUPPORTED,
        ),
        (
            {
                "schema_version": "2",
                "executions": [
                    {
                        "execution_id": "missing",
                        "requested_selectors": [
                            "tests/test_api.py::test_existing"
                        ],
                        "argv": ["pytest"],
                        "timeout_seconds": 60,
                    }
                ],
            },
            ErrorCode.TEST_RESULT_INCOMPLETE,
        ),
        (
            {
                "schema_version": "2",
                "executions": [
                    {
                        "execution_id": "unknown",
                        "requested_selectors": [
                            "tests/test_api.py::test_existing",
                            "tests/test_api.py::test_create",
                            "tests/test_api.py::test_unknown",
                        ],
                        "argv": ["pytest"],
                        "timeout_seconds": 60,
                    }
                ],
            },
            ErrorCode.TEST_RESULT_INCOMPLETE,
        ),
        (
            {
                "schema_version": "2",
                "executions": [
                    {
                        "execution_id": "timeout",
                        "requested_selectors": [
                            "tests/test_api.py::test_existing",
                            "tests/test_api.py::test_create",
                        ],
                        "argv": ["pytest"],
                        "timeout_seconds": 61,
                    }
                ],
            },
            ErrorCode.TEST_RESULT_SCHEMA_ERROR,
        ),
        (
            {
                "schema_version": "2",
                "executions": [
                    {
                        "execution_id": "unknown-field",
                        "requested_selectors": [
                            "tests/test_api.py::test_existing",
                            "tests/test_api.py::test_create",
                        ],
                        "argv": ["pytest"],
                        "timeout_seconds": 60,
                        "shell": False,
                    }
                ],
            },
            ErrorCode.TEST_RESULT_SCHEMA_ERROR,
        ),
    ],
)
def test_docker_evaluator_rejects_invalid_adapter_contracts(
    tmp_path: Path,
    payload: dict[str, object],
    error_code: ErrorCode,
) -> None:
    bundle_path = create_bundle(tmp_path / "bundle")
    _patch(bundle_path / "evaluation/hidden/test.patch", "tests/hidden_test.py")
    _patch(bundle_path / "evaluation/hidden/golden.patch", "calculator.go")
    database = Database(tmp_path / "task.db")
    InitService(
        database=database,
        cli_version="test",
        source_factory=StaticSourceFactory(tmp_path / "source"),
        docker_factory=lambda home: FakeDockerRunner(),
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
    runner = EvaluatorDockerRunner(
        result,
        execution_plan_payload=payload,
    )
    plan = EvaluationPlan(
        phase=EvaluationPhase.BASELINE,
        repeat_index=1,
        pass_to_pass=bundle.task.evaluation.pass_to_pass,
        fail_to_pass=bundle.task.evaluation.fail_to_pass,
        timeout_seconds=60,
    )

    with pytest.raises(TaskBundleError) as caught:
        DockerEvaluator(runner).run(
            EvaluationRequest(
                bundle=bundle,
                lock=lock,
                runtime_policy=create_runtime_policy(
                    bundle.task.environment.runtime
                ),
                command_id="cmd_" + "f" * 32,
                plan=plan,
            )
        )

    assert caught.value.code == error_code
    assert sum(
        command[:2] == ("rm", "--force") for command in runner.commands
    ) == 1
    assert sum(
        command[:3] == ("volume", "rm", "--force")
        for command in runner.commands
    ) == 2


@pytest.mark.parametrize(
    (
        "description",
        "occurrence",
        "expected_container_removals",
        "expected_volume_removals",
    ),
    [
        ("create evaluator storage", 2, 0, 1),
        ("create evaluator container", 1, 0, 2),
        ("start evaluator container", 1, 1, 2),
        ("stage evaluator files", 1, 1, 2),
        ("verify candidate evaluator processes terminated", 1, 1, 2),
        ("create trusted parser container", 1, 1, 2),
    ],
)
def test_evaluator_cleanup_ledger_covers_resource_boundaries(
    tmp_path: Path,
    description: str,
    occurrence: int,
    expected_container_removals: int,
    expected_volume_removals: int,
) -> None:
    bundle_path = create_bundle(tmp_path / "bundle")
    _patch(bundle_path / "evaluation/hidden/test.patch", "tests/hidden_test.py")
    _patch(bundle_path / "evaluation/hidden/golden.patch", "calculator.go")
    database = Database(tmp_path / "task.db")
    InitService(
        database=database,
        cli_version="test",
        source_factory=StaticSourceFactory(tmp_path / "source"),
        docker_factory=lambda home: FakeDockerRunner(),
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
    runner = EvaluatorDockerRunner(
        result,
        fail_description=description,
        fail_occurrence=occurrence,
    )
    plan = EvaluationPlan(
        phase=EvaluationPhase.BASELINE,
        repeat_index=1,
        pass_to_pass=bundle.task.evaluation.pass_to_pass,
        fail_to_pass=bundle.task.evaluation.fail_to_pass,
        timeout_seconds=60,
    )

    with pytest.raises(TaskBundleError):
        DockerEvaluator(runner).run(
            EvaluationRequest(
                bundle=bundle,
                lock=lock,
                runtime_policy=create_runtime_policy(
                    bundle.task.environment.runtime
                ),
                command_id="cmd_" + "1" * 32,
                plan=plan,
            )
        )

    assert sum(
        command[:2] == ("rm", "--force") for command in runner.commands
    ) == expected_container_removals
    assert sum(
        command[:3] == ("volume", "rm", "--force")
        for command in runner.commands
    ) == expected_volume_removals


def test_evaluator_cleanup_runs_when_parser_execution_is_interrupted(
    tmp_path: Path,
) -> None:
    bundle_path = create_bundle(tmp_path / "bundle")
    _patch(bundle_path / "evaluation/hidden/test.patch", "tests/hidden_test.py")
    _patch(bundle_path / "evaluation/hidden/golden.patch", "calculator.go")
    database = Database(tmp_path / "task.db")
    InitService(
        database=database,
        cli_version="test",
        source_factory=StaticSourceFactory(tmp_path / "source"),
        docker_factory=lambda home: FakeDockerRunner(),
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
    runner = EvaluatorDockerRunner(
        result,
        interrupt_description="run trusted result parser",
    )
    plan = EvaluationPlan(
        phase=EvaluationPhase.BASELINE,
        repeat_index=1,
        pass_to_pass=bundle.task.evaluation.pass_to_pass,
        fail_to_pass=bundle.task.evaluation.fail_to_pass,
        timeout_seconds=60,
    )

    with pytest.raises(KeyboardInterrupt):
        DockerEvaluator(runner).run(
            EvaluationRequest(
                bundle=bundle,
                lock=lock,
                runtime_policy=create_runtime_policy(
                    bundle.task.environment.runtime
                ),
                command_id="cmd_" + "2" * 32,
                plan=plan,
            )
        )

    assert sum(
        command[:2] == ("rm", "--force") for command in runner.commands
    ) == 2
    assert sum(
        command[:3] == ("volume", "rm", "--force")
        for command in runner.commands
    ) == 2
