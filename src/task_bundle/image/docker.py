import json
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, NoReturn, Protocol

from task_bundle.errors import ErrorCode, ErrorContext, TaskBundleError
from task_bundle.image.models import DockerEnvironment


@dataclass(frozen=True, slots=True)
class DockerCommandResult:
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: int
    output_truncated: bool


class DockerRunner(Protocol):
    environment_info: DockerEnvironment

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
    ) -> DockerCommandResult: ...


class SystemDockerRunner:
    def __init__(
        self,
        executable: str,
        environment: Mapping[str, str],
        environment_info: DockerEnvironment,
        max_output_bytes: int = 8_388_608,
    ) -> None:
        self.executable = executable
        self.environment = dict(environment)
        self.environment_info = environment_info
        self.max_output_bytes = max_output_bytes
        self.last_result: DockerCommandResult | None = None
        self.last_failure_result: DockerCommandResult | None = None

    @classmethod
    def create(
        cls,
        home: Path,
        timeout_seconds: int = 10,
    ) -> "SystemDockerRunner":
        environment = sanitized_docker_environment(home)
        executable = shutil.which("docker", path=environment.get("PATH"))
        if executable is None:
            _docker_error(
                ErrorCode.DOCKER_NOT_AVAILABLE,
                "Docker CLI is not available.",
                "A Docker CLI executable on PATH",
                "Docker was not found",
                "Install Docker and ensure the CLI is on PATH.",
                "docker-preflight",
            )
        executable = str(Path(executable).resolve())
        provisional = DockerEnvironment(
            executable=executable,
            client_version="unknown",
            server_version="unknown",
            host_os="unknown",
            host_architecture="unknown",
            rootless=False,
        )
        runner = cls(executable, environment, provisional)
        version_result = runner.run(
            ("version", "--format", "{{json .}}"),
            cwd=Path.cwd(),
            timeout_seconds=timeout_seconds,
            error_code=ErrorCode.DOCKER_DAEMON_ERROR,
            phase="docker-preflight",
            description="query Docker client and daemon versions",
        )
        info_result = runner.run(
            ("info", "--format", "{{json .}}"),
            cwd=Path.cwd(),
            timeout_seconds=timeout_seconds,
            error_code=ErrorCode.DOCKER_DAEMON_ERROR,
            phase="docker-preflight",
            description="query Docker daemon capabilities",
        )
        version = _json_object(
            version_result.stdout,
            ErrorCode.DOCKER_VERSION_ERROR,
            "Docker version",
        )
        info = _json_object(
            info_result.stdout,
            ErrorCode.DOCKER_DAEMON_ERROR,
            "Docker info",
        )
        client = _nested_string(version, "Client", "Version")
        server = _nested_string(version, "Server", "Version")
        host_os = _required_string(info, "OSType")
        host_architecture = _required_string(info, "Architecture")
        security_options = json.dumps(info.get("SecurityOptions", []), sort_keys=True)
        runner.environment_info = DockerEnvironment(
            executable=executable,
            client_version=client,
            server_version=server,
            host_os=host_os,
            host_architecture=host_architecture,
            rootless="rootless" in security_options.lower(),
        )
        return runner

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
        result = _execute(
            (self.executable, *args),
            cwd=cwd,
            environment=self.environment,
            timeout_seconds=timeout_seconds,
            max_output_bytes=self.max_output_bytes,
            timeout_code=timeout_code or error_code,
            phase=phase,
            description=description,
        )
        result = DockerCommandResult(
            stdout=_redact(result.stdout, redact),
            stderr=_redact(result.stderr, redact),
            exit_code=result.exit_code,
            duration_ms=result.duration_ms,
            output_truncated=result.output_truncated,
        )
        self.last_result = result
        if result.exit_code != 0:
            self.last_failure_result = result
        if check and result.exit_code != 0:
            output = result.stderr.strip() or result.stdout.strip() or "no Docker output"
            raise TaskBundleError(
                error_code,
                f"Docker failed while attempting to {description}.",
                ErrorContext(
                    phase=phase,
                    expected=f"Docker command to {description} to succeed",
                    actual=f"Exit code {result.exit_code}: {output[:2000]}",
                    corrective_action="Review the command artifacts and Docker daemon state.",
                    details={
                        "exit_code": result.exit_code,
                        "output_truncated": result.output_truncated,
                    },
                ),
            )
        return result


def sanitized_docker_environment(home: Path) -> dict[str, str]:
    try:
        home.mkdir(parents=True, exist_ok=True)
        xdg = home / "xdg"
        xdg.mkdir(exist_ok=True)
    except OSError as error:
        _docker_error(
            ErrorCode.DOCKER_NOT_AVAILABLE,
            "Docker configuration sandbox could not be created.",
            "A writable isolated Docker HOME",
            str(error),
            "Check temporary-directory permissions.",
            "docker-preflight",
        )
    _stage_docker_cli_plugin(home, "docker-buildx")
    environment = {
        "PATH": os.environ.get("PATH", os.defpath),
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(xdg),
        "LC_ALL": "C",
        "LANG": "C",
    }
    temporary = os.environ.get("TMPDIR")
    if temporary:
        environment["TMPDIR"] = temporary
    return environment


def _stage_docker_cli_plugin(home: Path, name: str) -> None:
    candidates = (
        Path.home() / ".docker" / "cli-plugins" / name,
        Path("/usr/local/lib/docker/cli-plugins") / name,
        Path("/usr/local/libexec/docker/cli-plugins") / name,
        Path("/usr/libexec/docker/cli-plugins") / name,
    )
    plugin = next(
        (
            candidate
            for candidate in candidates
            if candidate.is_file() and os.access(candidate, os.X_OK)
        ),
        None,
    )
    if plugin is None:
        return
    destination = home / ".docker" / "cli-plugins" / name
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.symlink_to(plugin.resolve())
    except FileExistsError:
        return
    except OSError as error:
        _docker_error(
            ErrorCode.DOCKER_NOT_AVAILABLE,
            "Docker CLI plugin could not be staged in the isolated configuration.",
            f"A usable isolated {name} plugin",
            str(error),
            "Check Docker CLI plugin permissions.",
            "docker-preflight",
        )


def _execute(
    command: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    timeout_seconds: int,
    max_output_bytes: int,
    timeout_code: ErrorCode,
    phase: str,
    description: str,
) -> DockerCommandResult:
    started = time.monotonic()
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        try:
            process = subprocess.Popen(
                list(command),
                cwd=cwd,
                env=dict(environment),
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                shell=False,
            )
        except FileNotFoundError as error:
            _docker_error(
                ErrorCode.DOCKER_NOT_AVAILABLE,
                "Docker CLI is not available.",
                f"Docker to {description}",
                str(error),
                "Install Docker and ensure it is on PATH.",
                phase,
            )
        try:
            exit_code = process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired as error:
            process.kill()
            process.wait()
            stdout, stdout_truncated = _read_output(stdout_file, max_output_bytes)
            stderr, stderr_truncated = _read_output(stderr_file, max_output_bytes)
            raise TaskBundleError(
                timeout_code,
                f"Docker timed out while attempting to {description}.",
                ErrorContext(
                    phase=phase,
                    expected=f"Completion within {timeout_seconds} seconds",
                    actual=(
                        "The Docker process exceeded its timeout. "
                        f"stderr: {(stderr.strip() or 'no stderr')[:2000]}"
                    ),
                    corrective_action="Review Docker daemon health and configured timeout.",
                    details={
                        "stdout": stdout,
                        "stderr": stderr,
                        "output_truncated": stdout_truncated or stderr_truncated,
                    },
                ),
            ) from error
        stdout, stdout_truncated = _read_output(stdout_file, max_output_bytes)
        stderr, stderr_truncated = _read_output(stderr_file, max_output_bytes)
    return DockerCommandResult(
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
        duration_ms=max(0, int((time.monotonic() - started) * 1000)),
        output_truncated=stdout_truncated or stderr_truncated,
    )


def _read_output(handle: BinaryIO, limit: int) -> tuple[str, bool]:
    handle.seek(0)
    content = handle.read(limit + 1)
    truncated = len(content) > limit
    text = content[:limit].decode("utf-8", errors="replace")
    if truncated:
        text += "\n[output truncated]"
    return text, truncated


def _json_object(value: str, code: ErrorCode, description: str) -> dict[str, object]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        _docker_error(
            code,
            f"{description} output is malformed.",
            "A JSON object from the Docker CLI",
            str(error),
            "Use a supported Docker CLI and daemon.",
            "docker-preflight",
        )
    if not isinstance(parsed, dict):
        _docker_error(
            code,
            f"{description} output has an unexpected shape.",
            "A JSON object from the Docker CLI",
            type(parsed).__name__,
            "Use a supported Docker CLI and daemon.",
            "docker-preflight",
        )
    return parsed


def _nested_string(value: dict[str, object], first: str, second: str) -> str:
    nested = value.get(first)
    if not isinstance(nested, dict):
        _malformed_docker_field(f"{first}.{second}")
    item = nested.get(second)
    if not isinstance(item, str) or not item:
        _malformed_docker_field(f"{first}.{second}")
    return item


def _required_string(value: dict[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        _malformed_docker_field(key)
    return item


def _malformed_docker_field(field: str) -> NoReturn:
    _docker_error(
        ErrorCode.DOCKER_VERSION_ERROR,
        "Docker preflight output is incomplete.",
        f"A non-empty {field} field",
        "The required field is absent or invalid.",
        "Use a supported Docker CLI and daemon.",
        "docker-preflight",
    )


def _redact(value: str, secrets: Sequence[str]) -> str:
    redacted = value
    for secret in sorted({item for item in secrets if item}, key=len, reverse=True):
        redacted = redacted.replace(secret, "[REDACTED]")
    return redacted


def _docker_error(
    code: ErrorCode,
    message: str,
    expected: str,
    actual: str,
    corrective_action: str,
    phase: str,
) -> NoReturn:
    raise TaskBundleError(
        code,
        message,
        ErrorContext(
            phase=phase,
            expected=expected,
            actual=actual,
            corrective_action=corrective_action,
        ),
    )
