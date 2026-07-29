import tempfile
import uuid
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from task_bundle.bundle.canonical import canonical_json_bytes, sha256_digest
from task_bundle.bundle.loader import LoadedBundle, load_bundle
from task_bundle.bundle.snapshot import create_snapshot
from task_bundle.database import Database
from task_bundle.errors import ErrorCode, ErrorContext, TaskBundleError
from task_bundle.image.artifacts import ArtifactWriter
from task_bundle.image.docker import (
    DockerCommandResult,
    DockerRunner,
    SystemDockerRunner,
)
from task_bundle.image.inspect import inspect_image_if_present
from task_bundle.image.lock import (
    LOCK_RELATIVE_PATH,
    compare_bundle_lock,
    load_bundle_lock,
)
from task_bundle.image.models import BundleLock, RuntimePolicy
from task_bundle.image.records import CommandStore
from task_bundle.image.runtime import create_runtime_policy
from task_bundle.image.validation import task_image_reference, validate_platform
from task_bundle.models import CommandStatus, EvaluationPhase, EvaluationPlan
from task_bundle.validation.docker import (
    DockerEvaluator,
    EvaluationBackend,
    EvaluationRequest,
)
from task_bundle.validation.models import (
    EvaluationRecord,
    EvaluatorExecution,
    PhaseSummary,
    SelectorResult,
    TestGroup,
    ValidationIdentity,
    ValidationResult,
    ValidationStatus,
)
from task_bundle.validation.records import ValidationStore
from task_bundle.validation.result import classify_result


@dataclass(frozen=True, slots=True)
class ValidationOptions:
    repeat: int | None = None
    keep_containers: bool = False


DockerFactory = Callable[[Path], DockerRunner]
BackendFactory = Callable[[DockerRunner], EvaluationBackend]


class ValidationService:
    def __init__(
        self,
        *,
        database: Database,
        cli_version: str,
        docker_factory: DockerFactory = SystemDockerRunner.create,
        backend_factory: BackendFactory = DockerEvaluator,
    ) -> None:
        self.command_store = CommandStore(database)
        self.validation_store = ValidationStore(database)
        self.cli_version = cli_version
        self.docker_factory = docker_factory
        self.backend_factory = backend_factory

    def run(self, bundle_path: Path, options: ValidationOptions) -> ValidationResult:
        command_id = f"cmd_{uuid.uuid4().hex}"
        started_at = datetime.now(UTC)
        self.command_store.start(
            command_id=command_id,
            task_id=bundle_path.name or "unknown-task",
            bundle_path=bundle_path,
            command_type="validate",
            started_at=started_at,
        )
        writer: ArtifactWriter | None = None
        runner: DockerRunner | None = None
        image_id: str | None = None
        try:
            bundle = load_bundle(bundle_path)
            repeat = options.repeat or bundle.task.evaluation.repeat
            if repeat < 1:
                raise TaskBundleError(
                    ErrorCode.CONFIG_ERROR,
                    "Validation repeat count must be positive.",
                    ErrorContext(
                        phase="validation-config",
                        expected="A repeat count of at least one",
                        actual=str(repeat),
                        corrective_action="Use `--repeat` with a positive integer.",
                    ),
                )
            self.command_store.update_identity(
                command_id,
                task_id=bundle.task.task.id,
                bundle_digest=bundle.bundle_input_digest,
            )
            writer = ArtifactWriter(
                bundle_root=bundle.root,
                task_id=bundle.task.task.id,
                command_id=command_id,
                store=self.command_store,
            )
            writer.write_json(
                "command.json",
                {
                    "schema_version": "1",
                    "command_id": command_id,
                    "command_type": "validate",
                    "task_id": bundle.task.task.id,
                    "bundle_input_digest": bundle.bundle_input_digest,
                    "options": {
                        "repeat": repeat,
                        "keep_containers": options.keep_containers,
                    },
                },
                "command",
            )
            lock, runtime_policy = self._load_preconditions(bundle)
            image_id = lock.image_id
            writer.write_model(
                "bundle.snapshot.json",
                create_snapshot(bundle, self.cli_version),
                "bundle-snapshot",
            )
            writer.write_model("bundle.lock.json", lock, "bundle-lock")
            with tempfile.TemporaryDirectory(
                prefix="task-bundle-validation-"
            ) as runtime_name:
                runner = self.docker_factory(Path(runtime_name) / "docker-home")
                self._verify_locked_image(bundle, lock, runtime_policy, runner)
                identity = create_validation_identity(bundle, lock, repeat)
                writer.write_model(
                    "validation-identity.json",
                    identity,
                    "validation-identity",
                )
                backend = self.backend_factory(runner)
                evaluations: list[EvaluationRecord] = []
                baseline = self._run_phase(
                    bundle=bundle,
                    lock=lock,
                    runtime_policy=runtime_policy,
                    command_id=command_id,
                    phase=EvaluationPhase.BASELINE,
                    repeat=repeat,
                    backend=backend,
                    writer=writer,
                    keep_containers=options.keep_containers,
                    records=evaluations,
                )
                golden: PhaseSummary | None = None
                if baseline.outcome == "accepted":
                    golden = self._run_phase(
                        bundle=bundle,
                        lock=lock,
                        runtime_policy=runtime_policy,
                        command_id=command_id,
                        phase=EvaluationPhase.GOLDEN,
                        repeat=repeat,
                        backend=backend,
                        writer=writer,
                        keep_containers=options.keep_containers,
                        records=evaluations,
                    )
                status = _validation_status(baseline, golden)
                finished_at = datetime.now(UTC)
                retained = tuple(
                    record.container_id
                    for record in evaluations
                    if not record.cleaned_up
                )
                warnings = (
                    (
                        "Retained evaluator containers contain hidden tests and "
                        "golden inputs; remove them after debugging."
                    ),
                ) if retained else ()
                result = ValidationResult(
                    command_id=command_id,
                    validation_id=identity.validation_id,
                    task_id=bundle.task.task.id,
                    command_status="succeeded",
                    validation_status=status,
                    bundle_input_digest=bundle.bundle_input_digest,
                    task_image_id=lock.image_id,
                    runtime_policy_digest=lock.runtime_policy_digest,
                    harness_digest=bundle.evaluation_inputs.harness_sha256,
                    selector_digest=bundle.evaluation_inputs.selectors_sha256,
                    test_patch_sha256=bundle.evaluation_inputs.test_patch_sha256,
                    golden_patch_sha256=bundle.evaluation_inputs.golden_patch_sha256,
                    repeat_count=repeat,
                    started_at=started_at,
                    finished_at=finished_at,
                    baseline=baseline,
                    golden=golden,
                    evaluations=tuple(evaluations),
                    artifact_directory=writer.root.relative_to(bundle.root).as_posix(),
                    artifact_paths=_artifact_paths(evaluations, golden is not None),
                    cleanup_complete=all(record.cleaned_up for record in evaluations),
                    retained_containers=retained,
                    warnings=warnings,
                )
                writer.write_model("report.json", result, "validation-report-json")
                writer.write_text(
                    "report.md",
                    _markdown_report(result),
                    "validation-report-markdown",
                )
                self.validation_store.finish(result)
                return result
        except TaskBundleError as error:
            self._record_failure(
                command_id=command_id,
                writer=writer,
                runner=runner,
                error=error,
                image_id=image_id,
            )
            raise
        except KeyboardInterrupt:
            with suppress(TaskBundleError):
                self.command_store.finish(
                    command_id,
                    status=CommandStatus.INTERRUPTED,
                    outcome="interrupted",
                    exit_code=130,
                    image_id=image_id,
                    message="Validation interrupted.",
                )
            raise
        except Exception as error:
            with suppress(TaskBundleError):
                self.command_store.finish(
                    command_id,
                    status=CommandStatus.FAILED,
                    outcome="INTERNAL_ERROR",
                    exit_code=3,
                    image_id=image_id,
                    message=f"Unexpected validation failure: {type(error).__name__}",
                )
            raise

    def _load_preconditions(
        self,
        bundle: LoadedBundle,
    ) -> tuple[BundleLock, RuntimePolicy]:
        lock_path = bundle.root / LOCK_RELATIVE_PATH
        if not lock_path.exists():
            raise TaskBundleError(
                ErrorCode.VALIDATION_LOCK_REQUIRED,
                "Task image lock is required before validation.",
                ErrorContext(
                    phase="validation-preflight",
                    expected="A current .task/bundle.lock.json",
                    actual="The lock file is missing.",
                    corrective_action="Run `task init` before `task validate`.",
                    path=lock_path,
                ),
            )
        try:
            lock = load_bundle_lock(lock_path)
        except TaskBundleError as error:
            raise TaskBundleError(
                ErrorCode.VALIDATION_LOCK_STALE,
                "Task image lock is invalid.",
                ErrorContext(
                    phase="validation-preflight",
                    expected="A current BundleLock schema 1",
                    actual=str(error),
                    corrective_action="Run `task init --rebuild`.",
                    path=lock_path,
                ),
            ) from error
        return lock, create_runtime_policy(bundle.task.environment.runtime)

    def _verify_locked_image(
        self,
        bundle: LoadedBundle,
        lock: BundleLock,
        runtime_policy: RuntimePolicy,
        runner: DockerRunner,
    ) -> None:
        inspection = inspect_image_if_present(runner, lock.image_reference)
        if inspection is None:
            raise TaskBundleError(
                ErrorCode.VALIDATION_IMAGE_MISSING,
                "Locked task image is missing.",
                ErrorContext(
                    phase="validation-preflight",
                    expected=lock.image_id,
                    actual="The locked image tag does not exist locally.",
                    corrective_action="Run `task init --rebuild`.",
                ),
            )
        platform = validate_platform(bundle.task.environment.platform)
        comparison = compare_bundle_lock(
            lock,
            bundle=bundle,
            runtime_policy=runtime_policy,
            image_reference=task_image_reference(
                bundle.task.task.id,
                bundle.bundle_input_digest,
                platform,
            ),
            selected_platform=platform,
            observed_image_id=inspection.image_id,
        )
        if not comparison.is_current:
            raise TaskBundleError(
                ErrorCode.VALIDATION_LOCK_STALE,
                "Task image lock is stale.",
                ErrorContext(
                    phase="validation-preflight",
                    expected="Bundle, runtime, image tag, and image ID to match the lock",
                    actual=f"Mismatches: {', '.join(comparison.reasons)}",
                    corrective_action="Run `task init --rebuild`.",
                    path=bundle.root / LOCK_RELATIVE_PATH,
                    details={"reasons": list(comparison.reasons)},
                ),
            )

    def _run_phase(
        self,
        *,
        bundle: LoadedBundle,
        lock: BundleLock,
        runtime_policy: RuntimePolicy,
        command_id: str,
        phase: EvaluationPhase,
        repeat: int,
        backend: EvaluationBackend,
        writer: ArtifactWriter,
        keep_containers: bool,
        records: list[EvaluationRecord],
    ) -> PhaseSummary:
        phase_records: list[EvaluationRecord] = []
        for repeat_index in range(1, repeat + 1):
            plan = EvaluationPlan(
                phase=phase,
                repeat_index=repeat_index,
                pass_to_pass=bundle.task.evaluation.pass_to_pass,
                fail_to_pass=bundle.task.evaluation.fail_to_pass,
                timeout_seconds=runtime_policy.timeout_seconds,
            )
            prefix = f"{phase.value}/repeat-{repeat_index:03d}"
            writer.write_model(f"{prefix}/plan.json", plan, "evaluation-plan")
            writer.write_json(
                f"{prefix}/task-metadata.json",
                {
                    "schema_version": "1",
                    "task_id": bundle.task.task.id,
                    "bundle_input_digest": bundle.bundle_input_digest,
                    "image_id": lock.image_id,
                    "phase": phase.value,
                    "repeat_index": repeat_index,
                },
                "evaluation-task-metadata",
            )
            execution = backend.run(
                EvaluationRequest(
                    bundle=bundle,
                    lock=lock,
                    runtime_policy=runtime_policy,
                    command_id=command_id,
                    plan=plan,
                    keep_container=keep_containers,
                )
            )
            selectors = classify_result(execution.result, plan)
            record = _evaluation_record(execution, selectors)
            phase_records.append(record)
            records.append(record)
            self._write_execution_artifacts(writer, prefix, execution, selectors)
        summary = _phase_summary(phase, phase_records)
        writer.write_model(
            f"{phase.value}/summary.json",
            summary,
            "evaluation-phase-summary",
        )
        return summary

    def _write_execution_artifacts(
        self,
        writer: ArtifactWriter,
        prefix: str,
        execution: EvaluatorExecution,
        selectors: tuple[SelectorResult, ...],
    ) -> None:
        writer.write_text(
            f"{prefix}/patch-apply.log",
            execution.patch_log,
            "patch-apply-log",
        )
        writer.write_text(
            f"{prefix}/prepare.stdout.log",
            execution.prepare_stdout,
            "prepare-stdout",
        )
        writer.write_text(
            f"{prefix}/prepare.stderr.log",
            execution.prepare_stderr,
            "prepare-stderr",
        )
        writer.write_text(
            f"{prefix}/runner.stdout.log",
            execution.runner_stdout,
            "runner-stdout",
        )
        writer.write_text(
            f"{prefix}/runner.stderr.log",
            execution.runner_stderr,
            "runner-stderr",
        )
        writer.write_bytes(
            f"{prefix}/results.json",
            execution.raw_result,
            "normalized-results",
        )
        writer.write_json(
            f"{prefix}/classification.json",
            [item.model_dump(mode="json") for item in selectors],
            "selector-classification",
        )

    def _record_failure(
        self,
        *,
        command_id: str,
        writer: ArtifactWriter | None,
        runner: DockerRunner | None,
        error: TaskBundleError,
        image_id: str | None,
    ) -> None:
        with suppress(TaskBundleError):
            if writer is not None:
                last_result = (
                    getattr(runner, "last_failure_result", None)
                    or getattr(runner, "last_result", None)
                    if runner is not None
                    else None
                )
                if isinstance(last_result, DockerCommandResult):
                    writer.write_text(
                        "failure/docker.stdout.log",
                        last_result.stdout,
                        "failure-docker-stdout",
                    )
                    writer.write_text(
                        "failure/docker.stderr.log",
                        last_result.stderr,
                        "failure-docker-stderr",
                    )
                writer.write_json(
                    "failure/report.json",
                    {
                        "schema_version": "1",
                        "command_id": command_id,
                        "status": "failed",
                        "error": {
                            "code": error.code.value,
                            "message": str(error),
                            "phase": error.context.phase,
                            "expected": error.context.expected,
                            "actual": error.context.actual,
                            "corrective_action": error.context.corrective_action,
                            "details": error.context.details,
                        },
                    },
                    "failure-report",
                )
        with suppress(TaskBundleError):
            self.command_store.finish(
                command_id,
                status=CommandStatus.FAILED,
                outcome=error.code.value,
                exit_code=error.exit_code,
                image_id=image_id,
                message=str(error),
            )


def create_validation_identity(
    bundle: LoadedBundle,
    lock: BundleLock,
    repeat: int,
) -> ValidationIdentity:
    document = {
        "schema_version": "1",
        "bundle_input_digest": bundle.bundle_input_digest,
        "task_image_id": lock.image_id,
        "runtime_policy_digest": lock.runtime_policy_digest,
        "harness_digest": bundle.evaluation_inputs.harness_sha256,
        "selector_digest": bundle.evaluation_inputs.selectors_sha256,
        "test_patch_sha256": bundle.evaluation_inputs.test_patch_sha256,
        "golden_patch_sha256": bundle.evaluation_inputs.golden_patch_sha256,
        "repeat_count": repeat,
    }
    digest = sha256_digest(canonical_json_bytes(document)).removeprefix("sha256:")
    return ValidationIdentity.model_validate(
        {"validation_id": f"val_{digest[:32]}", **document}
    )


def _evaluation_record(
    execution: EvaluatorExecution,
    selectors: tuple[SelectorResult, ...],
) -> EvaluationRecord:
    return EvaluationRecord(
        phase=execution.phase,
        repeat_index=execution.repeat_index,
        container_id=execution.container_id,
        workspace_id=execution.workspace_id,
        evaluation_storage_id=execution.evaluation_storage_id,
        status=execution.status,
        harness_status=execution.harness_status,
        runner_exit_code=execution.runner_exit_code,
        duration_ms=execution.duration_ms,
        test_patch_sha256=execution.test_patch_sha256,
        golden_patch_sha256=execution.golden_patch_sha256,
        outcome="accepted" if all(item.matched for item in selectors) else "rejected",
        selector_results=selectors,
        cleaned_up=execution.cleaned_up,
    )


def _phase_summary(
    phase: EvaluationPhase,
    records: list[EvaluationRecord],
) -> PhaseSummary:
    status_vectors = [
        tuple(
            (item.requested_selector, item.actual_status)
            for item in record.selector_results
        )
        for record in records
    ]
    flaky = any(vector != status_vectors[0] for vector in status_vectors[1:])
    accepted = all(record.outcome == "accepted" for record in records)
    selector_sets = [record.selector_results for record in records]
    p2p_sets = [
        [item for item in selectors if item.group == TestGroup.PASS_TO_PASS]
        for selectors in selector_sets
    ]
    f2p_sets = [
        [item for item in selectors if item.group == TestGroup.FAIL_TO_PASS]
        for selectors in selector_sets
    ]
    return PhaseSummary(
        phase=phase,
        repeat_count=len(records),
        outcome="flaky" if flaky else "accepted" if accepted else "rejected",
        pass_to_pass_matched=min(
            sum(item.matched for item in selectors) for selectors in p2p_sets
        ),
        pass_to_pass_total=len(p2p_sets[0]),
        fail_to_pass_matched=min(
            sum(item.matched for item in selectors) for selectors in f2p_sets
        ),
        fail_to_pass_total=len(f2p_sets[0]),
        duration_ms=sum(record.duration_ms for record in records),
    )


def _validation_status(
    baseline: PhaseSummary,
    golden: PhaseSummary | None,
) -> ValidationStatus:
    if baseline.outcome == "flaky":
        return ValidationStatus.INVALID_BASELINE_FLAKY
    if baseline.outcome == "rejected":
        return ValidationStatus.INVALID_BASELINE
    if golden is None:
        raise AssertionError("Accepted baseline must be followed by golden validation")
    if golden.outcome == "flaky":
        return ValidationStatus.INVALID_GOLDEN_FLAKY
    if golden.outcome == "rejected":
        return ValidationStatus.INVALID_GOLDEN
    return ValidationStatus.VALID


def _markdown_report(result: ValidationResult) -> str:
    baseline = result.baseline
    golden = result.golden
    golden_lines = (
        "Golden\n"
        f"  PASS_TO_PASS     {golden.pass_to_pass_matched}/"
        f"{golden.pass_to_pass_total} passed\n"
        f"  FAIL_TO_PASS     {golden.fail_to_pass_matched}/"
        f"{golden.fail_to_pass_total} passed\n"
        if golden is not None
        else "Golden\n  Not run because baseline validation was invalid.\n"
    )
    return (
        "# Task validation\n\n"
        "Baseline\n"
        f"  PASS_TO_PASS     {baseline.pass_to_pass_matched}/"
        f"{baseline.pass_to_pass_total} passed\n"
        f"  FAIL_TO_PASS     {baseline.fail_to_pass_matched}/"
        f"{baseline.fail_to_pass_total} matched expected baseline status\n\n"
        f"{golden_lines}\n"
        f"Result\n  {result.validation_status.value.upper()}\n"
    )


def _artifact_paths(
    evaluations: list[EvaluationRecord],
    golden_ran: bool,
) -> tuple[str, ...]:
    paths = [
        "command.json",
        "bundle.snapshot.json",
        "bundle.lock.json",
        "validation-identity.json",
    ]
    for evaluation in evaluations:
        prefix = (
            f"{evaluation.phase.value}/repeat-{evaluation.repeat_index:03d}"
        )
        paths.extend(
            (
                f"{prefix}/plan.json",
                f"{prefix}/task-metadata.json",
                f"{prefix}/patch-apply.log",
                f"{prefix}/prepare.stdout.log",
                f"{prefix}/prepare.stderr.log",
                f"{prefix}/runner.stdout.log",
                f"{prefix}/runner.stderr.log",
                f"{prefix}/results.json",
                f"{prefix}/classification.json",
            )
        )
    paths.append("baseline/summary.json")
    if golden_ran:
        paths.append("golden/summary.json")
    paths.extend(("report.json", "report.md"))
    return tuple(paths)
