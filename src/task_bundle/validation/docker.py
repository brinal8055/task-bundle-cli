import os
import stat
import tempfile
import time
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Protocol

from task_bundle.bundle.canonical import sha256_digest
from task_bundle.bundle.loader import LoadedBundle
from task_bundle.errors import ErrorCode, ErrorContext, TaskBundleError
from task_bundle.image.docker import DockerCommandResult, DockerRunner
from task_bundle.image.models import BundleLock, RuntimePolicy
from task_bundle.models import (
    EvaluationPhase,
    EvaluationPlan,
    InputManifestEntry,
)
from task_bundle.source.validation import validate_symlink_target
from task_bundle.validation.models import EvaluationStatus, EvaluatorExecution
from task_bundle.validation.patch import validate_patch, validate_patch_bytes
from task_bundle.validation.result import load_normalized_result

_KEEPER_SCRIPT = "trap 'exit 0' TERM INT; while :; do sleep 3600; done"
_SEED_SCRIPT = (
    "set -eu; "
    "mkdir -p /workspace/repo /evaluation/input /evaluation/harness /evaluation/output; "
    "cp -a /opt/task/repo/. /workspace/repo/; "
    "test -d /workspace/repo; "
    "git -C /workspace/repo init -q; "
    "git -C /workspace/repo config core.hooksPath /dev/null; "
    "git -C /workspace/repo add -A"
)
_PERMISSION_SCRIPT = (
    "set -eu; "
    "find /evaluation/input /evaluation/harness -type d -exec chmod 0555 {} +; "
    "find /evaluation/input -type f -exec chmod 0444 {} +; "
    "find /evaluation/harness -type f -perm -111 -exec chmod 0555 {} +; "
    "find /evaluation/harness -type f ! -perm -111 -exec chmod 0444 {} +; "
    "chmod 0755 /workspace /workspace/repo /evaluation/output"
)
_RUNTIME_PERMISSION_SCRIPT = (
    "set -eu; "
    "rm -rf /workspace/repo/.git; "
    "find /workspace -type d -exec chmod 0777 {} +; "
    "find /workspace -type f -exec chmod a+rw {} +; "
    "chmod 0777 /evaluation/output"
)
_CONTAINER_ID_LENGTHS = range(12, 65)


@dataclass(frozen=True, slots=True)
class EvaluationRequest:
    bundle: LoadedBundle
    lock: BundleLock
    runtime_policy: RuntimePolicy
    command_id: str
    plan: EvaluationPlan
    keep_container: bool = False
    candidate_patch: bytes | None = None


class EvaluationBackend(Protocol):
    def run(self, request: EvaluationRequest) -> EvaluatorExecution: ...


class DockerEvaluator:
    def __init__(self, runner: DockerRunner) -> None:
        self.runner = runner

    def run(self, request: EvaluationRequest) -> EvaluatorExecution:
        phase = request.plan.phase
        repeat = request.plan.repeat_index
        suffix = request.command_id.removeprefix("cmd_")[:20]
        base = f"task-bundle-{suffix}-{phase.value}-{repeat:03d}"
        workspace = f"{base}-workspace"
        evaluation = f"{base}-evaluation"
        container_name = base
        container_id: str | None = None
        created_volumes: list[str] = []
        primary_error: BaseException | None = None
        started = time.monotonic()
        prepare_stdout = ""
        prepare_stderr = ""
        runner_stdout = ""
        runner_stderr = ""
        patch_log = ""
        raw_result = b""
        normalized = None
        runner_exit_code: int | None = None
        cleaned_up = False
        test_patch = validate_patch(
            request.bundle.root / request.bundle.task.evaluation.test_patch,
            phase=phase,
            repeat_index=repeat,
            golden=False,
        )
        golden_patch = (
            validate_patch(
                request.bundle.root / request.bundle.task.evaluation.golden_patch,
                phase=phase,
                repeat_index=repeat,
                golden=True,
            )
            if phase == EvaluationPhase.GOLDEN
            else None
        )
        candidate_patch = request.candidate_patch
        if phase == EvaluationPhase.CANDIDATE:
            if candidate_patch is None:
                raise AssertionError("Candidate evaluation requires a finalized patch")
            validate_patch_bytes(
                candidate_patch,
                code=ErrorCode.CANDIDATE_EVALUATION_ERROR,
                phase=phase,
                repeat_index=repeat,
                artifact=Path("solver/candidate.patch"),
                max_bytes=request.bundle.task.solver.max_patch_bytes,
                allow_empty=True,
            )
        elif candidate_patch is not None:
            raise AssertionError("Validation phases cannot receive a candidate patch")
        try:
            for volume in (workspace, evaluation):
                self._run(
                    (
                        "volume",
                        "create",
                        "--label",
                        f"io.task-bundle.command-id={request.command_id}",
                        volume,
                    ),
                    request,
                    ErrorCode.EVALUATOR_CREATE_ERROR,
                    "create evaluator storage",
                )
                created_volumes.append(volume)
            created = self._run(
                self._create_args(
                    request,
                    container_name=container_name,
                    workspace=workspace,
                    evaluation=evaluation,
                ),
                request,
                ErrorCode.EVALUATOR_CREATE_ERROR,
                "create evaluator container",
            )
            candidate = created.stdout.strip().splitlines()[-1]
            if (
                len(candidate) not in _CONTAINER_ID_LENGTHS
                or any(character not in "0123456789abcdef" for character in candidate)
            ):
                self._error(
                    ErrorCode.EVALUATOR_CREATE_ERROR,
                    "Docker returned an invalid evaluator container ID.",
                    candidate[:200],
                    request,
                )
            container_id = candidate
            self._run(
                ("start", container_id),
                request,
                ErrorCode.EVALUATOR_CREATE_ERROR,
                "start evaluator container",
            )
            self._exec_root(
                request,
                container_id,
                ("/bin/sh", "-c", _SEED_SCRIPT),
                ErrorCode.WORKSPACE_SEED_ERROR,
                "seed pristine evaluator workspace",
            )
            with tempfile.TemporaryDirectory(prefix="task-bundle-evaluator-") as temporary:
                staging = Path(temporary)
                input_root = staging / "input"
                harness_root = staging / "harness"
                output_root = staging / "output"
                input_root.mkdir()
                harness_root.mkdir()
                output_root.mkdir()
                self._stage(
                    request,
                    input_root,
                    harness_root,
                    test_patch,
                    golden_patch,
                    candidate_patch,
                )
                self._copy_into(container_id, input_root, "/evaluation/input", request)
                self._copy_into(container_id, harness_root, "/evaluation/harness", request)
                self._exec_root(
                    request,
                    container_id,
                    ("/bin/sh", "-c", _PERMISSION_SCRIPT),
                    ErrorCode.EVALUATOR_PERMISSION_ERROR,
                    "apply evaluator permissions",
                )
                if golden_patch is not None:
                    result = self._apply_patch(
                        request,
                        container_id,
                        "/evaluation/input/golden.patch",
                        ErrorCode.GOLDEN_PATCH_APPLY_ERROR,
                    )
                    patch_log += result.stdout + result.stderr
                if candidate_patch:
                    result = self._apply_patch(
                        request,
                        container_id,
                        "/evaluation/input/candidate.patch",
                        ErrorCode.CANDIDATE_EVALUATION_ERROR,
                    )
                    patch_log += result.stdout + result.stderr
                result = self._apply_patch(
                    request,
                    container_id,
                    "/evaluation/input/test.patch",
                    ErrorCode.TEST_PATCH_APPLY_ERROR,
                )
                patch_log += result.stdout + result.stderr
                self._verify_index(
                    request,
                    container_id,
                    (
                        ErrorCode.CANDIDATE_EVALUATION_ERROR
                        if phase == EvaluationPhase.CANDIDATE
                        else ErrorCode.TEST_PATCH_APPLY_ERROR
                    ),
                )
                self._exec_root(
                    request,
                    container_id,
                    ("/bin/sh", "-c", _RUNTIME_PERMISSION_SCRIPT),
                    ErrorCode.EVALUATOR_PERMISSION_ERROR,
                    "make evaluator workspace writable by the runtime user",
                )
                prepare = request.bundle.task.evaluation.prepare
                if prepare is not None:
                    prepared = self._exec_user(
                        request,
                        container_id,
                        tuple(prepare.command),
                        ErrorCode.TEST_PREPARE_ERROR,
                        ErrorCode.TEST_PREPARE_TIMEOUT,
                        "run test preparation",
                    )
                    prepare_stdout = prepared.stdout
                    prepare_stderr = prepared.stderr
                executed = self._exec_user(
                    request,
                    container_id,
                    tuple(request.bundle.task.evaluation.runner.command),
                    ErrorCode.TEST_RUNNER_ERROR,
                    ErrorCode.TEST_RUNNER_TIMEOUT,
                    "run task test harness",
                    check=False,
                )
                runner_stdout = executed.stdout
                runner_stderr = executed.stderr
                runner_exit_code = executed.exit_code
                self._run(
                    ("cp", f"{container_id}:/evaluation/output/.", str(output_root)),
                    request,
                    ErrorCode.EVALUATOR_STAGE_ERROR,
                    "copy evaluator output",
                )
                result_path = _host_result_path(
                    output_root,
                    request.bundle.task.evaluation.runner.result_file,
                    request,
                )
                raw_result, normalized = load_normalized_result(
                    result_path,
                    phase=phase,
                    repeat_index=repeat,
                )
        except TaskBundleError as error:
            primary_error = error
            if request.keep_container and container_id is not None:
                details = dict(error.context.details or {})
                details.update(
                    {
                        "retained_container_id": container_id,
                        "retained_workspace_id": workspace,
                        "retained_evaluation_storage_id": evaluation,
                        "retained_resources_contain_hidden_inputs": True,
                    }
                )
                raise TaskBundleError(
                    error.code,
                    str(error),
                    replace(error.context, details=details),
                ) from error
            raise
        except BaseException as error:
            primary_error = error
            raise
        finally:
            if not request.keep_container:
                cleaned_up = self._cleanup(
                    request,
                    container_id,
                    tuple(created_volumes),
                    primary_error,
                )
        if normalized is None or container_id is None:
            raise AssertionError("Successful evaluator execution omitted required state")
        return EvaluatorExecution(
            phase=phase,
            repeat_index=repeat,
            container_id=container_id,
            workspace_id=workspace,
            evaluation_storage_id=evaluation,
            status=EvaluationStatus.COMPLETED,
            harness_status=normalized.harness_status,
            runner_exit_code=runner_exit_code,
            duration_ms=max(0, int((time.monotonic() - started) * 1000)),
            test_patch_sha256=sha256_digest(test_patch),
            golden_patch_sha256=(
                None if golden_patch is None else sha256_digest(golden_patch)
            ),
            prepare_stdout=prepare_stdout,
            prepare_stderr=prepare_stderr,
            runner_stdout=runner_stdout,
            runner_stderr=runner_stderr,
            patch_log=patch_log,
            raw_result=raw_result,
            result=normalized,
            cleaned_up=cleaned_up,
        )

    def _create_args(
        self,
        request: EvaluationRequest,
        *,
        container_name: str,
        workspace: str,
        evaluation: str,
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
            f"io.task-bundle.phase={request.plan.phase.value}",
            "--label",
            f"io.task-bundle.image-id={request.lock.image_id}",
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
            f"type=volume,source={evaluation},target=/evaluation",
            "--workdir",
            "/workspace/repo",
            "--entrypoint",
            "/bin/sh",
        ]
        for tmpfs in policy.tmpfs:
            args.extend(("--tmpfs", tmpfs))
        args.extend((request.lock.image_id, "-c", _KEEPER_SCRIPT))
        return tuple(args)

    def _stage(
        self,
        request: EvaluationRequest,
        input_root: Path,
        harness_root: Path,
        test_patch: bytes,
        golden_patch: bytes | None,
        candidate_patch: bytes | None,
    ) -> None:
        plan = request.plan.model_dump_json(indent=2).encode() + b"\n"
        metadata = (
            request.bundle.task.task.model_dump_json(indent=2).encode() + b"\n"
        )
        _write_staged(input_root / "plan.json", plan, 0o444)
        _write_staged(input_root / "task-metadata.json", metadata, 0o444)
        _write_staged(input_root / "test.patch", test_patch, 0o444)
        if golden_patch is not None:
            _write_staged(input_root / "golden.patch", golden_patch, 0o444)
        if candidate_patch is not None:
            _write_staged(input_root / "candidate.patch", candidate_patch, 0o444)
        excluded = {
            request.bundle.task.evaluation.test_patch,
            request.bundle.task.evaluation.golden_patch,
        }
        for entry in request.bundle.input_manifest:
            if (
                not entry.path.startswith("evaluation/")
                or entry.path.startswith("evaluation/hidden/")
                or entry.path in excluded
            ):
                continue
            relative = entry.path.removeprefix("evaluation/")
            destination = harness_root / Path(relative)
            _copy_manifest_file(request.bundle.root, entry, destination, request)

    def _copy_into(
        self,
        container_id: str,
        source: Path,
        destination: str,
        request: EvaluationRequest,
    ) -> None:
        self._run(
            ("cp", f"{source}/.", f"{container_id}:{destination}"),
            request,
            ErrorCode.EVALUATOR_STAGE_ERROR,
            "stage evaluator files",
        )

    def _apply_patch(
        self,
        request: EvaluationRequest,
        container_id: str,
        patch_path: str,
        code: ErrorCode,
    ) -> DockerCommandResult:
        common = (
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-C",
            "/workspace/repo",
            "apply",
            "--index",
            "--binary",
        )
        self._exec_root(
            request,
            container_id,
            (*common, "--check", patch_path),
            code,
            "check trusted evaluation patch",
        )
        return self._exec_root(
            request,
            container_id,
            (*common, patch_path),
            code,
            "apply trusted evaluation patch",
        )

    def _verify_index(
        self,
        request: EvaluationRequest,
        container_id: str,
        code: ErrorCode,
    ) -> None:
        listed = self._exec_root(
            request,
            container_id,
            ("git", "-C", "/workspace/repo", "ls-files", "-s", "-z"),
            code,
            "verify patched Git index",
        )
        for record in listed.stdout.split("\0"):
            if not record:
                continue
            metadata, path = record.split("\t", 1)
            mode = metadata.split(" ", 1)[0]
            if mode == "160000":
                self._error(
                    code,
                    "Evaluation patch introduced a submodule.",
                    path,
                    request,
                )
            if mode != "120000":
                continue
            target = self._exec_root(
                request,
                container_id,
                ("git", "-C", "/workspace/repo", "show", f":{path}"),
                code,
                "inspect patched symlink",
            ).stdout
            try:
                if "\ufffd" in target:
                    raise ValueError("symlink target is not valid UTF-8")
                validate_symlink_target(path, target)
            except (TaskBundleError, ValueError) as error:
                self._error(
                    code,
                    "Evaluation patch introduced an unsafe symlink.",
                    str(error),
                    request,
                )

    def _exec_root(
        self,
        request: EvaluationRequest,
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

    def _exec_user(
        self,
        request: EvaluationRequest,
        container_id: str,
        command: tuple[str, ...],
        code: ErrorCode,
        timeout_code: ErrorCode,
        description: str,
        *,
        check: bool = True,
    ) -> DockerCommandResult:
        return self._run(
            (
                "exec",
                "--user",
                request.runtime_policy.user,
                "--workdir",
                request.runtime_policy.working_directory,
                container_id,
                *command,
            ),
            request,
            code,
            description,
            check=check,
            timeout_code=timeout_code,
        )

    def _run(
        self,
        args: tuple[str, ...],
        request: EvaluationRequest,
        code: ErrorCode,
        description: str,
        *,
        check: bool = True,
        timeout_code: ErrorCode | None = None,
    ) -> DockerCommandResult:
        return self.runner.run(
            args,
            cwd=request.bundle.root,
            timeout_seconds=request.runtime_policy.timeout_seconds,
            error_code=code,
            timeout_code=timeout_code,
            phase=request.plan.phase.value,
            description=description,
            check=check,
        )

    def _cleanup(
        self,
        request: EvaluationRequest,
        container_id: str | None,
        volumes: tuple[str, ...],
        primary_error: BaseException | None,
    ) -> bool:
        failures: list[str] = []
        if container_id is not None:
            removed = self._run(
                ("rm", "--force", container_id),
                request,
                ErrorCode.VALIDATION_CLEANUP_ERROR,
                "remove evaluator container",
                check=False,
            )
            if removed.exit_code != 0:
                failures.append(f"container {container_id}")
        for volume in reversed(volumes):
            removed = self._run(
                ("volume", "rm", "--force", volume),
                request,
                ErrorCode.VALIDATION_CLEANUP_ERROR,
                "remove evaluator storage",
                check=False,
            )
            if removed.exit_code != 0:
                failures.append(f"volume {volume}")
        if failures and primary_error is None:
            self._error(
                ErrorCode.VALIDATION_CLEANUP_ERROR,
                "Evaluator resources could not be removed.",
                ", ".join(failures),
                request,
            )
        return not failures

    @staticmethod
    def _error(
        code: ErrorCode,
        message: str,
        actual: str,
        request: EvaluationRequest,
    ) -> None:
        raise TaskBundleError(
            code,
            message,
            ErrorContext(
                phase=request.plan.phase.value,
                expected="A restricted, isolated evaluator lifecycle",
                actual=actual[:2000],
                corrective_action="Inspect phase artifacts and Docker daemon state.",
                details={"repeat_index": request.plan.repeat_index},
            ),
        )


def _host_result_path(
    output_root: Path,
    configured: str,
    request: EvaluationRequest,
) -> Path:
    logical = PurePosixPath(configured)
    output = PurePosixPath("/evaluation/output")
    try:
        relative = logical.relative_to(output)
    except ValueError:
        DockerEvaluator._error(
            ErrorCode.TEST_RESULT_SCHEMA_ERROR,
            "Configured result file is outside evaluator output.",
            configured,
            request,
        )
    if (
        not logical.is_absolute()
        or ".." in logical.parts
        or logical.as_posix() != configured
        or relative.as_posix() in {"", "."}
    ):
        DockerEvaluator._error(
            ErrorCode.TEST_RESULT_SCHEMA_ERROR,
            "Configured result file path is unsafe.",
            configured,
            request,
        )
    result_path = output_root / Path(relative.as_posix())
    try:
        root_metadata = output_root.lstat()
        if not stat.S_ISDIR(root_metadata.st_mode):
            raise OSError("exported evaluation output root is not a directory")
        current = output_root
        for component in relative.parts[:-1]:
            current /= component
            metadata = current.lstat()
            if not stat.S_ISDIR(metadata.st_mode):
                raise OSError(f"unsafe result path component: {component}")
    except FileNotFoundError:
        return result_path
    except OSError as error:
        DockerEvaluator._error(
            ErrorCode.TEST_RESULT_SCHEMA_ERROR,
            "Configured result file has an unsafe exported path component.",
            str(error),
            request,
        )
    return result_path


def _copy_manifest_file(
    root: Path,
    entry: InputManifestEntry,
    destination: Path,
    request: EvaluationRequest,
) -> None:
    source = root / Path(entry.path)
    try:
        metadata = source.lstat()
        payload = source.read_bytes()
    except OSError as error:
        DockerEvaluator._error(
            ErrorCode.EVALUATOR_STAGE_ERROR,
            "Evaluator harness file could not be read.",
            str(error),
            request,
        )
    expected_mode = 0o755 if entry.mode == "0755" else 0o644
    if (
        not stat.S_ISREG(metadata.st_mode)
        or sha256_digest(payload) != entry.sha256
        or stat.S_IMODE(metadata.st_mode) & 0o111 != expected_mode & 0o111
    ):
        DockerEvaluator._error(
            ErrorCode.EVALUATOR_STAGE_ERROR,
            "Digest-covered evaluator harness changed during staging.",
            entry.path,
            request,
        )
    _write_staged(destination, payload, expected_mode)


def _write_staged(path: Path, payload: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    os.chmod(path, mode)
