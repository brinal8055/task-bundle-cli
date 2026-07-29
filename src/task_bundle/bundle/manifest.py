import stat
from pathlib import Path, PurePosixPath
from typing import NoReturn

from task_bundle.bundle.canonical import sha256_digest
from task_bundle.bundle.exclusions import is_generated_path
from task_bundle.errors import ErrorCode, ErrorContext, TaskBundleError
from task_bundle.models import InputManifestEntry


def build_input_manifest(
    bundle_root: Path,
    files: set[str],
    trees: set[str],
    execution_trees: set[str],
) -> tuple[InputManifestEntry, ...]:
    entries: dict[str, InputManifestEntry] = {}
    for relative in sorted(files):
        path = bundle_root / Path(relative)
        entries[relative] = _file_entry(path, relative)
    for relative in sorted(trees):
        _walk_tree(
            bundle_root,
            bundle_root / Path(relative),
            entries,
            apply_generated_exclusions=relative not in execution_trees,
        )
    return tuple(entries[path] for path in sorted(entries))


def _walk_tree(
    bundle_root: Path,
    directory: Path,
    entries: dict[str, InputManifestEntry],
    apply_generated_exclusions: bool,
) -> None:
    try:
        children = sorted(directory.iterdir(), key=lambda path: path.name)
    except OSError as error:
        _special_file_error(
            directory.relative_to(bundle_root).as_posix(),
            f"Directory traversal failed: {error}",
        )

    for child in children:
        relative = child.relative_to(bundle_root).as_posix()
        logical = PurePosixPath(relative)
        if apply_generated_exclusions and is_generated_path(logical):
            continue
        try:
            mode = child.lstat().st_mode
        except OSError as error:
            _special_file_error(relative, f"Filesystem metadata could not be read: {error}")
        if stat.S_ISLNK(mode):
            _special_file_error(relative, "Symlinks are not allowed in digest-covered inputs.")
        if stat.S_ISDIR(mode):
            _walk_tree(bundle_root, child, entries, apply_generated_exclusions)
        elif stat.S_ISREG(mode):
            entries[relative] = _file_entry(child, relative)
        else:
            _special_file_error(relative, "Unsupported special filesystem object.")


def _file_entry(path: Path, relative: str) -> InputManifestEntry:
    try:
        metadata = path.stat()
        content = path.read_bytes()
    except OSError as error:
        _special_file_error(relative, f"Regular file could not be read: {error}")
    executable = bool(metadata.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
    return InputManifestEntry(
        path=relative,
        mode="0755" if executable else "0644",
        size=len(content),
        sha256=sha256_digest(content),
    )


def _special_file_error(relative: str, reason: str) -> NoReturn:
    raise TaskBundleError(
        ErrorCode.BUNDLE_SPECIAL_FILE_ERROR,
        "Unsupported bundle input.",
        ErrorContext(
            phase="bundle-manifest",
            expected="Regular files and directories without symlinks",
            actual=reason,
            corrective_action="Replace the entry with a regular bundle-contained file.",
            path=Path(relative),
        ),
    )
