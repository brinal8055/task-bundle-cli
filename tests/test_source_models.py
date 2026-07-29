from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from task_bundle.models import (
    ResolvedSource,
    SourceFileEntry,
    SourceManifest,
    SourceSymlinkEntry,
)
from task_bundle.source.manifest import source_manifest_digest


def _resolved(**updates: object) -> ResolvedSource:
    values: dict[str, object] = {
        "repository_url": "https://example.com/Owner/repo.git",
        "requested_commit": "a" * 40,
        "resolved_commit": "a" * 40,
        "tree_sha": "b" * 40,
        "source_tree_digest": f"sha256:{'c' * 64}",
        "source_entry_count": 1,
        "source_total_bytes": 4,
        "symlink_count": 0,
        "executable_file_count": 0,
        "git_executable": "/usr/bin/git",
        "git_version": "2.50.0",
        "created_at": datetime(2026, 7, 29, tzinfo=UTC),
    }
    values.update(updates)
    return ResolvedSource.model_validate(values)


def test_resolved_source_is_immutable_strict_utc_and_round_trippable() -> None:
    resolved = _resolved()
    restored = ResolvedSource.model_validate_json(resolved.model_dump_json())

    assert restored == resolved
    assert restored.created_at.tzinfo == UTC
    assert "/tmp/" not in restored.model_dump_json()
    with pytest.raises(ValidationError):
        restored.tree_sha = "d" * 40
    with pytest.raises(ValidationError):
        ResolvedSource.model_validate({**resolved.model_dump(), "unknown": True})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("requested_commit", "short"),
        ("resolved_commit", "g" * 40),
        ("tree_sha", "short"),
        ("source_tree_digest", "invalid"),
        ("repository_url", "file:///tmp/repo"),
    ],
)
def test_resolved_source_rejects_invalid_identity(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        _resolved(**{field: value})


def test_creation_timestamp_does_not_affect_manifest_digest() -> None:
    manifest = SourceManifest(
        entries=(
            SourceFileEntry(
                path="file.txt",
                mode="0644",
                size=4,
                sha256=f"sha256:{'a' * 64}",
            ),
        )
    )
    digest = source_manifest_digest(manifest)
    first = _resolved(source_tree_digest=digest, created_at=datetime(2026, 7, 29, tzinfo=UTC))
    second = _resolved(source_tree_digest=digest, created_at=datetime(2026, 7, 30, tzinfo=UTC))

    assert first.created_at != second.created_at
    assert first.source_tree_digest == second.source_tree_digest == digest


def test_unsafe_symlink_model_is_rejected() -> None:
    with pytest.raises(ValidationError):
        SourceSymlinkEntry(path="bin/tool", target="../../outside")


def test_resolved_commit_must_equal_requested_commit() -> None:
    with pytest.raises(ValidationError):
        _resolved(resolved_commit="d" * 40)
