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
    image: str = Field(pattern=r"^.+@sha256:[0-9a-fA-F]{64}$")
    platform: str = "linux/amd64"
    build: BuildSettings = Field(default_factory=BuildSettings)
    runtime: RuntimeSettings = Field(default_factory=RuntimeSettings)


Environment = Annotated[
    DockerfileEnvironment | BaseImageEnvironment,
    Field(discriminator="type"),
]


class CommandSpec(StrictModel):
    command: list[str] = Field(min_length=1)


class PrepareSpec(CommandSpec):
    network: Literal[False] = False


class RunnerSpec(CommandSpec):
    result_file: str
    result_schema_version: Literal["1"] = "1"


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
    prepare: PrepareSpec
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
    requested_selector: str
    observed_id: str | None = None
    status: TestStatus
    duration_ms: int | None = Field(default=None, ge=0)
    message: str | None = None


class NormalizedResult(StrictModel):
    schema_version: Literal["1"]
    framework: str
    harness_status: HarnessStatus
    collection_succeeded: bool
    execution_started: bool
    command: list[str]
    started_at: datetime
    finished_at: datetime
    exit_code: int | None
    tests: list[TestResult]


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
