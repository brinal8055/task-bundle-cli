from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from task_bundle.bundle.canonical import sha256_digest
from task_bundle.database import Database
from task_bundle.errors import ErrorCode, TaskBundleError
from task_bundle.image.service import InitOptions, InitService
from task_bundle.models import EvaluationPhase, SolverConfig
from task_bundle.models import TestStatus as ResultStatus
from task_bundle.run.docker import SolverOutcome, SolverRequest
from task_bundle.run.filesystem import build_filesystem_manifest
from task_bundle.run.models import (
    CandidateTree,
    FilesystemManifest,
    RunOptions,
    SolverExecutionResult,
    SolverStatus,
    SolverType,
)
from task_bundle.run.records import RunStore
from task_bundle.run.service import RunService
from task_bundle.validation.service import ValidationOptions, ValidationService
from tests.bundle_helpers import create_bundle
from tests.image_helpers import FakeDockerRunner, StaticSourceFactory
from tests.test_validation_service import FakeEvaluationBackend


class FakeSolverBackend:
    def __init__(
        self,
        source: Path,
        *,
        status: SolverStatus = SolverStatus.SUCCEEDED,
    ) -> None:
        self.source = source
        self.status = status
        self.requests: list[SolverRequest] = []

    def run(self, request: SolverRequest) -> SolverOutcome:
        self.requests.append(request)
        now = datetime.now(UTC)
        execution = SolverExecutionResult(
            solver_type=request.options.solver,
            argv=request.options.command or ("/bin/true",),
            status=self.status,
            started_at=now,
            finished_at=now,
            duration_ms=5,
            exit_code=0 if self.status == SolverStatus.SUCCEEDED else 7,
            timed_out=False,
            container_id="s" * 12,
            stdout="solver output\n",
            stderr="" if self.status == SolverStatus.SUCCEEDED else "failed\n",
            workspace_export_status=(
                "completed" if self.status == SolverStatus.SUCCEEDED else "not_run"
            ),
            cleaned_up=not request.options.keep_containers,
        )
        if self.status != SolverStatus.SUCCEEDED:
            return SolverOutcome(
                execution=execution,
                baseline_root=None,
                baseline_manifest=None,
                candidate_root=None,
                candidate_manifest=None,
            )
        manifest = build_filesystem_manifest(
            self.source,
            phase="test-export",
            error_code=ErrorCode.WORKSPACE_EXPORT_UNSAFE,
            allow_symlinks=True,
            max_files=100,
            max_total_bytes=1024 * 1024,
            max_file_bytes=1024 * 1024,
        )
        return SolverOutcome(
            execution=execution,
            baseline_root=self.source,
            baseline_manifest=manifest,
            candidate_root=self.source,
            candidate_manifest=manifest,
        )


class FakeCandidateBuilder:
    def __init__(self, candidate: CandidateTree, patch: bytes) -> None:
        self.candidate = candidate
        self.patch = patch
        self.calls = 0

    def build(
        self,
        *,
        baseline_root: Path,
        baseline_manifest: FilesystemManifest,
        candidate_root: Path,
        candidate_manifest: FilesystemManifest,
        expected_baseline_tree: str,
        solver: SolverConfig,
    ) -> tuple[CandidateTree, bytes]:
        del (
            baseline_root,
            baseline_manifest,
            candidate_root,
            candidate_manifest,
            expected_baseline_tree,
            solver,
        )
        self.calls += 1
        return self.candidate, self.patch


def _patch(path: Path, target: str) -> None:
    path.write_text(
        f"diff --git a/{target} b/{target}\n"
        f"--- a/{target}\n"
        f"+++ b/{target}\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n",
    )


def _validation_status(
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


def _run_status(
    resolved: bool,
) -> Callable[[EvaluationPhase, int, str], ResultStatus]:
    def status(
        phase: EvaluationPhase,
        repeat: int,
        selector: str,
    ) -> ResultStatus:
        del repeat
        if "existing" in selector:
            return ResultStatus.PASSED
        if phase == EvaluationPhase.BASELINE or not resolved:
            return ResultStatus.FAILED
        return ResultStatus.PASSED

    return status


def _setup(
    tmp_path: Path,
    *,
    validate: bool = True,
) -> tuple[Path, Path, Database, FakeDockerRunner]:
    bundle = create_bundle(tmp_path / "bundle")
    _patch(bundle / "evaluation/hidden/test.patch", "tests/hidden_test.py")
    _patch(bundle / "evaluation/hidden/golden.patch", "README.md")
    database = Database(tmp_path / "task.db")
    docker = FakeDockerRunner()
    source_factory = StaticSourceFactory(tmp_path / "source")
    InitService(
        database=database,
        cli_version="test",
        source_factory=source_factory,
        docker_factory=lambda home: docker,
    ).run(bundle, InitOptions())
    if validate:
        validation_backend = FakeEvaluationBackend(_validation_status)
        ValidationService(
            database=database,
            cli_version="test",
            docker_factory=lambda home: docker,
            backend_factory=lambda runner: validation_backend,
        ).run(bundle, ValidationOptions())
    return bundle, source_factory.root, database, docker


def _candidate(patch: bytes, paths: tuple[str, ...]) -> CandidateTree:
    return CandidateTree(
        baseline_tree_sha="a" * 40,
        candidate_tree_sha="b" * 40,
        candidate_patch_sha256=sha256_digest(patch),
        candidate_patch_size=len(patch),
        changed_paths=paths,
    )


def test_noop_run_is_unresolved_persisted_and_queryable(tmp_path: Path) -> None:
    bundle, source, database, docker = _setup(tmp_path)
    solver = FakeSolverBackend(source)
    builder = FakeCandidateBuilder(_candidate(b"", ()), b"")
    evaluator = FakeEvaluationBackend(_run_status(False))
    service = RunService(
        database=database,
        cli_version="test",
        docker_factory=lambda home: docker,
        evaluator_factory=lambda runner: evaluator,
        solver_factory=lambda runner: solver,
        candidate_factory=lambda root: builder,
    )

    result = service.run(bundle, RunOptions(solver=SolverType.NOOP))

    assert not result.resolved
    assert result.evaluation_status == "unresolved"
    assert [request.plan.phase for request in evaluator.requests] == [
        EvaluationPhase.BASELINE,
        EvaluationPhase.CANDIDATE,
    ]
    assert len(solver.requests) == 1
    shown = RunStore(database).show(
        result.command_id,
        include_events=True,
        include_tests=True,
    )
    assert shown.command["command_status"] == "succeeded"
    assert shown.command["exit_code"] == 1
    assert shown.solver is not None
    assert shown.solver["status"] == "succeeded"
    event_names = [event["event_type"] for event in shown.events]
    assert event_names.index("CANDIDATE_FINALIZED") < event_names.index(
        "CANDIDATE_EVALUATOR_STARTED"
    )
    assert len(shown.evaluations) == 2
    assert len(shown.tests) == 4
    artifact_root = bundle / result.artifact_directory
    hidden_patch = (bundle / "evaluation/hidden/test.patch").read_bytes()
    assert all(
        hidden_patch not in path.read_bytes()
        for path in artifact_root.rglob("*")
        if path.is_file()
    )
    for artifact in shown.artifacts:
        path = bundle / str(artifact["relative_path"])
        payload = path.read_bytes()
        assert path.is_relative_to(artifact_root)
        assert artifact["sha256"] == sha256_digest(payload)
        assert artifact["size_bytes"] == len(payload)


def test_keep_containers_records_solver_and_evaluators_with_hidden_warning(
    tmp_path: Path,
) -> None:
    bundle, source, database, docker = _setup(tmp_path)
    result = RunService(
        database=database,
        cli_version="test",
        docker_factory=lambda home: docker,
        evaluator_factory=lambda runner: FakeEvaluationBackend(_run_status(False)),
        solver_factory=lambda runner: FakeSolverBackend(source),
        candidate_factory=lambda root: FakeCandidateBuilder(_candidate(b"", ()), b""),
    ).run(
        bundle,
        RunOptions(solver=SolverType.NOOP, keep_containers=True),
    )

    assert not result.cleanup_complete
    assert len(result.retained_containers) == 3
    warning = result.warnings[0]
    assert "hidden tests" in warning
    assert "selectors" in warning
    assert "evaluation output" in warning


def test_resolved_candidate_returns_success_and_records_patch(tmp_path: Path) -> None:
    bundle, source, database, docker = _setup(tmp_path)
    patch_path = bundle.parent / "candidate.patch"
    _patch(patch_path, "README.md")
    patch = patch_path.read_bytes()
    solver = FakeSolverBackend(source)
    builder = FakeCandidateBuilder(_candidate(patch, ("README.md",)), patch)
    evaluator = FakeEvaluationBackend(_run_status(True))

    result = RunService(
        database=database,
        cli_version="test",
        docker_factory=lambda home: docker,
        evaluator_factory=lambda runner: evaluator,
        solver_factory=lambda runner: solver,
        candidate_factory=lambda root: builder,
    ).run(
        bundle,
        RunOptions(solver=SolverType.PATCH, patch=patch_path),
    )

    assert result.resolved
    assert result.evaluation_status == "resolved"
    shown = RunStore(database).show(
        result.command_id,
        include_events=False,
        include_tests=False,
    )
    assert shown.command["exit_code"] == 0
    assert shown.solver is not None
    assert shown.solver["patch_digest"] == sha256_digest(patch)
    assert (bundle / result.artifact_directory / "solver/candidate.patch").read_bytes() == patch


def test_missing_validation_stops_before_solver(tmp_path: Path) -> None:
    bundle, source, database, docker = _setup(tmp_path, validate=False)
    solver = FakeSolverBackend(source)
    service = RunService(
        database=database,
        cli_version="test",
        docker_factory=lambda home: docker,
        solver_factory=lambda runner: solver,
    )

    with pytest.raises(TaskBundleError) as caught:
        service.run(bundle, RunOptions(solver=SolverType.NOOP))

    assert caught.value.code == ErrorCode.VALIDATION_REQUIRED
    assert not solver.requests


def test_run_rejects_missing_lock_stale_bundle_and_missing_image(
    tmp_path: Path,
) -> None:
    missing_bundle, _source, database, docker = _setup(tmp_path / "missing")
    (missing_bundle / ".task/bundle.lock.json").unlink()
    missing_service = RunService(
        database=database,
        cli_version="test",
        docker_factory=lambda home: docker,
    )
    with pytest.raises(TaskBundleError) as missing:
        missing_service.run(missing_bundle, RunOptions(solver=SolverType.NOOP))
    assert missing.value.code == ErrorCode.VALIDATION_LOCK_REQUIRED

    stale_bundle, _source, database, docker = _setup(tmp_path / "stale")
    (stale_bundle / "public/description.md").write_text("changed\n")
    stale_service = RunService(
        database=database,
        cli_version="test",
        docker_factory=lambda home: docker,
    )
    with pytest.raises(TaskBundleError) as stale:
        stale_service.run(stale_bundle, RunOptions(solver=SolverType.NOOP))
    assert stale.value.code == ErrorCode.VALIDATION_LOCK_STALE

    image_bundle, _source, database, docker = _setup(tmp_path / "image")
    docker.images.clear()
    image_service = RunService(
        database=database,
        cli_version="test",
        docker_factory=lambda home: docker,
    )
    with pytest.raises(TaskBundleError) as image:
        image_service.run(image_bundle, RunOptions(solver=SolverType.NOOP))
    assert image.value.code == ErrorCode.VALIDATION_IMAGE_MISSING


def test_baseline_guardrail_failure_stops_solver_and_persists_evidence(
    tmp_path: Path,
) -> None:
    bundle, source, database, docker = _setup(tmp_path)
    solver = FakeSolverBackend(source)

    def regression(
        phase: EvaluationPhase,
        repeat: int,
        selector: str,
    ) -> ResultStatus:
        del phase, repeat, selector
        return ResultStatus.FAILED

    evaluator = FakeEvaluationBackend(regression)
    service = RunService(
        database=database,
        cli_version="test",
        docker_factory=lambda home: docker,
        evaluator_factory=lambda runner: evaluator,
        solver_factory=lambda runner: solver,
    )

    with pytest.raises(TaskBundleError) as caught:
        service.run(bundle, RunOptions(solver=SolverType.NOOP))

    assert caught.value.code == ErrorCode.BASELINE_GUARDRAIL_FAILURE
    assert not solver.requests
    with database.connect() as connection:
        command = connection.execute(
            "SELECT id, command_status, exit_code FROM commands "
            "WHERE command_type = 'run' ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        evaluation_count = connection.execute(
            "SELECT COUNT(*) FROM evaluations WHERE command_id = ?",
            (command["id"],),
        ).fetchone()[0]
        solver_status = connection.execute(
            "SELECT status FROM solver_runs WHERE command_id = ?",
            (command["id"],),
        ).fetchone()[0]
    assert tuple(command)[1:] == ("failed", 4)
    assert evaluation_count == 1
    assert solver_status == "not_run"


def test_solver_failure_is_exit_five_and_candidate_is_not_evaluated(
    tmp_path: Path,
) -> None:
    bundle, source, database, docker = _setup(tmp_path)
    solver = FakeSolverBackend(source, status=SolverStatus.FAILED)
    evaluator = FakeEvaluationBackend(_run_status(True))
    service = RunService(
        database=database,
        cli_version="test",
        docker_factory=lambda home: docker,
        evaluator_factory=lambda runner: evaluator,
        solver_factory=lambda runner: solver,
    )

    with pytest.raises(TaskBundleError) as caught:
        service.run(
            bundle,
            RunOptions(solver=SolverType.COMMAND, command=("false",)),
        )

    assert caught.value.code == ErrorCode.SOLVER_EXECUTION_ERROR
    assert caught.value.exit_code == 5
    assert [request.plan.phase for request in evaluator.requests] == [
        EvaluationPhase.BASELINE
    ]
    with database.connect() as connection:
        command = connection.execute(
            "SELECT id, command_status, exit_code FROM commands "
            "WHERE command_type = 'run' ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        solver_status = connection.execute(
            "SELECT status FROM solver_runs WHERE command_id = ?",
            (command["id"],),
        ).fetchone()[0]
    assert tuple(command)[1:] == ("failed", 5)
    assert solver_status == "failed"


def test_hidden_path_conflict_is_exit_six_without_candidate_evaluation(
    tmp_path: Path,
) -> None:
    bundle, source, database, docker = _setup(tmp_path)
    conflict_patch = (
        b"diff --git a/tests/hidden_test.py b/tests/hidden_test.py\n"
        b"--- a/tests/hidden_test.py\n"
        b"+++ b/tests/hidden_test.py\n"
        b"@@ -1 +1 @@\n-old\n+new\n"
    )
    builder = FakeCandidateBuilder(
        _candidate(conflict_patch, ("tests/hidden_test.py",)),
        conflict_patch,
    )
    evaluator = FakeEvaluationBackend(_run_status(True))
    service = RunService(
        database=database,
        cli_version="test",
        docker_factory=lambda home: docker,
        evaluator_factory=lambda runner: evaluator,
        solver_factory=lambda runner: FakeSolverBackend(source),
        candidate_factory=lambda root: builder,
    )

    with pytest.raises(TaskBundleError) as caught:
        service.run(bundle, RunOptions(solver=SolverType.NOOP))

    assert caught.value.code == ErrorCode.PATCH_CONFLICT
    assert caught.value.exit_code == 6
    assert [request.plan.phase for request in evaluator.requests] == [
        EvaluationPhase.BASELINE
    ]
    with database.connect() as connection:
        command = connection.execute(
            "SELECT id, command_status, exit_code FROM commands "
            "WHERE command_type = 'run' ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        policy = connection.execute(
            "SELECT patch_policy_status FROM solver_runs WHERE command_id = ?",
            (command["id"],),
        ).fetchone()[0]
    assert tuple(command)[1:] == ("failed", 6)
    assert policy == "rejected"


def test_show_queries_init_validate_and_run_commands(tmp_path: Path) -> None:
    bundle, source, database, docker = _setup(tmp_path)
    run = RunService(
        database=database,
        cli_version="test",
        docker_factory=lambda home: docker,
        evaluator_factory=lambda runner: FakeEvaluationBackend(_run_status(False)),
        solver_factory=lambda runner: FakeSolverBackend(source),
        candidate_factory=lambda root: FakeCandidateBuilder(_candidate(b"", ()), b""),
    ).run(bundle, RunOptions(solver=SolverType.NOOP))

    with database.connect() as connection:
        rows = connection.execute(
            "SELECT id, command_type FROM commands ORDER BY started_at"
        ).fetchall()
    by_type = {row["command_type"]: row["id"] for row in rows}
    by_type["run"] = run.command_id
    store = RunStore(database)

    assert set(by_type) == {"init", "validate", "run"}
    for command_type, command_id in by_type.items():
        shown = store.show(
            command_id,
            include_events=True,
            include_tests=True,
        )
        assert shown.command["command_type"] == command_type
