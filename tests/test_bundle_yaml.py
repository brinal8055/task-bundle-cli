from pathlib import Path

import pytest

from task_bundle.bundle.loader import load_bundle
from task_bundle.bundle.yaml_loader import load_yaml_mapping
from task_bundle.errors import ErrorCode, TaskBundleError
from tests.bundle_helpers import BundleFactory


def test_valid_yaml_loads(tmp_path: Path, bundle_factory: BundleFactory) -> None:
    bundle = load_bundle(bundle_factory(tmp_path / "bundle"))

    assert bundle.task.task.id == "example-task"


@pytest.mark.parametrize("content", ["", " \n\t\n"])
def test_empty_yaml_is_rejected(tmp_path: Path, content: str) -> None:
    path = tmp_path / "task.yaml"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(TaskBundleError) as caught:
        load_yaml_mapping(path)

    assert caught.value.code == ErrorCode.BUNDLE_YAML_ERROR


def test_non_mapping_yaml_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "task.yaml"
    path.write_text("- not\n- a\n- mapping\n", encoding="utf-8")

    with pytest.raises(TaskBundleError) as caught:
        load_yaml_mapping(path)

    assert caught.value.code == ErrorCode.BUNDLE_YAML_ERROR


def test_malformed_yaml_reports_location(tmp_path: Path) -> None:
    path = tmp_path / "task.yaml"
    path.write_text("task: [unterminated\n", encoding="utf-8")

    with pytest.raises(TaskBundleError) as caught:
        load_yaml_mapping(path)

    assert caught.value.code == ErrorCode.BUNDLE_YAML_ERROR
    assert caught.value.context.details is not None
    assert "line" in caught.value.context.details


@pytest.mark.parametrize(
    "content",
    [
        "task: first\ntask: second\n",
        "task:\n  id: first\n  id: second\n",
    ],
)
def test_duplicate_keys_are_rejected(tmp_path: Path, content: str) -> None:
    path = tmp_path / "task.yaml"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(TaskBundleError) as caught:
        load_yaml_mapping(path)

    assert caught.value.code == ErrorCode.BUNDLE_DUPLICATE_KEY


def test_unsafe_custom_tag_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "task.yaml"
    path.write_text(
        "!!python/object/apply:os.system ['echo unsafe']\n",
        encoding="utf-8",
    )

    with pytest.raises(TaskBundleError) as caught:
        load_yaml_mapping(path)

    assert caught.value.code == ErrorCode.BUNDLE_YAML_ERROR


@pytest.mark.parametrize(
    "content",
    [
        "defaults: &defaults\n  timeout: 10\nvalue: *defaults\n",
        "defaults: &defaults\n  timeout: 10\nvalue:\n  <<: *defaults\n",
    ],
)
def test_yaml_indirection_is_rejected(tmp_path: Path, content: str) -> None:
    path = tmp_path / "task.yaml"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(TaskBundleError) as caught:
        load_yaml_mapping(path)

    assert caught.value.code == ErrorCode.BUNDLE_YAML_ERROR
    assert "anchors, aliases, and merge keys" in str(caught.value)


def test_unknown_field_becomes_domain_error(
    tmp_path: Path, bundle_factory: BundleFactory
) -> None:
    root = bundle_factory(tmp_path / "bundle")
    with (root / "task.yaml").open("a", encoding="utf-8") as handle:
        handle.write("unknown: true\n")

    with pytest.raises(TaskBundleError) as caught:
        load_bundle(root)

    assert caught.value.code == ErrorCode.BUNDLE_SCHEMA_ERROR
    assert caught.value.__cause__ is not None
