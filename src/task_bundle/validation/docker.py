import json
import os
import stat
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from task_bundle.bundle.canonical import sha256_digest
from task_bundle.bundle.loader import LoadedBundle
from task_bundle.errors import ErrorCode, ErrorContext, TaskBundleError
from task_bundle.image.docker import DockerCommandResult, DockerRunner
from task_bundle.image.models import BundleLock, RuntimePolicy
from task_bundle.models import (
    CapturedTestExecution,
    CapturedTestExecutions,
    EvaluationPhase,
    EvaluationPlan,
    InputManifestEntry,
    TestExecution,
    TestExecutionPlan,
)
from task_bundle.source.validation import validate_symlink_target
from task_bundle.validation.models import EvaluationStatus, EvaluatorExecution
from task_bundle.validation.patch import validate_patch, validate_patch_bytes
from task_bundle.validation.result import parse_normalized_result

_KEEPER_SCRIPT = "trap 'exit 0' TERM INT; while :; do sleep 3600; done"
_SEED_SCRIPT = (
    "set -eu; "
    "mkdir -p /workspace/repo /evaluation/input /evaluation/harness; "
    "cp -a /opt/task/repo/. /workspace/repo/; "
    "test -d /workspace/repo; "
    "git -C /workspace/repo init -q; "
    "git -C /workspace/repo config core.hooksPath /dev/null; "
    "git -C /workspace/repo add -A"
)
_PERMISSION_SCRIPT = (
    "set -eu; "
    "test -r /evaluation/input/plan.json; "
    "test -r /evaluation/input/test.patch; "
    "test ! -w /evaluation/input/plan.json; "
    "test ! -w /evaluation/input/test.patch; "
    "chmod 0755 /workspace /workspace/repo /evaluation; "
    "chmod 0555 /evaluation/input /evaluation/harness"
)
_RUNTIME_PERMISSION_SCRIPT = (
    "set -eu; "
    "rm -rf /workspace/repo/.git; "
    "find /workspace -type d -exec chmod 0777 {} +; "
    "find /workspace -type f -exec chmod a+rw {} +"
)
_CONTAINER_ID_LENGTHS = range(12, 65)
_MAX_EXECUTION_PLAN_BYTES = 1_048_576


@dataclass(frozen=True, slots=True)
class CapturedEvaluationEvidence:
    captured_executions: CapturedTestExecutions
    patch_log: str
    prepare_stdout: str
    prepare_stderr: str
    runner_stdout: str
    runner_stderr: str


@dataclass(frozen=True, slots=True)
class EvaluationRequest:
    bundle: LoadedBundle
    lock: BundleLock
    runtime_policy: RuntimePolicy
    command_id: str
    plan: EvaluationPlan
    keep_container: bool = False
    candidate_patch: bytes | None = None
    evidence_sink: Callable[[CapturedEvaluationEvidence], None] | None = None


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
        parser_container_id: str | None = None
        created_containers: list[str] = []
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
            created_containers.append(container_name)
            candidate = created.stdout.strip().splitlines()[-1]
            if (
                len(candidate) not in _CONTAINER_ID_LENGTHS
                or any(character not in "0123456789abcdef" for character in candidate)
            ):
                self._run(
                    ("rm", "--force", container_name),
                    request,
                    ErrorCode.VALIDATION_CLEANUP_ERROR,
                    "remove malformed evaluator container",
                    check=False,
                )
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
                input_root.mkdir()
                harness_root.mkdir()
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
                execution_plan = self._build_execution_plan(request, container_id)
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
                    self._terminate_candidate_processes(
                        request,
                        container_id,
                        restart=True,
                    )
                captured: list[CapturedTestExecution] = []
                for index, execution in enumerate(execution_plan.executions):
                    captured.append(
                        self._execute_test(
                            request,
                            container_id,
                            execution,
                            restart=index < len(execution_plan.executions) - 1,
                        )
                    )
                captured_set = CapturedTestExecutions(executions=captured)
                self._validate_captured_executions(
                    request,
                    execution_plan,
                    captured_set,
                )
                runner_stdout = "\n".join(item.stdout for item in captured)
                runner_stderr = "\n".join(item.stderr for item in captured)
                exit_codes = [
                    item.exit_code for item in captured if item.exit_code is not None
                ]
                runner_exit_code = max(exit_codes, default=0)
                evidence = CapturedEvaluationEvidence(
                    captured_executions=captured_set,
                    patch_log=patch_log,
                    prepare_stdout=prepare_stdout,
                    prepare_stderr=prepare_stderr,
                    runner_stdout=runner_stdout,
                    runner_stderr=runner_stderr,
                )
                if request.evidence_sink is not None:
                    request.evidence_sink(evidence)
                trusted_root = staging / "trusted"
                trusted_root.mkdir(mode=0o755)
                captured_path = trusted_root / "executions.json"
                _write_staged(
                    captured_path,
                    captured_set.model_dump_json(indent=2).encode() + b"\n",
                    0o444,
                )
                self._copy_into(
                    container_id,
                    trusted_root,
                    "/evaluation/trusted",
                    request,
                )
                parser_container_id = self._create_trusted_parser(
                    request,
                    evaluation=evaluation,
                    container_name=f"{base}-parser",
                    created_containers=created_containers,
                )
                parsed = self._run_trusted_parser(request, parser_container_id)
                raw_result, normalized = parse_normalized_result(
                    parsed,
                    phase=phase,
                    repeat_index=repeat,
                    source=Path("trusted-parser-stdout"),
                )
        except TaskBundleError as error:
            primary_error = error
            if request.keep_container and container_id is not None:
                details = dict(error.context.details or {})
                details.update(
                    {
                        "retained_container_id": container_id,
                        "retained_parser_container_id": parser_container_id,
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
                    tuple(reversed(created_containers)),
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
            captured_executions=captured_set,
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

    def _build_execution_plan(
        self,
        request: EvaluationRequest,
        container_id: str,
    ) -> TestExecutionPlan:
        command = tuple(request.bundle.task.evaluation.runner.build_plan)
        result = self._exec_root(
            request,
            container_id,
            command,
            ErrorCode.TEST_PARSE_ERROR,
            "build trusted test execution plan",
        )
        payload = result.stdout.encode()
        if len(payload) > _MAX_EXECUTION_PLAN_BYTES:
            self._error(
                ErrorCode.TEST_RESULT_TOO_LARGE,
                "Trusted execution plan exceeds its size limit.",
                f"{len(payload)} bytes",
                request,
            )
        try:
            raw_plan = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            self._error(
                ErrorCode.TEST_RESULT_SCHEMA_ERROR,
                "Trusted execution plan is malformed.",
                str(error),
                request,
            )
        if (
            isinstance(raw_plan, dict)
            and raw_plan.get("schema_version") != "2"
        ):
            self._error(
                ErrorCode.ADAPTER_CONTRACT_UNSUPPORTED,
                "Task adapter execution-plan contract is unsupported.",
                f"schema_version={raw_plan.get('schema_version')!r}; "
                "migrate the adapter to schema version 2 with execution IDs "
                "and requested_selectors arrays",
                request,
            )
        try:
            plan = TestExecutionPlan.model_validate_json(payload)
        except ValidationError as error:
            self._error(
                ErrorCode.TEST_RESULT_SCHEMA_ERROR,
                "Trusted execution plan is malformed.",
                f"{error.error_count()} validation error(s)",
                request,
            )
        expected = [
            *(item.selector for item in request.plan.pass_to_pass),
            *(item.selector for item in request.plan.fail_to_pass),
        ]
        observed = [
            selector
            for item in plan.executions
            for selector in item.requested_selectors
        ]
        if set(observed) != set(expected) or len(observed) != len(expected):
            self._error(
                ErrorCode.TEST_RESULT_INCOMPLETE,
                "Trusted execution plan does not map every selector exactly once.",
                f"expected selectors {expected!r}, observed selectors {observed!r}",
                request,
            )
        if any(
            item.timeout_seconds > request.plan.timeout_seconds
            for item in plan.executions
        ):
            self._error(
                ErrorCode.TEST_RESULT_SCHEMA_ERROR,
                "Trusted execution plan exceeds the evaluation timeout.",
                f"maximum allowed: {request.plan.timeout_seconds}",
                request,
            )
        return plan

    def _validate_captured_executions(
        self,
        request: EvaluationRequest,
        plan: TestExecutionPlan,
        captured: CapturedTestExecutions,
    ) -> None:
        expected = {item.execution_id: item for item in plan.executions}
        observed = {item.execution_id: item for item in captured.executions}
        if expected.keys() != observed.keys():
            self._error(
                ErrorCode.TEST_RESULT_INCOMPLETE,
                "Captured execution records do not match the trusted plan.",
                (
                    f"expected execution IDs {sorted(expected)!r}, "
                    f"observed execution IDs {sorted(observed)!r}"
                ),
                request,
            )
        for execution_id, planned in expected.items():
            actual = observed[execution_id]
            if (
                actual.requested_selectors != planned.requested_selectors
                or actual.argv != planned.argv
            ):
                self._error(
                    ErrorCode.TEST_RESULT_SCHEMA_ERROR,
                    "Captured execution record differs from the trusted plan.",
                    f"execution_id={execution_id!r}",
                    request,
                )

    def _execute_test(
        self,
        request: EvaluationRequest,
        container_id: str,
        execution: TestExecution,
        *,
        restart: bool,
    ) -> CapturedTestExecution:
        started_at = datetime.now(UTC)
        started = time.monotonic()
        exit_code: int | None = None
        stdout = ""
        stderr = ""
        stdout_truncated = False
        stderr_truncated = False
        timed_out = False
        try:
            result = self._exec_user(
                request,
                container_id,
                tuple(execution.argv),
                ErrorCode.TEST_RUNNER_ERROR,
                ErrorCode.TEST_RUNNER_TIMEOUT,
                f"run execution {execution.execution_id}",
                check=False,
                timeout_seconds=execution.timeout_seconds,
            )
            exit_code = result.exit_code
            stdout = result.stdout
            stderr = result.stderr
            stdout_truncated = (
                result.stdout_truncated
                or result.output_truncated
            )
            stderr_truncated = (
                result.stderr_truncated
                or result.output_truncated
            )
        except TaskBundleError as error:
            if error.code != ErrorCode.TEST_RUNNER_TIMEOUT:
                self._terminate_candidate_processes(
                    request,
                    container_id,
                    restart=False,
                )
                raise
            timed_out = True
            details = error.context.details or {}
            stdout = str(details.get("stdout", ""))
            stderr = str(details.get("stderr", ""))
            combined_truncated = bool(details.get("output_truncated", False))
            stdout_truncated = bool(
                details.get("stdout_truncated", combined_truncated)
            )
            stderr_truncated = bool(
                details.get("stderr_truncated", combined_truncated)
            )
        self._terminate_candidate_processes(
            request,
            container_id,
            restart=restart,
        )
        return CapturedTestExecution(
            execution_id=execution.execution_id,
            requested_selectors=execution.requested_selectors,
            argv=execution.argv,
            started_at=started_at,
            finished_at=datetime.now(UTC),
            duration_ms=max(0, int((time.monotonic() - started) * 1000)),
            exit_code=exit_code,
            timed_out=timed_out,
            stdout=stdout,
            stderr=stderr,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
            candidate_processes_terminated=True,
        )

    def _terminate_candidate_processes(
        self,
        request: EvaluationRequest,
        container_id: str,
        *,
        restart: bool,
    ) -> None:
        self._run(
            ("stop", "--time", "1", container_id),
            request,
            ErrorCode.VALIDATION_CLEANUP_ERROR,
            "terminate candidate evaluator processes",
        )
        inspected = self._run(
            (
                "inspect",
                "--format",
                "{{.State.Running}} {{.State.Pid}} "
                "{{.HostConfig.RestartPolicy.Name}}",
                container_id,
            ),
            request,
            ErrorCode.VALIDATION_CLEANUP_ERROR,
            "verify candidate evaluator processes terminated",
        )
        if inspected.stdout.strip() != "false 0 no":
            self._error(
                ErrorCode.VALIDATION_CLEANUP_ERROR,
                "Candidate evaluator still has running processes.",
                inspected.stdout.strip(),
                request,
            )
        if restart:
            self._run(
                ("start", container_id),
                request,
                ErrorCode.EVALUATOR_CREATE_ERROR,
                "restart clean evaluator container",
            )

    def _create_trusted_parser(
        self,
        request: EvaluationRequest,
        *,
        evaluation: str,
        container_name: str,
        created_containers: list[str],
    ) -> str:
        command = tuple(request.bundle.task.evaluation.runner.parse_result)
        created = self._run(
            self._parser_create_args(
                request,
                evaluation=evaluation,
                container_name=container_name,
                command=command,
            ),
            request,
            ErrorCode.TEST_PARSE_ERROR,
            "create trusted parser container",
        )
        created_containers.append(container_name)
        container_id = created.stdout.strip().splitlines()[-1]
        if (
            len(container_id) not in _CONTAINER_ID_LENGTHS
            or any(character not in "0123456789abcdef" for character in container_id)
        ):
            self._run(
                ("rm", "--force", container_name),
                request,
                ErrorCode.VALIDATION_CLEANUP_ERROR,
                "remove malformed trusted parser container",
                check=False,
            )
            self._error(
                ErrorCode.TEST_PARSE_ERROR,
                "Docker returned an invalid trusted parser container ID.",
                container_id[:200],
                request,
            )
        return container_id

    def _run_trusted_parser(
        self,
        request: EvaluationRequest,
        container_id: str,
    ) -> bytes:
        parsed = self._run(
            ("start", "--attach", container_id),
            request,
            ErrorCode.TEST_PARSE_ERROR,
            "run trusted result parser",
            check=False,
        )
        if parsed.exit_code != 0:
            self._error(
                ErrorCode.TEST_PARSE_ERROR,
                "Trusted result parser exited unsuccessfully.",
                parsed.stderr.strip() or f"exit {parsed.exit_code}",
                request,
            )
        return parsed.stdout.encode()

    def _parser_create_args(
        self,
        request: EvaluationRequest,
        *,
        evaluation: str,
        container_name: str,
        command: tuple[str, ...],
    ) -> tuple[str, ...]:
        args = [
            "create",
            "--name",
            container_name,
            "--label",
            f"io.task-bundle.task-id={request.bundle.task.task.id}",
            "--label",
            f"io.task-bundle.command-id={request.command_id}",
            "--label",
            "io.task-bundle.role=trusted-parser",
            "--network",
            "none",
            "--user",
            "65532:65532",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--mount",
            f"type=volume,source={evaluation},target=/evaluation,readonly",
            "--tmpfs",
            "/tmp:size=512m,exec",
            "--env",
            "HOME=/tmp",
            "--env",
            "GOCACHE=/tmp/go-cache",
            "--workdir",
            "/evaluation",
            "--entrypoint",
            command[0],
            request.lock.image_id,
            *command[1:],
        ]
        return tuple(args)

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
        timeout_seconds: int | None = None,
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
            timeout_seconds=timeout_seconds,
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
        timeout_seconds: int | None = None,
    ) -> DockerCommandResult:
        return self.runner.run(
            args,
            cwd=request.bundle.root,
            timeout_seconds=timeout_seconds or request.runtime_policy.timeout_seconds,
            error_code=code,
            timeout_code=timeout_code,
            phase=request.plan.phase.value,
            description=description,
            check=check,
        )

    def _cleanup(
        self,
        request: EvaluationRequest,
        containers: tuple[str, ...],
        volumes: tuple[str, ...],
        primary_error: BaseException | None,
    ) -> bool:
        failures: list[str] = []
        for container_id in containers:
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
