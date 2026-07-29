import stat
from pathlib import Path

from task_bundle.bundle.loader import LoadedBundle
from task_bundle.errors import ErrorCode, ErrorContext, TaskBundleError
from task_bundle.run.filesystem import build_filesystem_manifest
from task_bundle.run.models import FilesystemManifest


def validate_solver_context(
    path: Path | None,
    *,
    bundle: LoadedBundle,
) -> tuple[Path | None, FilesystemManifest | None]:
    if path is None:
        return None, None
    absolute = path.expanduser().absolute()
    try:
        metadata = absolute.lstat()
        resolved = absolute.resolve(strict=True)
    except OSError as error:
        _context_error("Solver context could not be inspected.", str(error), path)
    if not stat.S_ISDIR(metadata.st_mode):
        _context_error("Solver context is not a real directory.", "not a directory", path)
    bundle_root = bundle.root.resolve()
    artifact_root = (bundle.root / "artifacts").resolve()
    if _is_within(resolved, bundle_root) or _is_within(resolved, artifact_root):
        _context_error(
            "Solver context is inside a protected task directory.",
            "context resolves under the bundle or artifact root",
            path,
        )
    manifest = build_filesystem_manifest(
        resolved,
        phase="solver-context",
        error_code=ErrorCode.SOLVER_CONTEXT_UNSAFE,
        allow_symlinks=False,
        max_files=bundle.task.solver.max_context_files,
        max_total_bytes=bundle.task.solver.max_context_bytes,
        max_file_bytes=bundle.task.solver.max_context_bytes,
    )
    return resolved, manifest


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _context_error(message: str, actual: str, path: Path) -> None:
    raise TaskBundleError(
        ErrorCode.SOLVER_CONTEXT_ERROR,
        message,
        ErrorContext(
            phase="solver-context",
            expected="A bounded directory outside the bundle, .task, and artifacts",
            actual=actual,
            corrective_action="Use a separate regular solver-context directory.",
            path=path,
        ),
    )
