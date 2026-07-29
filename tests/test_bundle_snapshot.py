import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from task_bundle import __version__
from task_bundle.bundle.loader import load_bundle
from task_bundle.bundle.snapshot import (
    compare_snapshot,
    create_snapshot,
    load_snapshot,
    write_snapshot_atomic,
)
from task_bundle.errors import ErrorCode, TaskBundleError
from tests.bundle_helpers import BundleFactory, read_task, write_task


def test_snapshot_round_trip_is_immutable_and_host_independent(
    tmp_path: Path, bundle_factory: BundleFactory
) -> None:
    bundle = load_bundle(bundle_factory(tmp_path / "bundle"))
    snapshot = create_snapshot(bundle, __version__)
    destination = tmp_path / "state/bundle.snapshot.json"
    write_snapshot_atomic(snapshot, destination)
    restored = load_snapshot(destination)

    assert restored == snapshot
    assert restored.created_at.tzinfo == UTC
    assert str(bundle.root) not in destination.read_text(encoding="utf-8")
    assert destination.read_bytes().endswith(b"\n")
    with pytest.raises(ValidationError):
        restored.task_id = "changed"


def test_snapshot_creation_time_does_not_affect_bundle_digest(
    tmp_path: Path, bundle_factory: BundleFactory
) -> None:
    bundle = load_bundle(bundle_factory(tmp_path / "bundle"))
    first = create_snapshot(bundle, __version__, datetime(2026, 7, 29, tzinfo=UTC))
    second = create_snapshot(
        bundle,
        __version__,
        datetime(2026, 7, 30, tzinfo=UTC),
    )

    assert first.created_at != second.created_at
    assert first.bundle_input_digest == second.bundle_input_digest


@pytest.mark.parametrize("content", ["not json", "[]"])
def test_invalid_snapshot_document_is_rejected(tmp_path: Path, content: str) -> None:
    path = tmp_path / "snapshot.json"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(TaskBundleError) as caught:
        load_snapshot(path)

    assert caught.value.code in {
        ErrorCode.SNAPSHOT_READ_ERROR,
        ErrorCode.SNAPSHOT_SCHEMA_ERROR,
    }


def test_unsupported_or_unknown_snapshot_fields_are_rejected(
    tmp_path: Path, bundle_factory: BundleFactory
) -> None:
    bundle = load_bundle(bundle_factory(tmp_path / "bundle"))
    snapshot = create_snapshot(bundle, __version__)
    raw = snapshot.model_dump(mode="json")
    path = tmp_path / "snapshot.json"

    raw["schema_version"] = "2"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(TaskBundleError) as unsupported:
        load_snapshot(path)
    assert unsupported.value.code == ErrorCode.SNAPSHOT_SCHEMA_ERROR

    raw["schema_version"] = "1"
    raw["unknown"] = True
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(TaskBundleError) as unknown:
        load_snapshot(path)
    assert unknown.value.code == ErrorCode.SNAPSHOT_SCHEMA_ERROR


def test_invalid_snapshot_digest_is_rejected(
    tmp_path: Path, bundle_factory: BundleFactory
) -> None:
    bundle = load_bundle(bundle_factory(tmp_path / "bundle"))
    raw = create_snapshot(bundle, __version__).model_dump(mode="json")
    raw["bundle_input_digest"] = "invalid"
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(TaskBundleError) as caught:
        load_snapshot(path)

    assert caught.value.code == ErrorCode.SNAPSHOT_SCHEMA_ERROR


def test_absolute_manifest_path_is_rejected(
    tmp_path: Path, bundle_factory: BundleFactory
) -> None:
    bundle = load_bundle(bundle_factory(tmp_path / "bundle"))
    raw = create_snapshot(bundle, __version__).model_dump(mode="json")
    manifest = raw["input_manifest"]
    assert isinstance(manifest, list)
    first = manifest[0]
    assert isinstance(first, dict)
    first["path"] = "/absolute/input"
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(TaskBundleError) as caught:
        load_snapshot(path)

    assert caught.value.code == ErrorCode.SNAPSHOT_SCHEMA_ERROR


def test_fixed_provenance_survives_snapshot_round_trip(
    tmp_path: Path, bundle_factory: BundleFactory
) -> None:
    root = bundle_factory(tmp_path / "bundle")
    mapping = read_task(root)
    mapping["provenance"] = {
        "dataset": "dataset",
        "dataset_revision": "revision",
        "instance_id": "instance",
        "source_record_sha256": f"sha256:{'d' * 64}",
        "imported_at": "2026-07-29T05:30:00+05:30",
    }
    write_task(root, mapping)
    snapshot = create_snapshot(load_bundle(root), __version__)
    path = tmp_path / "snapshot.json"
    write_snapshot_atomic(snapshot, path)

    restored = load_snapshot(path)

    assert restored.provenance == snapshot.provenance
    assert restored.provenance is not None
    assert restored.provenance.imported_at.isoformat() == "2026-07-29T00:00:00+00:00"


def test_atomic_write_replaces_target_and_removes_temporary_file(
    tmp_path: Path, bundle_factory: BundleFactory
) -> None:
    bundle = load_bundle(bundle_factory(tmp_path / "bundle"))
    destination = tmp_path / ".task/bundle.snapshot.json"
    first = create_snapshot(bundle, "0.1.0")
    second = create_snapshot(bundle, "0.1.1")

    write_snapshot_atomic(first, destination)
    write_snapshot_atomic(second, destination)

    assert load_snapshot(destination).cli_version == "0.1.1"
    assert list(destination.parent.glob(".bundle.snapshot.*.tmp")) == []


@pytest.mark.parametrize("failure_point", ["fsync", "replace"])
def test_atomic_failure_preserves_existing_target(
    tmp_path: Path,
    bundle_factory: BundleFactory,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    bundle = load_bundle(bundle_factory(tmp_path / "bundle"))
    destination = tmp_path / ".task/bundle.snapshot.json"
    original = create_snapshot(bundle, "0.1.0")
    write_snapshot_atomic(original, destination)
    original_bytes = destination.read_bytes()

    if failure_point == "fsync":
        monkeypatch.setattr(os, "fsync", lambda descriptor: _raise_os_error())
    else:
        monkeypatch.setattr(os, "replace", lambda source, target: _raise_os_error())

    with pytest.raises(TaskBundleError) as caught:
        write_snapshot_atomic(create_snapshot(bundle, "0.1.1"), destination)

    assert caught.value.code == ErrorCode.SNAPSHOT_WRITE_ERROR
    assert caught.value.context.details == {
        "error_type": "OSError",
        "error": "simulated failure",
    }
    assert destination.read_bytes() == original_bytes
    assert list(destination.parent.glob(".bundle.snapshot.*.tmp")) == []


def test_snapshot_comparison_reports_content_add_remove_mode_and_config(
    tmp_path: Path, bundle_factory: BundleFactory
) -> None:
    root = bundle_factory(tmp_path / "bundle")
    original = load_bundle(root)
    snapshot = create_snapshot(original, __version__)

    assert compare_snapshot(snapshot, original).is_current

    (root / "public/description.md").write_text("changed\n", encoding="utf-8")
    changed = compare_snapshot(snapshot, load_bundle(root))
    assert "public/description.md" in changed.changed_inputs
    assert changed.expected_digest == snapshot.bundle_input_digest
    assert changed.actual_digest != changed.expected_digest

    (root / "environment/context/added.txt").write_text("added\n", encoding="utf-8")
    added = compare_snapshot(snapshot, load_bundle(root))
    assert "environment/context/added.txt" in added.changed_inputs

    (root / "environment/context/tool.conf").unlink()
    removed = compare_snapshot(snapshot, load_bundle(root))
    assert "environment/context/tool.conf" in removed.changed_inputs

    (root / "evaluation/run-tests.sh").chmod(0o644)
    mode = compare_snapshot(snapshot, load_bundle(root))
    assert "evaluation/run-tests.sh" in mode.changed_inputs

    mapping = read_task(root)
    environment = mapping["environment"]
    assert isinstance(environment, dict)
    environment["platform"] = "linux/arm64"
    write_task(root, mapping)
    config = compare_snapshot(snapshot, load_bundle(root))
    assert "<task-config>" in config.changed_inputs


def test_snapshot_timestamp_does_not_create_false_staleness(
    tmp_path: Path, bundle_factory: BundleFactory
) -> None:
    bundle = load_bundle(bundle_factory(tmp_path / "bundle"))
    snapshot = create_snapshot(
        bundle,
        __version__,
        datetime.now(UTC) - timedelta(days=10),
    )

    assert compare_snapshot(snapshot, bundle).is_current


def test_config_only_change_reports_synthetic_input(
    tmp_path: Path, bundle_factory: BundleFactory
) -> None:
    root = bundle_factory(tmp_path / "bundle")
    original = load_bundle(root)
    snapshot = create_snapshot(original, __version__)
    mapping = read_task(root)
    environment = mapping["environment"]
    assert isinstance(environment, dict)
    environment["platform"] = "linux/arm64"
    write_task(root, mapping)

    comparison = compare_snapshot(snapshot, load_bundle(root))

    assert comparison.changed_inputs == ("<task-config>",)


def _raise_os_error() -> None:
    raise OSError("simulated failure")
