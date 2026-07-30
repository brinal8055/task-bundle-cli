import pytest

from task_bundle.errors import ErrorCode, TaskBundleError
from task_bundle.image.inspect import inspect_image
from tests.image_helpers import FakeDockerRunner

REFERENCE = "task-bundle/example:sha256-test"


def _runner_with_volumes(*volumes: str) -> FakeDockerRunner:
    runner = FakeDockerRunner()
    runner.images[REFERENCE] = {
        "Id": "sha256:" + "a" * 64,
        "RepoTags": [REFERENCE],
        "RepoDigests": [],
        "Os": "linux",
        "Architecture": "amd64",
        "Variant": "",
        "Created": "2026-07-30T00:00:00Z",
        "Config": {
            "User": "1000:1000",
            "WorkingDir": "/workspace/repo",
            "Labels": {},
            "Volumes": dict.fromkeys(volumes),
        },
        "Size": 1024,
    }
    return runner


@pytest.mark.parametrize(
    "volume",
    [
        "/",
        "/opt",
        "/opt/task",
        "/opt/task/",
        "/opt/task/repo",
        "/opt/task/repo/",
        "/opt/task/repo/cache",
        "//opt//task//repo//cache/",
    ],
)
def test_image_inspection_rejects_source_volume_overlap(volume: str) -> None:
    runner = _runner_with_volumes(volume)

    with pytest.raises(TaskBundleError) as caught:
        inspect_image(runner, REFERENCE)

    assert caught.value.code == ErrorCode.IMAGE_SOURCE_VOLUME_CONFLICT
    assert caught.value.context.details is not None
    assert caught.value.context.details["protected_path"] == "/opt/task/repo"


@pytest.mark.parametrize(
    "volume",
    ["/opt/task/repository", "/opt/task/repo-cache", "/data"],
)
def test_image_inspection_accepts_unrelated_volume(volume: str) -> None:
    inspection = inspect_image(_runner_with_volumes(volume), REFERENCE)

    assert inspection.declared_volumes == (volume,)


@pytest.mark.parametrize(
    "volume",
    ["", "relative", "/opt/./task", "/opt/task/../repo"],
)
def test_image_inspection_rejects_malformed_volume(volume: str) -> None:
    with pytest.raises(TaskBundleError) as caught:
        inspect_image(_runner_with_volumes(volume), REFERENCE)

    assert caught.value.code == ErrorCode.IMAGE_SOURCE_VOLUME_CONFLICT
    assert caught.value.context.details is not None
    assert volume in caught.value.context.details["malformed_volume_paths"]


def test_image_inspection_parses_sorted_normalized_volumes() -> None:
    inspection = inspect_image(
        _runner_with_volumes("/var/lib/data/", "//data//cache"),
        REFERENCE,
    )

    assert inspection.declared_volumes == ("/data/cache", "/var/lib/data")
