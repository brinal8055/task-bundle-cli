import os
from pathlib import Path

import pytest

from task_bundle.bundle.loader import load_bundle
from task_bundle.errors import ErrorCode, TaskBundleError
from task_bundle.run.context import validate_solver_context
from task_bundle.run.filesystem import build_filesystem_manifest
from task_bundle.run.models import FilesystemManifest
from tests.bundle_helpers import create_bundle


def _manifest(
    root: Path,
    *,
    max_files: int = 20,
    max_bytes: int = 1024,
) -> FilesystemManifest:
    return build_filesystem_manifest(
        root,
        phase="workspace-export",
        error_code=ErrorCode.WORKSPACE_EXPORT_UNSAFE,
        allow_symlinks=True,
        max_files=max_files,
        max_total_bytes=max_bytes,
        max_file_bytes=max_bytes,
    )


def test_workspace_manifest_is_binary_safe_deterministic_and_host_independent(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    for root in (first, second):
        (root / "bin").mkdir(parents=True)
        (root / "binary.dat").write_bytes(b"\x00\xff\x80data")
        tool = root / "bin/tool"
        tool.write_text("#!/bin/sh\n", encoding="utf-8")
        tool.chmod(0o755)
        (root / "tool-link").symlink_to("bin/tool")

    first_manifest = _manifest(first)
    second_manifest = _manifest(second)

    assert first_manifest == second_manifest
    assert first_manifest.entry_count == 3
    assert first_manifest.entries[0].path == "bin/tool"


def test_workspace_manifest_sorts_nested_paths_globally(tmp_path: Path) -> None:
    root = tmp_path / "nested"
    (root / "a").mkdir(parents=True)
    (root / "a/file").write_text("nested\n")
    (root / "a.txt").write_text("sibling\n")

    manifest = _manifest(root)

    assert [entry.path for entry in manifest.entries] == ["a.txt", "a/file"]


@pytest.mark.parametrize("kind", ["unsafe-symlink", "git", "fifo", "hardlink"])
def test_workspace_manifest_rejects_unsafe_entries(
    tmp_path: Path,
    kind: str,
) -> None:
    root = tmp_path / kind
    root.mkdir()
    if kind == "unsafe-symlink":
        (root / "escape").symlink_to("../outside")
    elif kind == "git":
        (root / ".git").mkdir()
        (root / ".git/config").write_text("unsafe\n")
    elif kind == "fifo":
        os.mkfifo(root / "pipe")
    else:
        source = root / "one"
        source.write_text("same\n")
        os.link(source, root / "two")

    with pytest.raises(TaskBundleError) as caught:
        _manifest(root)

    assert caught.value.code == ErrorCode.WORKSPACE_EXPORT_UNSAFE


def test_workspace_manifest_enforces_file_and_byte_limits(tmp_path: Path) -> None:
    root = tmp_path / "limits"
    root.mkdir()
    (root / "one").write_bytes(b"1234")
    (root / "two").write_bytes(b"5678")

    with pytest.raises(TaskBundleError) as files:
        _manifest(root, max_files=1)
    with pytest.raises(TaskBundleError) as size:
        _manifest(root, max_bytes=7)

    assert files.value.code == ErrorCode.CANDIDATE_FILE_LIMIT_ERROR
    assert size.value.code == ErrorCode.CANDIDATE_FILE_LIMIT_ERROR


def test_solver_context_is_outside_bundle_without_symlinks_and_digest_is_portable(
    tmp_path: Path,
) -> None:
    bundle = load_bundle(create_bundle(tmp_path / "bundle"))
    first = tmp_path / "context-one"
    second = tmp_path / "context-two"
    for root in (first, second):
        root.mkdir()
        script = root / "solve.py"
        script.write_text("print('solve')\n")
        script.chmod(0o755)

    _, first_manifest = validate_solver_context(first, bundle=bundle)
    _, second_manifest = validate_solver_context(second, bundle=bundle)
    assert first_manifest is not None
    assert second_manifest is not None
    assert first_manifest.digest == second_manifest.digest

    with pytest.raises(TaskBundleError) as under_bundle:
        validate_solver_context(bundle.root / "public", bundle=bundle)
    assert under_bundle.value.code == ErrorCode.SOLVER_CONTEXT_ERROR

    unsafe = tmp_path / "unsafe-context"
    unsafe.mkdir()
    (unsafe / "link").symlink_to("../context-one/solve.py")
    with pytest.raises(TaskBundleError) as symlink:
        validate_solver_context(unsafe, bundle=bundle)
    assert symlink.value.code == ErrorCode.SOLVER_CONTEXT_UNSAFE
