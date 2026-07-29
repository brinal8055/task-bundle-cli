from datetime import UTC, datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PositiveFloat,
    PositiveInt,
    field_validator,
    model_validator,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CommandStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class HarnessStatus(StrEnum):
    COMPLETED = "completed"
    COLLECTION_FAILED = "collection_failed"
    CRASHED = "crashed"
    TIMED_OUT = "timed_out"
    RESULT_MISSING = "result_missing"
    PARSER_FAILED = "parser_failed"
    PREPARE_FAILED = "prepare_failed"


class TestStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"
    XFAILED = "xfailed"
    XPASSED = "xpassed"
    TIMEOUT = "timeout"
    MISSING = "missing"


class EvaluationPhase(StrEnum):
    BASELINE = "baseline"
    GOLDEN = "golden"
    CANDIDATE = "candidate"


class TaskIdentity(StrictModel):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)


class Provenance(StrictModel):
    dataset: str = Field(min_length=1)
    dataset_revision: str = Field(min_length=1)
    instance_id: str = Field(min_length=1)
    source_record_sha256: str = Field(pattern=r"^sha256:[0-9a-fA-F]{64}$")
    imported_at: datetime

    @field_validator("imported_at")
    @classmethod
    def imported_at_must_include_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("imported_at must include a timezone")
        return value.astimezone(UTC)


class Repository(StrictModel):
    url: str = Field(min_length=1)
    commit: str = Field(pattern=r"^[0-9a-fA-F]{40}$")
    submodules: Literal[False] = False


class PublicFiles(StrictModel):
    description: str
    requirements: str | None = None
    interface: str | None = None


class BuildSettings(StrictModel):
    timeout_seconds: PositiveInt = 1800
    network: bool = True
    no_cache: bool = False
    build_args: dict[str, str] = Field(default_factory=dict)


class RuntimeSettings(StrictModel):
    working_directory: str = "/workspace/repo"
    user: str = "1000:1000"
    network: Literal["none"] = "none"
    timeout_seconds: PositiveInt = 1800
    cpus: PositiveFloat = 2
    memory_mb: PositiveInt = 4096
    pids_limit: PositiveInt = 256
    read_only_root: Literal[True] = True
    tmpfs: list[str] = Field(default_factory=lambda: ["/tmp:size=512m"])


class DockerfileEnvironment(StrictModel):
    type: Literal["dockerfile"]
    dockerfile: str
    context: str
    platform: str = "linux/amd64"
    build: BuildSettings = Field(default_factory=BuildSettings)
    runtime: RuntimeSettings = Field(default_factory=RuntimeSettings)


class BaseImageEnvironment(StrictModel):
    type: Literal["base_image"]
    image: str = Field(pattern=r"^.+@sha256:[0-9a-f]{64}$")
    platform: str = "linux/amd64"
    build: BuildSettings = Field(default_factory=BuildSettings)
    runtime: RuntimeSettings = Field(default_factory=RuntimeSettings)


Environment = Annotated[
    DockerfileEnvironment | BaseImageEnvironment,
    Field(discriminator="type"),
]


class CommandSpec(StrictModel):
    command: list[str] = Field(min_length=1)

    @field_validator("command")
    @classmethod
    def command_must_be_safe_argv(cls, value: list[str]) -> list[str]:
        if any(not item or "\0" in item for item in value):
            raise ValueError("command arguments must be non-empty and contain no NUL")
        return value


class PrepareSpec(CommandSpec):
    network: Literal[False] = False


class RunnerSpec(CommandSpec):
    result_file: str
    result_schema_version: Literal["1"] = "1"

    @field_validator("result_file")
    @classmethod
    def result_file_must_be_inside_output(cls, value: str) -> str:
        path = PurePosixPath(value)
        output = PurePosixPath("/evaluation/output")
        try:
            relative = path.relative_to(output)
        except ValueError as error:
            raise ValueError("result_file must be inside /evaluation/output") from error
        if (
            not path.is_absolute()
            or ".." in path.parts
            or path.as_posix() != value
            or relative.as_posix() in {"", "."}
        ):
            raise ValueError("result_file must be a normalized output file path")
        return value


class PassToPass(StrictModel):
    selector: str = Field(min_length=1)


class FailToPass(PassToPass):
    baseline_statuses: list[TestStatus] = Field(
        default_factory=lambda: [TestStatus.FAILED],
        min_length=1,
    )

    @field_validator("baseline_statuses")
    @classmethod
    def baseline_statuses_must_be_intentional(
        cls, statuses: list[TestStatus]
    ) -> list[TestStatus]:
        allowed = {TestStatus.FAILED, TestStatus.ERROR}
        if not set(statuses) <= allowed:
            raise ValueError("baseline statuses may contain only 'failed' and 'error'")
        if len(statuses) != len(set(statuses)):
            raise ValueError("baseline statuses must be unique")
        return statuses


class EvaluationConfig(StrictModel):
    test_patch: str
    golden_patch: str
    prepare: PrepareSpec | None = None
    runner: RunnerSpec
    pass_to_pass: list[PassToPass] = Field(min_length=1)
    fail_to_pass: list[FailToPass] = Field(min_length=1)
    repeat: PositiveInt = 1

    @model_validator(mode="after")
    def selectors_must_be_unique(self) -> "EvaluationConfig":
        selectors = [
            *(item.selector for item in self.pass_to_pass),
            *(item.selector for item in self.fail_to_pass),
        ]
        if len(selectors) != len(set(selectors)):
            raise ValueError("test selectors must be unique across evaluation groups")
        return self


class SolverConfig(StrictModel):
    timeout_seconds: PositiveInt = 1800
    max_patch_bytes: PositiveInt = 5_242_880
    max_changed_files: PositiveInt = 200
    max_context_bytes: PositiveInt = 10_485_760
    max_context_files: PositiveInt = 500
    allow_network: Literal[False] = False


class TaskConfig(StrictModel):
    schema_version: Literal["1"]
    task: TaskIdentity
    provenance: Provenance | None = None
    repository: Repository
    public: PublicFiles
    environment: Environment
    evaluation: EvaluationConfig
    solver: SolverConfig = Field(default_factory=SolverConfig)


class TestResult(StrictModel):
    requested_selector: str = Field(min_length=1)
    observed_id: str | None = None
    status: TestStatus
    duration_ms: int | None = Field(default=None, ge=0)
    message: str | None = None


class NormalizedResult(StrictModel):
    schema_version: Literal["1"]
    framework: str = Field(min_length=1)
    harness_status: HarnessStatus
    collection_succeeded: bool
    execution_started: bool
    command: list[str] = Field(min_length=1)
    started_at: datetime
    finished_at: datetime
    exit_code: int | None
    tests: list[TestResult]

    @field_validator("started_at", "finished_at")
    @classmethod
    def timestamps_must_include_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("result timestamps must include a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def finished_at_must_not_precede_start(self) -> "NormalizedResult":
        if self.finished_at < self.started_at:
            raise ValueError("finished_at must not precede started_at")
        return self


class EvaluationPlan(StrictModel):
    schema_version: Literal["1"] = "1"
    phase: EvaluationPhase
    repeat_index: PositiveInt
    pass_to_pass: list[PassToPass]
    fail_to_pass: list[FailToPass]
    timeout_seconds: PositiveInt


class InputManifestEntry(StrictModel):
    path: str = Field(min_length=1)
    type: Literal["file"] = "file"
    mode: Literal["0644", "0755"]
    size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @field_validator("path")
    @classmethod
    def path_must_be_normalized_relative_posix(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or path.as_posix() != value:
            raise ValueError("manifest path must be a normalized relative POSIX path")
        return value


class EvaluationInputDigests(StrictModel):
    test_patch_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    golden_patch_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    harness_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    selectors_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class BundleSnapshot(StrictModel):
    schema_version: Literal["1"] = "1"
    task_id: str = Field(min_length=1)
    bundle_input_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    cli_version: str = Field(min_length=1)
    created_at: datetime
    provenance: Provenance | None
    canonical_config_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    input_manifest: tuple[InputManifestEntry, ...]
    evaluation_inputs: EvaluationInputDigests

    @field_validator("created_at")
    @classmethod
    def created_at_must_include_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must include a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def manifest_must_be_sorted_and_unique(self) -> "BundleSnapshot":
        paths = [entry.path for entry in self.input_manifest]
        if paths != sorted(set(paths)):
            raise ValueError("input manifest paths must be sorted and unique")
        return self


class SourceRequest(StrictModel):
    repository_url: str
    commit: str
    timeout_seconds: PositiveInt = 300

    @field_validator("repository_url")
    @classmethod
    def repository_url_must_be_public_https(cls, value: str) -> str:
        from task_bundle.source.validation import normalize_repository_url

        return normalize_repository_url(value)

    @field_validator("commit")
    @classmethod
    def commit_must_be_full_sha(cls, value: str) -> str:
        from task_bundle.source.validation import normalize_commit_sha

        return normalize_commit_sha(value)


class SourceFileEntry(StrictModel):
    path: str = Field(min_length=1)
    type: Literal["file"] = "file"
    mode: Literal["0644", "0755"]
    size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @field_validator("path")
    @classmethod
    def path_must_be_normalized(cls, value: str) -> str:
        return _normalized_relative_posix_path(value)


class SourceSymlinkEntry(StrictModel):
    path: str = Field(min_length=1)
    type: Literal["symlink"] = "symlink"
    target: str = Field(min_length=1)

    @field_validator("path")
    @classmethod
    def path_must_be_normalized(cls, value: str) -> str:
        return _normalized_relative_posix_path(value)

    @model_validator(mode="after")
    def target_must_remain_inside_source(self) -> "SourceSymlinkEntry":
        from task_bundle.source.validation import validate_symlink_target

        validate_symlink_target(self.path, self.target)
        return self


SourceManifestEntry = Annotated[
    SourceFileEntry | SourceSymlinkEntry,
    Field(discriminator="type"),
]


class SourceManifest(StrictModel):
    schema_version: Literal["1"] = "1"
    entries: tuple[SourceManifestEntry, ...]

    @model_validator(mode="after")
    def entries_must_be_sorted_and_unique(self) -> "SourceManifest":
        paths = [entry.path for entry in self.entries]
        if paths != sorted(set(paths)):
            raise ValueError("source manifest paths must be sorted and unique")
        return self


class ResolvedSource(StrictModel):
    schema_version: Literal["1"] = "1"
    repository_url: str
    requested_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    resolved_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    tree_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    source_tree_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    source_entry_count: int = Field(ge=0)
    source_total_bytes: int = Field(ge=0)
    symlink_count: int = Field(ge=0)
    executable_file_count: int = Field(ge=0)
    git_executable: str = Field(min_length=1)
    git_version: str = Field(min_length=1)
    created_at: datetime

    @field_validator("repository_url")
    @classmethod
    def repository_url_must_be_public_https(cls, value: str) -> str:
        from task_bundle.source.validation import normalize_repository_url

        return normalize_repository_url(value)

    @field_validator("created_at")
    @classmethod
    def created_at_must_include_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must include a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def resolved_commit_must_match_request(self) -> "ResolvedSource":
        if self.resolved_commit != self.requested_commit:
            raise ValueError("resolved commit must equal requested commit")
        return self


def _normalized_relative_posix_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or ".." in path.parts
        or "\\" in value
        or path.as_posix() != value
        or value in {"", "."}
    ):
        raise ValueError("path must be a normalized relative POSIX path")
    if ".git" in path.parts:
        raise ValueError(".git is not allowed in source manifests")
    return value
