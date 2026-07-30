import json
import stat
from pathlib import Path
from typing import NoReturn

from pydantic import ValidationError

from task_bundle.errors import ErrorCode, ErrorContext, TaskBundleError
from task_bundle.models import (
    EvaluationPhase,
    EvaluationPlan,
    HarnessStatus,
    NormalizedResult,
    TestResult,
    TestStatus,
)
from task_bundle.validation.models import SelectorResult, TestGroup

MAX_RESULT_BYTES = 5_242_880
MAX_MESSAGE_CHARACTERS = 16_384


def load_normalized_result(
    path: Path,
    *,
    phase: EvaluationPhase,
    repeat_index: int,
) -> tuple[bytes, NormalizedResult]:
    """Load normalized output from a host-controlled trusted-parser artifact."""
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        _result_error(
            ErrorCode.TEST_RESULT_MISSING,
            "Test runner did not produce its result file.",
            "A regular normalized results.json file",
            "The configured result file is missing.",
            phase,
            repeat_index,
            path,
        )
    except OSError as error:
        _result_error(
            ErrorCode.TEST_RESULT_MISSING,
            "Test result file could not be inspected.",
            "A readable regular normalized result file",
            str(error),
            phase,
            repeat_index,
            path,
        )
    if not stat.S_ISREG(metadata.st_mode):
        _result_error(
            ErrorCode.TEST_RESULT_SCHEMA_ERROR,
            "Test result path is not a regular file.",
            "A regular host-controlled trusted-parser result artifact",
            "The result path is a symlink or special file.",
            phase,
            repeat_index,
            path,
        )
    if metadata.st_size > MAX_RESULT_BYTES:
        _result_error(
            ErrorCode.TEST_RESULT_TOO_LARGE,
            "Test result file exceeds the size limit.",
            f"At most {MAX_RESULT_BYTES} bytes",
            f"{metadata.st_size} bytes",
            phase,
            repeat_index,
            path,
        )
    try:
        payload = path.read_bytes()
    except OSError as error:
        _result_error(
            ErrorCode.TEST_PARSE_ERROR,
            "Test result file could not be read.",
            "Readable UTF-8 JSON matching normalized result schema 1",
            str(error),
            phase,
            repeat_index,
            path,
        )
    return parse_normalized_result(
        payload,
        phase=phase,
        repeat_index=repeat_index,
        source=path,
    )


def parse_normalized_result(
    payload: bytes,
    *,
    phase: EvaluationPhase,
    repeat_index: int,
    source: Path,
) -> tuple[bytes, NormalizedResult]:
    if len(payload) > MAX_RESULT_BYTES:
        _result_error(
            ErrorCode.TEST_RESULT_TOO_LARGE,
            "Trusted parser output exceeds the size limit.",
            f"At most {MAX_RESULT_BYTES} bytes",
            f"{len(payload)} bytes",
            phase,
            repeat_index,
            source,
        )
    try:
        raw = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        _result_error(
            ErrorCode.TEST_PARSE_ERROR,
            "Trusted parser output is malformed.",
            "UTF-8 JSON matching normalized result schema 1",
            str(error),
            phase,
            repeat_index,
            source,
        )
    if not isinstance(raw, dict):
        _result_error(
            ErrorCode.TEST_RESULT_SCHEMA_ERROR,
            "Test result root has an unexpected shape.",
            "A JSON object",
            type(raw).__name__,
            phase,
            repeat_index,
            source,
        )
    try:
        result = NormalizedResult.model_validate(raw)
    except ValidationError as error:
        _result_error(
            ErrorCode.TEST_RESULT_SCHEMA_ERROR,
            "Test result does not match normalized schema 1.",
            "A complete strict normalized test result",
            f"{error.error_count()} validation error(s)",
            phase,
            repeat_index,
            source,
            details={"errors": json.loads(error.json(include_url=False))},
        )
    for test in result.tests:
        if test.message is not None and len(test.message) > MAX_MESSAGE_CHARACTERS:
            _result_error(
                ErrorCode.TEST_RESULT_SCHEMA_ERROR,
                "Test result message exceeds the size limit.",
                f"At most {MAX_MESSAGE_CHARACTERS} characters",
                f"{len(test.message)} characters",
                phase,
                repeat_index,
                source,
                selector=test.requested_selector,
            )
    return payload, result


def classify_result(
    result: NormalizedResult,
    plan: EvaluationPlan,
) -> tuple[SelectorResult, ...]:
    if (
        result.harness_status != HarnessStatus.COMPLETED
        or not result.collection_succeeded
        or not result.execution_started
    ):
        _incomplete(
            "Harness did not complete collection and execution.",
            plan,
            actual=result.harness_status.value,
        )
    requested = {
        *(item.selector for item in plan.pass_to_pass),
        *(item.selector for item in plan.fail_to_pass),
    }
    observed: dict[str, TestResult] = {}
    duplicates: set[str] = set()
    for test in result.tests:
        if test.requested_selector not in requested:
            continue
        if test.requested_selector in observed:
            duplicates.add(test.requested_selector)
        observed[test.requested_selector] = test
    if duplicates:
        _incomplete(
            "Requested selectors appear more than once.",
            plan,
            actual=", ".join(sorted(duplicates)),
        )
    missing = requested - observed.keys()
    if missing:
        _incomplete(
            "Requested selectors are missing from the result.",
            plan,
            actual=", ".join(sorted(missing)),
        )
    classified: list[SelectorResult] = []
    for item in plan.pass_to_pass:
        test = observed[item.selector]
        classified.append(
            _selector_record(
                TestGroup.PASS_TO_PASS,
                test,
                (TestStatus.PASSED,),
            )
        )
    for item in plan.fail_to_pass:
        test = observed[item.selector]
        expected = (
            tuple(item.baseline_statuses)
            if plan.phase == EvaluationPhase.BASELINE
            else (TestStatus.PASSED,)
        )
        classified.append(_selector_record(TestGroup.FAIL_TO_PASS, test, expected))
    return tuple(classified)


def _selector_record(
    group: TestGroup,
    test: TestResult,
    expected: tuple[TestStatus, ...],
) -> SelectorResult:
    return SelectorResult(
        group=group,
        requested_selector=test.requested_selector,
        observed_id=test.observed_id,
        expected_statuses=expected,
        actual_status=test.status,
        duration_ms=test.duration_ms,
        message=test.message,
        matched=test.status in expected,
    )


def _incomplete(message: str, plan: EvaluationPlan, actual: str) -> NoReturn:
    raise TaskBundleError(
        ErrorCode.TEST_RESULT_INCOMPLETE,
        message,
        ErrorContext(
            phase=plan.phase.value,
            expected="Every requested selector exactly once with a completed harness",
            actual=actual,
            corrective_action="Correct the task-owned harness result mapping.",
            details={"repeat_index": plan.repeat_index},
        ),
    )


def _result_error(
    code: ErrorCode,
    message: str,
    expected: str,
    actual: str,
    phase: EvaluationPhase,
    repeat_index: int,
    path: Path,
    *,
    selector: str | None = None,
    details: dict[str, object] | None = None,
) -> NoReturn:
    context_details: dict[str, object] = {"repeat_index": repeat_index}
    context_details.update(details or {})
    raise TaskBundleError(
        code,
        message,
        ErrorContext(
            phase=phase.value,
            expected=expected,
            actual=actual[:2000],
            corrective_action="Inspect the phase artifacts and correct the evaluation harness.",
            selector=selector,
            path=path,
            details=context_details,
        ),
    )
