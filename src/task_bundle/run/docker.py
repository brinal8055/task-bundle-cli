import os
import stat
import tempfile
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from task_bundle.bundle.canonical import sha256_digest
from task_bundle.bundle.loader import LoadedBundle
from task_bundle.errors import ErrorCode, ErrorContext, TaskBundleError
from task_bundle.image.docker import DockerCommandResult, DockerRunner
from task_bundle.image.models import BundleLock, RuntimePolicy
from task_bundle.models import EvaluationPhase
from task_bundle.run.filesystem import (
    build_filesystem_manifest,
    copy_manifest_tree,
)
from task_bundle.run.models import (
    FilesystemManifest,
    RunOptions,
    SolverExecutionResult,
    SolverStatus,
    SolverType,
)
from task_bundle.validation.patch import validate_patch_bytes

_KEEPER_SCRIPT = "trap 'exit 0' TERM INT; while :; do sleep 3600; done"
_SEED_SCRIPT = (
    "set -eu; "
    "mkdir -p /workspace/repo /task/public /task/solver /task/input; "
    "cp -a /opt/task/repo/. /workspace/repo/; "
    "find /workspace -type d -exec chmod 0777 {} +; "
    "find /workspace -type f -exec chmod a+rw {} +; "
    "find /task -type d -exec chmod 0555 {} +; "
    "find /task -type f -perm -111 -exec chmod 0555 {} +; "
    "find /task -type f ! -perm -111 -exec chmod 0444 {} +"
)
_CONTAINER_ID_LENGTHS = range(12, 65)


@dataclass(frozen=True, slots=True)
class SolverRequest:
    bundle: LoadedBundle
    lock: BundleLock
    runtime_policy: RuntimePolicy
    command_id: str
    options: RunOptions
    context_root: Path | None
    context_manifest: FilesystemManifest | None
    patch_input: bytes | None
    export_root: Path


@dataclass(frozen=True, slots=True)
class SolverOutcome:
    execution: SolverExecutionResult
    baseline_root: Path | None
    baseline_manifest: FilesystemManifest | None
    candidate_root: Path | None
    candidate_manifest: FilesystemManifest | None


class SolverBackend(Protocol):
    def run(self, request: SolverRequest) -> SolverOutcome: ...


class DockerSolver:
    def __init__(self, runner: DockerRunner) -> None:
        self.runner = runner

    def run(self, request: SolverRequest) -> SolverOutcome:
        suffix = request.command_id.removeprefix("cmd_")[:20]
        base = f"task-bundle-{suffix}-solver"
        workspace = f"{base}-workspace"
        task_storage = f"{base}-task"
        container_id: str | None = None
        created_volumes: list[str] = []
        primary_error: BaseException | None = None
        cleaned_up = False
        started_at = datetime.now(UTC)
        started = time.monotonic()
        stdout = ""
        stderr = ""
        exit_code: int | None = None
        status = SolverStatus.NOT_RUN
        export_status: Literal["completed", "not_run"] = "not_run"
        baseline_root: Path | None = None
        candidate_root: Path | None = None
        baseline_manifest: FilesystemManifest | None = None
        candidate_manifest: FilesystemManifest | None = None
        try:
            for volume in (workspace, task_storage):
                self._run(
                    (
                        "volume",
                        "create",
                        "--label",
                        f"io.task-bundle.command-id={request.command_id}",
                        volume,
                    ),
                    request,
                    ErrorCode.SOLVER_CREATE_ERROR,
                    "create solver storage",
                )
                created_volumes.append(volume)
            created = self._run(
                self._create_args(
                    request,
                    container_name=base,
                    workspace=workspace,
                    task_storage=task_storage,
                ),
                request,
                ErrorCode.SOLVER_CREATE_ERROR,
                "create solver container",
            )
            container_id = _container_id(created, request)
            self._run(
                ("start", container_id),
                request,
                ErrorCode.SOLVER_CREATE_ERROR,
                "start solver container",
            )
            with tempfile.TemporaryDirectory(prefix="task-bundle-solver-stage-") as name:
                staging = Path(name) / "task"
                self._stage_task(request, staging)
                self._run(
                    ("cp", f"{staging}/.", f"{container_id}:/task"),
                    request,
                    ErrorCode.SOLVER_STAGE_ERROR,
                    "stage public solver inputs",
                )
            self._exec_root(
                request,
                container_id,
                ("/bin/sh", "-c", _SEED_SCRIPT),
                ErrorCode.SOLVER_STAGE_ERROR,
                "seed the solver workspace",
            )
            executed = self._exec_solver(request, container_id)
            stdout = executed.stdout
            stderr = executed.stderr
            exit_code = executed.exit_code
            status = (
                SolverStatus.SUCCEEDED
                if executed.exit_code == 0
                else SolverStatus.FAILED
            )
            if status == SolverStatus.SUCCEEDED:
                self._run(
                    ("stop", "--time", "10", container_id),
                    request,
                    ErrorCode.WORKSPACE_EXPORT_ERROR,
                    "freeze solver workspace",
                )
                baseline_root = request.export_root / "baseline"
                candidate_root = request.export_root / "candidate"
                baseline_root.mkdir(parents=True)
                candidate_root.mkdir()
                self._copy_out(
                    request,
                    container_id,
                    "/opt/task/repo",
                    baseline_root,
                )
                self._copy_out(
                    request,
                    container_id,
                    "/workspace/repo",
                    candidate_root,
                )
                limits = request.bundle.task.solver
                baseline_manifest = build_filesystem_manifest(
                    baseline_root,
                    phase="workspace-export",
                    error_code=ErrorCode.WORKSPACE_EXPORT_UNSAFE,
                    allow_symlinks=True,
                    max_files=limits.max_context_files,
                    max_total_bytes=limits.max_context_bytes,
                    max_file_bytes=limits.max_context_bytes,
                )
                candidate_manifest = build_filesystem_manifest(
                    candidate_root,
                    phase="workspace-export",
                    error_code=ErrorCode.WORKSPACE_EXPORT_UNSAFE,
                    allow_symlinks=True,
                    max_files=limits.max_context_files,
                    max_total_bytes=limits.max_context_bytes,
                    max_file_bytes=limits.max_context_bytes,
                )
                export_status = "completed"
        except BaseException as error:
            primary_error = error
        finally:
            if not request.options.keep_containers:
                cleaned_up = self._cleanup(
                    request,
                    container_id,
                    tuple(created_volumes),
                    primary_error,
                )
        if primary_error is not None:
            if isinstance(primary_error, TaskBundleError):
                details = dict(primary_error.context.details or {})
                details.update(
                    {
                        "solver": request.options.solver.value,
                        "container_id": container_id,
                        "cleaned_up": cleaned_up,
                    }
                )
                raise TaskBundleError(
                    primary_error.code,
                    str(primary_error),
                    replace(primary_error.context, details=details),
                ) from primary_error
            raise primary_error
        if container_id is None:
            raise AssertionError("Solver execution omitted its container ID")
        finished_at = datetime.now(UTC)
        execution = SolverExecutionResult(
            solver_type=request.options.solver,
            argv=_solver_argv(request.options),
            context_digest=(
                None
                if request.context_manifest is None
                else request.context_manifest.digest
            ),
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=max(0, int((time.monotonic() - started) * 1000)),
            exit_code=exit_code,
            timed_out=False,
            container_id=container_id,
            stdout=stdout,
            stderr=stderr,
            workspace_export_status=export_status,
            cleaned_up=cleaned_up,
        )
        return SolverOutcome(
            execution=execution,
            baseline_root=baseline_root,
            baseline_manifest=baseline_manifest,
            candidate_root=candidate_root,
            candidate_manifest=candidate_manifest,
        )

    def _create_args(
        self,
        request: SolverRequest,
        *,
        container_name: str,
        workspace: str,
        task_storage: str,
    ) -> tuple[str, ...]:
        policy = request.runtime_policy
        args = [
            "create",
            "--name",
            container_name,
            "--label",
            f"io.task-bundle.task-id={request.bundle.task.task.id}",
            "--label",
            f"io.task-bundle.command-id={request.command_id}",
            "--label",
            "io.task-bundle.phase=solver",
            "--network",
            "none",
            "--user",
            "0:0",
            "--read-only",
            "--cpus",
            str(policy.cpus),
            "--memory",
            f"{policy.memory_mb}m",
            "--pids-limit",
            str(policy.pids_limit),
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--mount",
            f"type=volume,source={workspace},target=/workspace",
            "--mount",
            f"type=volume,source={task_storage},target=/task",
            "--workdir",
            "/workspace/repo",
            "--entrypoint",
            "/bin/sh",
        ]
        for tmpfs in policy.tmpfs:
            args.extend(("--tmpfs", tmpfs))
        args.extend((request.lock.image_id, "-c", _KEEPER_SCRIPT))
        return tuple(args)

    def _stage_task(self, request: SolverRequest, root: Path) -> None:
        public = root / "public"
        public.mkdir(parents=True)
        public_names = (
            ("description", request.bundle.task.public.description),
            ("requirements", request.bundle.task.public.requirements),
            ("interface", request.bundle.task.public.interface),
        )
        for name, relative in public_names:
            if relative is None:
                continue
            payload, mode = _verified_bundle_file(request.bundle, relative)
            destination = public / f"{name}.md"
            destination.write_bytes(payload)
            destination.chmod(mode)
        solver_root = root / "solver"
        if request.context_root is None or request.context_manifest is None:
            solver_root.mkdir()
        else:
            copy_manifest_tree(
                request.context_root,
                solver_root,
                request.context_manifest,
                phase="solver-context-stage",
                error_code=ErrorCode.SOLVER_STAGE_ERROR,
            )
        input_root = root / "input"
        input_root.mkdir()
        if request.patch_input is not None:
            patch = input_root / "candidate.patch"
            patch.write_bytes(request.patch_input)
            patch.chmod(0o444)

    def _exec_solver(
        self,
        request: SolverRequest,
        container_id: str,
    ) -> DockerCommandResult:
        command = _solver_argv(request.options)
        args: list[str] = [
            "exec",
            "--user",
            request.runtime_policy.user,
            "--workdir",
            "/workspace/repo",
            "--env",
            "TASK_WORKSPACE=/workspace/repo",
            "--env",
            "TASK_SOLVER_ROOT=/task/solver",
        ]
        for name in ("description", "requirements", "interface"):
            configured = getattr(request.bundle.task.public, name)
            if configured is not None:
                args.extend(
                    (
                        "--env",
                        f"TASK_{name.upper()}_FILE=/task/public/{name}.md",
                    )
                )
        args.extend((container_id, *command))
        return self._run(
            tuple(args),
            request,
            ErrorCode.SOLVER_EXECUTION_ERROR,
            "execute the solver",
            check=False,
            timeout_code=ErrorCode.SOLVER_TIMEOUT,
        )

    def _exec_root(
        self,
        request: SolverRequest,
        container_id: str,
        command: tuple[str, ...],
        code: ErrorCode,
        description: str,
    ) -> DockerCommandResult:
        return self._run(
            ("exec", "--user", "0:0", container_id, *command),
            request,
            code,
            description,
        )

    def _copy_out(
        self,
        request: SolverRequest,
        container_id: str,
        source: str,
        destination: Path,
    ) -> None:
        self._run(
            ("cp", f"{container_id}:{source}/.", str(destination)),
            request,
            ErrorCode.WORKSPACE_EXPORT_ERROR,
            "export solver workspace",
        )

    def _run(
        self,
        args: tuple[str, ...],
        request: SolverRequest,
        code: ErrorCode,
        description: str,
        *,
        check: bool = True,
        timeout_code: ErrorCode | None = None,
    ) -> DockerCommandResult:
        return self.runner.run(
            args,
            cwd=request.bundle.root,
            timeout_seconds=request.bundle.task.solver.timeout_seconds,
            error_code=code,
            timeout_code=timeout_code,
            phase="solver",
            description=description,
            check=check,
        )

    def _cleanup(
        self,
        request: SolverRequest,
        container_id: str | None,
        volumes: tuple[str, ...],
        primary_error: BaseException | None,
    ) -> bool:
        failures: list[str] = []
        if container_id is not None:
            result = self._run(
                ("rm", "--force", container_id),
                request,
                ErrorCode.RUN_CLEANUP_ERROR,
                "remove solver container",
                check=False,
            )
            if result.exit_code != 0:
                failures.append(f"container {container_id}")
        for volume in reversed(volumes):
            result = self._run(
                ("volume", "rm", "--force", volume),
                request,
                ErrorCode.RUN_CLEANUP_ERROR,
                "remove solver storage",
                check=False,
            )
            if result.exit_code != 0:
                failures.append(f"volume {volume}")
        if failures and primary_error is None:
            raise TaskBundleError(
                ErrorCode.RUN_CLEANUP_ERROR,
                "Solver resources could not be removed.",
                ErrorContext(
                    phase="solver-cleanup",
                    expected="All solver containers and volumes to be deleted",
                    actual=", ".join(failures),
                    corrective_action="Remove the recorded resources manually.",
                ),
            )
        return not failures


def validate_patch_input(path: Path, max_bytes: int) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as error:
        _patch_input_error(str(error))
    if not stat.S_ISREG(metadata.st_mode):
        _patch_input_error("patch input is a symlink or special file")
    if metadata.st_size > max_bytes:
        _patch_input_error(f"patch input exceeds {max_bytes} bytes")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or opened.st_size > max_bytes
            ):
                _patch_input_error("patch input changed or exceeds its size limit")
            payload = handle.read(max_bytes + 1)
    except OSError as error:
        _patch_input_error(str(error))
    validate_patch_bytes(
        payload,
        code=ErrorCode.PATCH_POLICY_ERROR,
        phase=EvaluationPhase.CANDIDATE,
        repeat_index=1,
        artifact=Path("solver/patch-input"),
        max_bytes=max_bytes,
        allow_empty=True,
    )
    return payload


def _solver_argv(options: RunOptions) -> tuple[str, ...]:
    if options.solver == SolverType.NOOP:
        return ("/bin/true",)
    if options.solver == SolverType.PATCH:
        return (
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-C",
            "/workspace/repo",
            "apply",
            "--binary",
            "/task/input/candidate.patch",
        )
    return options.command


def _verified_bundle_file(bundle: LoadedBundle, relative: str) -> tuple[bytes, int]:
    entry = next((item for item in bundle.input_manifest if item.path == relative), None)
    if entry is None:
        raise AssertionError("Loaded bundle omitted configured public input")
    source = bundle.root / Path(relative)
    try:
        metadata = source.lstat()
        payload = source.read_bytes()
    except OSError as error:
        raise TaskBundleError(
            ErrorCode.SOLVER_STAGE_ERROR,
            "Public solver context could not be read.",
            ErrorContext(
                phase="solver-stage",
                expected="Digest-covered public task files",
                actual=str(error),
                corrective_action="Restore the initialized bundle inputs.",
                path=Path(relative),
            ),
        ) from error
    expected_mode = 0o755 if entry.mode == "0755" else 0o644
    if (
        not stat.S_ISREG(metadata.st_mode)
        or sha256_digest(payload) != entry.sha256
        or bool(metadata.st_mode & 0o111) != bool(expected_mode & 0o111)
    ):
        raise TaskBundleError(
            ErrorCode.SOLVER_STAGE_ERROR,
            "Public solver context changed during staging.",
            ErrorContext(
                phase="solver-stage",
                expected="The loaded digest and executable mode",
                actual=relative,
                corrective_action="Restore the initialized bundle inputs.",
                path=Path(relative),
            ),
        )
    return payload, expected_mode


def _container_id(
    result: DockerCommandResult,
    request: SolverRequest,
) -> str:
    lines = result.stdout.strip().splitlines()
    candidate = lines[-1] if lines else ""
    if (
        len(candidate) not in _CONTAINER_ID_LENGTHS
        or any(character not in "0123456789abcdef" for character in candidate)
    ):
        raise TaskBundleError(
            ErrorCode.SOLVER_CREATE_ERROR,
            "Docker returned an invalid solver container ID.",
            ErrorContext(
                phase="solver",
                expected="A hexadecimal Docker container ID",
                actual=candidate[:200],
                corrective_action="Inspect Docker daemon output.",
                details={"solver": request.options.solver.value},
            ),
        )
    return candidate


def _patch_input_error(actual: str) -> None:
    raise TaskBundleError(
        ErrorCode.PATCH_POLICY_ERROR,
        "Patch-solver input is unsafe.",
        ErrorContext(
            phase="solver-input",
            expected="A bounded regular non-symlink Git patch",
            actual=actual,
            corrective_action="Provide a safe regular patch file.",
            artifact=Path("solver/patch-input-metadata.json"),
        ),
    )
