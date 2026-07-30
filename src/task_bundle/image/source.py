import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

from task_bundle.errors import ErrorCode, ErrorContext, TaskBundleError
from task_bundle.image.docker import DockerRunner
from task_bundle.models import SourceFileEntry, SourceManifest
from task_bundle.run.candidate import CandidateBuilder
from task_bundle.run.filesystem import build_filesystem_manifest
from task_bundle.run.models import FilesystemManifest, ManifestFile

_CONTAINER_ID = re.compile(r"^[0-9a-f]{12,64}$")


@dataclass(frozen=True, slots=True)
class ImageSourceVerification:
    manifest: FilesystemManifest
    tree_sha: str


def verify_image_source(
    runner: DockerRunner,
    *,
    image_id: str,
    expected_manifest: SourceManifest,
    expected_tree_sha: str,
    expected_source_digest: str,
    command_id: str,
    cwd: Path,
) -> ImageSourceVerification:
    name = f"task-bundle-{command_id.removeprefix('cmd_')[:20]}-source-inspect"
    container_id: str | None = None
    cleanup_target: str | None = None
    primary_error: BaseException | None = None
    try:
        created = runner.run(
            (
                "create",
                "--name",
                name,
                "--label",
                f"io.task-bundle.command-id={command_id}",
                "--entrypoint",
                "/bin/sh",
                image_id,
                "-c",
                "exit 0",
            ),
            cwd=cwd,
            timeout_seconds=30,
            error_code=ErrorCode.IMAGE_SOURCE_MISMATCH,
            phase="image-source-verification",
            description="create image source inspection container",
        )
        cleanup_target = name
        candidate = created.stdout.strip().splitlines()[-1]
        if _CONTAINER_ID.fullmatch(candidate) is None:
            _mismatch("Docker returned an invalid inspection container ID.")
        container_id = candidate
        with tempfile.TemporaryDirectory(
            prefix="task-bundle-image-source-"
        ) as temporary:
            root = Path(temporary) / "repo"
            root.mkdir()
            runner.run(
                ("cp", f"{container_id}:/opt/task/repo/.", str(root)),
                cwd=cwd,
                timeout_seconds=300,
                error_code=ErrorCode.IMAGE_SOURCE_MISMATCH,
                phase="image-source-verification",
                description="export image source for host verification",
            )
            expected_files = tuple(
                entry
                for entry in expected_manifest.entries
                if isinstance(entry, SourceFileEntry)
            )
            expected_bytes = sum(entry.size for entry in expected_files)
            largest_file = max((entry.size for entry in expected_files), default=0)
            actual = build_filesystem_manifest(
                root,
                phase="image-source-verification",
                error_code=ErrorCode.IMAGE_SOURCE_MISMATCH,
                allow_symlinks=True,
                max_files=max(len(expected_manifest.entries) * 2 + 1, 1024),
                max_total_bytes=max(expected_bytes * 2 + 1, 1_048_576),
                max_file_bytes=max(largest_file * 2 + 1, 1_048_576),
            )
            expected = _filesystem_manifest(expected_manifest, expected_source_digest)
            differences = _manifest_differences(expected, actual)
            if any(differences.values()) or actual.digest != expected_source_digest:
                _mismatch(
                    "Exported /opt/task/repo differs from the verified source manifest.",
                    details=differences,
                )
            tree_sha = CandidateBuilder(
                Path(temporary) / "tree",
                error_code=ErrorCode.IMAGE_SOURCE_MISMATCH,
                phase="image-source-verification",
            ).write_tree(root, actual, index_name="image-source.index")
            if tree_sha != expected_tree_sha:
                _mismatch(
                    "Exported /opt/task/repo has a different raw Git tree.",
                    details={
                        "expected_tree_sha": expected_tree_sha,
                        "actual_tree_sha": tree_sha,
                    },
                )
            return ImageSourceVerification(manifest=actual, tree_sha=tree_sha)
    except BaseException as error:
        primary_error = error
        raise
    finally:
        if cleanup_target is not None:
            removed = runner.run(
                ("rm", "--force", container_id or cleanup_target),
                cwd=cwd,
                timeout_seconds=30,
                error_code=ErrorCode.IMAGE_SOURCE_MISMATCH,
                phase="image-source-verification",
                description="remove image source inspection container",
                check=False,
            )
            if removed.exit_code != 0 and primary_error is None:
                _mismatch("Image source inspection container could not be removed.")


def _filesystem_manifest(
    source: SourceManifest,
    digest: str,
) -> FilesystemManifest:
    rows = [entry.model_dump(mode="json") for entry in source.entries]
    return FilesystemManifest.model_validate(
        {
            "entries": rows,
            "entry_count": len(rows),
            "total_bytes": sum(
                entry.size
                for entry in source.entries
                if isinstance(entry, SourceFileEntry)
            ),
            "digest": digest,
        }
    )


def _manifest_differences(
    expected: FilesystemManifest,
    actual: FilesystemManifest,
) -> dict[str, object]:
    before = {entry.path: entry for entry in expected.entries}
    after = {entry.path: entry for entry in actual.entries}
    common = before.keys() & after.keys()
    type_changed: list[str] = []
    mode_changed: list[str] = []
    changed: list[str] = []
    for path in common:
        expected_entry = before[path]
        actual_entry = after[path]
        if expected_entry.type != actual_entry.type:
            type_changed.append(path)
        elif isinstance(expected_entry, ManifestFile) and isinstance(
            actual_entry,
            ManifestFile,
        ):
            if expected_entry.mode != actual_entry.mode:
                mode_changed.append(path)
            if (
                expected_entry.size != actual_entry.size
                or expected_entry.sha256 != actual_entry.sha256
            ):
                changed.append(path)
        elif expected_entry != actual_entry:
            changed.append(path)
    return {
        "added_paths": sorted(after.keys() - before.keys()),
        "removed_paths": sorted(before.keys() - after.keys()),
        "changed_paths": sorted(changed),
        "mode_changed_paths": sorted(mode_changed),
        "type_changed_paths": sorted(type_changed),
    }


def _mismatch(
    actual: str,
    *,
    details: dict[str, object] | None = None,
) -> NoReturn:
    raise TaskBundleError(
        ErrorCode.IMAGE_SOURCE_MISMATCH,
        "Task image source does not match the verified source.",
        ErrorContext(
            phase="image-source-verification",
            expected=(
                "Complete /opt/task/repo manifest, source digest, and raw Git tree "
                "matching the materialized source"
            ),
            actual=actual,
            corrective_action="Remove source mutations from the task Dockerfile.",
            details=details,
        ),
    )
