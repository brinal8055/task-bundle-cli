from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from task_bundle.bundle.loader import load_bundle
from task_bundle.database import Database
from task_bundle.errors import ErrorCode, ErrorContext, TaskBundleError
from task_bundle.image.lock import LOCK_RELATIVE_PATH, load_bundle_lock
from task_bundle.image.service import InitOptions, InitService
from task_bundle.models import (
    CapturedTestExecution,
    CapturedTestExecutions,
    EvaluationPhase,
    HarnessStatus,
    NormalizedResult,
)
from task_bundle.models import TestResult as ResultItem
from task_bundle.models import TestStatus as ResultStatus
from task_bundle.validation.docker import EvaluationRequest
from task_bundle.validation.models import (
    EvaluationStatus,
    EvaluatorExecution,
    ValidationStatus,
)
from task_bundle.validation.service import (
    ValidationOptions,
    ValidationService,
    create_validation_identity,
)
from tests.bundle_helpers import create_bundle, read_task, write_task
from tests.image_helpers import FakeDockerRunner, StaticSourceFactory


class FakeEvaluationBackend:
    def __init__(
        self,
        status_for: Callable[[EvaluationPhase, int, str], ResultStatus],
    ) -> None:
        self.status_for = status_for
        self.requests: list[EvaluationRequest] = []

    def run(self, request: EvaluationRequest) -> EvaluatorExecution:
        self.requests.append(request)
        started = datetime.now(UTC)
        tests = [
            ResultItem(
                requested_selector=item.selector,
                status=self.status_for(
                    request.plan.phase,
                    request.plan.repeat_index,
                    item.selector,
                ),
            )
            for item in (
                *request.plan.pass_to_pass,
                *request.plan.fail_to_pass,
            )
        ]
        result = NormalizedResult(
            schema_version="1",
            framework="fake",
            harness_status=HarnessStatus.COMPLETED,
            collection_succeeded=True,
            execution_started=True,
            command=["fake"],
            started_at=started,
            finished_at=started + timedelta(milliseconds=5),
            exit_code=0,
            tests=tests,
        )
        finished = started + timedelta(milliseconds=5)
        return EvaluatorExecution(
            phase=request.plan.phase,
            repeat_index=request.plan.repeat_index,
            container_id=f"{request.plan.phase.value}-{request.plan.repeat_index}",
            workspace_id=f"workspace-{len(self.requests)}",
            evaluation_storage_id=f"evaluation-{len(self.requests)}",
            status=EvaluationStatus.COMPLETED,
            harness_status=HarnessStatus.COMPLETED,
            runner_exit_code=0,
            duration_ms=5,
            test_patch_sha256=request.bundle.evaluation_inputs.test_patch_sha256,
            golden_patch_sha256=(
                request.bundle.evaluation_inputs.golden_patch_sha256
                if request.plan.phase == EvaluationPhase.GOLDEN
                else None
            ),
            captured_executions=CapturedTestExecutions(
                executions=[
                    CapturedTestExecution(
                        execution_id="fake-group",
                        requested_selectors=[
                            item.requested_selector for item in tests
                        ],
                        argv=["fake"],
                        started_at=started,
                        finished_at=finished,
                        duration_ms=5,
                        exit_code=0,
                        timed_out=False,
                        stdout="",
                        stderr="",
                        stdout_truncated=False,
                        stderr_truncated=False,
                        candidate_processes_terminated=True,
                    )
                ]
            ),
            raw_result=result.model_dump_json().encode(),
            result=result,
            cleaned_up=not request.keep_container,
        )


def _initialized(
    tmp_path: Path,
) -> tuple[Path, Database, FakeDockerRunner]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    bundle = create_bundle(tmp_path / "bundle")
    database = Database(tmp_path / "state/task.db")
    docker = FakeDockerRunner()
    init = InitService(
        database=database,
        cli_version="test",
        source_factory=StaticSourceFactory(tmp_path / "source"),
        docker_factory=lambda home: docker,
    )
    init.run(bundle, InitOptions())
    return bundle, database, docker


def _status(
    phase: EvaluationPhase,
    repeat: int,
    selector: str,
) -> ResultStatus:
    del repeat
    if "existing" in selector:
        return ResultStatus.PASSED
    return (
        ResultStatus.FAILED
        if phase == EvaluationPhase.BASELINE
        else ResultStatus.PASSED
    )


def test_valid_validation_uses_fresh_evaluators_and_persists_evidence(
    tmp_path: Path,
) -> None:
    bundle, database, docker = _initialized(tmp_path)
    backend = FakeEvaluationBackend(_status)
    service = ValidationService(
        database=database,
        cli_version="test",
        docker_factory=lambda home: docker,
        backend_factory=lambda runner: backend,
    )

    result = service.run(bundle, ValidationOptions(repeat=2))

    assert result.validation_status == ValidationStatus.VALID
    assert len(backend.requests) == 4
    assert len({item.workspace_id for item in result.evaluations}) == 4
    assert all(item.cleaned_up for item in result.evaluations)
    artifact_root = bundle / result.artifact_directory
    assert (
        artifact_root / "baseline/repeat-001/captured-executions.json"
    ).is_file()
    assert (artifact_root / "baseline/repeat-001/results.json").is_file()
    assert (artifact_root / "golden/repeat-002/classification.json").is_file()
    assert (artifact_root / "report.md").is_file()
    with database.connect() as connection:
        command = connection.execute(
            "SELECT * FROM commands WHERE id = ?",
            (result.command_id,),
        ).fetchone()
        validation_count = connection.execute(
            "SELECT COUNT(*) FROM validations WHERE command_id = ?",
            (result.command_id,),
        ).fetchone()[0]
        evaluation_count = connection.execute(
            "SELECT COUNT(*) FROM evaluations WHERE command_id = ?",
            (result.command_id,),
        ).fetchone()[0]
        test_count = connection.execute(
            """
            SELECT COUNT(*) FROM test_results
            WHERE evaluation_id IN (
                SELECT id FROM evaluations WHERE command_id = ?
            )
            """,
            (result.command_id,),
        ).fetchone()[0]
    assert command["command_status"] == "succeeded"
    assert command["exit_code"] == 0
    assert validation_count == 1
    assert evaluation_count == 4
    assert test_count == 8


def test_invalid_baseline_stops_before_golden_and_exits_as_completed(
    tmp_path: Path,
) -> None:
    bundle, database, docker = _initialized(tmp_path)

    def invalid(
        phase: EvaluationPhase,
        repeat: int,
        selector: str,
    ) -> ResultStatus:
        del phase, repeat, selector
        return ResultStatus.FAILED

    backend = FakeEvaluationBackend(invalid)
    service = ValidationService(
        database=database,
        cli_version="test",
        docker_factory=lambda home: docker,
        backend_factory=lambda runner: backend,
    )

    result = service.run(bundle, ValidationOptions())

    assert result.validation_status == ValidationStatus.INVALID_BASELINE
    assert result.golden is None
    assert [item.plan.phase for item in backend.requests] == [EvaluationPhase.BASELINE]
    with database.connect() as connection:
        command = connection.execute(
            "SELECT command_status, exit_code FROM commands WHERE id = ?",
            (result.command_id,),
        ).fetchone()
    assert tuple(command) == ("succeeded", 4)


def test_status_changes_across_repeats_are_flaky(tmp_path: Path) -> None:
    bundle, database, docker = _initialized(tmp_path)

    def flaky(
        phase: EvaluationPhase,
        repeat: int,
        selector: str,
    ) -> ResultStatus:
        if "existing" in selector:
            return ResultStatus.PASSED
        if phase == EvaluationPhase.BASELINE:
            return ResultStatus.FAILED if repeat == 1 else ResultStatus.ERROR
        return ResultStatus.PASSED

    mapping = read_task(bundle)
    mapping["evaluation"]["fail_to_pass"][0]["baseline_statuses"] = ["failed", "error"]
    write_task(bundle, mapping)
    # The digest-covered bundle changed, so rebuild the lock before validation.
    InitService(
        database=database,
        cli_version="test",
        source_factory=StaticSourceFactory(tmp_path / "source-rebuild"),
        docker_factory=lambda home: docker,
    ).run(bundle, InitOptions(rebuild=True))
    backend = FakeEvaluationBackend(flaky)
    service = ValidationService(
        database=database,
        cli_version="test",
        docker_factory=lambda home: docker,
        backend_factory=lambda runner: backend,
    )

    result = service.run(bundle, ValidationOptions(repeat=2))

    assert result.validation_status == ValidationStatus.INVALID_BASELINE_FLAKY
    assert result.golden is None


def test_keep_containers_is_explicit_and_warns_about_hidden_inputs(
    tmp_path: Path,
) -> None:
    bundle, database, docker = _initialized(tmp_path)
    backend = FakeEvaluationBackend(_status)
    result = ValidationService(
        database=database,
        cli_version="test",
        docker_factory=lambda home: docker,
        backend_factory=lambda runner: backend,
    ).run(bundle, ValidationOptions(keep_containers=True))

    assert not result.cleanup_complete
    assert len(result.retained_containers) == 2
    warning = result.warnings[0]
    assert "hidden tests" in warning
    assert "test selectors" in warning
    assert "evaluation output" in warning
    assert "golden-patch content" in warning


def test_missing_lock_and_missing_image_are_configuration_errors(tmp_path: Path) -> None:
    bundle = create_bundle(tmp_path / "missing-lock")
    service = ValidationService(
        database=Database(tmp_path / "missing.db"),
        cli_version="test",
        docker_factory=lambda home: FakeDockerRunner(),
    )

    with pytest.raises(TaskBundleError) as missing_lock:
        service.run(bundle, ValidationOptions())
    assert missing_lock.value.code == ErrorCode.VALIDATION_LOCK_REQUIRED

    initialized, database, docker = _initialized(tmp_path / "initialized")
    docker.images.clear()
    with pytest.raises(TaskBundleError) as missing_image:
        ValidationService(
            database=database,
            cli_version="test",
            docker_factory=lambda home: docker,
        ).run(initialized, ValidationOptions())
    assert missing_image.value.code == ErrorCode.VALIDATION_IMAGE_MISSING


def test_stale_lock_requires_rebuild_and_command_is_finalized(tmp_path: Path) -> None:
    bundle, database, docker = _initialized(tmp_path)
    (bundle / "public/description.md").write_text("changed\n", encoding="utf-8")

    with pytest.raises(TaskBundleError) as caught:
        ValidationService(
            database=database,
            cli_version="test",
            docker_factory=lambda home: docker,
        ).run(bundle, ValidationOptions())

    assert caught.value.code == ErrorCode.VALIDATION_LOCK_STALE
    with database.connect() as connection:
        command = connection.execute(
            """
            SELECT command_status, exit_code FROM commands
            WHERE command_type = 'validate'
            ORDER BY started_at DESC LIMIT 1
            """
        ).fetchone()
    assert tuple(command) == ("failed", 2)


def test_validation_identity_is_deterministic_but_execution_is_not_cached(
    tmp_path: Path,
) -> None:
    bundle, database, docker = _initialized(tmp_path)
    backend = FakeEvaluationBackend(_status)
    service = ValidationService(
        database=database,
        cli_version="test",
        docker_factory=lambda home: docker,
        backend_factory=lambda runner: backend,
    )

    first = service.run(bundle, ValidationOptions())
    second = service.run(bundle, ValidationOptions())

    assert first.validation_id == second.validation_id
    assert len(backend.requests) == 4
    assert service.validation_store.matching_success_exists(first.validation_id)
    with database.connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM validations WHERE validation_key = ?",
            (first.validation_id,),
        ).fetchone()[0]
    assert count == 2


def test_matching_validation_requires_all_inputs_and_accepts_stronger_repeat(
    tmp_path: Path,
) -> None:
    bundle_path, database, docker = _initialized(tmp_path)
    backend = FakeEvaluationBackend(_status)
    service = ValidationService(
        database=database,
        cli_version="test",
        docker_factory=lambda home: docker,
        backend_factory=lambda runner: backend,
    )
    service.run(bundle_path, ValidationOptions(repeat=1))
    bundle = load_bundle(bundle_path)
    lock = load_bundle_lock(bundle_path / LOCK_RELATIVE_PATH)
    required = create_validation_identity(bundle, lock, 2)

    assert service.validation_store.matching_success(required) is None

    equal = service.run(bundle_path, ValidationOptions(repeat=2))
    match = service.validation_store.matching_success(required)
    assert match is not None
    assert match.validation_id == equal.validation_id
    assert match.repeat_count == 2

    stronger = service.run(bundle_path, ValidationOptions(repeat=3))
    match = service.validation_store.matching_success(required)
    assert match is not None
    assert match.validation_id == stronger.validation_id
    assert match.repeat_count == 3


def test_backend_failure_never_leaves_command_running(tmp_path: Path) -> None:
    bundle, database, docker = _initialized(tmp_path)

    class FailingBackend:
        def run(self, request: EvaluationRequest) -> EvaluatorExecution:
            raise TaskBundleError(
                ErrorCode.TEST_PREPARE_ERROR,
                "Preparation failed.",
                ErrorContext(
                    phase=request.plan.phase.value,
                    expected="Preparation success",
                    actual="exit 1",
                    corrective_action="Inspect prepare logs.",
                ),
            )

    with pytest.raises(TaskBundleError):
        ValidationService(
            database=database,
            cli_version="test",
            docker_factory=lambda home: docker,
            backend_factory=lambda runner: FailingBackend(),
        ).run(bundle, ValidationOptions())

    with database.connect() as connection:
        command = connection.execute(
            """
            SELECT command_status FROM commands
            WHERE command_type = 'validate'
            ORDER BY started_at DESC LIMIT 1
            """
        ).fetchone()
    assert command["command_status"] == "failed"
    assert (bundle / LOCK_RELATIVE_PATH).is_file()


def test_baseline_evidence_survives_later_golden_infrastructure_failure(
    tmp_path: Path,
) -> None:
    bundle, database, docker = _initialized(tmp_path)

    class GoldenFailureBackend(FakeEvaluationBackend):
        def run(self, request: EvaluationRequest) -> EvaluatorExecution:
            if request.plan.phase == EvaluationPhase.GOLDEN:
                raise TaskBundleError(
                    ErrorCode.TEST_RUNNER_ERROR,
                    "Golden runner failed.",
                    ErrorContext(
                        phase=request.plan.phase.value,
                        expected="A completed golden harness",
                        actual="simulated runner failure",
                        corrective_action="Inspect golden artifacts.",
                    ),
                )
            return super().run(request)

    backend = GoldenFailureBackend(_status)
    with pytest.raises(TaskBundleError):
        ValidationService(
            database=database,
            cli_version="test",
            docker_factory=lambda home: docker,
            backend_factory=lambda runner: backend,
        ).run(bundle, ValidationOptions())

    with database.connect() as connection:
        command = connection.execute(
            """
            SELECT id, command_status FROM commands
            WHERE command_type = 'validate'
            ORDER BY started_at DESC LIMIT 1
            """
        ).fetchone()
        validation = connection.execute(
            "SELECT outcome FROM validations WHERE command_id = ?",
            (command["id"],),
        ).fetchone()
        evaluations = connection.execute(
            "SELECT phase, outcome FROM evaluations WHERE command_id = ?",
            (command["id"],),
        ).fetchall()

    assert command["command_status"] == "failed"
    assert validation["outcome"] == ValidationStatus.INFRA_ERROR.value
    assert [tuple(row) for row in evaluations] == [("baseline", "accepted")]
