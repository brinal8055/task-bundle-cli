from pathlib import Path

import pytest

from task_bundle.bundle.loader import load_bundle
from task_bundle.errors import ErrorCode, TaskBundleError
from task_bundle.image.validation import (
    task_image_reference,
    validate_base_image_reference,
    validate_build_args,
    validate_platform,
)
from tests.bundle_helpers import create_bundle, read_task, write_task


def test_base_image_must_be_safe_lowercase_digest_reference() -> None:
    valid = f"registry.example/team/image@sha256:{'a' * 64}"

    assert validate_base_image_reference(valid) == valid

    for invalid in (
        "ubuntu:latest",
        "registry/image",
        f"https://registry/image@sha256:{'a' * 64}",
        f"User:pass@registry/image@sha256:{'a' * 64}",
        f"Registry/Image@sha256:{'a' * 64}",
        f"registry/image@SHA256:{'a' * 64}",
        f"registry/image@sha512:{'a' * 64}",
        f"registry/image@sha256:{'A' * 64}",
        f"registry/image@sha256:{'a' * 63}",
        f"registry/image@sha256:{'a' * 65}",
        f"registry/image@sha256:{'a' * 64};touch",
    ):
        with pytest.raises(TaskBundleError) as caught:
            validate_base_image_reference(invalid)
        assert caught.value.code == ErrorCode.BUILD_CONFIG_ERROR


def test_platform_and_image_reference_are_deterministic() -> None:
    assert validate_platform("linux/amd64") == "linux/amd64"
    reference = task_image_reference("Task / One", "sha256:" + "b" * 64, "linux/amd64")

    assert reference == f"task-bundle/task-one:{'b' * 16}-linux-amd64"

    with pytest.raises(TaskBundleError):
        validate_platform("Linux/AMD64")


@pytest.mark.parametrize("name", ["TOKEN", "DB_PASSWORD", "API_KEY", "AUTH_HEADER"])
def test_secret_like_build_argument_names_are_rejected(name: str) -> None:
    with pytest.raises(TaskBundleError) as caught:
        validate_build_args({name: "must-never-appear"})

    assert caught.value.code == ErrorCode.BUILD_CONFIG_ERROR
    assert "must-never-appear" not in caught.value.context.actual


def test_non_secret_word_containing_auth_prefix_is_not_over_rejected() -> None:
    validate_build_args({"AUTHOR": "Task Bundle"})


def test_loaded_base_image_stays_digest_covered(tmp_path: Path) -> None:
    bundle_path = create_bundle(tmp_path / "bundle")
    mapping = read_task(bundle_path)
    mapping["environment"] = {
        "type": "base_image",
        "image": f"busybox@sha256:{'a' * 64}",
        "platform": "linux/amd64",
    }
    write_task(bundle_path, mapping)

    bundle = load_bundle(bundle_path)

    assert bundle.task.environment.type == "base_image"
