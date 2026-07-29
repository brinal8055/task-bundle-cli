import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from task_bundle.errors import ErrorCode, TaskBundleError
from task_bundle.image.lock import load_bundle_lock, write_bundle_lock
from task_bundle.image.models import (
    BundleLock,
    LockEnvironment,
    LockEvaluation,
    LockSource,
)


def _lock() -> BundleLock:
    return BundleLock(
        task_id="task",
        bundle_input_digest="sha256:" + "a" * 64,
        cli_version="test",
        created_at=datetime.now(UTC),
        provenance=None,
        source=LockSource(
            repository_url="https://example.com/repo.git",
            requested_commit="b" * 40,
            resolved_commit="b" * 40,
            tree_sha="c" * 40,
            source_tree_digest="sha256:" + "d" * 64,
        ),
        environment=LockEnvironment(
            type="base_image",
            configured_reference=f"busybox@sha256:{'e' * 64}",
            platform="linux/amd64",
            build_context_digest="sha256:" + "f" * 64,
            dockerfile_sha256="sha256:" + "1" * 64,
        ),
        image_reference="task-bundle/task:tag",
        image_id="sha256:" + "2" * 64,
        image_repo_digests=(),
        image_created=None,
        actual_platform="linux/amd64",
        runtime_policy_digest="sha256:" + "3" * 64,
        evaluation=LockEvaluation(
            test_patch_sha256="sha256:" + "4" * 64,
            golden_patch_sha256="sha256:" + "5" * 64,
            harness_sha256="sha256:" + "6" * 64,
            selectors_sha256="sha256:" + "7" * 64,
        ),
    )


def test_lock_round_trip_is_strict_and_atomic(tmp_path: Path) -> None:
    path = tmp_path / ".task/bundle.lock.json"
    lock = _lock()

    write_bundle_lock(lock, path)

    assert load_bundle_lock(path) == lock
    assert list(path.parent.glob("*.tmp")) == []


@pytest.mark.parametrize(
    "mutation",
    [
        {"schema_version": "2"},
        {"unexpected": True},
        {"image_id": "not-an-image-id"},
    ],
)
def test_tampered_or_future_lock_is_rejected(
    tmp_path: Path,
    mutation: dict[str, object],
) -> None:
    path = tmp_path / "bundle.lock.json"
    document = _lock().model_dump(mode="json")
    document.update(mutation)
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(TaskBundleError) as caught:
        load_bundle_lock(path)

    assert caught.value.code == ErrorCode.LOCK_READ_ERROR
