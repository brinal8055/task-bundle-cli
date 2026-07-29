import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from task_bundle.errors import ErrorCode, TaskBundleError
from task_bundle.models import SourceRequest
from task_bundle.source.git import GitCommandResult, GitInstallation, SystemGitRunner
from task_bundle.source.service import (
    MaterializedSource,
    _materialize_source_with_runner,
    materialize_source,
)
from tests.source_helpers import (
    GitFixture,
    LocalFetchGitRunner,
    create_git_repository,
    local_fetch_runner,
)

PUBLIC_FIXTURE_URL = "https://fixture.invalid/owner/repository.git"


class VerificationRunner:
    def __init__(
        self,
        *,
        commit: str,
        object_type: str = "commit",
        resolved_commit: str | None = None,
        tree: str | None = None,
    ) -> None:
        self.installation = GitInstallation("/usr/bin/git", "test")
        self.commit = commit
        self.object_type = object_type
        self.resolved_commit = resolved_commit or commit
        self.tree = tree or "b" * 40

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        timeout_seconds: int,
        error_code: ErrorCode,
        phase: str,
        description: str,
    ) -> GitCommandResult:
        if "cat-file" in args:
            output = self.object_type
        elif "rev-parse" in args and f"{self.commit}^{{commit}}" in args:
            output = self.resolved_commit
        elif "rev-parse" in args and f"{self.commit}^{{tree}}" in args:
            output = self.tree
        else:
            output = ""
        return GitCommandResult(f"{output}\n", "", 0, False)


def _materialize(
    tmp_path: Path, fixture: GitFixture
) -> tuple[Path, MaterializedSource]:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    runner = local_fetch_runner(workspace / "home", fixture.root)
    materialized = _materialize_source_with_runner(
        PUBLIC_FIXTURE_URL,
        fixture.commit,
        30,
        workspace,
        runner,
    )
    return workspace, materialized


def test_exact_local_commit_is_verified_and_materialised(tmp_path: Path) -> None:
    fixture = create_git_repository(tmp_path / "repository")
    workspace, materialized = _materialize(tmp_path, fixture)

    assert materialized.resolved.requested_commit == fixture.commit
    assert materialized.resolved.resolved_commit == fixture.commit
    assert materialized.resolved.tree_sha == fixture.tree
    assert materialized.resolved.source_entry_count == len(materialized.manifest.entries)
    assert (materialized.root / "README.md").read_text(encoding="utf-8") == "example\n"
    assert (materialized.root / "bin/tool").stat().st_mode & 0o111
    assert (materialized.root / "tool-link").is_symlink()
    assert not (materialized.root / ".git").exists()
    assert isinstance(materialized.fetch_stdout, str)
    assert isinstance(materialized.fetch_stderr, str)
    assert str(workspace) not in materialized.resolved.model_dump_json()
    assert str(workspace) not in materialized.manifest.model_dump_json()


def test_same_commit_materialises_with_stable_identity(tmp_path: Path) -> None:
    fixture = create_git_repository(tmp_path / "repository")
    _, first = _materialize(tmp_path / "first", fixture)
    _, second = _materialize(tmp_path / "second", fixture)

    assert first.manifest == second.manifest
    assert first.resolved.source_tree_digest == second.resolved.source_tree_digest
    assert first.resolved.tree_sha == second.resolved.tree_sha


def test_missing_commit_is_rejected(tmp_path: Path) -> None:
    fixture = create_git_repository(tmp_path / "repository")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runner = local_fetch_runner(workspace / "home", fixture.root)

    with pytest.raises(TaskBundleError) as caught:
        _materialize_source_with_runner(
            PUBLIC_FIXTURE_URL,
            "f" * 40,
            30,
            workspace,
            runner,
        )

    assert caught.value.code == ErrorCode.SOURCE_FETCH_ERROR


@pytest.mark.parametrize(
    ("runner", "expected_code"),
    [
        (
            VerificationRunner(commit="a" * 40, object_type="tag"),
            ErrorCode.SOURCE_OBJECT_ERROR,
        ),
        (
            VerificationRunner(commit="a" * 40, resolved_commit="c" * 40),
            ErrorCode.SOURCE_COMMIT_MISMATCH,
        ),
        (
            VerificationRunner(commit="a" * 40, tree="not-a-tree-sha"),
            ErrorCode.SOURCE_TREE_ERROR,
        ),
    ],
)
def test_source_identity_verification_rejects_invalid_git_results(
    tmp_path: Path,
    runner: VerificationRunner,
    expected_code: ErrorCode,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(TaskBundleError) as caught:
        _materialize_source_with_runner(
            PUBLIC_FIXTURE_URL,
            runner.commit,
            30,
            workspace,
            runner,
        )

    assert caught.value.code == expected_code


def test_gitlink_is_rejected_from_tree_not_gitmodules(tmp_path: Path) -> None:
    fixture = create_git_repository(tmp_path / "repository", gitlink=True)

    with pytest.raises(TaskBundleError) as caught:
        _materialize(tmp_path, fixture)

    assert caught.value.code == ErrorCode.SOURCE_SUBMODULE_UNSUPPORTED
    assert caught.value.context.details is not None
    assert caught.value.context.details["paths"] == ["vendor/sub"]


def test_gitmodules_file_without_gitlink_is_accepted(tmp_path: Path) -> None:
    fixture = create_git_repository(
        tmp_path / "repository",
        gitmodules_only=True,
    )

    _, materialized = _materialize(tmp_path, fixture)

    assert (materialized.root / ".gitmodules").is_file()


def test_exact_fetch_does_not_create_tag_refs(tmp_path: Path) -> None:
    fixture = create_git_repository(tmp_path / "repository")
    subprocess.run(
        ["git", "tag", "fixture-tag"],
        cwd=fixture.root,
        check=True,
        capture_output=True,
        shell=False,
    )
    workspace, _ = _materialize(tmp_path, fixture)
    result = subprocess.run(
        ["git", "--git-dir", str(workspace / "objects.git"), "tag", "--list"],
        check=True,
        capture_output=True,
        text=True,
        shell=False,
    )

    assert result.stdout == ""


@pytest.mark.parametrize("raise_inside", [False, True])
def test_materialization_context_cleans_all_temporary_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    raise_inside: bool,
) -> None:
    fixture = create_git_repository(tmp_path / "repository")
    original_create = SystemGitRunner.create

    def create_runner(home: Path, timeout_seconds: int = 5) -> LocalFetchGitRunner:
        return LocalFetchGitRunner(original_create(home, timeout_seconds), fixture.root)

    monkeypatch.setattr(
        "task_bundle.source.service.SystemGitRunner.create",
        classmethod(lambda cls, home, timeout_seconds=5: create_runner(home, timeout_seconds)),
    )
    request = SourceRequest(repository_url=PUBLIC_FIXTURE_URL, commit=fixture.commit)
    workspace: Path | None = None

    if raise_inside:
        with pytest.raises(RuntimeError), materialize_source(request) as materialized:
            workspace = materialized.root.parent
            raise RuntimeError("simulated interruption")
    else:
        with materialize_source(request) as materialized:
            workspace = materialized.root.parent
            assert materialized.root.exists()

    assert workspace is not None
    assert not workspace.exists()


def test_cleanup_failure_is_structured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = create_git_repository(tmp_path / "repository")
    original_create = SystemGitRunner.create
    real_rmtree = shutil.rmtree
    workspace: Path | None = None

    def create_runner(home: Path, timeout_seconds: int = 5) -> LocalFetchGitRunner:
        return LocalFetchGitRunner(original_create(home, timeout_seconds), fixture.root)

    def fail_cleanup(path: Path) -> None:
        raise OSError("simulated cleanup failure")

    monkeypatch.setattr(
        "task_bundle.source.service.SystemGitRunner.create",
        classmethod(lambda cls, home, timeout_seconds=5: create_runner(home, timeout_seconds)),
    )
    monkeypatch.setattr("task_bundle.source.service.shutil.rmtree", fail_cleanup)
    request = SourceRequest(repository_url=PUBLIC_FIXTURE_URL, commit=fixture.commit)
    try:
        with pytest.raises(TaskBundleError) as caught, materialize_source(
            request
        ) as materialized:
            workspace = materialized.root.parent
        assert caught.value.code == ErrorCode.SOURCE_CLEANUP_ERROR
    finally:
        if workspace is not None:
            real_rmtree(workspace)
