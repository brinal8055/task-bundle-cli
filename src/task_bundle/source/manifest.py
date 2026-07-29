import os
import stat
from pathlib import Path

from task_bundle.bundle.canonical import canonical_json_bytes, sha256_digest
from task_bundle.errors import ErrorCode, ErrorContext, TaskBundleError
from task_bundle.models import (
    SourceFileEntry,
    SourceManifest,
    SourceManifestEntry,
    SourceSymlinkEntry,
)
from task_bundle.source.validation import validate_symlink_target


def build_source_manifest(root: Path) -> SourceManifest:
    entries: list[SourceManifestEntry] = []
    _walk_source(root, root, entries)
    entries.sort(key=lambda entry: entry.path)
    return SourceManifest(entries=tuple(entries))


def source_manifest_digest(manifest: SourceManifest) -> str:
    document = {
        "schema_version": manifest.schema_version,
        "entries": [entry.model_dump(mode="json") for entry in manifest.entries],
    }
    return sha256_digest(canonical_json_bytes(document))


def _walk_source(
    root: Path,
    directory: Path,
    entries: list[SourceManifestEntry],
) -> None:
    try:
        children = sorted(directory.iterdir(), key=lambda path: path.name)
    except OSError as error:
        _manifest_error(directory.relative_to(root), str(error))
    for child in children:
        relative = child.relative_to(root).as_posix()
        if "\\" in relative:
            _manifest_error(Path(relative), "Backslashes are not allowed in source paths")
        if relative == ".git" or relative.startswith(".git/"):
            _manifest_error(Path(relative), ".git must not exist in materialised source")
        try:
            metadata = child.lstat()
        except OSError as error:
            _manifest_error(Path(relative), str(error))
        if stat.S_ISLNK(metadata.st_mode):
            try:
                target = os.readlink(child)
            except OSError as error:
                _manifest_error(Path(relative), str(error))
            try:
                validate_symlink_target(relative, target)
            except ValueError as error:
                raise TaskBundleError(
                    ErrorCode.SOURCE_SYMLINK_UNSAFE,
                    "Materialised source contains an unsafe symlink.",
                    ErrorContext(
                        phase="source-manifest",
                        expected="A relative symlink target within the source root",
                        actual=str(error),
                        corrective_action="Replace the unsafe repository symlink.",
                        path=Path(relative),
                        details={"target": target},
                    ),
                ) from error
            entries.append(SourceSymlinkEntry(path=relative, target=target))
        elif stat.S_ISDIR(metadata.st_mode):
            _walk_source(root, child, entries)
        elif stat.S_ISREG(metadata.st_mode):
            try:
                content = child.read_bytes()
            except OSError as error:
                _manifest_error(Path(relative), str(error))
            executable = bool(
                metadata.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            )
            entries.append(
                SourceFileEntry(
                    path=relative,
                    mode="0755" if executable else "0644",
                    size=len(content),
                    sha256=sha256_digest(content),
                )
            )
        else:
            _manifest_error(Path(relative), "Unsupported special filesystem object")


def _manifest_error(path: Path, actual: str) -> None:
    raise TaskBundleError(
        ErrorCode.SOURCE_MANIFEST_ERROR,
        "Materialised source cannot be represented safely.",
        ErrorContext(
            phase="source-manifest",
            expected="Regular files, directories, and safe internal symlinks",
            actual=actual,
            corrective_action="Remove the unsupported source entry.",
            path=path,
        ),
    )
