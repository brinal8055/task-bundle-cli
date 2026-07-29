import shutil
import subprocess
from pathlib import Path

import pytest

from task_bundle.errors import ErrorCode, TaskBundleError
from task_bundle.models import SolverConfig
from task_bundle.run.candidate import CandidateBuilder, enforce_patch_policy
from task_bundle.run.filesystem import build_filesystem_manifest
from task_bundle.run.models import CandidateTree, FilesystemManifest


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        shell=False,
        text=True,
    ).stdout.strip()


def _manifest(root: Path) -> FilesystemManifest:
    return build_filesystem_manifest(
        root,
        phase="test",
        error_code=ErrorCode.WORKSPACE_EXPORT_UNSAFE,
        allow_symlinks=True,
        max_files=100,
        max_total_bytes=1024 * 1024,
        max_file_bytes=1024 * 1024,
    )


def _trees(tmp_path: Path) -> tuple[Path, Path, str]:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "keep.txt").write_text("keep\n")
    (repository / "delete.txt").write_text("delete\n")
    (repository / "binary.dat").write_bytes(b"\x00\xffold")
    tool = repository / "tool"
    tool.write_text("#!/bin/sh\nexit 0\n")
    _git(repository, "init", "-q")
    _git(repository, "config", "user.name", "Tests")
    _git(repository, "config", "user.email", "tests@example.invalid")
    _git(repository, "add", "-A")
    _git(repository, "commit", "-q", "-m", "baseline")
    tree = _git(repository, "rev-parse", "HEAD^{tree}")
    baseline = tmp_path / "baseline"
    candidate = tmp_path / "candidate"
    shutil.copytree(repository, baseline, ignore=shutil.ignore_patterns(".git"))
    shutil.copytree(baseline, candidate, symlinks=True)
    return baseline, candidate, tree


def test_candidate_builder_uses_raw_trees_binary_diff_and_round_trip(
    tmp_path: Path,
) -> None:
    baseline, candidate, tree = _trees(tmp_path)
    (candidate / "keep.txt").write_text("changed\n")
    (candidate / "delete.txt").unlink()
    (candidate / "added.txt").write_text("added\n")
    (candidate / "binary.dat").write_bytes(b"\x00\xfenew\x80")
    (candidate / "tool").chmod(0o755)
    (candidate / "tool-link").symlink_to("tool")
    trusted = tmp_path / "trusted"
    trusted.mkdir()

    result, patch = CandidateBuilder(trusted).build(
        baseline_root=baseline,
        baseline_manifest=_manifest(baseline),
        candidate_root=candidate,
        candidate_manifest=_manifest(candidate),
        expected_baseline_tree=tree,
        solver=SolverConfig(),
    )

    assert result.baseline_tree_sha == tree
    assert set(result.changed_paths) == {
        "added.txt",
        "binary.dat",
        "delete.txt",
        "keep.txt",
        "tool",
        "tool-link",
    }
    assert b"GIT binary patch" in patch
    assert b"similarity index" not in patch
    assert b"old mode 100644" in patch
    assert b"new mode 100755" in patch
    assert b"index " in patch


def test_candidate_builder_accepts_empty_noop_patch(tmp_path: Path) -> None:
    baseline, candidate, tree = _trees(tmp_path)
    trusted = tmp_path / "trusted"
    trusted.mkdir()

    result, patch = CandidateBuilder(trusted).build(
        baseline_root=baseline,
        baseline_manifest=_manifest(baseline),
        candidate_root=candidate,
        candidate_manifest=_manifest(candidate),
        expected_baseline_tree=tree,
        solver=SolverConfig(),
    )

    assert patch == b""
    assert result.changed_paths == ()
    assert result.candidate_tree_sha == tree


def test_candidate_tree_bypasses_worktree_attribute_filters(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / ".gitattributes").write_text("*.txt text eol=lf\n")
    (repository / "value.txt").write_bytes(b"baseline\n")
    _git(repository, "init", "-q")
    _git(repository, "config", "user.name", "Tests")
    _git(repository, "config", "user.email", "tests@example.invalid")
    _git(repository, "add", "-A")
    _git(repository, "commit", "-q", "-m", "baseline")
    tree = _git(repository, "rev-parse", "HEAD^{tree}")
    baseline = tmp_path / "baseline"
    candidate = tmp_path / "candidate"
    shutil.copytree(repository, baseline, ignore=shutil.ignore_patterns(".git"))
    shutil.copytree(baseline, candidate)
    (candidate / "value.txt").write_bytes(b"candidate\r\n")
    trusted = tmp_path / "trusted"
    trusted.mkdir()

    result, _ = CandidateBuilder(trusted).build(
        baseline_root=baseline,
        baseline_manifest=_manifest(baseline),
        candidate_root=candidate,
        candidate_manifest=_manifest(candidate),
        expected_baseline_tree=tree,
        solver=SolverConfig(),
    )
    raw = subprocess.run(
        [
            "git",
            f"--git-dir={trusted / 'objects.git'}",
            "cat-file",
            "blob",
            f"{result.candidate_tree_sha}:value.txt",
        ],
        check=True,
        capture_output=True,
        shell=False,
    ).stdout

    assert raw == b"candidate\r\n"


def test_patch_policy_rejects_hidden_overlap_without_exposing_hidden_content(
    tmp_path: Path,
) -> None:
    patch = (
        b"diff --git a/hidden_test.py b/hidden_test.py\n"
        b"--- a/hidden_test.py\n"
        b"+++ b/hidden_test.py\n"
        b"@@ -1 +1 @@\n-old\n+new\n"
    )
    hidden = (
        b"diff --git a/hidden_test.py b/hidden_test.py\n"
        b"--- /dev/null\n"
        b"+++ b/hidden_test.py\n"
        b"@@ -0,0 +1 @@\n+secret assertion\n"
    )
    root = tmp_path / "candidate"
    root.mkdir()
    (root / "hidden_test.py").write_text("new\n")
    manifest = _manifest(root)
    candidate = CandidateTree(
        baseline_tree_sha="a" * 40,
        candidate_tree_sha="b" * 40,
        candidate_patch_sha256="sha256:" + "c" * 64,
        candidate_patch_size=len(patch),
        changed_paths=("hidden_test.py",),
    )

    with pytest.raises(TaskBundleError) as caught:
        enforce_patch_policy(
            candidate=candidate,
            patch=patch,
            candidate_manifest=manifest,
            hidden_patch=hidden,
            solver=SolverConfig(),
        )

    assert caught.value.code == ErrorCode.PATCH_CONFLICT
    assert caught.value.context.details == {"conflicting_paths": ["hidden_test.py"]}
    assert "secret assertion" not in caught.value.context.actual


def test_candidate_round_trip_rejects_a_patch_that_does_not_rebuild_export(
    tmp_path: Path,
) -> None:
    baseline, candidate, _tree = _trees(tmp_path)
    (candidate / "keep.txt").write_text("changed\n")
    trusted = tmp_path / "trusted"
    trusted.mkdir()

    with pytest.raises(TaskBundleError) as caught:
        CandidateBuilder(trusted)._round_trip(
            baseline_root=baseline,
            baseline_manifest=_manifest(baseline),
            candidate_manifest=_manifest(candidate),
            patch=b"",
            solver=SolverConfig(),
        )

    assert caught.value.code == ErrorCode.CANDIDATE_PATCH_ROUNDTRIP_ERROR


def test_patch_policy_enforces_size_and_changed_file_limits(tmp_path: Path) -> None:
    patch = (
        b"diff --git a/one.txt b/one.txt\n"
        b"--- a/one.txt\n"
        b"+++ b/one.txt\n"
        b"@@ -1 +1 @@\n-old\n+new\n"
    )
    hidden = (
        b"diff --git a/hidden.txt b/hidden.txt\n"
        b"--- /dev/null\n"
        b"+++ b/hidden.txt\n"
        b"@@ -0,0 +1 @@\n+hidden\n"
    )
    root = tmp_path / "candidate"
    root.mkdir()
    (root / "one.txt").write_text("new\n")
    (root / "two.txt").write_text("new\n")
    manifest = _manifest(root)
    candidate = CandidateTree(
        baseline_tree_sha="a" * 40,
        candidate_tree_sha="b" * 40,
        candidate_patch_sha256="sha256:" + "c" * 64,
        candidate_patch_size=len(patch),
        changed_paths=("one.txt", "two.txt"),
    )

    with pytest.raises(TaskBundleError) as size:
        enforce_patch_policy(
            candidate=candidate,
            patch=patch,
            candidate_manifest=manifest,
            hidden_patch=hidden,
            solver=SolverConfig(max_patch_bytes=len(patch) - 1),
        )
    with pytest.raises(TaskBundleError) as files:
        enforce_patch_policy(
            candidate=candidate,
            patch=patch,
            candidate_manifest=manifest,
            hidden_patch=hidden,
            solver=SolverConfig(max_changed_files=1),
        )

    assert size.value.code == ErrorCode.CANDIDATE_PATCH_TOO_LARGE
    assert files.value.code == ErrorCode.PATCH_POLICY_ERROR
