from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, field_validator

from task_bundle.models import (
    EvaluationPhase,
    HarnessStatus,
    NormalizedResult,
    StrictModel,
    TestStatus,
)


class ValidationStatus(StrEnum):
    VALID = "valid"
    INVALID_BASELINE = "invalid_baseline"
    INVALID_BASELINE_FLAKY = "invalid_baseline_flaky"
    INVALID_GOLDEN = "invalid_golden"
    INVALID_GOLDEN_FLAKY = "invalid_golden_flaky"
    INFRA_ERROR = "infra_error"


class EvaluationStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


class TestGroup(StrEnum):
    PASS_TO_PASS = "pass_to_pass"
    FAIL_TO_PASS = "fail_to_pass"


class ValidationIdentity(StrictModel):
    schema_version: Literal["1"] = "1"
    validation_id: str = Field(pattern=r"^val_[0-9a-f]{32}$")
    bundle_input_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    task_image_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    runtime_policy_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    harness_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    selector_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    test_patch_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    golden_patch_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    repeat_count: int = Field(gt=0)


class SelectorResult(StrictModel):
    group: TestGroup
    requested_selector: str = Field(min_length=1)
    observed_id: str | None = None
    expected_statuses: tuple[TestStatus, ...]
    actual_status: TestStatus
    duration_ms: int | None = Field(default=None, ge=0)
    message: str | None = None
    matched: bool


class EvaluatorExecution(StrictModel):
    phase: EvaluationPhase
    repeat_index: int = Field(gt=0)
    container_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    evaluation_storage_id: str = Field(min_length=1)
    status: EvaluationStatus
    harness_status: HarnessStatus
    runner_exit_code: int | None = None
    duration_ms: int = Field(ge=0)
    test_patch_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    golden_patch_sha256: str | None = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    prepare_stdout: str = ""
    prepare_stderr: str = ""
    runner_stdout: str = ""
    runner_stderr: str = ""
    patch_log: str = ""
    raw_result: bytes
    result: NormalizedResult
    cleaned_up: bool


class EvaluationRecord(StrictModel):
    phase: EvaluationPhase
    repeat_index: int = Field(gt=0)
    container_id: str
    workspace_id: str
    evaluation_storage_id: str
    status: EvaluationStatus
    harness_status: HarnessStatus
    runner_exit_code: int | None
    duration_ms: int = Field(ge=0)
    test_patch_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    golden_patch_sha256: str | None = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    outcome: Literal["accepted", "rejected"]
    selector_results: tuple[SelectorResult, ...]
    cleaned_up: bool


class PhaseSummary(StrictModel):
    phase: EvaluationPhase
    repeat_count: int = Field(gt=0)
    outcome: Literal["accepted", "rejected", "flaky"]
    pass_to_pass_matched: int = Field(ge=0)
    pass_to_pass_total: int = Field(ge=0)
    fail_to_pass_matched: int = Field(ge=0)
    fail_to_pass_total: int = Field(ge=0)
    duration_ms: int = Field(ge=0)


class ValidationResult(StrictModel):
    schema_version: Literal["1"] = "1"
    command_id: str = Field(min_length=1)
    validation_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    command_status: Literal["succeeded"]
    validation_status: ValidationStatus
    bundle_input_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    task_image_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    runtime_policy_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    harness_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    selector_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    test_patch_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    golden_patch_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    repeat_count: int = Field(gt=0)
    started_at: datetime
    finished_at: datetime
    baseline: PhaseSummary
    golden: PhaseSummary | None = None
    evaluations: tuple[EvaluationRecord, ...]
    artifact_directory: str
    artifact_paths: tuple[str, ...]
    cleanup_complete: bool
    retained_containers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @field_validator("started_at", "finished_at")
    @classmethod
    def timestamps_must_include_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("validation timestamps must include a timezone")
        return value.astimezone(UTC)
