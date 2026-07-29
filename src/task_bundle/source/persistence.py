import json
from pathlib import Path
from typing import Any, NoReturn

from pydantic import BaseModel, ValidationError

from task_bundle.bundle.snapshot import write_json_atomic
from task_bundle.errors import ErrorCode, ErrorContext, TaskBundleError
from task_bundle.models import ResolvedSource, SourceManifest

SOURCE_SNAPSHOT_RELATIVE_PATH = Path(".task/source.snapshot.json")
SOURCE_MANIFEST_RELATIVE_PATH = Path(".task/source.manifest.json")

def write_source_metadata(
    bundle_root: Path,
    resolved: ResolvedSource,
    manifest: SourceManifest,
) -> None:
    write_json_atomic(
        resolved,
        bundle_root / SOURCE_SNAPSHOT_RELATIVE_PATH,
        error_code=ErrorCode.SOURCE_PERSISTENCE_ERROR,
        phase="source-persistence",
        message="Resolved source snapshot could not be written atomically.",
    )
    write_json_atomic(
        manifest,
        bundle_root / SOURCE_MANIFEST_RELATIVE_PATH,
        error_code=ErrorCode.SOURCE_PERSISTENCE_ERROR,
        phase="source-persistence",
        message="Source manifest could not be written atomically.",
    )


def load_source_snapshot(path: Path) -> ResolvedSource:
    return _load_model(path, ResolvedSource)


def load_source_manifest(path: Path) -> SourceManifest:
    return _load_model(path, SourceManifest)


def _load_model[ModelT: BaseModel](path: Path, model: type[ModelT]) -> ModelT:
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        _persistence_error(path, f"Source metadata could not be read: {error}", error)
    if not isinstance(raw, dict):
        _persistence_error(path, "Source metadata JSON root is not an object.")
    if raw.get("schema_version") != "1":
        _persistence_error(path, "Source metadata schema version is unsupported.")
    try:
        return model.model_validate(raw)
    except ValidationError as error:
        raise TaskBundleError(
            ErrorCode.SOURCE_PERSISTENCE_ERROR,
            "Source metadata does not match schema version 1.",
            ErrorContext(
                phase="source-persistence",
                expected=f"A valid {model.__name__}",
                actual=f"{error.error_count()} validation error(s)",
                corrective_action="Regenerate the source metadata.",
                path=path,
                details={"errors": error.errors(include_url=False)},
            ),
        ) from error


def _persistence_error(
    path: Path,
    actual: str,
    cause: BaseException | None = None,
) -> NoReturn:
    error = TaskBundleError(
        ErrorCode.SOURCE_PERSISTENCE_ERROR,
        "Source metadata is unreadable or unsupported.",
        ErrorContext(
            phase="source-persistence",
            expected="Readable source metadata using schema version 1",
            actual=actual,
            corrective_action="Regenerate the source metadata.",
            path=path,
        ),
    )
    if cause is None:
        raise error
    raise error from cause
