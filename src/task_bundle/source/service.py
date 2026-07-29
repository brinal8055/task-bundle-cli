import re
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn

from task_bundle.bundle.loader import LoadedBundle
from task_bundle.errors import ErrorCode, ErrorContext, TaskBundleError
from task_bundle.models import (
    ResolvedSource,
    SourceFileEntry,
    SourceManifest,
    SourceRequest,
    SourceSymlinkEntry,
)
from task_bundle.source.archive import extract_source_archive
from task_bundle.source.git import GitRunner, SystemGitRunner
from task_bundle.source.manifest import build_source_manifest, source_manifest_digest
from task_bundle.source.validation import (
    normalize_commit_sha,
    validate_commit_sha,
    validate_repository_url,
)

_TREE_ENTRY = re.compile(r"^([0-9]{6}) ([a-z]+) ([0-9a-f]{40})\t(.*)$")


@dataclass(frozen=True, slots=True)
class MaterializedSource:
    root: Path
    resolved: ResolvedSource
    manifest: SourceManifest
    fetch_stdout: str
    fetch_stderr: str


@contextmanager
def materialize_source(request: SourceRequest) -> Iterator[MaterializedSource]:
    repository_url = validate_repository_url(request.repository_url)
    commit = validate_commit_sha(request.commit)
    workspace = Path(tempfile.mkdtemp(prefix="task-bundle-source-"))
    try:
        runner = SystemGitRunner.create(workspace / "home")
        yield _materialize_source_with_runner(
            repository_url,
            commit,
            request.timeout_seconds,
            workspace,
            runner,
        )
    finally:
        _cleanup_workspace(workspace)


@contextmanager
def materialize_bundle_source(bundle: LoadedBundle) -> Iterator[MaterializedSource]:
    repository_url = validate_repository_url(bundle.task.repository.url)
    commit = validate_commit_sha(bundle.task.repository.commit)
    request = SourceRequest(repository_url=repository_url, commit=commit)
    with materialize_source(request) as materialized:
        yield materialized


def _materialize_source_with_runner(
    repository_url: str,
    commit: str,
    timeout_seconds: int,
    workspace: Path,
    runner: GitRunner,
) -> MaterializedSource:
    object_repository = workspace / "objects.git"
    archive_path = workspace / "source.tar"
    source_root = workspace / "source"
    runner.run(
        ("init", "--bare", str(object_repository)),
        cwd=workspace,
        timeout_seconds=timeout_seconds,
        error_code=ErrorCode.SOURCE_FETCH_ERROR,
        phase="source-fetch",
        description="initialise the temporary object repository",
    )
    fetch_result = runner.run(
        (
            "-C",
            str(object_repository),
            "fetch",
            "--no-tags",
            "--depth=1",
            repository_url,
            commit,
        ),
        cwd=workspace,
        timeout_seconds=timeout_seconds,
        error_code=ErrorCode.SOURCE_FETCH_ERROR,
        phase="source-fetch",
        description=f"fetch exact commit from {repository_url}",
    )
    object_type = runner.run(
        ("-C", str(object_repository), "cat-file", "-t", commit),
        cwd=workspace,
        timeout_seconds=timeout_seconds,
        error_code=ErrorCode.SOURCE_OBJECT_ERROR,
        phase="source-verify",
        description="verify the requested object type",
    ).stdout.strip()
    if object_type != "commit":
        _source_error(
            ErrorCode.SOURCE_OBJECT_ERROR,
            "Requested Git object is not a commit.",
            "A commit object",
            object_type or "missing object type",
            "Pin the task to a commit object rather than a tag or tree.",
        )
    resolved_commit = runner.run(
        ("-C", str(object_repository), "rev-parse", "--verify", f"{commit}^{{commit}}"),
        cwd=workspace,
        timeout_seconds=timeout_seconds,
        error_code=ErrorCode.SOURCE_OBJECT_ERROR,
        phase="source-verify",
        description="resolve the exact commit",
    ).stdout.strip().lower()
    if resolved_commit != commit:
        _source_error(
            ErrorCode.SOURCE_COMMIT_MISMATCH,
            "Resolved commit does not match the requested commit.",
            commit,
            resolved_commit,
            "Verify the repository and exact commit identity.",
        )
    tree_output = runner.run(
        ("-C", str(object_repository), "rev-parse", f"{commit}^{{tree}}"),
        cwd=workspace,
        timeout_seconds=timeout_seconds,
        error_code=ErrorCode.SOURCE_TREE_ERROR,
        phase="source-verify",
        description="resolve the commit tree",
    ).stdout.strip()
    try:
        tree_sha = normalize_commit_sha(tree_output)
    except ValueError as error:
        raise TaskBundleError(
            ErrorCode.SOURCE_TREE_ERROR,
            "Git returned an invalid tree object ID.",
            ErrorContext(
                phase="source-verify",
                expected="A full 40-character hexadecimal tree SHA",
                actual=tree_output,
                corrective_action="Verify repository object integrity.",
            ),
        ) from error
    tree_listing = runner.run(
        ("-C", str(object_repository), "ls-tree", "-r", "-z", commit),
        cwd=workspace,
        timeout_seconds=timeout_seconds,
        error_code=ErrorCode.SOURCE_TREE_ERROR,
        phase="source-verify",
        description="inspect the source tree",
    ).stdout
    gitlinks = _gitlink_paths(tree_listing)
    if gitlinks:
        raise TaskBundleError(
            ErrorCode.SOURCE_SUBMODULE_UNSUPPORTED,
            "Repository contains unsupported Git submodules.",
            ErrorContext(
                phase="source-verify",
                expected="A source tree without mode 160000 gitlinks",
                actual=f"Gitlinks: {', '.join(gitlinks)}",
                corrective_action="Use a commit without submodules.",
                details={"paths": gitlinks},
            ),
        )
    runner.run(
        (
            "-C",
            str(object_repository),
            "archive",
            "--format=tar",
            f"--output={archive_path}",
            commit,
        ),
        cwd=workspace,
        timeout_seconds=timeout_seconds,
        error_code=ErrorCode.SOURCE_ARCHIVE_ERROR,
        phase="source-archive",
        description="archive the verified commit",
    )
    extract_source_archive(archive_path, source_root)
    manifest = build_source_manifest(source_root)
    digest = source_manifest_digest(manifest)
    resolved = _resolved_source(
        repository_url,
        commit,
        resolved_commit,
        tree_sha,
        digest,
        manifest,
        runner.installation.executable,
        runner.installation.version,
    )
    return MaterializedSource(
        root=source_root,
        resolved=resolved,
        manifest=manifest,
        fetch_stdout=fetch_result.stdout,
        fetch_stderr=fetch_result.stderr,
    )


def _gitlink_paths(listing: str) -> list[str]:
    gitlinks: list[str] = []
    for record in listing.split("\0"):
        if not record:
            continue
        match = _TREE_ENTRY.fullmatch(record)
        if match is None:
            _source_error(
                ErrorCode.SOURCE_TREE_ERROR,
                "Git tree listing is malformed.",
                "A valid `git ls-tree -r -z` record",
                record[:200],
                "Verify repository object integrity and Git compatibility.",
            )
        if match.group(1) == "160000":
            gitlinks.append(match.group(4))
    return sorted(gitlinks)


def _resolved_source(
    repository_url: str,
    requested_commit: str,
    resolved_commit: str,
    tree_sha: str,
    digest: str,
    manifest: SourceManifest,
    git_executable: str,
    git_version: str,
) -> ResolvedSource:
    files = [entry for entry in manifest.entries if isinstance(entry, SourceFileEntry)]
    symlinks = [
        entry for entry in manifest.entries if isinstance(entry, SourceSymlinkEntry)
    ]
    return ResolvedSource(
        repository_url=repository_url,
        requested_commit=requested_commit,
        resolved_commit=resolved_commit,
        tree_sha=tree_sha,
        source_tree_digest=digest,
        source_entry_count=len(manifest.entries),
        source_total_bytes=sum(entry.size for entry in files),
        symlink_count=len(symlinks),
        executable_file_count=sum(entry.mode == "0755" for entry in files),
        git_executable=git_executable,
        git_version=git_version,
        created_at=datetime.now(UTC),
    )


def _cleanup_workspace(workspace: Path) -> None:
    try:
        shutil.rmtree(workspace)
    except OSError as error:
        raise TaskBundleError(
            ErrorCode.SOURCE_CLEANUP_ERROR,
            "Temporary source workspace could not be removed.",
            ErrorContext(
                phase="source-cleanup",
                expected="Complete removal of temporary Git and source files",
                actual=str(error),
                corrective_action="Remove the temporary workspace manually.",
                details={"error_type": type(error).__name__, "error": str(error)},
            ),
        ) from error


def _source_error(
    code: ErrorCode,
    message: str,
    expected: str,
    actual: str,
    hint: str,
) -> NoReturn:
    raise TaskBundleError(
        code,
        message,
        ErrorContext(
            phase="source-verify",
            expected=expected,
            actual=actual,
            corrective_action=hint,
        ),
    )
