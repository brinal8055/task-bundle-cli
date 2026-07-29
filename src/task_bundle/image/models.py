from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Literal

from pydantic import Field, field_validator, model_validator

from task_bundle.models import (
    Provenance,
    SourceManifestEntry,
    StrictModel,
)


class DockerEnvironment(StrictModel):
    executable: str = Field(min_length=1)
    client_version: str = Field(min_length=1)
    server_version: str = Field(min_length=1)
    host_os: str = Field(min_length=1)
    host_architecture: str = Field(min_length=1)
    rootless: bool


class BuildContextManifest(StrictModel):
    schema_version: Literal["1"] = "1"
    entries: tuple[SourceManifestEntry, ...]

    @model_validator(mode="after")
    def entries_must_be_sorted_and_unique(self) -> "BuildContextManifest":
        paths = [entry.path for entry in self.entries]
        if paths != sorted(set(paths)):
            raise ValueError("build-context manifest paths must be sorted and unique")
        return self


class BuildContextMetadata(StrictModel):
    schema_version: Literal["1"] = "1"
    context_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    dockerfile_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    repository_source_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    environment_context_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    entry_count: int = Field(ge=0)
    total_bytes: int = Field(ge=0)
    source_entry_count: int = Field(ge=0)
    source_total_bytes: int = Field(ge=0)
    generated_dockerfile: bool
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def created_at_must_include_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must include a timezone")
        return value.astimezone(UTC)


class DockerCommandRecord(StrictModel):
    schema_version: Literal["1"] = "1"
    phase: str = Field(min_length=1)
    argv: tuple[str, ...]
    timeout_seconds: int = Field(gt=0)


class ImageInspection(StrictModel):
    schema_version: Literal["1"] = "1"
    image_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    reference: str = Field(min_length=1)
    repo_tags: tuple[str, ...]
    repo_digests: tuple[str, ...]
    os: str = Field(min_length=1)
    architecture: str = Field(min_length=1)
    variant: str | None = None
    platform: str = Field(min_length=1)
    created: str | None = None
    configured_user: str | None = None
    working_directory: str | None = None
    labels: dict[str, str]
    size_bytes: int = Field(ge=0)


class SmokeCheckResult(StrictModel):
    schema_version: Literal["1"] = "1"
    container_name: str = Field(min_length=1)
    image_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    source_probe: str
    stdout: str
    stderr: str
    duration_ms: int = Field(ge=0)
    cleaned_up: bool


class RuntimePolicy(StrictModel):
    schema_version: Literal["1"] = "1"
    user: str
    working_directory: str
    network: Literal["none"]
    timeout_seconds: int = Field(gt=0)
    cpus: float = Field(gt=0)
    memory_mb: int = Field(gt=0)
    pids_limit: int = Field(gt=0)
    read_only_root: Literal[True]
    tmpfs: tuple[str, ...]
    cap_drop: tuple[Literal["ALL"], ...] = ("ALL",)
    no_new_privileges: Literal[True] = True

    @field_validator("working_directory")
    @classmethod
    def working_directory_must_be_absolute_posix(cls, value: str) -> str:
        path = PurePosixPath(value)
        if not path.is_absolute() or ".." in path.parts or path.as_posix() != value:
            raise ValueError("working directory must be a normalized absolute POSIX path")
        return value


class LockSource(StrictModel):
    repository_url: str
    requested_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    resolved_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    tree_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    source_tree_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class LockEnvironment(StrictModel):
    type: Literal["dockerfile", "base_image"]
    configured_reference: str
    platform: str
    build_context_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    dockerfile_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class LockEvaluation(StrictModel):
    test_patch_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    golden_patch_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    harness_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    selectors_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class BundleLock(StrictModel):
    schema_version: Literal["1"] = "1"
    task_id: str = Field(min_length=1)
    bundle_input_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    cli_version: str = Field(min_length=1)
    created_at: datetime
    provenance: Provenance | None
    source: LockSource
    environment: LockEnvironment
    image_reference: str = Field(min_length=1)
    image_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    image_repo_digests: tuple[str, ...]
    image_created: str | None
    actual_platform: str
    runtime_policy_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    evaluation: LockEvaluation

    @field_validator("created_at")
    @classmethod
    def created_at_must_include_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must include a timezone")
        return value.astimezone(UTC)


class InitResult(StrictModel):
    schema_version: Literal["1"] = "1"
    command_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    status: Literal["initialized", "already_initialized"]
    bundle_input_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    source_tree_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    image_reference: str = Field(min_length=1)
    image_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    platform: str
    lock_path: str
    artifact_directory: str
    build_context_path: str | None = None
    warnings: tuple[str, ...] = ()
