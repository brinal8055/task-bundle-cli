import os
import shutil
from pathlib import Path

import pytest

from task_bundle.errors import ErrorCode, TaskBundleError
from task_bundle.image.docker import (
    SystemDockerRunner,
    sanitized_docker_environment,
)
from task_bundle.image.models import DockerEnvironment


def _executable(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)
    return path


def _environment_info(executable: Path) -> DockerEnvironment:
    return DockerEnvironment(
        executable=str(executable),
        client_version="test",
        server_version="test",
        host_os="linux",
        host_architecture="amd64",
        rootless=False,
    )


def test_docker_preflight_detects_cli_daemon_platform_and_rootless(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = _executable(
        tmp_path / "docker",
        "#!/bin/sh\n"
        'if [ "$1" = version ]; then\n'
        '  echo \'{"Client":{"Version":"28.1"},"Server":{"Version":"28.0"}}\'\n'
        "else\n"
        '  echo \'{"OSType":"linux","Architecture":"arm64",'
        '"SecurityOptions":["name=rootless"]}\'\n'
        "fi\n",
    )
    monkeypatch.setenv("PATH", str(tmp_path))

    runner = SystemDockerRunner.create(tmp_path / "home")

    assert runner.environment_info.executable == str(executable)
    assert runner.environment_info.client_version == "28.1"
    assert runner.environment_info.server_version == "28.0"
    assert runner.environment_info.host_architecture == "arm64"
    assert runner.environment_info.rootless


def test_sanitized_docker_environment_drops_host_and_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOCKER_HOST", "tcp://remote.invalid:2375")
    monkeypatch.setenv("DOCKER_CONTEXT", "remote")
    monkeypatch.setenv("DOCKER_CONFIG", str(tmp_path / "credentialed-config"))
    monkeypatch.setenv("GITHUB_TOKEN", "secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")

    environment = sanitized_docker_environment(tmp_path / "home")

    assert "DOCKER_HOST" not in environment
    assert "DOCKER_CONTEXT" not in environment
    assert "DOCKER_CONFIG" not in environment
    assert "GITHUB_TOKEN" not in environment
    assert "AWS_SECRET_ACCESS_KEY" not in environment
    assert environment["HOME"] == str(tmp_path / "home")


def test_sanitized_docker_environment_stages_only_buildx_plugin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host_home = tmp_path / "host-home"
    (host_home / ".docker/cli-plugins").mkdir(parents=True)
    plugin = _executable(
        host_home / ".docker/cli-plugins/docker-buildx",
        "#!/bin/sh\nexit 0\n",
    )
    (host_home / ".docker/config.json").write_text('{"auths":{"secret":{}}}\n')
    monkeypatch.setenv("HOME", str(host_home))
    isolated = tmp_path / "isolated"

    sanitized_docker_environment(isolated)

    staged = isolated / ".docker/cli-plugins/docker-buildx"
    assert staged.is_symlink()
    assert staged.resolve() == plugin
    assert not (isolated / ".docker/config.json").exists()


def test_docker_runner_never_invokes_a_shell_and_redacts_values(tmp_path: Path) -> None:
    executable = _executable(
        tmp_path / "docker",
        "#!/bin/sh\nprintf '%s\\n' \"$@\"\n",
    )
    runner = SystemDockerRunner(
        str(executable),
        sanitized_docker_environment(tmp_path / "home"),
        _environment_info(executable),
    )
    marker = tmp_path / "must-not-exist"

    result = runner.run(
        ("build", f";touch {marker}", "TOP_SECRET"),
        cwd=tmp_path,
        timeout_seconds=5,
        error_code=ErrorCode.IMAGE_BUILD_ERROR,
        phase="image-build",
        description="test safe invocation",
        redact=("TOP_SECRET",),
    )

    assert "[REDACTED]" in result.stdout
    assert "TOP_SECRET" not in result.stdout
    assert not marker.exists()


def test_docker_timeout_uses_phase_specific_code(tmp_path: Path) -> None:
    executable = _executable(tmp_path / "docker", "#!/bin/sh\nsleep 2\n")
    runner = SystemDockerRunner(
        str(executable),
        sanitized_docker_environment(tmp_path / "home"),
        _environment_info(executable),
    )

    with pytest.raises(TaskBundleError) as caught:
        runner.run(
            ("build",),
            cwd=tmp_path,
            timeout_seconds=1,
            error_code=ErrorCode.IMAGE_BUILD_ERROR,
            timeout_code=ErrorCode.BUILD_TIMEOUT,
            phase="image-build",
            description="build image",
        )

    assert caught.value.code == ErrorCode.BUILD_TIMEOUT


def test_missing_docker_is_structured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))

    with pytest.raises(TaskBundleError) as caught:
        SystemDockerRunner.create(tmp_path / "home")

    assert caught.value.code == ErrorCode.DOCKER_NOT_AVAILABLE


def test_real_docker_preflight_when_available(tmp_path: Path) -> None:
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI is not installed")
    try:
        runner = SystemDockerRunner.create(tmp_path / "docker-home")
    except TaskBundleError as error:
        pytest.skip(f"Docker daemon unavailable to the isolated CLI: {error.code}")

    assert runner.environment_info.client_version
    assert runner.environment_info.server_version
    assert os.path.isabs(runner.environment_info.executable)
