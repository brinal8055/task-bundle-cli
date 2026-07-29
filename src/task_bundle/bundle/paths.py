import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal, NoReturn

from task_bundle.bundle.exclusions import is_generated_path
from task_bundle.errors import ErrorCode, ErrorContext, TaskBundleError

PathKind = Literal["file", "directory"]


@dataclass(frozen=True, slots=True)
class ResolvedBundlePath:
    absolute: Path
    relative: str


def resolve_bundle_path(
    bundle_root: Path,
    configured_path: str,
    expected: PathKind,
) -> ResolvedBundlePath:
    if not configured_path.strip():
        _path_error(configured_path, expected, "The configured path is empty.")

    raw = Path(configured_path)
    if raw.is_absolute():
        _path_error(configured_path, expected, "Absolute paths are not allowed.")

    root = bundle_root.resolve()
    candidate = root / raw
    logical = PurePosixPath(raw.as_posix())
    if is_generated_path(logical):
        _path_error(
            configured_path,
            expected,
            "Generated or runtime-state paths cannot be bundle inputs.",
        )

    try:
        relative_candidate = candidate.absolute().relative_to(root)
    except ValueError:
        _path_error(configured_path, expected, "The path escapes the bundle root.")

    current = root
    for part in relative_candidate.parts:
        current = current / part
        if current.is_symlink():
            _path_error(
                configured_path,
                expected,
                "Symlinks are not allowed in digest-covered bundle inputs.",
            )

    try:
        resolved = candidate.resolve(strict=True)
        relative = resolved.relative_to(root)
    except FileNotFoundError:
        _path_error(configured_path, expected, "The referenced path does not exist.")
    except ValueError:
        _path_error(configured_path, expected, "The path resolves outside the bundle root.")
    except OSError as error:
        _path_error(configured_path, expected, f"The path cannot be resolved: {error}.")

    mode = resolved.stat().st_mode
    if expected == "file" and not stat.S_ISREG(mode):
        _path_error(configured_path, expected, "The referenced path is not a regular file.")
    if expected == "directory" and not stat.S_ISDIR(mode):
        _path_error(configured_path, expected, "The referenced path is not a directory.")

    return ResolvedBundlePath(absolute=resolved, relative=relative.as_posix())


def _path_error(configured_path: str, expected: PathKind, reason: str) -> NoReturn:
    raise TaskBundleError(
        ErrorCode.BUNDLE_PATH_ERROR,
        f"Invalid bundle {expected} path.",
        ErrorContext(
            phase="bundle-path-validation",
            expected=f"A relative {expected} path inside the bundle",
            actual=reason,
            corrective_action=f"Use an existing {expected} located inside the task bundle.",
            path=Path(configured_path),
        ),
    )
