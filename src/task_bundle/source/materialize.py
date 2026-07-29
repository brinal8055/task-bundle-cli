import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import NoReturn

from task_bundle.errors import ErrorCode, ErrorContext, TaskBundleError
from task_bundle.source.git import GitRunner
from task_bundle.source.validation import validate_symlink_target

_TREE_ENTRY = re.compile(r"^([0-9]{6}) ([a-z]+) ([0-9a-f]{40})\t(.*)$", re.DOTALL)
_SUPPORTED_BLOB_MODES = {"100644", "100755", "120000"}


@dataclass(frozen=True, slots=True)
class GitTreeEntry:
    mode: str
    object_type: str
    object_id: str
    path: str


def validate_tree_listing(listing: str) -> tuple[GitTreeEntry, ...]:
    entries: list[GitTreeEntry] = []
    for record in listing.split("\0"):
        if not record:
            continue
        match = _TREE_ENTRY.fullmatch(record)
        if match is None:
            _tree_error(
                "Git tree listing is malformed.",
                "A valid `git ls-tree -r -z` record",
                _safe_excerpt(record),
            )
        entry = GitTreeEntry(
            mode=match.group(1),
            object_type=match.group(2),
            object_id=match.group(3),
            path=match.group(4),
        )
        _validate_entry(entry)
        entries.append(entry)

    entries.sort(key=lambda entry: entry.path)
    _validate_collisions(entries)
    return tuple(entries)


def materialize_tree(
    runner: GitRunner,
    *,
    object_repository: Path,
    source_root: Path,
    entries: Sequence[GitTreeEntry],
    timeout_seconds: int,
) -> None:
    try:
        source_root.mkdir(parents=True, exist_ok=False)
    except OSError as error:
        _materialization_error(source_root, str(error))
    symlink_buffer = object_repository.parent / "symlink-target"
    for entry in entries:
        if entry.mode == "160000":
            raise AssertionError("Gitlinks must be rejected before materialisation")
        destination = source_root / Path(entry.path)
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            _materialization_error(destination.parent, str(error))
        if entry.mode == "120000":
            runner.write_blob(
                object_repository=object_repository,
                object_id=entry.object_id,
                destination=symlink_buffer,
                timeout_seconds=timeout_seconds,
            )
            _materialize_symlink(entry.path, symlink_buffer, destination)
            continue
        runner.write_blob(
            object_repository=object_repository,
            object_id=entry.object_id,
            destination=destination,
            timeout_seconds=timeout_seconds,
        )
        mode = 0o755 if entry.mode == "100755" else 0o644
        try:
            os.chmod(destination, mode)
        except OSError as error:
            _materialization_error(destination, str(error))


def _materialize_symlink(path: str, buffer: Path, destination: Path) -> None:
    try:
        raw_target = buffer.read_bytes()
        buffer.unlink()
        target = raw_target.decode("utf-8")
        validate_symlink_target(path, target)
        destination.symlink_to(target)
    except (OSError, UnicodeDecodeError, ValueError) as error:
        buffer.unlink(missing_ok=True)
        raise TaskBundleError(
            ErrorCode.SOURCE_SYMLINK_UNSAFE,
            "Repository symlink cannot be materialised safely.",
            ErrorContext(
                phase="source-materialize",
                expected="A UTF-8 relative symlink target within the source root",
                actual=str(error),
                corrective_action="Replace the symlink with a safe internal target.",
                path=Path(path),
            ),
        ) from error


def _validate_entry(entry: GitTreeEntry) -> None:
    if entry.mode == "160000":
        if entry.object_type != "commit":
            _unsafe_tree(
                entry.path,
                f"Unsupported Git entry mode/type {entry.mode} {entry.object_type}.",
            )
        _validate_path(entry.path)
        return
    if entry.mode not in _SUPPORTED_BLOB_MODES or entry.object_type != "blob":
        _unsafe_tree(
            entry.path,
            f"Unsupported Git entry mode/type {entry.mode} {entry.object_type}.",
        )
    _validate_path(entry.path)


def _validate_path(value: str) -> None:
    if _contains_surrogate(value):
        _unsafe_tree(value, "Repository path is not valid UTF-8.")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        _unsafe_tree(value, "Repository path contains control characters.")
    path = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or path.is_absolute()
        or value in {".", ".."}
        or ".." in path.parts
        or path.as_posix() != value
    ):
        _unsafe_tree(value, "Repository path is absolute, escaping, or non-normalized.")
    if ".git" in path.parts:
        _unsafe_tree(value, ".git content is not allowed in materialised source.")


def _validate_collisions(entries: Sequence[GitTreeEntry]) -> None:
    nodes: dict[str, tuple[str, str]] = {}
    for entry in entries:
        _record_node(nodes, entry.path, "entry")
        parent = PurePosixPath(entry.path).parent
        while parent != PurePosixPath("."):
            _record_node(nodes, parent.as_posix(), "directory")
            parent = parent.parent


def _record_node(nodes: dict[str, tuple[str, str]], path: str, kind: str) -> None:
    folded = path.casefold()
    previous = nodes.get(folded)
    if previous is None:
        nodes[folded] = (path, kind)
        return
    previous_path, previous_kind = previous
    if previous_path != path:
        _unsafe_tree(
            path,
            f"Case-colliding paths are not portable: {previous_path!r} and {path!r}.",
        )
    if previous_kind != kind:
        _unsafe_tree(path, "A repository entry collides with a parent directory.")
    if kind == "entry":
        _unsafe_tree(path, "Repository tree contains a duplicate entry path.")


def _contains_surrogate(value: str) -> bool:
    return any(0xDC80 <= ord(character) <= 0xDCFF for character in value)


def _safe_excerpt(value: str, limit: int = 200) -> str:
    return value.encode("utf-8", errors="backslashreplace").decode("utf-8")[:limit]


def _tree_error(message: str, expected: str, actual: str) -> NoReturn:
    raise TaskBundleError(
        ErrorCode.SOURCE_TREE_ERROR,
        message,
        ErrorContext(
            phase="source-verify",
            expected=expected,
            actual=actual,
            corrective_action="Verify repository object integrity and Git compatibility.",
        ),
    )


def _unsafe_tree(path: str, reason: str) -> NoReturn:
    raise TaskBundleError(
        ErrorCode.SOURCE_TREE_UNSAFE,
        "Git source tree contains an unsafe entry.",
        ErrorContext(
            phase="source-verify",
            expected="Portable regular files, executable files, and safe symlinks",
            actual=reason,
            corrective_action="Remove or rename the unsafe repository entry.",
            path=Path(_safe_excerpt(path)),
        ),
    )


def _materialization_error(path: Path, actual: str) -> NoReturn:
    raise TaskBundleError(
        ErrorCode.SOURCE_MATERIALIZATION_ERROR,
        "Verified Git source could not be materialised.",
        ErrorContext(
            phase="source-materialize",
            expected="An empty writable temporary source directory",
            actual=actual,
            corrective_action="Check temporary-directory permissions and available disk space.",
            path=path,
        ),
    )
