from datetime import UTC, datetime

import pytest
from pydantic import TypeAdapter, ValidationError

from task_bundle.models import (
    BaseImageEnvironment,
    CapturedTestExecution,
    CapturedTestExecutions,
    DockerfileEnvironment,
    Environment,
    EvaluationConfig,
    FailToPass,
    PassToPass,
    PrepareSpec,
    Provenance,
    RunnerSpec,
    RuntimeSettings,
)
from task_bundle.models import (
    TestExecutionPlan as ExecutionPlan,
)
from task_bundle.models import TestStatus as Status


def test_fail_to_pass_defaults_to_failed() -> None:
    selector = FailToPass(selector="tests/test_api.py::test_create")

    assert selector.baseline_statuses == [Status.FAILED]


def test_models_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        FailToPass.model_validate({"selector": "test", "unexpected": True})


def test_base_image_must_be_digest_pinned() -> None:
    for image in ("python:3.12", f"python@sha256:{'A' * 64}"):
        with pytest.raises(ValidationError):
            BaseImageEnvironment(type="base_image", image=image)


def test_environment_types_have_distinct_requirements() -> None:
    dockerfile = DockerfileEnvironment(
        type="dockerfile",
        dockerfile="environment/Dockerfile",
        context="environment/context",
    )

    assert dockerfile.runtime.network == "none"


def test_invalid_environment_union_is_rejected() -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(Environment).validate_python(
            {"type": "dockerfile", "image": f"python@sha256:{'a' * 64}"}
        )


def test_duplicate_selectors_are_rejected() -> None:
    with pytest.raises(ValidationError):
        EvaluationConfig(
            test_patch="evaluation/hidden/test.patch",
            golden_patch="evaluation/hidden/golden.patch",
            prepare=PrepareSpec(command=["/evaluation/harness/prepare.sh"]),
            runner=RunnerSpec(
                build_plan=["/evaluation/harness/build-plan"],
                parse_result=["/evaluation/harness/parse-result"],
                adapter_contract_version="2",
            ),
            pass_to_pass=[PassToPass(selector="tests/test_api.py::test_create")],
            fail_to_pass=[FailToPass(selector="tests/test_api.py::test_create")],
        )


def test_invalid_resource_limit_is_rejected() -> None:
    with pytest.raises(ValidationError):
        RuntimeSettings(memory_mb=0)


def test_invalid_baseline_status_is_rejected() -> None:
    with pytest.raises(ValidationError):
        FailToPass(selector="test", baseline_statuses=[Status.PASSED])


def test_provenance_requires_sha256_and_timezone() -> None:
    with pytest.raises(ValidationError):
        Provenance(
            dataset="dataset",
            dataset_revision="revision",
            instance_id="instance",
            source_record_sha256="not-a-hash",
            imported_at=datetime(2026, 7, 29),
        )


def test_provenance_timestamp_is_normalized_to_utc() -> None:
    provenance = Provenance(
        dataset="dataset",
        dataset_revision="revision",
        instance_id="instance",
        source_record_sha256=f"sha256:{'a' * 64}",
        imported_at=datetime.fromisoformat("2026-07-29T05:30:00+05:30"),
    )

    assert '"imported_at":"2026-07-29T00:00:00Z"' in provenance.model_dump_json()


def test_models_are_immutable() -> None:
    selector = PassToPass(selector="test")

    with pytest.raises(ValidationError):
        selector.selector = "changed"


def _execution(
    execution_id: str,
    selectors: list[str],
    *,
    timeout_seconds: int = 60,
) -> dict[str, object]:
    return {
        "execution_id": execution_id,
        "requested_selectors": selectors,
        "argv": ["pytest", *selectors],
        "timeout_seconds": timeout_seconds,
    }


def test_execution_plan_v2_supports_grouped_and_separate_units() -> None:
    grouped = ExecutionPlan.model_validate(
        {
            "schema_version": "2",
            "executions": [_execution("group", ["one", "two"])],
        }
    )
    separate = ExecutionPlan.model_validate(
        {
            "schema_version": "2",
            "executions": [
                _execution("one", ["one"]),
                _execution("two", ["two"]),
            ],
        }
    )

    assert grouped.executions[0].requested_selectors == ["one", "two"]
    assert len(separate.executions) == 2


@pytest.mark.parametrize(
    "payload",
    [
        {
            "schema_version": "1",
            "executions": [_execution("one", ["one"])],
        },
        {
            "schema_version": "2",
            "executions": [
                _execution("duplicate", ["one"]),
                _execution("duplicate", ["two"]),
            ],
        },
        {
            "schema_version": "2",
            "executions": [
                _execution("one", ["one"]),
                _execution("two", ["one"]),
            ],
        },
        {
            "schema_version": "2",
            "executions": [_execution("empty-selectors", [])],
        },
        {
            "schema_version": "2",
            "executions": [
                {
                    **_execution("empty-argv", ["one"]),
                    "argv": [],
                }
            ],
        },
        {
            "schema_version": "2",
            "executions": [_execution("bad-timeout", ["one"], timeout_seconds=0)],
        },
        {
            "schema_version": "2",
            "executions": [
                {
                    **_execution("unknown", ["one"]),
                    "unknown": True,
                }
            ],
        },
    ],
)
def test_execution_plan_v2_rejects_invalid_shapes(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ExecutionPlan.model_validate(payload)


def test_captured_execution_schema_is_strict_and_consistent() -> None:
    now = datetime.now(UTC)
    execution = CapturedTestExecution(
        execution_id="group",
        requested_selectors=["one", "two"],
        argv=["pytest", "one", "two"],
        started_at=now,
        finished_at=now,
        duration_ms=0,
        exit_code=0,
        timed_out=False,
        stdout="",
        stderr="",
        stdout_truncated=False,
        stderr_truncated=False,
    )
    captured = CapturedTestExecutions(executions=[execution])

    assert captured.executions[0].execution_id == "group"
    with pytest.raises(ValidationError):
        CapturedTestExecution.model_validate(
            {
                **execution.model_dump(mode="json"),
                "timed_out": True,
                "exit_code": 0,
            }
        )
    with pytest.raises(ValidationError):
        CapturedTestExecutions(executions=[execution, execution])
