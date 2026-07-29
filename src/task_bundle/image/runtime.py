import re
from dataclasses import dataclass
from pathlib import Path

from task_bundle.bundle.canonical import canonical_json_bytes, sha256_digest
from task_bundle.errors import ErrorCode, ErrorContext, TaskBundleError
from task_bundle.image.docker import DockerRunner
from task_bundle.image.models import (
    DockerCommandRecord,
    ImageInspection,
    RuntimePolicy,
    SmokeCheckResult,
)
from task_bundle.image.validation import runtime_policy_from_settings
from task_bundle.models import RuntimeSettings, SourceFileEntry, SourceManifest

_CONTAINER_ID = re.compile(r"^[0-9a-f]{12,64}$")
_SMOKE_SCRIPT = (
    "set -eu; "
    "test -d /opt/task/repo; "
    "test -r /opt/task/repo; "
    'test -d "$PWD"; '
    'test -r "$1"; '
    "if command -v sha256sum >/dev/null 2>&1; then "
    'printf \'%s  %s\\n\' "$2" "$1" | sha256sum -c - >/dev/null; '
    "elif command -v shasum >/dev/null 2>&1; then "
    'test "$(shasum -a 256 "$1" | cut -d \' \' -f 1)" = "$2"; '
    "else echo 'sha256 utility unavailable' >&2; exit 70; fi"
)


@dataclass(frozen=True, slots=True)
class SmokeOutcome:
    result: SmokeCheckResult
    command: DockerCommandRecord


@dataclass(frozen=True, slots=True)
class SmokePlan:
    args: tuple[str, ...]
    command: DockerCommandRecord
    container_name: str
    source_probe: str
    timeout_seconds: int


def create_runtime_policy(settings: RuntimeSettings) -> RuntimePolicy:
    return RuntimePolicy.model_validate(runtime_policy_from_settings(settings))


def runtime_policy_digest(policy: RuntimePolicy) -> str:
    return sha256_digest(canonical_json_bytes(policy))


def smoke_check(
    runner: DockerRunner,
    *,
    inspection: ImageInspection,
    source_manifest: SourceManifest,
    settings: RuntimeSettings,
    command_id: str,
    plan: SmokePlan | None = None,
) -> SmokeOutcome:
    smoke_plan = plan or create_smoke_plan(
        inspection=inspection,
        source_manifest=source_manifest,
        settings=settings,
        command_id=command_id,
    )
    container_name = smoke_plan.container_name
    source_probe = smoke_plan.source_probe
    timeout = smoke_plan.timeout_seconds
    args = smoke_plan.args
    command = smoke_plan.command
    container_id: str | None = None
    start_stdout = ""
    start_stderr = ""
    duration_ms = 0
    cleanup_succeeded = False
    primary_error: BaseException | None = None
    try:
        created = runner.run(
            args,
            cwd=Path.cwd(),
            timeout_seconds=30,
            error_code=ErrorCode.SMOKE_CHECK_ERROR,
            phase="image-smoke-check",
            description="create the restricted image smoke-check container",
        )
        container_lines = created.stdout.strip().splitlines()
        candidate_id = container_lines[-1] if container_lines else ""
        if _CONTAINER_ID.fullmatch(candidate_id) is None:
            raise TaskBundleError(
                ErrorCode.SMOKE_CHECK_ERROR,
                "Docker returned an invalid smoke-check container ID.",
                ErrorContext(
                    phase="image-smoke-check",
                    expected="A hexadecimal Docker container ID",
                    actual=candidate_id[:200],
                    corrective_action="Review Docker daemon output.",
                ),
            )
        container_id = candidate_id
        started = runner.run(
            ("start", "--attach", container_id),
            cwd=Path.cwd(),
            timeout_seconds=timeout,
            error_code=ErrorCode.SMOKE_CHECK_ERROR,
            phase="image-smoke-check",
            description="run the restricted image smoke check",
            timeout_code=ErrorCode.SMOKE_TIMEOUT,
        )
        start_stdout = started.stdout
        start_stderr = started.stderr
        duration_ms = started.duration_ms
    except BaseException as error:
        primary_error = error
        raise
    finally:
        if container_id is not None:
            try:
                cleanup = runner.run(
                    ("rm", "--force", container_id),
                    cwd=Path.cwd(),
                    timeout_seconds=30,
                    error_code=ErrorCode.CLEANUP_ERROR,
                    phase="image-smoke-cleanup",
                    description="remove the image smoke-check container",
                    check=False,
                )
            except TaskBundleError:
                if primary_error is None:
                    raise
            else:
                cleanup_succeeded = cleanup.exit_code == 0
                if not cleanup_succeeded and primary_error is None:
                    raise TaskBundleError(
                        ErrorCode.CLEANUP_ERROR,
                        "Smoke-check container could not be removed.",
                        ErrorContext(
                            phase="image-smoke-cleanup",
                            expected="The temporary container to be deleted",
                            actual=f"Docker exit code {cleanup.exit_code}",
                            corrective_action="Remove the container manually.",
                        ),
                    )
    return SmokeOutcome(
        result=SmokeCheckResult(
            container_name=container_name,
            image_id=inspection.image_id,
            source_probe=source_probe,
            stdout=start_stdout,
            stderr=start_stderr,
            duration_ms=duration_ms,
            cleaned_up=cleanup_succeeded,
        ),
        command=command,
    )


def create_smoke_plan(
    *,
    inspection: ImageInspection,
    source_manifest: SourceManifest,
    settings: RuntimeSettings,
    command_id: str,
) -> SmokePlan:
    policy = create_runtime_policy(settings)
    probe_entry = next(
        (entry for entry in source_manifest.entries if isinstance(entry, SourceFileEntry)),
        None,
    )
    if probe_entry is None:
        raise TaskBundleError(
            ErrorCode.SMOKE_CHECK_ERROR,
            "Task source contains no regular file to probe.",
            ErrorContext(
                phase="image-smoke-check",
                expected="At least one readable regular source file",
                actual="The materialised repository has no regular files",
                corrective_action="Use a repository commit containing task source.",
            ),
        )
    source_probe = f"/opt/task/repo/{probe_entry.path}"
    container_name = f"task-bundle-smoke-{command_id.removeprefix('cmd_')[:24]}"
    args: list[str] = [
        "create",
        "--name",
        container_name,
        "--network",
        "none",
        "--user",
        policy.user,
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
        "--workdir",
        policy.working_directory,
    ]
    for tmpfs in policy.tmpfs:
        args.extend(("--tmpfs", tmpfs))
    args.extend(
        (
            inspection.image_id,
            "/bin/sh",
            "-c",
            _SMOKE_SCRIPT,
            "task-bundle-smoke",
            source_probe,
            probe_entry.sha256.removeprefix("sha256:"),
        )
    )
    timeout = min(policy.timeout_seconds, 120)
    command = DockerCommandRecord(
        phase="image-smoke-check",
        argv=("docker", *args),
        timeout_seconds=timeout,
    )
    return SmokePlan(
        args=tuple(args),
        command=command,
        container_name=container_name,
        source_probe=source_probe,
        timeout_seconds=timeout,
    )
