from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any


class ErrorCode(StrEnum):
    BUNDLE_NOT_FOUND = "BUNDLE_NOT_FOUND"
    BUNDLE_YAML_ERROR = "BUNDLE_YAML_ERROR"
    BUNDLE_DUPLICATE_KEY = "BUNDLE_DUPLICATE_KEY"
    BUNDLE_SCHEMA_ERROR = "BUNDLE_SCHEMA_ERROR"
    BUNDLE_PATH_ERROR = "BUNDLE_PATH_ERROR"
    BUNDLE_SPECIAL_FILE_ERROR = "BUNDLE_SPECIAL_FILE_ERROR"
    BUNDLE_DIGEST_ERROR = "BUNDLE_DIGEST_ERROR"
    SNAPSHOT_WRITE_ERROR = "SNAPSHOT_WRITE_ERROR"
    SNAPSHOT_READ_ERROR = "SNAPSHOT_READ_ERROR"
    SNAPSHOT_SCHEMA_ERROR = "SNAPSHOT_SCHEMA_ERROR"
    SNAPSHOT_STALE = "SNAPSHOT_STALE"
    CONFIG_ERROR = "CONFIG_ERROR"
    SOURCE_ERROR = "SOURCE_ERROR"
    BUILD_ERROR = "BUILD_ERROR"
    IMAGE_ERROR = "IMAGE_ERROR"
    SMOKE_CHECK_ERROR = "SMOKE_CHECK_ERROR"
    LOCK_MISMATCH = "LOCK_MISMATCH"
    VALIDATION_REQUIRED = "VALIDATION_REQUIRED"
    SOLVER_CONTEXT_ERROR = "SOLVER_CONTEXT_ERROR"
    PATCH_EXTRACTION_ERROR = "PATCH_EXTRACTION_ERROR"
    PATCH_APPLY_ERROR = "PATCH_APPLY_ERROR"
    PATCH_POLICY_ERROR = "PATCH_POLICY_ERROR"
    PATCH_CONFLICT = "PATCH_CONFLICT"
    TEST_PREPARE_ERROR = "TEST_PREPARE_ERROR"
    TEST_RUNNER_ERROR = "TEST_RUNNER_ERROR"
    TEST_PARSE_ERROR = "TEST_PARSE_ERROR"
    TEST_RESULT_INCOMPLETE = "TEST_RESULT_INCOMPLETE"
    BASELINE_GUARDRAIL_ERROR = "BASELINE_GUARDRAIL_ERROR"
    GOLDEN_VALIDATION_ERROR = "GOLDEN_VALIDATION_ERROR"
    SOLVER_ERROR = "SOLVER_ERROR"
    SOLVER_TIMEOUT = "SOLVER_TIMEOUT"
    SOLVER_OUTPUT_ERROR = "SOLVER_OUTPUT_ERROR"
    CONTAINER_ERROR = "CONTAINER_ERROR"
    TIMEOUT = "TIMEOUT"
    DATABASE_ERROR = "DATABASE_ERROR"
    CLEANUP_ERROR = "CLEANUP_ERROR"


@dataclass(frozen=True, slots=True)
class ErrorContext:
    phase: str
    expected: str
    actual: str
    corrective_action: str
    selector: str | None = None
    path: Path | None = None
    artifact: Path | None = None
    details: dict[str, Any] | None = None


class TaskBundleError(Exception):
    def __init__(self, code: ErrorCode, message: str, context: ErrorContext) -> None:
        super().__init__(message)
        self.code = code
        self.context = context

    @property
    def exit_code(self) -> int:
        return exit_code_for_error(self.code)


_CONFIG_CODES = {
    ErrorCode.BUNDLE_NOT_FOUND,
    ErrorCode.BUNDLE_YAML_ERROR,
    ErrorCode.BUNDLE_DUPLICATE_KEY,
    ErrorCode.BUNDLE_SCHEMA_ERROR,
    ErrorCode.BUNDLE_PATH_ERROR,
    ErrorCode.BUNDLE_SPECIAL_FILE_ERROR,
    ErrorCode.SNAPSHOT_READ_ERROR,
    ErrorCode.SNAPSHOT_SCHEMA_ERROR,
    ErrorCode.SNAPSHOT_STALE,
    ErrorCode.CONFIG_ERROR,
    ErrorCode.LOCK_MISMATCH,
    ErrorCode.VALIDATION_REQUIRED,
}
_VALIDATION_CODES = {
    ErrorCode.BASELINE_GUARDRAIL_ERROR,
    ErrorCode.GOLDEN_VALIDATION_ERROR,
}
_SOLVER_CODES = {
    ErrorCode.SOLVER_CONTEXT_ERROR,
    ErrorCode.SOLVER_ERROR,
    ErrorCode.SOLVER_TIMEOUT,
    ErrorCode.SOLVER_OUTPUT_ERROR,
}
_PATCH_CODES = {
    ErrorCode.PATCH_EXTRACTION_ERROR,
    ErrorCode.PATCH_APPLY_ERROR,
    ErrorCode.PATCH_POLICY_ERROR,
    ErrorCode.PATCH_CONFLICT,
}


def exit_code_for_error(code: ErrorCode) -> int:
    if code in _CONFIG_CODES:
        return 2
    if code in _VALIDATION_CODES:
        return 4
    if code in _SOLVER_CODES:
        return 5
    if code in _PATCH_CODES:
        return 6
    return 3
