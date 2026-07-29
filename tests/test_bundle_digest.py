import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from task_bundle.bundle.loader import load_bundle
from task_bundle.errors import ErrorCode, TaskBundleError
from tests.bundle_helpers import BundleFactory, read_task, write_task


def _update_task(root: Path, path: tuple[str, ...], value: Any) -> None:
    mapping = read_task(root)
    current: dict[str, Any] = mapping
    for part in path[:-1]:
        nested = current[part]
        assert isinstance(nested, dict)
        current = nested
    current[path[-1]] = value
    write_task(root, mapping)


def test_digest_is_stable_across_roots_and_yaml_formatting(
    tmp_path: Path, bundle_factory: BundleFactory
) -> None:
    first = bundle_factory(tmp_path / "first")
    second = tmp_path / "second"
    shutil.copytree(first, second)
    mapping = read_task(second)
    (second / "task.yaml").write_text(
        "# comment\n" + __import__("yaml").safe_dump(mapping, sort_keys=True, indent=4),
        encoding="utf-8",
    )

    first_loaded = load_bundle(first)
    second_loaded = load_bundle(second)

    assert first_loaded.canonical_config == second_loaded.canonical_config
    assert first_loaded.bundle_input_digest == second_loaded.bundle_input_digest
    assert str(first.resolve()).encode() not in first_loaded.canonical_config
    assert b'"provenance":null' in first_loaded.canonical_config


@pytest.mark.parametrize(
    "relative",
    [
        "public/description.md",
        "public/requirements.md",
        "public/interface.md",
        "environment/Dockerfile",
        "environment/context/tool.conf",
        "evaluation/hidden/test.patch",
        "evaluation/hidden/golden.patch",
        "evaluation/run-tests.sh",
        "evaluation/parse-results.py",
        "evaluation/prepare.sh",
    ],
)
def test_referenced_file_change_changes_digest(
    tmp_path: Path,
    bundle_factory: BundleFactory,
    relative: str,
) -> None:
    root = bundle_factory(tmp_path / "bundle")
    before = load_bundle(root).bundle_input_digest
    with (root / relative).open("a", encoding="utf-8") as handle:
        handle.write("changed\n")

    assert load_bundle(root).bundle_input_digest != before


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("environment", "platform"), "linux/arm64"),
        (("environment", "build"), {"build_args": {"VERSION": "2"}}),
        (
            ("evaluation", "pass_to_pass"),
            [{"selector": "tests/test_api.py::test_other"}],
        ),
        (
            ("evaluation", "fail_to_pass"),
            [
                {
                    "selector": "tests/test_api.py::test_create",
                    "baseline_statuses": ["error"],
                }
            ],
        ),
    ],
)
def test_configuration_change_changes_digest(
    tmp_path: Path,
    bundle_factory: BundleFactory,
    path: tuple[str, ...],
    value: Any,
) -> None:
    root = bundle_factory(tmp_path / "bundle")
    before = load_bundle(root).bundle_input_digest
    _update_task(root, path, value)

    assert load_bundle(root).bundle_input_digest != before


def test_base_image_reference_changes_digest(
    tmp_path: Path, bundle_factory: BundleFactory
) -> None:
    root = bundle_factory(tmp_path / "bundle")
    environment = {
        "type": "base_image",
        "image": f"example/image@sha256:{'a' * 64}",
    }
    _update_task(root, ("environment",), environment)
    before = load_bundle(root).bundle_input_digest
    environment["image"] = f"example/image@sha256:{'b' * 64}"
    _update_task(root, ("environment",), environment)

    assert load_bundle(root).bundle_input_digest != before


def test_provenance_changes_digest(tmp_path: Path, bundle_factory: BundleFactory) -> None:
    root = bundle_factory(tmp_path / "bundle")
    before = load_bundle(root).bundle_input_digest
    provenance = {
        "dataset": "dataset",
        "dataset_revision": "revision",
        "instance_id": "instance",
        "source_record_sha256": f"sha256:{'c' * 64}",
        "imported_at": "2026-07-29T05:30:00+05:30",
    }
    _update_task(root, ("provenance",), provenance)
    after = load_bundle(root)

    assert after.bundle_input_digest != before
    assert after.task.provenance is not None
    assert after.task.provenance.imported_at.isoformat() == "2026-07-29T00:00:00+00:00"


def test_executable_mode_changes_digest(
    tmp_path: Path, bundle_factory: BundleFactory
) -> None:
    root = bundle_factory(tmp_path / "bundle")
    runner = root / "evaluation/run-tests.sh"
    before = load_bundle(root).bundle_input_digest
    runner.chmod(0o644)

    assert load_bundle(root).bundle_input_digest != before


def test_irrelevant_permission_bits_do_not_change_digest(
    tmp_path: Path, bundle_factory: BundleFactory
) -> None:
    root = bundle_factory(tmp_path / "bundle")
    description = root / "public/description.md"
    before = load_bundle(root).bundle_input_digest
    description.chmod(0o640)

    assert load_bundle(root).bundle_input_digest == before


@pytest.mark.parametrize(
    "relative",
    [
        ".task/state.txt",
        "artifacts/report.json",
        "environment/context/__pycache__/cache.pyc",
        "environment/context/state.db",
        "environment/context/generated-build-context/generated.txt",
    ],
)
def test_generated_inputs_do_not_change_digest(
    tmp_path: Path,
    bundle_factory: BundleFactory,
    relative: str,
) -> None:
    root = bundle_factory(tmp_path / "bundle")
    before = load_bundle(root).bundle_input_digest
    generated = root / relative
    generated.parent.mkdir(parents=True, exist_ok=True)
    generated.write_text("generated", encoding="utf-8")

    assert load_bundle(root).bundle_input_digest == before


def test_database_fixture_inside_evaluation_is_digest_covered(
    tmp_path: Path, bundle_factory: BundleFactory
) -> None:
    root = bundle_factory(tmp_path / "bundle")
    fixture = root / "evaluation/fixtures/baseline.db"
    fixture.parent.mkdir()
    fixture.write_text("first", encoding="utf-8")
    first = load_bundle(root)
    fixture.write_text("second", encoding="utf-8")
    second = load_bundle(root)

    assert "evaluation/fixtures/baseline.db" in {
        entry.path for entry in first.input_manifest
    }
    assert first.bundle_input_digest != second.bundle_input_digest


def test_hidden_inputs_are_digest_covered_but_excluded_from_harness_identity(
    tmp_path: Path,
    bundle_factory: BundleFactory,
) -> None:
    root = bundle_factory(tmp_path / "bundle")
    first = load_bundle(root)
    hidden = root / "evaluation/hidden/extra-secret.txt"
    hidden.write_text("first", encoding="utf-8")
    second = load_bundle(root)
    hidden.write_text("second", encoding="utf-8")
    third = load_bundle(root)

    assert first.bundle_input_digest != second.bundle_input_digest
    assert second.bundle_input_digest != third.bundle_input_digest
    assert (
        first.evaluation_inputs.harness_sha256
        == second.evaluation_inputs.harness_sha256
        == third.evaluation_inputs.harness_sha256
    )


def test_manifest_is_sorted_and_digest_is_prefixed_lowercase(
    tmp_path: Path, bundle_factory: BundleFactory
) -> None:
    loaded = load_bundle(bundle_factory(tmp_path / "bundle"))
    paths = [entry.path for entry in loaded.input_manifest]

    assert paths == sorted(paths)
    assert loaded.bundle_input_digest.startswith("sha256:")
    assert loaded.bundle_input_digest == loaded.bundle_input_digest.lower()
    assert len(loaded.bundle_input_digest) == 71


def test_directory_creation_order_does_not_affect_digest(
    tmp_path: Path, bundle_factory: BundleFactory
) -> None:
    first = bundle_factory(tmp_path / "first")
    second = bundle_factory(tmp_path / "second")
    context = second / "environment/context"
    original = context / "tool.conf"
    content = original.read_text(encoding="utf-8")
    original.unlink()
    (context / "z-last.txt").write_text("z", encoding="utf-8")
    original.write_text(content, encoding="utf-8")
    (context / "z-last.txt").unlink()

    assert load_bundle(first).bundle_input_digest == load_bundle(second).bundle_input_digest


def test_special_file_in_digest_tree_is_rejected(
    tmp_path: Path, bundle_factory: BundleFactory
) -> None:
    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFOs are not supported on this platform")
    root = bundle_factory(tmp_path / "bundle")
    os.mkfifo(root / "environment/context/input.fifo")

    with pytest.raises(TaskBundleError) as caught:
        load_bundle(root)

    assert caught.value.code == ErrorCode.BUNDLE_SPECIAL_FILE_ERROR
