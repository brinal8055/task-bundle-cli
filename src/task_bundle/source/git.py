import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, NoReturn, Protocol

from task_bundle.errors import ErrorCode, ErrorContext, TaskBundleError

_GIT_VERSION = re.compile(r"^git version ([^\s]+)")
_SAFE_CONFIG = (
    "core.hooksPath=/dev/null",
    "credential.helper=",
    "protocol.allow=never",
    "protocol.https.allow=always",
    "protocol.file.allow=never",
    "protocol.ext.allow=never",
    "protocol.ssh.allow=never",
    "submodule.recurse=false",
)


@dataclass(frozen=True, slots=True)
class GitInstallation:
    executable: str
    version: str


@dataclass(frozen=True, slots=True)
class GitCommandResult:
    stdout: str
    stderr: str
    exit_code: int
    output_truncated: bool


class GitRunner(Protocol):
    installation: GitInstallation

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        timeout_seconds: int,
        error_code: ErrorCode,
        phase: str,
        description: str,
    ) -> GitCommandResult: ...


class SystemGitRunner:
    def __init__(
        self,
        installation: GitInstallation,
        environment: Mapping[str, str],
        max_output_bytes: int = 1_048_576,
    ) -> None:
        self.installation = installation
        self.environment = dict(environment)
        self.max_output_bytes = max_output_bytes

    @classmethod
    def create(cls, home: Path, timeout_seconds: int = 5) -> "SystemGitRunner":
        environment = sanitized_git_environment(home)
        installation = detect_git(environment, timeout_seconds)
        return cls(installation, environment)

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        timeout_seconds: int,
        error_code: ErrorCode,
        phase: str,
        description: str,
    ) -> GitCommandResult:
        command = [self.installation.executable]
        for setting in _SAFE_CONFIG:
            command.extend(("-c", setting))
        command.extend(args)
        result = _execute(
            command,
            cwd=cwd,
            environment=self.environment,
            timeout_seconds=timeout_seconds,
            max_output_bytes=self.max_output_bytes,
            timeout_code=error_code,
            phase=phase,
            description=description,
        )
        if result.exit_code != 0:
            raise TaskBundleError(
                error_code,
                f"Git failed while attempting to {description}.",
                ErrorContext(
                    phase=phase,
                    expected=f"Git command to {description} to succeed",
                    actual=f"Exit code {result.exit_code}: {_stderr_excerpt(result.stderr)}",
                    corrective_action=(
                        "Verify the public repository and exact commit are available."
                    ),
                    details={
                        "exit_code": result.exit_code,
                        "stderr": _stderr_excerpt(result.stderr),
                        "output_truncated": result.output_truncated,
                    },
                ),
            )
        return result


def sanitized_git_environment(home: Path) -> dict[str, str]:
    home.mkdir(parents=True, exist_ok=True)
    xdg = home / "xdg"
    xdg.mkdir()
    environment = {
        "PATH": os.environ.get("PATH", os.defpath),
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(xdg),
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "/bin/false",
        "SSH_ASKPASS": "/bin/false",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "LC_ALL": "C",
        "LANG": "C",
    }
    temporary = os.environ.get("TMPDIR")
    if temporary:
        environment["TMPDIR"] = temporary
    return environment


def detect_git(
    environment: Mapping[str, str],
    timeout_seconds: int = 5,
    executable: str = "git",
) -> GitInstallation:
    resolved = shutil.which(executable, path=environment.get("PATH"))
    if resolved is None:
        _git_error(
            ErrorCode.GIT_NOT_AVAILABLE,
            "Git executable is not available.",
            "Install Git and ensure it is present on PATH.",
        )
    resolved = str(Path(resolved).resolve())
    result = _execute(
        [resolved, "--version"],
        cwd=Path.cwd(),
        environment=environment,
        timeout_seconds=timeout_seconds,
        max_output_bytes=4096,
        timeout_code=ErrorCode.GIT_VERSION_ERROR,
        phase="git-version",
        description="detect the Git version",
    )
    if result.exit_code != 0:
        _git_error(
            ErrorCode.GIT_VERSION_ERROR,
            "Git version detection failed.",
            "Verify the Git installation is executable.",
            actual=_stderr_excerpt(result.stderr),
        )
    match = _GIT_VERSION.match(result.stdout.strip())
    if match is None:
        _git_error(
            ErrorCode.GIT_VERSION_ERROR,
            "Git version output is malformed.",
            "Use a standard Git executable that supports `git --version`.",
            actual=result.stdout.strip(),
        )
    return GitInstallation(executable=resolved, version=match.group(1))


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
) -> GitCommandResult:
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
            raise TaskBundleError(
                ErrorCode.GIT_NOT_AVAILABLE,
                "Git executable is not available.",
                ErrorContext(
                    phase=phase,
                    expected=f"Git to {description}",
                    actual=str(error),
                    corrective_action="Install Git and ensure it is present on PATH.",
                ),
            ) from error
        try:
            exit_code = process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired as error:
            process.kill()
            process.wait()
            raise TaskBundleError(
                timeout_code,
                f"Git timed out while attempting to {description}.",
                ErrorContext(
                    phase=phase,
                    expected=f"Completion within {timeout_seconds} seconds",
                    actual="The Git process exceeded its timeout",
                    corrective_action="Check repository availability and increase the timeout.",
                ),
            ) from error
        stdout, stdout_truncated = _read_output(stdout_file, max_output_bytes)
        stderr, stderr_truncated = _read_output(stderr_file, max_output_bytes)
    return GitCommandResult(
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
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


def _stderr_excerpt(stderr: str, limit: int = 2000) -> str:
    return stderr.strip()[:limit] or "no stderr output"


def _git_error(
    code: ErrorCode,
    message: str,
    hint: str,
    actual: str = "Git was not detected",
) -> NoReturn:
    raise TaskBundleError(
        code,
        message,
        ErrorContext(
            phase="git-version",
            expected="A working Git executable",
            actual=actual,
            corrective_action=hint,
        ),
    )
