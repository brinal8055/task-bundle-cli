import os
import posixpath
import stat
import tarfile
from pathlib import Path, PurePosixPath
from typing import NoReturn

from task_bundle.errors import ErrorCode, ErrorContext, TaskBundleError
from task_bundle.source.validation import validate_symlink_target


def extract_source_archive(archive_path: Path, destination: Path) -> None:
    try:
        with tarfile.open(archive_path, mode="r:") as archive:
            members = archive.getmembers()
            _validate_members(members)
            destination.mkdir(parents=True, exist_ok=False)
            for member in members:
                if member.isdir():
                    (destination / member.name).mkdir(parents=True, exist_ok=True)
            for member in members:
                if member.isfile():
                    _extract_file(archive, member, destination)
            for member in members:
                if member.issym():
                    target = destination / member.name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.symlink_to(member.linkname)
    except TaskBundleError:
        raise
    except (OSError, tarfile.TarError) as error:
        raise TaskBundleError(
            ErrorCode.SOURCE_ARCHIVE_ERROR,
            "Git source archive could not be materialised.",
            ErrorContext(
                phase="source-archive",
                expected="A readable validated Git tar archive",
                actual=str(error),
                corrective_action="Verify the fetched commit can be archived.",
                details={"error_type": type(error).__name__, "error": str(error)},
            ),
        ) from error


def _validate_members(members: list[tarfile.TarInfo]) -> None:
    paths: set[str] = set()
    symlinks: set[str] = set()
    for member in members:
        path = _validate_member_path(member.name)
        if path in paths:
            _unsafe_archive(path, "Archive contains duplicate member paths.")
        paths.add(path)
        if ".git" in PurePosixPath(path).parts:
            _unsafe_archive(path, ".git content is not allowed in materialised source.")
        if member.issym():
            try:
                validate_symlink_target(path, member.linkname)
            except ValueError as error:
                raise TaskBundleError(
                    ErrorCode.SOURCE_SYMLINK_UNSAFE,
                    "Repository symlink escapes the source root.",
                    ErrorContext(
                        phase="source-archive",
                        expected="A relative symlink target within the source root",
                        actual=str(error),
                        corrective_action="Replace the symlink with a safe internal target.",
                        path=Path(path),
                        details={"target": member.linkname},
                    ),
                ) from error
            symlinks.add(path)
        elif not (member.isdir() or member.isfile()):
            kind = "hard link" if member.islnk() else "special filesystem object"
            _unsafe_archive(path, f"Archive contains an unsupported {kind}.")
    for path in paths:
        parent = PurePosixPath(path).parent
        while parent != PurePosixPath("."):
            if parent.as_posix() in symlinks:
                _unsafe_archive(path, "Archive member is nested beneath a symlink.")
            parent = parent.parent


def _validate_member_path(value: str) -> str:
    path = PurePosixPath(value)
    normalized = posixpath.normpath(value)
    if (
        not value
        or "\\" in value
        or path.is_absolute()
        or normalized in {"", ".", ".."}
        or normalized.startswith("../")
        or normalized != value.rstrip("/")
    ):
        _unsafe_archive(value, "Archive member path is absolute, escaping, or non-normalized.")
    return normalized


def _extract_file(
    archive: tarfile.TarFile,
    member: tarfile.TarInfo,
    destination: Path,
) -> None:
    source = archive.extractfile(member)
    if source is None:
        _unsafe_archive(member.name, "Regular archive member has no readable content.")
    target = destination / member.name
    target.parent.mkdir(parents=True, exist_ok=True)
    with source, target.open("xb") as output:
        while chunk := source.read(1024 * 1024):
            output.write(chunk)
    executable = bool(member.mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
    os.chmod(target, 0o755 if executable else 0o644)


def _unsafe_archive(path: str, reason: str) -> NoReturn:
    raise TaskBundleError(
        ErrorCode.SOURCE_ARCHIVE_UNSAFE,
        "Git source archive contains an unsafe member.",
        ErrorContext(
            phase="source-archive",
            expected="Normalized regular files, directories, and safe internal symlinks",
            actual=reason,
            corrective_action="Remove the unsafe repository entry.",
            path=Path(path),
        ),
    )
