from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from task_bundle.models import StrictModel
from task_bundle.validation.models import PhaseSummary, SelectorResult


class SolverType(StrEnum):
    NOOP = "noop"
    PATCH = "patch"
    COMMAND = "command"


class SolverStatus(StrEnum):
    NOT_RUN = "not_run"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


class RunEvaluationStatus(StrEnum):
    NOT_RUN = "not_run"
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    INFRA_ERROR = "infra_error"


class PatchPolicyStatus(StrEnum):
    NOT_RUN = "not_run"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class RunOptions(StrictModel):
    solver: SolverType
    patch: Path | None = None
    solver_context: Path | None = None
    command: tuple[str, ...] = ()
    keep_containers: bool = False

    @field_validator("command")
    @classmethod
    def command_must_be_structured(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item or "\0" in item for item in value):
            raise ValueError("command arguments must be non-empty and contain no NUL")
        return value

    @model_validator(mode="after")
    def solver_options_must_match_type(self) -> "RunOptions":
        if self.solver == SolverType.NOOP:
            if self.patch is not None or self.solver_context is not None or self.command:
                raise ValueError("noop does not accept patch, context, or command arguments")
        elif self.solver == SolverType.PATCH:
            if self.patch is None:
                raise ValueError("patch solver requires --patch")
            if self.solver_context is not None or self.command:
                raise ValueError("patch solver does not accept context or command arguments")
        elif not self.command:
            raise ValueError("command solver requires argv after --")
        elif self.patch is not None:
            raise ValueError("command solver does not accept --patch")
        return self


class ManifestFile(StrictModel):
    path: str = Field(min_length=1)
    type: Literal["file"] = "file"
    mode: Literal["0644", "0755"]
    size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class ManifestSymlink(StrictModel):
    path: str = Field(min_length=1)
    type: Literal["symlink"] = "symlink"
    target: str = Field(min_length=1)


ManifestEntry = Annotated[
    ManifestFile | ManifestSymlink,
    Field(discriminator="type"),
]


class FilesystemManifest(StrictModel):
    schema_version: Literal["1"] = "1"
    entries: tuple[ManifestEntry, ...]
    entry_count: int = Field(ge=0)
    total_bytes: int = Field(ge=0)
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def entries_must_be_sorted_and_counted(self) -> "FilesystemManifest":
        paths = [entry.path for entry in self.entries]
        if paths != sorted(set(paths)):
            raise ValueError("manifest paths must be sorted and unique")
        if self.entry_count != len(self.entries):
            raise ValueError("manifest entry_count does not match entries")
        return self


class SolverExecutionResult(StrictModel):
    solver_type: SolverType
    argv: tuple[str, ...]
    context_digest: str | None = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    status: SolverStatus
    started_at: datetime
    finished_at: datetime
    duration_ms: int = Field(ge=0)
    exit_code: int | None = None
    timed_out: bool
    container_id: str = Field(min_length=1)
    stdout: str
    stderr: str
    workspace_export_status: Literal["completed", "not_run"]
    cleaned_up: bool

    @field_validator("started_at", "finished_at")
    @classmethod
    def timestamps_must_include_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("solver timestamps must include a timezone")
        return value.astimezone(UTC)


class CandidateTree(StrictModel):
    schema_version: Literal["1"] = "1"
    baseline_tree_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    candidate_tree_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    candidate_patch_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    candidate_patch_size: int = Field(ge=0)
    changed_paths: tuple[str, ...]


class RunResult(StrictModel):
    schema_version: Literal["1"] = "1"
    command_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    command_status: Literal["succeeded"]
    evaluation_status: Literal["resolved", "unresolved"]
    resolved: bool
    bundle_input_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    task_image_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    validation_id: str = Field(pattern=r"^val_[0-9a-f]{32}$")
    selector_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    hidden_patch_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    golden_patch_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    baseline_preflight: PhaseSummary
    solver: SolverExecutionResult
    solver_context_digest: str | None = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    candidate_tree: CandidateTree
    patch_policy_status: Literal["accepted"]
    candidate_summary: PhaseSummary
    candidate_results: tuple[SelectorResult, ...]
    started_at: datetime
    finished_at: datetime
    artifact_directory: str
    artifact_paths: tuple[str, ...]
    cleanup_complete: bool
    retained_containers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @field_validator("started_at", "finished_at")
    @classmethod
    def run_timestamps_must_include_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("run timestamps must include a timezone")
        return value.astimezone(UTC)


class ShowResult(StrictModel):
    schema_version: Literal["1"] = "1"
    command: dict[str, object]
    solver: dict[str, object] | None = None
    evaluations: tuple[dict[str, object], ...] = ()
    tests: tuple[dict[str, object], ...] = ()
    events: tuple[dict[str, object], ...] = ()
    artifacts: tuple[dict[str, object], ...] = ()
