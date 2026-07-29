import tempfile
import uuid
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from task_bundle.bundle.loader import LoadedBundle, load_bundle
from task_bundle.bundle.snapshot import create_snapshot
from task_bundle.database import Database
from task_bundle.errors import ErrorCode, ErrorContext, TaskBundleError
from task_bundle.image.artifacts import ArtifactWriter
from task_bundle.image.docker import DockerCommandResult, DockerRunner, SystemDockerRunner
from task_bundle.image.models import BundleLock, RuntimePolicy
from task_bundle.image.records import CommandStore
from task_bundle.models import (
    CommandStatus,
    EvaluationPhase,
    EvaluationPlan,
    ResolvedSource,
    SolverConfig,
    SourceManifest,
)
from task_bundle.run.candidate import (
    CandidateBuilder,
    enforce_patch_policy,
    verify_baseline_manifest,
)
from task_bundle.run.context import validate_solver_context
from task_bundle.run.docker import (
    DockerSolver,
    SolverBackend,
    SolverOutcome,
    SolverRequest,
    validate_patch_input,
)
from task_bundle.run.models import (
    CandidateTree,
    FilesystemManifest,
    PatchPolicyStatus,
    RunEvaluationStatus,
    RunOptions,
    RunResult,
    SolverExecutionResult,
    SolverStatus,
    SolverType,
)
from task_bundle.run.records import RunStore
from task_bundle.source.manifest import source_manifest_digest
from task_bundle.source.persistence import (
    SOURCE_MANIFEST_RELATIVE_PATH,
    SOURCE_SNAPSHOT_RELATIVE_PATH,
    load_source_manifest,
    load_source_snapshot,
)
from task_bundle.validation.docker import (
    DockerEvaluator,
    EvaluationBackend,
    EvaluationRequest,
)
from task_bundle.validation.models import (
    EvaluationRecord,
    PhaseSummary,
)
from task_bundle.validation.patch import validate_patch
from task_bundle.validation.records import ValidationStore
from task_bundle.validation.result import classify_result
from task_bundle.validation.service import (
    ValidationService,
    create_validation_identity,
    evaluation_record,
    phase_summary,
    write_execution_artifacts,
)

DockerFactory = Callable[[Path], DockerRunner]
EvaluatorFactory = Callable[[DockerRunner], EvaluationBackend]
SolverFactory = Callable[[DockerRunner], SolverBackend]


class CandidateBackend(Protocol):
    def build(
        self,
        *,
        baseline_root: Path,
        baseline_manifest: FilesystemManifest,
        candidate_root: Path,
        candidate_manifest: FilesystemManifest,
        expected_baseline_tree: str,
        solver: SolverConfig,
    ) -> tuple[CandidateTree, bytes]: ...


CandidateFactory = Callable[[Path], CandidateBackend]


class RunService:
    def __init__(
        self,
        *,
        database: Database,
        cli_version: str,
        docker_factory: DockerFactory = SystemDockerRunner.create,
        evaluator_factory: EvaluatorFactory = DockerEvaluator,
        solver_factory: SolverFactory = DockerSolver,
        candidate_factory: CandidateFactory = CandidateBuilder,
    ) -> None:
        self.command_store = CommandStore(database)
        self.run_store = RunStore(database)
        self.validation_store = ValidationStore(database)
        self.validation_service = ValidationService(
            database=database,
            cli_version=cli_version,
            docker_factory=docker_factory,
            backend_factory=evaluator_factory,
        )
        self.cli_version = cli_version
        self.docker_factory = docker_factory
        self.evaluator_factory = evaluator_factory
        self.solver_factory = solver_factory
        self.candidate_factory = candidate_factory

    def run(self, bundle_path: Path, options: RunOptions) -> RunResult:
        command_id = f"cmd_{uuid.uuid4().hex}"
        started_at = datetime.now(UTC)
        self.command_store.start(
            command_id=command_id,
            task_id=bundle_path.name or "unknown-task",
            bundle_path=bundle_path,
            command_type="run",
            started_at=started_at,
        )
        writer: ArtifactWriter | None = None
        runner: DockerRunner | None = None
        image_id: str | None = None
        validation_id: str | None = None
        solver_execution: SolverExecutionResult | None = None
        solver_recorded = False
        context_digest: str | None = None
        try:
            bundle = load_bundle(bundle_path)
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
            artifact_relative = writer.root.relative_to(bundle.root).as_posix()
            self.run_store.set_artifact_root(command_id, artifact_relative)
            self._write_command_artifacts(writer, bundle, command_id, options)
            lock, runtime_policy = self.validation_service.load_preconditions(bundle)
            image_id = lock.image_id
            writer.write_model("bundle.lock.json", lock, "bundle-lock")
            source_manifest, source_snapshot = self._load_source_metadata(bundle)
            if (
                source_manifest_digest(source_manifest)
                != lock.source.source_tree_digest
                or source_snapshot.tree_sha != lock.source.tree_sha
            ):
                raise TaskBundleError(
                    ErrorCode.LOCK_MISMATCH,
                    "Locked source metadata is stale.",
                    ErrorContext(
                        phase="run-preflight",
                        expected="Source manifest and tree SHA matching the bundle lock",
                        actual="Persisted source metadata differs from the lock",
                        corrective_action="Run `task init --rebuild`.",
                    ),
                )
            with tempfile.TemporaryDirectory(prefix="task-bundle-run-") as runtime_name:
                runtime_root = Path(runtime_name)
                runner = self.docker_factory(runtime_root / "docker-home")
                self.validation_service.verify_locked_image(
                    bundle,
                    lock,
                    runtime_policy,
                    runner,
                )
                identity = create_validation_identity(
                    bundle,
                    lock,
                    bundle.task.evaluation.repeat,
                )
                validation = self.validation_store.matching_success(identity)
                if validation is None:
                    raise TaskBundleError(
                        ErrorCode.VALIDATION_REQUIRED,
                        "A matching successful validation is required.",
                        ErrorContext(
                            phase="run-preflight",
                            expected=(
                                "Successful validation for all locked evaluation inputs "
                                f"with at least {identity.repeat_count} repeat(s)"
                            ),
                            actual="No reusable validation was found",
                            corrective_action=f"Run `task validate {bundle.root}`.",
                        ),
                    )
                validation_id = validation.validation_id
                writer.write_json(
                    "validation-reference.json",
                    {
                        "schema_version": "1",
                        "validation_id": validation.validation_id,
                        "validation_command_id": validation.command_id,
                        "repeat_count": validation.repeat_count,
                        "required_repeat_count": identity.repeat_count,
                        "selector_digest": identity.selector_digest,
                        "hidden_patch_digest": identity.test_patch_sha256,
                        "golden_patch_digest": identity.golden_patch_sha256,
                    },
                    "validation-reference",
                )
                self.run_store.event(
                    command_id,
                    "RUN_PRECONDITIONS_VERIFIED",
                    {"validation_id": validation.validation_id},
                )
                evaluator = self.evaluator_factory(runner)
                preflight_summary, preflight_record = self._run_evaluation(
                    bundle=bundle,
                    lock=lock,
                    runtime_policy=runtime_policy,
                    command_id=command_id,
                    phase=EvaluationPhase.BASELINE,
                    backend=evaluator,
                    writer=writer,
                    prefix="baseline",
                    keep_containers=options.keep_containers,
                )
                self.run_store.record_evaluation(
                    command_id,
                    preflight_record,
                    patch_digest=bundle.evaluation_inputs.test_patch_sha256,
                )
                self.run_store.event(
                    command_id,
                    "BASELINE_PREFLIGHT_COMPLETED",
                    {"outcome": preflight_summary.outcome},
                )
                if preflight_summary.outcome != "accepted":
                    self.run_store.record_solver_not_run(
                        command_id,
                        solver_type=options.solver,
                        validation_id=validation.validation_id,
                    )
                    solver_recorded = True
                    raise TaskBundleError(
                        ErrorCode.BASELINE_GUARDRAIL_FAILURE,
                        "Fresh baseline preflight no longer matches task expectations.",
                        ErrorContext(
                            phase="baseline-preflight",
                            expected="P2P passing and F2P matching baseline statuses",
                            actual="One or more baseline selectors did not match",
                            corrective_action="Inspect baseline artifacts and revalidate the task.",
                            artifact=Path(f"{artifact_relative}/baseline"),
                        ),
                    )
                context_root, context_manifest = validate_solver_context(
                    options.solver_context,
                    bundle=bundle,
                )
                context_digest = (
                    None if context_manifest is None else context_manifest.digest
                )
                if context_manifest is not None:
                    writer.write_model(
                        "solver/context-manifest.json",
                        context_manifest,
                        "solver-context-manifest",
                    )
                patch_input = (
                    validate_patch_input(
                        options.patch,
                        bundle.task.solver.max_patch_bytes,
                    )
                    if options.patch is not None
                    else None
                )
                if patch_input is not None:
                    writer.write_json(
                        "solver/patch-input-metadata.json",
                        {
                            "schema_version": "1",
                            "size_bytes": len(patch_input),
                            "sha256": _digest(patch_input),
                        },
                        "solver-patch-input-metadata",
                    )
                export_root = runtime_root / "solver-export"
                export_root.mkdir()
                outcome = self.solver_factory(runner).run(
                    SolverRequest(
                        bundle=bundle,
                        lock=lock,
                        runtime_policy=runtime_policy,
                        command_id=command_id,
                        options=options,
                        context_root=context_root,
                        context_manifest=context_manifest,
                        patch_input=patch_input,
                        export_root=export_root,
                    )
                )
                solver_execution = outcome.execution
                self._write_solver_artifacts(writer, outcome)
                if solver_execution.status != SolverStatus.SUCCEEDED:
                    self.run_store.record_solver(
                        command_id,
                        solver_execution,
                        validation_id=validation.validation_id,
                        candidate=None,
                        patch_policy=PatchPolicyStatus.NOT_RUN,
                    )
                    solver_recorded = True
                    raise TaskBundleError(
                        ErrorCode.SOLVER_EXECUTION_ERROR,
                        "Solver exited unsuccessfully.",
                        ErrorContext(
                            phase="solver",
                            expected="Solver exit code 0",
                            actual=(
                                f"exit {solver_execution.exit_code}: "
                                f"{solver_execution.stderr.strip()[:1000]}"
                            ),
                            corrective_action="Inspect solver stdout and stderr artifacts.",
                            artifact=Path(f"{artifact_relative}/solver"),
                            details={"solver": options.solver.value},
                        ),
                    )
                self.run_store.record_solver(
                    command_id,
                    solver_execution,
                    validation_id=validation.validation_id,
                    candidate=None,
                    patch_policy=PatchPolicyStatus.NOT_RUN,
                )
                solver_recorded = True
                self.run_store.event(command_id, "SOLVER_COMPLETED")
                self.run_store.event(command_id, "WORKSPACE_EXPORT_VALIDATED")
                candidate, patch = self._finalize_candidate(
                    bundle=bundle,
                    lock_tree_sha=lock.source.tree_sha,
                    source_manifest=source_manifest,
                    outcome=outcome,
                    export_root=export_root,
                )
                self.run_store.event(
                    command_id,
                    "CANDIDATE_TREE_CONSTRUCTED",
                    {"candidate_tree_sha": candidate.candidate_tree_sha},
                )
                self.run_store.event(
                    command_id,
                    "CANDIDATE_PATCH_GENERATED",
                    {"candidate_patch_sha256": candidate.candidate_patch_sha256},
                )
                self.run_store.event(
                    command_id,
                    "CANDIDATE_PATCH_ROUNDTRIP_VERIFIED",
                )
                hidden_patch = validate_patch(
                    bundle.root / bundle.task.evaluation.test_patch,
                    phase=EvaluationPhase.CANDIDATE,
                    repeat_index=1,
                    golden=False,
                )
                try:
                    enforce_patch_policy(
                        candidate=candidate,
                        patch=patch,
                        candidate_manifest=_required_candidate_manifest(outcome),
                        hidden_patch=hidden_patch,
                        solver=bundle.task.solver,
                    )
                except TaskBundleError:
                    self.run_store.update_solver_candidate(
                        command_id,
                        candidate=candidate,
                        patch_policy=PatchPolicyStatus.REJECTED,
                    )
                    raise
                self.run_store.event(command_id, "PATCH_POLICY_ACCEPTED")
                self._write_candidate_artifacts(
                    writer,
                    candidate,
                    patch,
                    _required_candidate_manifest(outcome),
                )
                self.run_store.update_solver_candidate(
                    command_id,
                    candidate=candidate,
                    patch_policy=PatchPolicyStatus.ACCEPTED,
                )
                self.run_store.event(
                    command_id,
                    "CANDIDATE_FINALIZED",
                    {"candidate_patch_sha256": candidate.candidate_patch_sha256},
                )
                self.run_store.event(command_id, "CANDIDATE_EVALUATOR_STARTED")
                candidate_summary, candidate_record = self._run_evaluation(
                    bundle=bundle,
                    lock=lock,
                    runtime_policy=runtime_policy,
                    command_id=command_id,
                    phase=EvaluationPhase.CANDIDATE,
                    backend=evaluator,
                    writer=writer,
                    prefix="candidate",
                    keep_containers=options.keep_containers,
                    candidate_patch=patch,
                )
                self.run_store.record_evaluation(
                    command_id,
                    candidate_record,
                    patch_digest=candidate.candidate_patch_sha256,
                )
                resolved = candidate_summary.outcome == "accepted"
                finished_at = datetime.now(UTC)
                records = (preflight_record, candidate_record)
                retained = tuple(
                    record.container_id for record in records if not record.cleaned_up
                )
                if not solver_execution.cleaned_up:
                    retained = (*retained, solver_execution.container_id)
                warnings = (
                    (
                        "Retained solver or evaluator resources may contain candidate "
                        "workspaces; retained candidate evaluators contain hidden tests, "
                        "selectors, and evaluation output."
                    ),
                ) if retained else ()
                result = RunResult(
                    command_id=command_id,
                    task_id=bundle.task.task.id,
                    command_status="succeeded",
                    evaluation_status="resolved" if resolved else "unresolved",
                    resolved=resolved,
                    bundle_input_digest=bundle.bundle_input_digest,
                    task_image_id=lock.image_id,
                    validation_id=validation.validation_id,
                    selector_digest=bundle.evaluation_inputs.selectors_sha256,
                    hidden_patch_digest=bundle.evaluation_inputs.test_patch_sha256,
                    golden_patch_digest=bundle.evaluation_inputs.golden_patch_sha256,
                    baseline_preflight=preflight_summary,
                    solver=solver_execution,
                    solver_context_digest=context_digest,
                    candidate_tree=candidate,
                    patch_policy_status="accepted",
                    candidate_summary=candidate_summary,
                    candidate_results=candidate_record.selector_results,
                    started_at=started_at,
                    finished_at=finished_at,
                    artifact_directory=artifact_relative,
                    artifact_paths=_run_artifact_paths(
                        has_context=context_manifest is not None,
                        has_patch_input=patch_input is not None,
                    ),
                    cleanup_complete=all(record.cleaned_up for record in records)
                    and solver_execution.cleaned_up,
                    retained_containers=retained,
                    warnings=warnings,
                )
                writer.write_model("report.json", result, "run-report-json")
                writer.write_text(
                    "report.md",
                    _markdown_report(result),
                    "run-report-markdown",
                )
                self.run_store.finish(
                    command_id,
                    status=CommandStatus.SUCCEEDED,
                    evaluation_status=(
                        RunEvaluationStatus.RESOLVED
                        if resolved
                        else RunEvaluationStatus.UNRESOLVED
                    ),
                    resolved=resolved,
                    exit_code=0 if resolved else 1,
                    image_id=lock.image_id,
                    message=(
                        "Candidate resolved the task."
                        if resolved
                        else "Candidate evaluated successfully but did not resolve the task."
                    ),
                )
                return result
        except TaskBundleError as error:
            if (
                validation_id is not None
                and not solver_recorded
                and error.exit_code == 5
            ):
                with suppress(TaskBundleError):
                    self.run_store.record_solver_error(
                        command_id,
                        solver_type=options.solver,
                        argv=_safe_solver_argv(options),
                        context_digest=context_digest,
                        validation_id=validation_id,
                        timed_out=error.code == ErrorCode.SOLVER_TIMEOUT,
                        container_id=_error_detail_string(error, "container_id"),
                        cleaned_up=_error_detail_bool(error, "cleaned_up"),
                    )
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
                self.run_store.finish(
                    command_id,
                    status=CommandStatus.INTERRUPTED,
                    evaluation_status=RunEvaluationStatus.NOT_RUN,
                    resolved=None,
                    exit_code=130,
                    image_id=image_id,
                    message="Run interrupted.",
                    outcome="interrupted",
                )
            raise
        except Exception as error:
            with suppress(TaskBundleError):
                self.run_store.finish(
                    command_id,
                    status=CommandStatus.FAILED,
                    evaluation_status=RunEvaluationStatus.INFRA_ERROR,
                    resolved=None,
                    exit_code=3,
                    image_id=image_id,
                    message=f"Unexpected run failure: {type(error).__name__}",
                    outcome="INTERNAL_ERROR",
                )
            raise

    def _run_evaluation(
        self,
        *,
        bundle: LoadedBundle,
        lock: BundleLock,
        runtime_policy: RuntimePolicy,
        command_id: str,
        phase: EvaluationPhase,
        backend: EvaluationBackend,
        writer: ArtifactWriter,
        prefix: str,
        keep_containers: bool,
        candidate_patch: bytes | None = None,
    ) -> tuple[PhaseSummary, EvaluationRecord]:
        plan = EvaluationPlan(
            phase=phase,
            repeat_index=1,
            pass_to_pass=bundle.task.evaluation.pass_to_pass,
            fail_to_pass=bundle.task.evaluation.fail_to_pass,
            timeout_seconds=runtime_policy.timeout_seconds,
        )
        writer.write_model(f"{prefix}/plan.json", plan, "evaluation-plan")
        execution = backend.run(
            EvaluationRequest(
                bundle=bundle,
                lock=lock,
                runtime_policy=runtime_policy,
                command_id=command_id,
                plan=plan,
                keep_container=keep_containers,
                candidate_patch=candidate_patch,
            )
        )
        selectors = classify_result(execution.result, plan)
        record = evaluation_record(execution, selectors)
        write_execution_artifacts(writer, prefix, execution, selectors)
        summary = phase_summary(phase, [record])
        writer.write_model(f"{prefix}/summary.json", summary, "evaluation-phase-summary")
        return summary, record

    def _finalize_candidate(
        self,
        *,
        bundle: LoadedBundle,
        lock_tree_sha: str,
        source_manifest: SourceManifest,
        outcome: SolverOutcome,
        export_root: Path,
    ) -> tuple[CandidateTree, bytes]:
        baseline_root = _required_path(outcome.baseline_root)
        baseline_manifest = _required_manifest(outcome.baseline_manifest)
        candidate_root = _required_path(outcome.candidate_root)
        candidate_manifest = _required_manifest(outcome.candidate_manifest)
        verify_baseline_manifest(baseline_manifest, source_manifest)
        trusted = export_root / "trusted"
        trusted.mkdir()
        return self.candidate_factory(trusted).build(
            baseline_root=baseline_root,
            baseline_manifest=baseline_manifest,
            candidate_root=candidate_root,
            candidate_manifest=candidate_manifest,
            expected_baseline_tree=lock_tree_sha,
            solver=bundle.task.solver,
        )

    def _load_source_metadata(
        self,
        bundle: LoadedBundle,
    ) -> tuple[SourceManifest, ResolvedSource]:
        try:
            manifest = load_source_manifest(bundle.root / SOURCE_MANIFEST_RELATIVE_PATH)
            snapshot = load_source_snapshot(bundle.root / SOURCE_SNAPSHOT_RELATIVE_PATH)
        except TaskBundleError as error:
            raise TaskBundleError(
                ErrorCode.LOCK_MISMATCH,
                "Locked source metadata is required for candidate extraction.",
                ErrorContext(
                    phase="run-preflight",
                    expected="Current .task source snapshot and manifest",
                    actual=str(error),
                    corrective_action="Run `task init --rebuild`.",
                ),
            ) from error
        return manifest, snapshot

    def _write_command_artifacts(
        self,
        writer: ArtifactWriter,
        bundle: LoadedBundle,
        command_id: str,
        options: RunOptions,
    ) -> None:
        writer.write_json(
            "command.json",
            {
                "schema_version": "1",
                "command_id": command_id,
                "command_type": "run",
                "task_id": bundle.task.task.id,
                "bundle_input_digest": bundle.bundle_input_digest,
                "solver": options.solver.value,
                "argv": list(options.command),
                "keep_containers": options.keep_containers,
            },
            "command",
        )
        writer.write_model(
            "bundle.snapshot.json",
            create_snapshot(bundle, self.cli_version),
            "bundle-snapshot",
        )
        sections: list[str] = []
        for name, relative in (
            ("Description", bundle.task.public.description),
            ("Requirements", bundle.task.public.requirements),
            ("Interface", bundle.task.public.interface),
        ):
            if relative is None:
                continue
            sections.append(
                f"## {name}\n\n"
                f"{(bundle.root / Path(relative)).read_text(encoding='utf-8')}"
            )
        writer.write_text(
            "solver/public-context.md",
            "# Public task context\n\n" + "\n\n".join(sections),
            "solver-public-context",
        )

    def _write_solver_artifacts(
        self,
        writer: ArtifactWriter,
        outcome: SolverOutcome,
    ) -> None:
        writer.write_model(
            "solver/command.json",
            outcome.execution,
            "solver-command",
        )
        writer.write_text("solver/stdout.log", outcome.execution.stdout, "solver-stdout")
        writer.write_text("solver/stderr.log", outcome.execution.stderr, "solver-stderr")
        if outcome.candidate_manifest is not None:
            writer.write_model(
                "solver/exported-tree-manifest.json",
                outcome.candidate_manifest,
                "solver-export-manifest",
            )

    def _write_candidate_artifacts(
        self,
        writer: ArtifactWriter,
        candidate: CandidateTree,
        patch: bytes,
        manifest: FilesystemManifest,
    ) -> None:
        writer.write_model("solver/candidate-tree.json", candidate, "candidate-tree")
        writer.write_json(
            "solver/changed-paths.json",
            {"schema_version": "1", "paths": list(candidate.changed_paths)},
            "candidate-changed-paths",
        )
        writer.write_json(
            "solver/patch-policy.json",
            {"schema_version": "1", "status": "accepted"},
            "candidate-patch-policy",
        )
        writer.write_bytes("solver/candidate.patch", patch, "candidate-patch")

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
                last = (
                    getattr(runner, "last_failure_result", None)
                    or getattr(runner, "last_result", None)
                    if runner is not None
                    else None
                )
                if isinstance(last, DockerCommandResult):
                    writer.write_text(
                        "failure/docker.stdout.log",
                        last.stdout,
                        "failure-docker-stdout",
                    )
                    writer.write_text(
                        "failure/docker.stderr.log",
                        last.stderr,
                        "failure-docker-stderr",
                    )
                elif error.context.details is not None:
                    stdout = error.context.details.get("stdout")
                    stderr = error.context.details.get("stderr")
                    if isinstance(stdout, str):
                        writer.write_text(
                            "failure/docker.stdout.log",
                            stdout,
                            "failure-docker-stdout",
                        )
                    if isinstance(stderr, str):
                        writer.write_text(
                            "failure/docker.stderr.log",
                            stderr,
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
                            "actual": error.context.actual,
                            "corrective_action": error.context.corrective_action,
                        },
                    },
                    "failure-report",
                )
        with suppress(TaskBundleError):
            self.run_store.finish(
                command_id,
                status=CommandStatus.FAILED,
                evaluation_status=(
                    RunEvaluationStatus.INFRA_ERROR
                    if error.exit_code == 3
                    else RunEvaluationStatus.NOT_RUN
                ),
                resolved=None,
                exit_code=error.exit_code,
                image_id=image_id,
                message=str(error),
                outcome=error.code.value,
            )


def _required_path(path: Path | None) -> Path:
    if path is None:
        raise AssertionError("Successful solver omitted exported workspace")
    return path


def _required_manifest(
    manifest: FilesystemManifest | None,
) -> FilesystemManifest:
    if not isinstance(manifest, FilesystemManifest):
        raise AssertionError("Successful solver omitted exported manifest")
    return manifest


def _required_candidate_manifest(outcome: SolverOutcome) -> FilesystemManifest:
    return _required_manifest(outcome.candidate_manifest)


def _safe_solver_argv(options: RunOptions) -> tuple[str, ...]:
    if options.solver == SolverType.NOOP:
        return ("/bin/true",)
    if options.solver == SolverType.PATCH:
        return ("git", "apply", "/task/input/candidate.patch")
    return options.command


def _digest(payload: bytes) -> str:
    from task_bundle.bundle.canonical import sha256_digest

    return sha256_digest(payload)


def _error_detail_string(error: TaskBundleError, name: str) -> str | None:
    details = error.context.details or {}
    value = details.get(name)
    return value if isinstance(value, str) else None


def _error_detail_bool(error: TaskBundleError, name: str) -> bool:
    details = error.context.details or {}
    value = details.get(name)
    return value if isinstance(value, bool) else False


def _markdown_report(result: RunResult) -> str:
    baseline = result.baseline_preflight
    candidate = result.candidate_summary
    return (
        "# Task run\n\n"
        "Baseline preflight\n"
        f"  PASS_TO_PASS     {baseline.pass_to_pass_matched}/"
        f"{baseline.pass_to_pass_total} passed\n"
        f"  FAIL_TO_PASS     {baseline.fail_to_pass_matched}/"
        f"{baseline.fail_to_pass_total} matched baseline expectations\n\n"
        "Solver\n"
        f"  Type             {result.solver.solver_type.value}\n"
        f"  Status           {result.solver.status.value}\n"
        f"  Duration         {result.solver.duration_ms} ms\n"
        f"  Changed files    {len(result.candidate_tree.changed_paths)}\n\n"
        "Candidate\n"
        f"  PASS_TO_PASS     {candidate.pass_to_pass_matched}/"
        f"{candidate.pass_to_pass_total} passed\n"
        f"  FAIL_TO_PASS     {candidate.fail_to_pass_matched}/"
        f"{candidate.fail_to_pass_total} passed\n\n"
        f"Result\n  {'RESOLVED' if result.resolved else 'UNRESOLVED'}\n"
    )


def _run_artifact_paths(
    *,
    has_context: bool,
    has_patch_input: bool,
) -> tuple[str, ...]:
    paths = [
        "command.json",
        "bundle.snapshot.json",
        "bundle.lock.json",
        "validation-reference.json",
        "baseline/plan.json",
        "baseline/patch-apply.log",
        "baseline/prepare.stdout.log",
        "baseline/prepare.stderr.log",
        "baseline/runner.stdout.log",
        "baseline/runner.stderr.log",
        "baseline/results.json",
        "baseline/classification.json",
        "baseline/summary.json",
        "solver/public-context.md",
        "solver/command.json",
        "solver/stdout.log",
        "solver/stderr.log",
        "solver/exported-tree-manifest.json",
        "solver/candidate-tree.json",
        "solver/changed-paths.json",
        "solver/patch-policy.json",
        "solver/candidate.patch",
        "candidate/plan.json",
        "candidate/patch-apply.log",
        "candidate/prepare.stdout.log",
        "candidate/prepare.stderr.log",
        "candidate/runner.stdout.log",
        "candidate/runner.stderr.log",
        "candidate/results.json",
        "candidate/classification.json",
        "candidate/summary.json",
        "report.json",
        "report.md",
    ]
    if has_context:
        paths.append("solver/context-manifest.json")
    if has_patch_input:
        paths.append("solver/patch-input-metadata.json")
    return tuple(paths)
