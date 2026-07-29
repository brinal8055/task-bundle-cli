from datetime import datetime

import pytest
from pydantic import TypeAdapter, ValidationError

from task_bundle.models import (
    BaseImageEnvironment,
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
from task_bundle.models import TestStatus as Status


def test_fail_to_pass_defaults_to_failed() -> None:
    selector = FailToPass(selector="tests/test_api.py::test_create")

    assert selector.baseline_statuses == [Status.FAILED]


def test_models_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        FailToPass.model_validate({"selector": "test", "unexpected": True})


def test_base_image_must_be_digest_pinned() -> None:
    with pytest.raises(ValidationError):
        BaseImageEnvironment(type="base_image", image="python:3.12")


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
                command=["/evaluation/harness/run-tests.sh"],
                result_file="/evaluation/output/results.json",
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
