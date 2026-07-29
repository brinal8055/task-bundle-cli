import os
import shutil
import stat
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn

from task_bundle.bundle.canonical import canonical_json_bytes, sha256_digest
from task_bundle.bundle.loader import LoadedBundle
from task_bundle.errors import ErrorCode, ErrorContext, TaskBundleError
from task_bundle.image.models import BuildContextManifest, BuildContextMetadata
from task_bundle.image.validation import validate_base_image_reference
from task_bundle.models import (
    BaseImageEnvironment,
    DockerfileEnvironment,
    SourceFileEntry,
    SourceManifestEntry,
    SourceSymlinkEntry,
)
from task_bundle.source.manifest import build_source_manifest
from task_bundle.source.service import MaterializedSource


@dataclass(frozen=True, slots=True)
class BuildContext:
    root: Path
    manifest: BuildContextManifest
    metadata: BuildContextMetadata
    generated_dockerfile: bool


@contextmanager
def create_build_context(
    bundle: LoadedBundle,
    source: MaterializedSource,
    *,
    command_id: str,
    keep: bool,
) -> Iterator[BuildContext]:
    root = _create_root(bundle.root, command_id, keep)
    try:
        context = _populate_context(bundle, source, root)
        yield context
    except BaseException:
        if not keep:
            with suppress(TaskBundleError):
                _cleanup_context(root)
        raise
    else:
        if not keep:
            _cleanup_context(root)


def _create_root(bundle_root: Path, command_id: str, keep: bool) -> Path:
    try:
        if keep:
            root = bundle_root / ".task" / "build-contexts" / command_id
            root.mkdir(parents=True, exist_ok=False)
            return root
        return Path(tempfile.mkdtemp(prefix="task-bundle-build-"))
    except OSError as error:
        _context_error(
            "Build context directory could not be created.",
            str(error),
            bundle_root,
        )


def _populate_context(
    bundle: LoadedBundle,
    source: MaterializedSource,
    root: Path,
) -> BuildContext:
    try:
        (root / "repo").mkdir()
        (root / "env").mkdir()
    except OSError as error:
        _context_error("Build context layout could not be created.", str(error), root)

    _copy_source(source, root / "repo")
    environment = bundle.task.environment
    generated = isinstance(environment, BaseImageEnvironment)
    if isinstance(environment, DockerfileEnvironment):
        dockerfile = _read_verified_bundle_file(bundle, environment.dockerfile)
        _copy_environment_context(bundle, environment.context, root / "env")
    else:
        dockerfile = _base_image_dockerfile(
            validate_base_image_reference(environment.image),
            environment.runtime.working_directory,
        )
    _write_file(root / "Dockerfile", dockerfile, 0o644)

    source_manifest = build_source_manifest(root)
    manifest = BuildContextManifest(entries=source_manifest.entries)
    context_digest = _build_context_digest(manifest)
    environment_entries = tuple(
        entry for entry in manifest.entries if entry.path == "env" or entry.path.startswith("env/")
    )
    environment_digest = sha256_digest(
        canonical_json_bytes(
            {
                "schema_version": "1",
                "entries": [entry.model_dump(mode="json") for entry in environment_entries],
            }
        )
    )
    files = [entry for entry in manifest.entries if isinstance(entry, SourceFileEntry)]
    metadata = BuildContextMetadata(
        context_digest=context_digest,
        dockerfile_sha256=sha256_digest(dockerfile),
        repository_source_digest=source.resolved.source_tree_digest,
        environment_context_digest=environment_digest,
        entry_count=len(manifest.entries),
        total_bytes=sum(entry.size for entry in files),
        source_entry_count=source.resolved.source_entry_count,
        source_total_bytes=source.resolved.source_total_bytes,
        generated_dockerfile=generated,
        created_at=datetime.now(UTC),
    )
    return BuildContext(
        root=root,
        manifest=manifest,
        metadata=metadata,
        generated_dockerfile=generated,
    )


def _copy_source(source: MaterializedSource, destination: Path) -> None:
    _copy_source_entries(source.root, source.manifest.entries, destination)


def _copy_source_entries(
    source_root: Path,
    entries: Sequence[SourceManifestEntry],
    destination_root: Path,
) -> None:
    for entry in entries:
        source = source_root / Path(entry.path)
        destination = destination_root / Path(entry.path)
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            _context_error("Source parent directory could not be staged.", str(error), destination)
        if isinstance(entry, SourceSymlinkEntry):
            try:
                if not source.is_symlink() or os.readlink(source) != entry.target:
                    _context_changed(entry.path)
                destination.symlink_to(entry.target)
            except OSError as error:
                _context_error("Source symlink could not be staged.", str(error), destination)
            continue
        _copy_verified_file(source, destination, entry.sha256, entry.mode)


def _copy_environment_context(
    bundle: LoadedBundle,
    context_path: str,
    destination_root: Path,
) -> None:
    prefix = f"{context_path.rstrip('/')}/"
    for entry in bundle.input_manifest:
        if not entry.path.startswith(prefix):
            continue
        relative = entry.path.removeprefix(prefix)
        if not relative:
            continue
        destination = destination_root / Path(relative)
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            _context_error(
                "Environment parent directory could not be staged.",
                str(error),
                destination,
            )
        _copy_verified_file(
            bundle.root / Path(entry.path),
            destination,
            entry.sha256,
            entry.mode,
        )


def _read_verified_bundle_file(bundle: LoadedBundle, path: str) -> bytes:
    entry = next((item for item in bundle.input_manifest if item.path == path), None)
    if entry is None:
        raise AssertionError("Loaded bundle omitted a configured Dockerfile")
    source = bundle.root / Path(path)
    try:
        metadata = source.lstat()
        content = source.read_bytes()
    except OSError as error:
        _context_error("Dockerfile could not be read.", str(error), source)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or sha256_digest(content) != entry.sha256
        or _canonical_mode(metadata.st_mode) != entry.mode
    ):
        _context_changed(path)
    return content


def _copy_verified_file(
    source: Path,
    destination: Path,
    expected_digest: str,
    expected_mode: str,
) -> None:
    try:
        metadata = source.lstat()
        content = source.read_bytes()
    except OSError as error:
        _context_error("Build input could not be read.", str(error), source)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or sha256_digest(content) != expected_digest
        or _canonical_mode(metadata.st_mode) != expected_mode
    ):
        _context_changed(source.as_posix())
    _write_file(destination, content, 0o755 if expected_mode == "0755" else 0o644)


def _write_file(path: Path, content: bytes, mode: int) -> None:
    try:
        with path.open("xb") as handle:
            handle.write(content)
        os.chmod(path, mode)
    except OSError as error:
        _context_error("Build-context file could not be written.", str(error), path)


def _base_image_dockerfile(reference: str, working_directory: str) -> bytes:
    return (f"FROM {reference}\nCOPY repo/ /opt/task/repo/\nWORKDIR {working_directory}\n").encode()


def _build_context_digest(manifest: BuildContextManifest) -> str:
    return sha256_digest(
        canonical_json_bytes(
            {
                "schema_version": manifest.schema_version,
                "entries": [entry.model_dump(mode="json") for entry in manifest.entries],
            }
        )
    )


def _canonical_mode(mode: int) -> str:
    executable = bool(mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
    return "0755" if executable else "0644"


def _cleanup_context(root: Path) -> None:
    try:
        shutil.rmtree(root)
    except OSError as error:
        raise TaskBundleError(
            ErrorCode.CLEANUP_ERROR,
            "Temporary build context could not be removed.",
            ErrorContext(
                phase="build-context-cleanup",
                expected="Complete removal of the generated build context",
                actual=str(error),
                corrective_action="Remove the temporary build context manually.",
                path=root,
            ),
        ) from error


def _context_changed(path: str) -> NoReturn:
    raise TaskBundleError(
        ErrorCode.BUNDLE_DIGEST_ERROR,
        "A digest-covered input changed while staging the build context.",
        ErrorContext(
            phase="build-context",
            expected="Input content and executable mode to match the loaded manifest",
            actual="The input changed after bundle/source verification.",
            corrective_action="Retry after stopping concurrent modifications.",
            path=Path(path),
        ),
    )


def _context_error(message: str, actual: str, path: Path) -> NoReturn:
    raise TaskBundleError(
        ErrorCode.BUILD_CONTEXT_ERROR,
        message,
        ErrorContext(
            phase="build-context",
            expected="A safe physical build-context copy",
            actual=actual,
            corrective_action="Check input types, permissions, and available disk space.",
            path=path,
        ),
    )
