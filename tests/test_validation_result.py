import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from task_bundle.errors import ErrorCode, TaskBundleError
from task_bundle.models import (
    EvaluationPhase,
    EvaluationPlan,
    FailToPass,
    HarnessStatus,
    NormalizedResult,
    PassToPass,
)
from task_bundle.models import TestResult as ResultItem
from task_bundle.models import TestStatus as ResultStatus
from task_bundle.validation.result import classify_result, load_normalized_result


def _plan(phase: EvaluationPhase = EvaluationPhase.BASELINE) -> EvaluationPlan:
    return EvaluationPlan(
        phase=phase,
        repeat_index=1,
        pass_to_pass=[PassToPass(selector="existing")],
        fail_to_pass=[FailToPass(selector="fixed")],
        timeout_seconds=60,
    )


def _result(
    p2p: ResultStatus = ResultStatus.PASSED,
    f2p: ResultStatus = ResultStatus.FAILED,
    *,
    harness_status: HarnessStatus = HarnessStatus.COMPLETED,
) -> NormalizedResult:
    started = datetime.now(UTC)
    return NormalizedResult(
        schema_version="1",
        framework="synthetic",
        harness_status=harness_status,
        collection_succeeded=True,
        execution_started=True,
        command=["test"],
        started_at=started,
        finished_at=started + timedelta(milliseconds=10),
        exit_code=1,
        tests=[
            ResultItem(requested_selector="existing", status=p2p),
            ResultItem(requested_selector="fixed", status=f2p),
            ResultItem(requested_selector="additional", status=ResultStatus.PASSED),
        ],
    )


def test_baseline_and_golden_selector_semantics() -> None:
    baseline = classify_result(_result(), _plan())
    golden = classify_result(
        _result(f2p=ResultStatus.PASSED),
        _plan(EvaluationPhase.GOLDEN),
    )

    assert all(item.matched for item in baseline)
    assert all(item.matched for item in golden)


@pytest.mark.parametrize(
    "status",
    [ResultStatus.ERROR, ResultStatus.SKIPPED, ResultStatus.XFAILED],
)
def test_baseline_fail_to_pass_rejects_unconfigured_status(status: ResultStatus) -> None:
    classified = classify_result(_result(f2p=status), _plan())

    assert not classified[-1].matched


def test_explicit_baseline_error_is_accepted() -> None:
    plan = _plan().model_copy(
        update={
            "fail_to_pass": [
                FailToPass(
                    selector="fixed",
                    baseline_statuses=[ResultStatus.FAILED, ResultStatus.ERROR],
                )
            ]
        }
    )

    assert classify_result(_result(f2p=ResultStatus.ERROR), plan)[-1].matched


def test_missing_duplicate_and_global_failure_are_infrastructure_errors() -> None:
    missing = _result().model_copy(
        update={
            "tests": [
                ResultItem(
                    requested_selector="existing",
                    status=ResultStatus.PASSED,
                )
            ]
        }
    )
    duplicate = _result().model_copy(
        update={
            "tests": [
                ResultItem(
                    requested_selector="existing",
                    status=ResultStatus.PASSED,
                ),
                ResultItem(
                    requested_selector="fixed",
                    status=ResultStatus.FAILED,
                ),
                ResultItem(
                    requested_selector="fixed",
                    status=ResultStatus.FAILED,
                ),
            ]
        }
    )

    for result in (
        missing,
        duplicate,
        _result(harness_status=HarnessStatus.COLLECTION_FAILED),
        _result(harness_status=HarnessStatus.CRASHED),
    ):
        with pytest.raises(TaskBundleError) as caught:
            classify_result(result, _plan())
        assert caught.value.code == ErrorCode.TEST_RESULT_INCOMPLETE


def test_result_loader_enforces_schema_timestamp_and_message_bounds(tmp_path: Path) -> None:
    result_path = tmp_path / "results.json"
    result_path.write_text(_result().model_dump_json(), encoding="utf-8")

    payload, loaded = load_normalized_result(
        result_path,
        phase=EvaluationPhase.BASELINE,
        repeat_index=1,
    )

    assert payload
    assert loaded.framework == "synthetic"

    raw = json.loads(result_path.read_text())
    raw["started_at"] = "2026-01-01T00:00:00"
    result_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(TaskBundleError) as caught:
        load_normalized_result(
            result_path,
            phase=EvaluationPhase.BASELINE,
            repeat_index=1,
        )
    assert caught.value.code == ErrorCode.TEST_RESULT_SCHEMA_ERROR


def test_result_loader_rejects_missing_malformed_and_non_object(tmp_path: Path) -> None:
    result_path = tmp_path / "results.json"
    with pytest.raises(TaskBundleError) as missing:
        load_normalized_result(
            result_path,
            phase=EvaluationPhase.BASELINE,
            repeat_index=1,
        )
    assert missing.value.code == ErrorCode.TEST_RESULT_MISSING

    for payload in ("{", "[]"):
        result_path.write_text(payload, encoding="utf-8")
        with pytest.raises(TaskBundleError):
            load_normalized_result(
                result_path,
                phase=EvaluationPhase.BASELINE,
                repeat_index=1,
            )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", "2"),
        ("harness_status", "unknown"),
    ],
)
def test_result_loader_rejects_unknown_schema_values(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    path = tmp_path / "results.json"
    raw = _result().model_dump(mode="json")
    raw[field] = value
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(TaskBundleError) as caught:
        load_normalized_result(
            path,
            phase=EvaluationPhase.BASELINE,
            repeat_index=1,
        )

    assert caught.value.code == ErrorCode.TEST_RESULT_SCHEMA_ERROR


def test_result_loader_rejects_negative_duration_and_oversized_message(
    tmp_path: Path,
) -> None:
    path = tmp_path / "results.json"
    for update in (
        {"duration_ms": -1},
        {"message": "x" * 16_385},
    ):
        raw = _result().model_dump(mode="json")
        raw["tests"][0].update(update)
        path.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(TaskBundleError) as caught:
            load_normalized_result(
                path,
                phase=EvaluationPhase.BASELINE,
                repeat_index=1,
            )
        assert caught.value.code == ErrorCode.TEST_RESULT_SCHEMA_ERROR


def test_result_loader_rejects_symlink_result(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_text(_result().model_dump_json(), encoding="utf-8")
    path = tmp_path / "results.json"
    path.symlink_to(target)

    with pytest.raises(TaskBundleError) as caught:
        load_normalized_result(
            path,
            phase=EvaluationPhase.BASELINE,
            repeat_index=1,
        )

    assert caught.value.code == ErrorCode.TEST_RESULT_SCHEMA_ERROR
