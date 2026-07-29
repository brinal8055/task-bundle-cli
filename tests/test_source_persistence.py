import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from task_bundle.errors import ErrorCode, TaskBundleError
from task_bundle.models import ResolvedSource, SourceFileEntry, SourceManifest
from task_bundle.source.persistence import (
    SOURCE_MANIFEST_RELATIVE_PATH,
    SOURCE_SNAPSHOT_RELATIVE_PATH,
    load_source_manifest,
    load_source_snapshot,
    write_source_metadata,
)


def _metadata() -> tuple[ResolvedSource, SourceManifest]:
    entry = SourceFileEntry(
        path="README.md",
        mode="0644",
        size=8,
        sha256=f"sha256:{'a' * 64}",
    )
    manifest = SourceManifest(entries=(entry,))
    resolved = ResolvedSource(
        repository_url="https://example.com/owner/repo.git",
        requested_commit="b" * 40,
        resolved_commit="b" * 40,
        tree_sha="c" * 40,
        source_tree_digest=f"sha256:{'d' * 64}",
        source_entry_count=1,
        source_total_bytes=8,
        symlink_count=0,
        executable_file_count=0,
        git_executable="/usr/bin/git",
        git_version="2.50.0",
        created_at=datetime(2026, 7, 29, tzinfo=UTC),
    )
    return resolved, manifest


def test_source_metadata_atomic_round_trip(tmp_path: Path) -> None:
    resolved, manifest = _metadata()

    write_source_metadata(tmp_path, resolved, manifest)

    snapshot_path = tmp_path / SOURCE_SNAPSHOT_RELATIVE_PATH
    manifest_path = tmp_path / SOURCE_MANIFEST_RELATIVE_PATH
    assert load_source_snapshot(snapshot_path) == resolved
    assert load_source_manifest(manifest_path) == manifest
    assert snapshot_path.read_bytes().endswith(b"\n")
    assert manifest_path.read_bytes().endswith(b"\n")
    assert list(snapshot_path.parent.glob(".bundle.snapshot.*.tmp")) == []


def test_unsupported_source_metadata_schema_is_rejected(tmp_path: Path) -> None:
    resolved, _ = _metadata()
    raw = resolved.model_dump(mode="json")
    raw["schema_version"] = "2"
    path = tmp_path / "source.snapshot.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(TaskBundleError) as caught:
        load_source_snapshot(path)

    assert caught.value.code == ErrorCode.SOURCE_PERSISTENCE_ERROR


def test_unsupported_source_manifest_schema_is_rejected(tmp_path: Path) -> None:
    _, manifest = _metadata()
    raw = manifest.model_dump(mode="json")
    raw["schema_version"] = "2"
    path = tmp_path / "source.manifest.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(TaskBundleError) as caught:
        load_source_manifest(path)

    assert caught.value.code == ErrorCode.SOURCE_PERSISTENCE_ERROR


def test_failed_source_replacement_preserves_existing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolved, manifest = _metadata()
    write_source_metadata(tmp_path, resolved, manifest)
    snapshot_path = tmp_path / SOURCE_SNAPSHOT_RELATIVE_PATH
    original = snapshot_path.read_bytes()

    def fail_replace(source: Path, target: Path) -> None:
        raise OSError("simulated replacement failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(TaskBundleError):
        write_source_metadata(tmp_path, resolved, manifest)

    assert snapshot_path.read_bytes() == original
    assert list(snapshot_path.parent.glob(".bundle.snapshot.*.tmp")) == []


def test_failed_manifest_replacement_preserves_existing_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolved, manifest = _metadata()
    write_source_metadata(tmp_path, resolved, manifest)
    manifest_path = tmp_path / SOURCE_MANIFEST_RELATIVE_PATH
    original = manifest_path.read_bytes()
    real_replace = os.replace

    def fail_manifest_replace(source: Path, target: Path) -> None:
        if Path(target) == manifest_path:
            raise OSError("simulated manifest replacement failure")
        real_replace(source, target)

    monkeypatch.setattr(os, "replace", fail_manifest_replace)
    with pytest.raises(TaskBundleError):
        write_source_metadata(tmp_path, resolved, manifest)

    assert manifest_path.read_bytes() == original
    assert list(manifest_path.parent.glob(".bundle.snapshot.*.tmp")) == []
