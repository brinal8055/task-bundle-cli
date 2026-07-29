import tempfile
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager, suppress
from dataclasses import dataclass
from pathlib import Path

from task_bundle.bundle.loader import LoadedBundle, load_bundle
from task_bundle.bundle.snapshot import (
    create_snapshot,
    write_bytes_atomic,
)
from task_bundle.database import Database
from task_bundle.errors import ErrorCode, ErrorContext, TaskBundleError
from task_bundle.image.artifacts import ArtifactWriter
from task_bundle.image.build import (
    build_command_record,
    build_image,
    required_image_labels,
)
from task_bundle.image.context import create_build_context
from task_bundle.image.docker import (
    DockerCommandResult,
    DockerRunner,
    SystemDockerRunner,
)
from task_bundle.image.inspect import inspect_image, inspect_image_if_present
from task_bundle.image.lock import (
    LOCK_RELATIVE_PATH,
    compare_bundle_lock,
    create_bundle_lock,
    load_bundle_lock,
    stale_lock_error,
    write_bundle_lock,
)
from task_bundle.image.models import (
    BundleLock,
    DockerCommandRecord,
    InitResult,
    RuntimePolicy,
)
from task_bundle.image.records import CommandStore
from task_bundle.image.runtime import (
    create_runtime_policy,
    create_smoke_plan,
    smoke_check,
)
from task_bundle.image.validation import (
    task_image_reference,
    validate_base_image_reference,
    validate_build_args,
    validate_platform,
    validate_runtime_settings,
    validate_task_id_for_build,
)
from task_bundle.models import (
    BaseImageEnvironment,
    CommandStatus,
)
from task_bundle.source.service import (
    MaterializedSource,
    materialize_bundle_source,
)


@dataclass(frozen=True, slots=True)
class InitOptions:
    rebuild: bool = False
    no_cache: bool = False
    platform: str | None = None
    keep_build_context: bool = False


SourceFactory = Callable[
    [LoadedBundle],
    AbstractContextManager[MaterializedSource],
]
DockerFactory = Callable[[Path], DockerRunner]


class InitService:
    def __init__(
        self,
        *,
        database: Database,
        cli_version: str,
        source_factory: SourceFactory = materialize_bundle_source,
        docker_factory: DockerFactory = SystemDockerRunner.create,
    ) -> None:
        self.store = CommandStore(database)
        self.cli_version = cli_version
        self.source_factory = source_factory
        self.docker_factory = docker_factory

    def run(self, bundle_path: Path, options: InitOptions) -> InitResult:
        command_id = f"cmd_{uuid.uuid4().hex}"
        self.store.start(
            command_id=command_id,
            task_id=bundle_path.name or "unknown-task",
            bundle_path=bundle_path,
        )
        writer: ArtifactWriter | None = None
        runner: DockerRunner | None = None
        image_id: str | None = None
        lock_path: Path | None = None
        lock_written = False
        previous_lock: bytes | None = None
        try:
            bundle = load_bundle(bundle_path)
            self.store.update_identity(
                command_id,
                task_id=bundle.task.task.id,
                bundle_digest=bundle.bundle_input_digest,
            )
            self.store.event(
                command_id,
                "BUNDLE_LOADED",
                {"bundle_input_digest": bundle.bundle_input_digest},
            )
            writer = ArtifactWriter(
                bundle_root=bundle.root,
                task_id=bundle.task.task.id,
                command_id=command_id,
                store=self.store,
            )
            writer.write_json(
                "command.json",
                {
                    "schema_version": "1",
                    "command_id": command_id,
                    "command_type": "init",
                    "task_id": bundle.task.task.id,
                    "bundle_input_digest": bundle.bundle_input_digest,
                    "options": {
                        "rebuild": options.rebuild,
                        "no_cache": options.no_cache,
                        "platform": options.platform,
                        "keep_build_context": options.keep_build_context,
                    },
                },
                "command",
            )
            platform = self._validate_configuration(bundle, options)
            runtime_policy = create_runtime_policy(bundle.task.environment.runtime)
            writer.write_model(
                "environment/runtime-policy.json",
                runtime_policy,
                "runtime-policy",
            )
            image_reference = task_image_reference(
                bundle.task.task.id,
                bundle.bundle_input_digest,
                platform,
            )
            lock_path = bundle.root / LOCK_RELATIVE_PATH
            if lock_path.exists():
                try:
                    previous_lock = lock_path.read_bytes()
                except OSError as error:
                    raise TaskBundleError(
                        ErrorCode.LOCK_READ_ERROR,
                        "Existing bundle lock could not be read.",
                        ErrorContext(
                            phase="lock-read",
                            expected="A readable existing lock for transactional replacement",
                            actual=str(error),
                            corrective_action="Correct lockfile permissions and retry.",
                            path=lock_path,
                        ),
                    ) from error

            with tempfile.TemporaryDirectory(prefix="task-bundle-init-") as runtime:
                runner = self.docker_factory(Path(runtime) / "docker-home")
                self.store.event(
                    command_id,
                    "DOCKER_PREFLIGHT_PASSED",
                    {
                        "client_version": runner.environment_info.client_version,
                        "server_version": runner.environment_info.server_version,
                        "rootless": runner.environment_info.rootless,
                    },
                )
                writer.write_model(
                    "environment/docker.json",
                    runner.environment_info,
                    "docker-environment",
                )
                writer.write_json(
                    "environment/docker-preflight-commands.json",
                    {
                        "schema_version": "1",
                        "commands": [
                            ["docker", "version", "--format", "{{json .}}"],
                            ["docker", "info", "--format", "{{json .}}"],
                        ],
                    },
                    "docker-preflight-commands",
                )
                warnings = self._environment_warnings(runner, platform)
                current = self._current_lock_result(
                    bundle=bundle,
                    options=options,
                    runtime_policy=runtime_policy,
                    image_reference=image_reference,
                    platform=platform,
                    lock_path=lock_path,
                    runner=runner,
                    command_id=command_id,
                    writer=writer,
                    warnings=warnings,
                )
                if current is not None:
                    self.store.finish(
                        command_id,
                        status=CommandStatus.SUCCEEDED,
                        outcome=current.status,
                        exit_code=0,
                        image_id=current.image_id,
                        message="Existing task image and bundle lock are current.",
                    )
                    return current

                snapshot = create_snapshot(bundle, self.cli_version)
                writer.write_model(
                    "bundle/bundle.snapshot.json",
                    snapshot,
                    "bundle-snapshot",
                )
                with self.source_factory(bundle) as source:
                    self.store.event(
                        command_id,
                        "SOURCE_MATERIALIZED",
                        {
                            "resolved_commit": source.resolved.resolved_commit,
                            "tree_sha": source.resolved.tree_sha,
                            "source_tree_digest": source.resolved.source_tree_digest,
                        },
                    )
                    writer.write_model(
                        "source/source.snapshot.json",
                        source.resolved,
                        "source-snapshot",
                    )
                    writer.write_model(
                        "source/source.manifest.json",
                        source.manifest,
                        "source-manifest",
                    )
                    writer.write_text(
                        "source/fetch.stdout.log",
                        source.fetch_stdout,
                        "source-fetch-stdout",
                    )
                    writer.write_text(
                        "source/fetch.stderr.log",
                        source.fetch_stderr,
                        "source-fetch-stderr",
                    )
                    with create_build_context(
                        bundle,
                        source,
                        command_id=command_id,
                        keep=options.keep_build_context,
                    ) as context:
                        self.store.event(
                            command_id,
                            "BUILD_CONTEXT_CREATED",
                            {"context_digest": context.metadata.context_digest},
                        )
                        writer.write_model(
                            "build/context.manifest.json",
                            context.manifest,
                            "build-context-manifest",
                        )
                        writer.write_model(
                            "build/context.metadata.json",
                            context.metadata,
                            "build-context-metadata",
                        )
                        labels = required_image_labels(bundle, context, self.cli_version)
                        build_command = build_command_record(
                            bundle=bundle,
                            labels=labels,
                            image_reference=image_reference,
                            platform=platform,
                            no_cache=options.no_cache,
                        )
                        writer.write_model(
                            "build/docker-command.json",
                            build_command,
                            "docker-build-command",
                        )
                        build = build_image(
                            runner,
                            bundle=bundle,
                            context=context,
                            image_reference=image_reference,
                            platform=platform,
                            no_cache=options.no_cache,
                            cli_version=self.cli_version,
                        )
                        self.store.event(
                            command_id,
                            "IMAGE_BUILT",
                            {"image_reference": image_reference},
                        )
                        writer.write_text(
                            "build/stdout.log",
                            build.result.stdout,
                            "docker-build-stdout",
                        )
                        writer.write_text(
                            "build/stderr.log",
                            build.result.stderr,
                            "docker-build-stderr",
                        )
                        writer.write_model(
                            "build/image-inspect-command.json",
                            DockerCommandRecord(
                                phase="image-inspect",
                                argv=(
                                    "docker",
                                    "image",
                                    "inspect",
                                    "--format",
                                    "{{json .}}",
                                    image_reference,
                                ),
                                timeout_seconds=30,
                            ),
                            "image-inspect-command",
                        )
                        inspection = inspect_image(
                            runner,
                            image_reference,
                            expected_platform=platform,
                            expected_labels=build.labels,
                        )
                        image_id = inspection.image_id
                        self.store.event(
                            command_id,
                            "IMAGE_INSPECTED",
                            {
                                "image_id": image_id,
                                "platform": inspection.platform,
                            },
                        )
                        writer.write_model(
                            "build/image-inspect.json",
                            inspection,
                            "image-inspection",
                        )
                        smoke_plan = create_smoke_plan(
                            inspection=inspection,
                            source_manifest=source.manifest,
                            settings=bundle.task.environment.runtime,
                            command_id=command_id,
                        )
                        writer.write_model(
                            "smoke/docker-command.json",
                            smoke_plan.command,
                            "smoke-command",
                        )
                        writer.write_json(
                            "smoke/docker-commands.json",
                            {
                                "schema_version": "1",
                                "commands": [
                                    list(smoke_plan.command.argv),
                                    [
                                        "docker",
                                        "start",
                                        "--attach",
                                        "<container-id>",
                                    ],
                                    [
                                        "docker",
                                        "rm",
                                        "--force",
                                        "<container-id>",
                                    ],
                                ],
                            },
                            "smoke-commands",
                        )
                        smoke = smoke_check(
                            runner,
                            inspection=inspection,
                            source_manifest=source.manifest,
                            settings=bundle.task.environment.runtime,
                            command_id=command_id,
                            plan=smoke_plan,
                        )
                        self.store.event(
                            command_id,
                            "IMAGE_SMOKE_CHECK_PASSED",
                            {"duration_ms": smoke.result.duration_ms},
                        )
                        writer.write_model(
                            "smoke/result.json",
                            smoke.result,
                            "smoke-result",
                        )
                        writer.write_text(
                            "smoke/stdout.log",
                            smoke.result.stdout,
                            "smoke-stdout",
                        )
                        writer.write_text(
                            "smoke/stderr.log",
                            smoke.result.stderr,
                            "smoke-stderr",
                        )
                        lock = create_bundle_lock(
                            bundle=bundle,
                            source=source.resolved,
                            context=context,
                            inspection=inspection,
                            image_reference=image_reference,
                            runtime_policy=runtime_policy,
                            cli_version=self.cli_version,
                        )
                        writer.write_model(
                            "lock/bundle.lock.json",
                            lock,
                            "bundle-lock",
                        )
                        result = self._success_result(
                            bundle=bundle,
                            command_id=command_id,
                            lock=lock,
                            writer=writer,
                            context_path=(
                                context.root.relative_to(bundle.root).as_posix()
                                if options.keep_build_context
                                else None
                            ),
                            warnings=warnings,
                        )
                        self._write_success_report(writer, result)

                write_bundle_lock(lock, lock_path)
                lock_written = True
                self.store.event(
                    command_id,
                    "LOCK_WRITTEN",
                    {"lock_path": LOCK_RELATIVE_PATH.as_posix()},
                )
                self.store.finish(
                    command_id,
                    status=CommandStatus.SUCCEEDED,
                    outcome=result.status,
                    exit_code=0,
                    image_id=result.image_id,
                    message="Task image initialized and locked.",
                )
                return result
        except TaskBundleError as error:
            if lock_written and lock_path is not None:
                self._restore_lock(lock_path, previous_lock)
            self._record_failure(
                writer=writer,
                runner=runner,
                command_id=command_id,
                error=error,
                image_id=image_id,
            )
            raise
        except KeyboardInterrupt:
            if lock_written and lock_path is not None:
                self._restore_lock(lock_path, previous_lock)
            with suppress(TaskBundleError):
                self.store.finish(
                    command_id,
                    status=CommandStatus.INTERRUPTED,
                    outcome="interrupted",
                    exit_code=130,
                    image_id=image_id,
                    message="Initialization interrupted.",
                )
            raise

    def _validate_configuration(
        self,
        bundle: LoadedBundle,
        options: InitOptions,
    ) -> str:
        validate_task_id_for_build(bundle.task.task.id)
        environment = bundle.task.environment
        if isinstance(environment, BaseImageEnvironment):
            validate_base_image_reference(environment.image)
        validate_build_args(environment.build.build_args)
        validate_runtime_settings(environment.runtime)
        return validate_platform(options.platform or environment.platform)

    def _current_lock_result(
        self,
        *,
        bundle: LoadedBundle,
        options: InitOptions,
        runtime_policy: RuntimePolicy,
        image_reference: str,
        platform: str,
        lock_path: Path,
        runner: DockerRunner,
        command_id: str,
        writer: ArtifactWriter,
        warnings: tuple[str, ...],
    ) -> InitResult | None:
        if not lock_path.exists():
            return None
        try:
            lock = load_bundle_lock(lock_path)
        except TaskBundleError:
            if options.rebuild:
                self.store.event(command_id, "STALE_LOCK_DETECTED", {"reasons": ["invalid"]})
                return None
            raise
        writer.write_model(
            "lock/image-inspect-command.json",
            DockerCommandRecord(
                phase="lock-freshness",
                argv=(
                    "docker",
                    "image",
                    "inspect",
                    "--format",
                    "{{json .}}",
                    lock.image_reference,
                ),
                timeout_seconds=30,
            ),
            "lock-image-inspect-command",
        )
        observed = inspect_image_if_present(runner, lock.image_reference)
        comparison = compare_bundle_lock(
            lock,
            bundle=bundle,
            runtime_policy=runtime_policy,
            image_reference=image_reference,
            selected_platform=platform,
            observed_image_id=None if observed is None else observed.image_id,
        )
        writer.write_json(
            "lock/freshness.json",
            {
                "schema_version": "1",
                "is_current": comparison.is_current,
                "reasons": list(comparison.reasons),
            },
            "lock-freshness",
        )
        if not comparison.is_current:
            self.store.event(
                command_id,
                "STALE_LOCK_DETECTED",
                {"reasons": list(comparison.reasons)},
            )
            if not options.rebuild:
                stale_lock_error(lock_path, comparison)
            return None
        if options.rebuild:
            self.store.event(command_id, "REBUILD_REQUESTED", {})
            return None
        result = InitResult(
            command_id=command_id,
            task_id=bundle.task.task.id,
            status="already_initialized",
            bundle_input_digest=bundle.bundle_input_digest,
            source_tree_digest=lock.source.source_tree_digest,
            image_reference=lock.image_reference,
            image_id=lock.image_id,
            platform=lock.actual_platform,
            lock_path=LOCK_RELATIVE_PATH.as_posix(),
            artifact_directory=writer.root.relative_to(bundle.root).as_posix(),
            warnings=warnings,
        )
        self.store.event(
            command_id,
            "INITIALIZATION_REUSED",
            {"image_id": lock.image_id},
        )
        self._write_success_report(writer, result)
        return result

    def _success_result(
        self,
        *,
        bundle: LoadedBundle,
        command_id: str,
        lock: BundleLock,
        writer: ArtifactWriter,
        context_path: str | None,
        warnings: tuple[str, ...],
    ) -> InitResult:
        return InitResult(
            command_id=command_id,
            task_id=bundle.task.task.id,
            status="initialized",
            bundle_input_digest=bundle.bundle_input_digest,
            source_tree_digest=lock.source.source_tree_digest,
            image_reference=lock.image_reference,
            image_id=lock.image_id,
            platform=lock.actual_platform,
            lock_path=LOCK_RELATIVE_PATH.as_posix(),
            artifact_directory=writer.root.relative_to(bundle.root).as_posix(),
            build_context_path=context_path,
            warnings=warnings,
        )

    def _write_success_report(
        self,
        writer: ArtifactWriter,
        result: InitResult,
    ) -> None:
        writer.write_model("report.json", result, "init-report-json")
        warning_lines = "".join(f"- Warning: {warning}\n" for warning in result.warnings)
        writer.write_text(
            "report.md",
            (
                "# Task image initialization\n\n"
                f"- Command: `{result.command_id}`\n"
                f"- Status: `{result.status}`\n"
                f"- Task: `{result.task_id}`\n"
                f"- Bundle digest: `{result.bundle_input_digest}`\n"
                f"- Source digest: `{result.source_tree_digest}`\n"
                f"- Image: `{result.image_reference}`\n"
                f"- Image ID: `{result.image_id}`\n"
                f"- Platform: `{result.platform}`\n"
                f"- Lock: `{result.lock_path}`\n"
                f"{warning_lines}"
            ),
            "init-report-markdown",
        )

    def _record_failure(
        self,
        *,
        writer: ArtifactWriter | None,
        runner: DockerRunner | None,
        command_id: str,
        error: TaskBundleError,
        image_id: str | None,
    ) -> None:
        try:
            if writer is not None:
                last_result = (
                    getattr(runner, "last_failure_result", None) if runner is not None else None
                )
                if isinstance(last_result, DockerCommandResult) and (
                    error.context.phase.startswith("docker")
                    or error.context.phase.startswith("image")
                ):
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
        except TaskBundleError:
            pass
        try:
            self.store.event(
                command_id,
                "COMMAND_FAILED",
                {"code": error.code.value, "phase": error.context.phase},
            )
            self.store.finish(
                command_id,
                status=CommandStatus.FAILED,
                outcome=error.code.value,
                exit_code=error.exit_code,
                image_id=image_id,
                message=str(error),
            )
        except TaskBundleError:
            pass

    def _environment_warnings(
        self,
        runner: DockerRunner,
        platform: str,
    ) -> tuple[str, ...]:
        warnings: list[str] = []
        if runner.environment_info.rootless:
            warnings.append("Docker daemon is running in rootless mode.")
        requested_arch = _architecture_alias(platform.split("/")[1])
        host_arch = _architecture_alias(runner.environment_info.host_architecture)
        if requested_arch != host_arch:
            warnings.append(
                "Requested platform differs from the Docker host; emulation may be in use."
            )
        return tuple(warnings)

    def _restore_lock(self, lock_path: Path, previous: bytes | None) -> None:
        try:
            if previous is None:
                lock_path.unlink(missing_ok=True)
            else:
                write_bytes_atomic(
                    previous,
                    lock_path,
                    error_code=ErrorCode.LOCK_WRITE_ERROR,
                    phase="lock-rollback",
                    message="Previous bundle lock could not be restored.",
                )
        except (OSError, TaskBundleError):
            pass


def _architecture_alias(value: str) -> str:
    aliases = {
        "x86_64": "amd64",
        "aarch64": "arm64",
    }
    return aliases.get(value.lower(), value.lower())
