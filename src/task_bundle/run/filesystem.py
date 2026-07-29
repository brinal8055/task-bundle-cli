import os
import stat
from pathlib import Path, PurePosixPath
from typing import Literal, NoReturn

from task_bundle.bundle.canonical import canonical_json_bytes, sha256_digest
from task_bundle.errors import ErrorCode, ErrorContext, TaskBundleError
from task_bundle.run.models import (
    FilesystemManifest,
    ManifestEntry,
    ManifestFile,
    ManifestSymlink,
)
from task_bundle.source.validation import validate_symlink_target


def build_filesystem_manifest(
    root: Path,
    *,
    phase: str,
    error_code: ErrorCode,
    allow_symlinks: bool,
    max_files: int,
    max_total_bytes: int,
    max_file_bytes: int,
) -> FilesystemManifest:
    try:
        root_metadata = root.lstat()
    except OSError as error:
        _unsafe(error_code, phase, root, str(error))
    if not stat.S_ISDIR(root_metadata.st_mode):
        _unsafe(error_code, phase, root, "root is not a real directory")
    entries: list[ManifestEntry] = []
    total_bytes = 0
    case_paths: dict[str, str] = {}

    def walk(directory: Path) -> None:
        nonlocal total_bytes
        try:
            children = sorted(directory.iterdir(), key=lambda item: item.name)
        except OSError as error:
            _unsafe(error_code, phase, directory, str(error))
        for child in children:
            relative = child.relative_to(root).as_posix()
            _validate_relative_path(relative, error_code, phase)
            case_key = relative.casefold()
            previous = case_paths.get(case_key)
            if previous is not None and previous != relative:
                _unsafe(
                    error_code,
                    phase,
                    Path(relative),
                    f"case-colliding paths: {previous} and {relative}",
                )
            case_paths[case_key] = relative
            try:
                metadata = child.lstat()
            except OSError as error:
                _unsafe(error_code, phase, Path(relative), str(error))
            if stat.S_ISDIR(metadata.st_mode):
                walk(child)
                continue
            if len(entries) >= max_files:
                _unsafe(
                    ErrorCode.CANDIDATE_FILE_LIMIT_ERROR
                    if error_code == ErrorCode.WORKSPACE_EXPORT_UNSAFE
                    else error_code,
                    phase,
                    Path(relative),
                    f"file count exceeds {max_files}",
                )
            if stat.S_ISLNK(metadata.st_mode):
                if not allow_symlinks:
                    _unsafe(error_code, phase, Path(relative), "symlinks are not allowed")
                try:
                    target = os.readlink(child)
                    validate_symlink_target(relative, target)
                except (OSError, ValueError) as error:
                    _unsafe(error_code, phase, Path(relative), str(error))
                entries.append(ManifestSymlink(path=relative, target=target))
                continue
            if not stat.S_ISREG(metadata.st_mode):
                _unsafe(
                    error_code,
                    phase,
                    Path(relative),
                    "special filesystem entries are not allowed",
                )
            if metadata.st_nlink != 1:
                _unsafe(
                    error_code,
                    phase,
                    Path(relative),
                    "regular files must not have ambiguous hard links",
                )
            payload, mode = _read_regular_file(
                child,
                error_code=error_code,
                phase=phase,
                relative=relative,
                max_file_bytes=max_file_bytes,
            )
            total_bytes += len(payload)
            if total_bytes > max_total_bytes:
                _unsafe(
                    ErrorCode.CANDIDATE_FILE_LIMIT_ERROR
                    if error_code == ErrorCode.WORKSPACE_EXPORT_UNSAFE
                    else error_code,
                    phase,
                    Path(relative),
                    f"total bytes exceed {max_total_bytes}",
                )
            entries.append(
                ManifestFile(
                    path=relative,
                    mode=mode,
                    size=len(payload),
                    sha256=sha256_digest(payload),
                )
            )

    walk(root)
    entries.sort(key=lambda entry: entry.path)
    document = {
        "schema_version": "1",
        "entries": [entry.model_dump(mode="json") for entry in entries],
    }
    return FilesystemManifest(
        entries=tuple(entries),
        entry_count=len(entries),
        total_bytes=total_bytes,
        digest=sha256_digest(canonical_json_bytes(document)),
    )


def copy_manifest_tree(
    source: Path,
    destination: Path,
    manifest: FilesystemManifest,
    *,
    phase: str,
    error_code: ErrorCode,
) -> None:
    try:
        destination.mkdir(parents=True, exist_ok=False)
    except OSError as error:
        _unsafe(error_code, phase, destination, str(error))
    for entry in manifest.entries:
        target = destination / Path(entry.path)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            _unsafe(error_code, phase, Path(entry.path), str(error))
        if isinstance(entry, ManifestSymlink):
            try:
                if not (source / Path(entry.path)).is_symlink():
                    raise OSError("source symlink changed during staging")
                if os.readlink(source / Path(entry.path)) != entry.target:
                    raise OSError("source symlink target changed during staging")
                target.symlink_to(entry.target)
            except OSError as error:
                _unsafe(error_code, phase, Path(entry.path), str(error))
            continue
        payload, mode = _read_regular_file(
            source / Path(entry.path),
            error_code=error_code,
            phase=phase,
            relative=entry.path,
            max_file_bytes=entry.size,
        )
        if (
            sha256_digest(payload) != entry.sha256
            or len(payload) != entry.size
            or mode != entry.mode
        ):
            _unsafe(
                error_code,
                phase,
                Path(entry.path),
                "source entry changed after manifest validation",
            )
        try:
            target.write_bytes(payload)
            target.chmod(0o755 if entry.mode == "0755" else 0o644)
        except OSError as error:
            _unsafe(error_code, phase, Path(entry.path), str(error))


def manifests_equal(first: FilesystemManifest, second: FilesystemManifest) -> bool:
    return first.entries == second.entries


def read_manifest_file(
    root: Path,
    entry: ManifestFile,
    *,
    phase: str,
    error_code: ErrorCode,
) -> bytes:
    payload, mode = _read_regular_file(
        root / Path(entry.path),
        error_code=error_code,
        phase=phase,
        relative=entry.path,
        max_file_bytes=entry.size,
    )
    if (
        len(payload) != entry.size
        or sha256_digest(payload) != entry.sha256
        or mode != entry.mode
    ):
        _unsafe(
            error_code,
            phase,
            Path(entry.path),
            "source entry changed after manifest validation",
        )
    return payload


def _read_regular_file(
    path: Path,
    *,
    error_code: ErrorCode,
    phase: str,
    relative: str,
    max_file_bytes: int,
) -> tuple[bytes, Literal["0644", "0755"]]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise OSError("entry is not a uniquely linked regular file")
        if metadata.st_size > max_file_bytes:
            raise OSError(f"file exceeds {max_file_bytes} bytes")
        chunks: list[bytes] = []
        remaining = max_file_bytes + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1_048_576))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > max_file_bytes:
            raise OSError(f"file exceeds {max_file_bytes} bytes")
        executable = bool(metadata.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
        mode: Literal["0644", "0755"] = "0755" if executable else "0644"
        return payload, mode
    except OSError as error:
        _unsafe(error_code, phase, Path(relative), str(error))
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _validate_relative_path(value: str, code: ErrorCode, phase: str) -> None:
    logical = PurePosixPath(value)
    if (
        not value
        or value in {".", ".."}
        or logical.is_absolute()
        or ".." in logical.parts
        or "\\" in value
        or logical.as_posix() != value
        or ".git" in logical.parts
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        _unsafe(code, phase, Path(value), "path is unsafe or unsupported")


def _unsafe(code: ErrorCode, phase: str, path: Path, actual: str) -> NoReturn:
    raise TaskBundleError(
        code,
        "Filesystem input cannot be represented safely.",
        ErrorContext(
            phase=phase,
            expected="Bounded regular files and safe normalized repository entries",
            actual=actual[:2000],
            corrective_action="Remove unsafe entries or reduce the input size.",
            path=path,
        ),
    )
