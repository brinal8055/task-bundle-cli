import os
from pathlib import Path

import pytest

from task_bundle.errors import ErrorCode, TaskBundleError
from task_bundle.models import SourceFileEntry, SourceSymlinkEntry
from task_bundle.source.manifest import build_source_manifest, source_manifest_digest


def _source_tree(root: Path) -> Path:
    root.mkdir()
    (root / "src").mkdir()
    (root / "src/data.txt").write_text("data\n", encoding="utf-8")
    script = root / "tool"
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    script.chmod(0o755)
    (root / "tool-link").symlink_to("tool")
    return root


def test_source_manifest_is_sorted_and_host_independent(tmp_path: Path) -> None:
    root = _source_tree(tmp_path / "source")
    first = build_source_manifest(root)
    first_digest = source_manifest_digest(first)
    os.utime(root / "src/data.txt", (1, 1))
    (root / "src/data.txt").chmod(0o640)
    second = build_source_manifest(root)

    assert [entry.path for entry in first.entries] == sorted(
        entry.path for entry in first.entries
    )
    assert first == second
    assert first_digest == source_manifest_digest(second)
    assert str(root) not in first.model_dump_json()


def test_same_tree_in_different_roots_has_same_digest(tmp_path: Path) -> None:
    first = build_source_manifest(_source_tree(tmp_path / "first"))
    second = build_source_manifest(_source_tree(tmp_path / "second"))

    assert first == second
    assert source_manifest_digest(first) == source_manifest_digest(second)


def test_content_mode_and_symlink_target_change_digest(tmp_path: Path) -> None:
    root = _source_tree(tmp_path / "source")
    original = source_manifest_digest(build_source_manifest(root))

    (root / "src/data.txt").write_text("changed\n", encoding="utf-8")
    content_digest = source_manifest_digest(build_source_manifest(root))
    assert content_digest != original

    (root / "src/data.txt").write_text("data\n", encoding="utf-8")
    (root / "tool").chmod(0o644)
    mode_digest = source_manifest_digest(build_source_manifest(root))
    assert mode_digest != original

    (root / "tool").chmod(0o755)
    (root / "tool-link").unlink()
    (root / "tool-link").symlink_to("src/data.txt")
    target_digest = source_manifest_digest(build_source_manifest(root))
    assert target_digest != original


def test_manifest_records_file_and_symlink_types(tmp_path: Path) -> None:
    manifest = build_source_manifest(_source_tree(tmp_path / "source"))

    assert any(isinstance(entry, SourceFileEntry) for entry in manifest.entries)
    assert any(isinstance(entry, SourceSymlinkEntry) for entry in manifest.entries)


def test_git_directory_and_special_file_are_rejected(tmp_path: Path) -> None:
    root = _source_tree(tmp_path / "source")
    (root / ".git").mkdir()

    with pytest.raises(TaskBundleError) as git_error:
        build_source_manifest(root)
    assert git_error.value.code == ErrorCode.SOURCE_MANIFEST_ERROR

    (root / ".git").rmdir()
    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFOs are unavailable on this platform")
    os.mkfifo(root / "input.fifo")
    with pytest.raises(TaskBundleError) as special_error:
        build_source_manifest(root)
    assert special_error.value.code == ErrorCode.SOURCE_MANIFEST_ERROR


def test_unsafe_source_symlink_is_structured_error(tmp_path: Path) -> None:
    root = _source_tree(tmp_path / "source")
    (root / "tool-link").unlink()
    (root / "tool-link").symlink_to("../../outside")

    with pytest.raises(TaskBundleError) as caught:
        build_source_manifest(root)

    assert caught.value.code == ErrorCode.SOURCE_SYMLINK_UNSAFE
