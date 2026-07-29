import os
from pathlib import Path

import pytest

from task_bundle.database import Database
from task_bundle.errors import ErrorCode, TaskBundleError
from task_bundle.image.docker import SystemDockerRunner
from task_bundle.image.service import InitOptions, InitService
from tests.bundle_helpers import create_bundle, read_task, write_task
from tests.image_helpers import StaticSourceFactory


def test_real_docker_synthetic_init_when_configured(tmp_path: Path) -> None:
    base_image = os.environ.get("TASK_BUNDLE_REAL_DOCKER_BASE")
    if not base_image:
        pytest.skip("set TASK_BUNDLE_REAL_DOCKER_BASE to a locally present digest-pinned image")
    platform = os.environ.get("TASK_BUNDLE_REAL_DOCKER_PLATFORM", "linux/amd64")
    bundle = create_bundle(tmp_path / "bundle")
    mapping = read_task(bundle)
    mapping["environment"] = {
        "type": "base_image",
        "image": base_image,
        "platform": platform,
        "build": {"network": False},
    }
    write_task(bundle, mapping)
    source = StaticSourceFactory(tmp_path / "source")
    runners: list[SystemDockerRunner] = []

    def docker_factory(home: Path) -> SystemDockerRunner:
        runner = SystemDockerRunner.create(home)
        runners.append(runner)
        return runner

    service = InitService(
        database=Database(tmp_path / "task.db"),
        cli_version="test",
        source_factory=source,
        docker_factory=docker_factory,
    )
    image_reference: str | None = None
    try:
        try:
            first = service.run(bundle, InitOptions(no_cache=True))
        except TaskBundleError as error:
            pytest.fail(f"{error.code}: {error.context.actual}")
        image_reference = first.image_reference
        second = service.run(bundle, InitOptions())

        assert first.status == "initialized"
        assert second.status == "already_initialized"
        assert first.image_id == second.image_id
        assert source.calls == 1
    finally:
        if image_reference is not None and runners:
            runners[-1].run(
                ("image", "rm", "--force", image_reference),
                cwd=tmp_path,
                timeout_seconds=60,
                error_code=ErrorCode.CLEANUP_ERROR,
                phase="test-cleanup",
                description="remove synthetic test image",
                check=False,
            )
