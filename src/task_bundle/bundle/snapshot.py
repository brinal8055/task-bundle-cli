import json
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn

from pydantic import BaseModel, ValidationError

from task_bundle.bundle.loader import LoadedBundle
from task_bundle.errors import ErrorCode, ErrorContext, TaskBundleError
from task_bundle.models import BundleSnapshot

SNAPSHOT_RELATIVE_PATH = Path(".task/bundle.snapshot.json")


@dataclass(frozen=True, slots=True)
class SnapshotComparison:
    is_current: bool
    expected_digest: str
    actual_digest: str
    changed_inputs: tuple[str, ...]


def create_snapshot(
    bundle: LoadedBundle,
    cli_version: str,
    created_at: datetime | None = None,
) -> BundleSnapshot:
    return BundleSnapshot(
        task_id=bundle.task.task.id,
        bundle_input_digest=bundle.bundle_input_digest,
        cli_version=cli_version,
        created_at=created_at or datetime.now(UTC),
        provenance=bundle.task.provenance,
        canonical_config_sha256=bundle.canonical_config_sha256,
        input_manifest=bundle.input_manifest,
        evaluation_inputs=bundle.evaluation_inputs,
    )


def write_snapshot_atomic(snapshot: BundleSnapshot, destination: Path) -> None:
    write_json_atomic(
        snapshot,
        destination,
        error_code=ErrorCode.SNAPSHOT_WRITE_ERROR,
        phase="snapshot-write",
        message="Bundle snapshot could not be written atomically.",
    )


def write_json_atomic(
    value: BaseModel,
    destination: Path,
    *,
    error_code: ErrorCode,
    phase: str,
    message: str,
) -> None:
    temporary: Path | None = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            value.model_dump(mode="json", exclude_none=False),
            sort_keys=True,
            indent=2,
            ensure_ascii=False,
        ).encode("utf-8") + b"\n"
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".bundle.snapshot.",
            suffix=".tmp",
            dir=destination.parent,
        )
        temporary = Path(temporary_name)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        temporary = None
        _fsync_directory(destination.parent)
    except OSError as error:
        raise TaskBundleError(
            error_code,
            message,
            ErrorContext(
                phase=phase,
                expected="An atomic snapshot replacement",
                actual=str(error),
                corrective_action="Check destination permissions and available disk space.",
                path=destination,
                details={"error_type": type(error).__name__, "error": str(error)},
            ),
        ) from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_snapshot(path: Path) -> BundleSnapshot:
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise TaskBundleError(
            ErrorCode.SNAPSHOT_READ_ERROR,
            "Bundle snapshot could not be read.",
            ErrorContext(
                phase="snapshot-read",
                expected="A readable JSON bundle snapshot",
                actual=str(error),
                corrective_action="Regenerate the bundle snapshot.",
                path=path,
            ),
        ) from error
    if not isinstance(raw, dict):
        _snapshot_schema_error(path, "The JSON root is not an object.")
    if raw.get("schema_version") != "1":
        _snapshot_schema_error(path, "The snapshot schema version is unsupported.")
    try:
        return BundleSnapshot.model_validate(raw)
    except ValidationError as error:
        raise TaskBundleError(
            ErrorCode.SNAPSHOT_SCHEMA_ERROR,
            "Bundle snapshot does not match schema version 1.",
            ErrorContext(
                phase="snapshot-read",
                expected="A valid strict BundleSnapshot",
                actual=f"{error.error_count()} validation error(s)",
                corrective_action="Regenerate the bundle snapshot.",
                path=path,
                details={"errors": error.errors(include_url=False)},
            ),
        ) from error


def compare_snapshot(
    snapshot: BundleSnapshot,
    current: LoadedBundle,
) -> SnapshotComparison:
    expected = {entry.path: entry for entry in snapshot.input_manifest}
    actual = {entry.path: entry for entry in current.input_manifest}
    changed = {
        path
        for path in expected.keys() | actual.keys()
        if expected.get(path) != actual.get(path)
    }
    if snapshot.canonical_config_sha256 != current.canonical_config_sha256:
        changed.add("<task-config>")
    return SnapshotComparison(
        is_current=snapshot.bundle_input_digest == current.bundle_input_digest,
        expected_digest=snapshot.bundle_input_digest,
        actual_digest=current.bundle_input_digest,
        changed_inputs=tuple(sorted(changed)),
    )


def _snapshot_schema_error(path: Path, actual: str) -> NoReturn:
    raise TaskBundleError(
        ErrorCode.SNAPSHOT_SCHEMA_ERROR,
        "Bundle snapshot schema is unsupported or invalid.",
        ErrorContext(
            phase="snapshot-read",
            expected="BundleSnapshot schema version 1",
            actual=actual,
            corrective_action="Regenerate the bundle snapshot with this CLI version.",
            path=path,
        ),
    )


def _fsync_directory(directory: Path) -> None:
    if not hasattr(os, "O_DIRECTORY"):
        return
    descriptor: int | None = None
    try:
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        if descriptor is not None:
            os.close(descriptor)
