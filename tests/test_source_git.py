import os
from pathlib import Path

import pytest

from task_bundle.errors import ErrorCode, TaskBundleError
from task_bundle.source.git import (
    GitInstallation,
    SystemGitRunner,
    detect_git,
    sanitized_git_environment,
)


def _write_executable(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)
    return path


def test_git_is_detected_with_version(tmp_path: Path) -> None:
    environment = sanitized_git_environment(tmp_path / "home")

    installation = detect_git(environment)

    assert Path(installation.executable).is_absolute()
    assert installation.version


def test_missing_git_is_structured_error(tmp_path: Path) -> None:
    environment = sanitized_git_environment(tmp_path / "home")
    environment["PATH"] = str(tmp_path / "empty")

    with pytest.raises(TaskBundleError) as caught:
        detect_git(environment)

    assert caught.value.code == ErrorCode.GIT_NOT_AVAILABLE


def test_malformed_git_version_is_rejected(tmp_path: Path) -> None:
    executable = _write_executable(tmp_path / "fake-git", "#!/bin/sh\necho unexpected\n")
    environment = sanitized_git_environment(tmp_path / "home")

    with pytest.raises(TaskBundleError) as caught:
        detect_git(environment, executable=str(executable))

    assert caught.value.code == ErrorCode.GIT_VERSION_ERROR


def test_git_version_timeout_is_structured(tmp_path: Path) -> None:
    executable = _write_executable(tmp_path / "slow-git", "#!/bin/sh\nsleep 2\n")
    environment = sanitized_git_environment(tmp_path / "home")

    with pytest.raises(TaskBundleError) as caught:
        detect_git(environment, timeout_seconds=1, executable=str(executable))

    assert caught.value.code == ErrorCode.GIT_VERSION_ERROR


def test_sanitized_environment_does_not_forward_host_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SSH_AUTH_SOCK", "/secret/agent")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("GITHUB_TOKEN", "secret")

    environment = sanitized_git_environment(tmp_path / "home")

    assert environment["GIT_TERMINAL_PROMPT"] == "0"
    assert environment["GIT_ASKPASS"] == "/bin/false"
    assert environment["SSH_ASKPASS"] == "/bin/false"
    assert environment["GIT_CONFIG_NOSYSTEM"] == "1"
    assert environment["GIT_CONFIG_GLOBAL"] == "/dev/null"
    assert "SSH_AUTH_SOCK" not in environment
    assert "AWS_SECRET_ACCESS_KEY" not in environment
    assert "GITHUB_TOKEN" not in environment


def test_runner_captures_and_truncates_output_without_shell(
    tmp_path: Path,
) -> None:
    executable = _write_executable(
        tmp_path / "fake-git",
        "#!/bin/sh\nprintf 'abcdefghijklmnopqrstuvwxyz'\nprintf 'error-output' >&2\n",
    )
    runner = SystemGitRunner(
        GitInstallation(str(executable), "test"),
        sanitized_git_environment(tmp_path / "home"),
        max_output_bytes=10,
    )
    marker = tmp_path / "must-not-exist"

    result = runner.run(
        ("status", f";touch {marker}"),
        cwd=tmp_path,
        timeout_seconds=5,
        error_code=ErrorCode.SOURCE_FETCH_ERROR,
        phase="test",
        description="capture output",
    )

    assert result.stdout.startswith("abcdefghij")
    assert result.stderr.startswith("error-outp")
    assert result.output_truncated
    assert not marker.exists()


def test_runner_applies_git_security_configuration(tmp_path: Path) -> None:
    executable = _write_executable(
        tmp_path / "fake-git",
        "#!/bin/sh\nprintf '%s\\n' \"$@\"\n",
    )
    runner = SystemGitRunner(
        GitInstallation(str(executable), "test"),
        sanitized_git_environment(tmp_path / "home"),
    )

    result = runner.run(
        ("status",),
        cwd=tmp_path,
        timeout_seconds=5,
        error_code=ErrorCode.SOURCE_FETCH_ERROR,
        phase="test",
        description="inspect configuration",
    )

    assert "credential.helper=" in result.stdout
    assert "protocol.allow=never" in result.stdout
    assert "protocol.https.allow=always" in result.stdout
    assert "protocol.http.allow=never" in result.stdout
    assert "protocol.git.allow=never" in result.stdout
    assert "protocol.file.allow=never" in result.stdout
    assert "protocol.ext.allow=never" in result.stdout
    assert "protocol.ssh.allow=never" in result.stdout
    assert "http.followRedirects=initial" in result.stdout
    assert "submodule.recurse=false" in result.stdout


def test_runner_maps_nonzero_exit_without_environment_leak(tmp_path: Path) -> None:
    executable = _write_executable(
        tmp_path / "fake-git",
        "#!/bin/sh\necho safe-error >&2\nexit 7\n",
    )
    runner = SystemGitRunner(
        GitInstallation(str(executable), "test"),
        sanitized_git_environment(tmp_path / "home"),
    )

    with pytest.raises(TaskBundleError) as caught:
        runner.run(
            ("fetch",),
            cwd=tmp_path,
            timeout_seconds=5,
            error_code=ErrorCode.SOURCE_FETCH_ERROR,
            phase="source-fetch",
            description="fetch exact commit",
        )

    assert caught.value.code == ErrorCode.SOURCE_FETCH_ERROR
    assert caught.value.context.details is not None
    assert caught.value.context.details["exit_code"] == 7
    assert "safe-error" in caught.value.context.actual
    assert os.environ.get("PATH", "") not in caught.value.context.actual


def test_runner_command_timeout_is_structured(tmp_path: Path) -> None:
    executable = _write_executable(tmp_path / "slow-git", "#!/bin/sh\nsleep 2\n")
    runner = SystemGitRunner(
        GitInstallation(str(executable), "test"),
        sanitized_git_environment(tmp_path / "home"),
    )

    with pytest.raises(TaskBundleError) as caught:
        runner.run(
            ("fetch",),
            cwd=tmp_path,
            timeout_seconds=1,
            error_code=ErrorCode.SOURCE_FETCH_ERROR,
            phase="source-fetch",
            description="fetch exact commit",
        )

    assert caught.value.code == ErrorCode.SOURCE_FETCH_ERROR
