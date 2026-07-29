import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn

from pydantic import ValidationError

from task_bundle.bundle.loader import LoadedBundle
from task_bundle.bundle.snapshot import write_json_atomic
from task_bundle.errors import ErrorCode, ErrorContext, TaskBundleError
from task_bundle.image.context import BuildContext
from task_bundle.image.models import (
    BundleLock,
    ImageInspection,
    LockEnvironment,
    LockEvaluation,
    LockSource,
    RuntimePolicy,
)
from task_bundle.image.runtime import runtime_policy_digest
from task_bundle.models import BaseImageEnvironment, ResolvedSource
from task_bundle.source.validation import normalize_repository_url

LOCK_RELATIVE_PATH = Path(".task/bundle.lock.json")


@dataclass(frozen=True, slots=True)
class LockComparison:
    is_current: bool
    reasons: tuple[str, ...]


def create_bundle_lock(
    *,
    bundle: LoadedBundle,
    source: ResolvedSource,
    context: BuildContext,
    inspection: ImageInspection,
    image_reference: str,
    runtime_policy: RuntimePolicy,
    cli_version: str,
    created_at: datetime | None = None,
) -> BundleLock:
    environment = bundle.task.environment
    configured_reference = (
        environment.image
        if isinstance(environment, BaseImageEnvironment)
        else environment.dockerfile
    )
    return BundleLock(
        task_id=bundle.task.task.id,
        bundle_input_digest=bundle.bundle_input_digest,
        cli_version=cli_version,
        created_at=created_at or datetime.now(UTC),
        provenance=bundle.task.provenance,
        source=LockSource(
            repository_url=source.repository_url,
            requested_commit=source.requested_commit,
            resolved_commit=source.resolved_commit,
            tree_sha=source.tree_sha,
            source_tree_digest=source.source_tree_digest,
        ),
        environment=LockEnvironment(
            type=environment.type,
            configured_reference=configured_reference,
            platform=inspection.platform,
            build_context_digest=context.metadata.context_digest,
            dockerfile_sha256=context.metadata.dockerfile_sha256,
        ),
        image_reference=image_reference,
        image_id=inspection.image_id,
        image_repo_digests=inspection.repo_digests,
        image_created=inspection.created,
        actual_platform=inspection.platform,
        runtime_policy_digest=runtime_policy_digest(runtime_policy),
        evaluation=LockEvaluation.model_validate(bundle.evaluation_inputs.model_dump(mode="json")),
    )


def write_bundle_lock(lock: BundleLock, path: Path) -> None:
    write_json_atomic(
        lock,
        path,
        error_code=ErrorCode.LOCK_WRITE_ERROR,
        phase="lock-write",
        message="Bundle lock could not be written atomically.",
    )


def load_bundle_lock(path: Path) -> BundleLock:
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise TaskBundleError(
            ErrorCode.LOCK_READ_ERROR,
            "Bundle lock could not be read.",
            ErrorContext(
                phase="lock-read",
                expected="A readable JSON bundle lock",
                actual=str(error),
                corrective_action="Rebuild the task image with `task init --rebuild`.",
                path=path,
            ),
        ) from error
    try:
        return BundleLock.model_validate(raw)
    except ValidationError as error:
        raise TaskBundleError(
            ErrorCode.LOCK_READ_ERROR,
            "Bundle lock schema is invalid or unsupported.",
            ErrorContext(
                phase="lock-read",
                expected="BundleLock schema version 1",
                actual=f"{error.error_count()} validation error(s)",
                corrective_action="Rebuild the task image with `task init --rebuild`.",
                path=path,
                details={"errors": error.errors(include_url=False)},
            ),
        ) from error


def compare_bundle_lock(
    lock: BundleLock,
    *,
    bundle: LoadedBundle,
    runtime_policy: RuntimePolicy,
    image_reference: str,
    selected_platform: str,
    observed_image_id: str | None,
) -> LockComparison:
    reasons: list[str] = []
    _compare(reasons, "task_id", lock.task_id, bundle.task.task.id)
    _compare(
        reasons,
        "bundle_input_digest",
        lock.bundle_input_digest,
        bundle.bundle_input_digest,
    )
    _compare(
        reasons,
        "source_repository_url",
        lock.source.repository_url,
        normalize_repository_url(bundle.task.repository.url),
    )
    _compare(
        reasons,
        "source_requested_commit",
        lock.source.requested_commit,
        bundle.task.repository.commit.lower(),
    )
    _compare(reasons, "image_reference", lock.image_reference, image_reference)
    _compare(reasons, "platform", lock.actual_platform, selected_platform)
    _compare(
        reasons,
        "runtime_policy_digest",
        lock.runtime_policy_digest,
        runtime_policy_digest(runtime_policy),
    )
    if observed_image_id is None:
        reasons.append("image_missing")
    elif observed_image_id != lock.image_id:
        reasons.append("image_id")
    return LockComparison(is_current=not reasons, reasons=tuple(reasons))


def stale_lock_error(path: Path, comparison: LockComparison) -> NoReturn:
    raise TaskBundleError(
        ErrorCode.LOCK_MISMATCH,
        "Existing bundle lock is stale.",
        ErrorContext(
            phase="lock-freshness",
            expected="Bundle, source, runtime policy, image tag, and image ID to match",
            actual=f"Mismatches: {', '.join(comparison.reasons)}",
            corrective_action="Run `task init --rebuild` to replace the stale image and lock.",
            path=path,
            details={"reasons": list(comparison.reasons)},
        ),
    )


def _compare(
    reasons: list[str],
    name: str,
    expected: object,
    actual: object,
) -> None:
    if expected != actual:
        reasons.append(name)
