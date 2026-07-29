import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from task_bundle import __version__, lifecycle
from task_bundle.cli import app
from task_bundle.errors import ErrorCode, ErrorContext, TaskBundleError
from task_bundle.image.models import InitResult
from task_bundle.image.service import InitOptions
from task_bundle.models import EvaluationPhase
from task_bundle.validation.models import (
    PhaseSummary,
    ValidationResult,
    ValidationStatus,
)
from task_bundle.validation.service import ValidationOptions

runner = CliRunner()


def test_help_lists_primary_commands() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    for command in ("init", "validate", "run", "show"):
        assert command in result.stdout


def test_version() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == __version__


def test_domain_error_has_stable_exit_code_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(bundle: Path, options: InitOptions) -> None:
        del bundle, options
        raise TaskBundleError(
            ErrorCode.CONFIG_ERROR,
            "Bundle path is invalid.",
            ErrorContext(
                phase="bundle",
                expected="A bundle-contained path",
                actual="Path escapes the bundle",
                corrective_action="Use a relative path inside the bundle.",
                path=Path("evaluation/hidden/test.patch"),
            ),
        )

    monkeypatch.setattr(lifecycle, "init_bundle", fail)
    result = runner.invoke(app, ["init", "bundle"])

    assert result.exit_code == 2
    assert "Bundle path is invalid." in result.stdout
    assert "CONFIG_ERROR" in result.stdout
    assert "Traceback" not in result.stdout


def test_init_help_lists_only_supported_phase_3_options() -> None:
    result = runner.invoke(app, ["init", "--help"])

    assert result.exit_code == 0
    for option in (
        "--rebuild",
        "--no-cache",
        "--platform",
        "--keep-build-context",
        "--json",
        "--no-colour",
    ):
        assert option in result.stdout
    assert "--solver" not in result.stdout
    assert "--remote" not in result.stdout


def test_init_json_output_and_options_are_forwarded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: InitOptions | None = None

    def succeed(bundle: Path, options: InitOptions) -> InitResult:
        nonlocal captured
        del bundle
        captured = options
        return InitResult(
            command_id="cmd_test",
            task_id="task",
            status="initialized",
            bundle_input_digest="sha256:" + "a" * 64,
            source_tree_digest="sha256:" + "b" * 64,
            image_reference="task-bundle/task:tag",
            image_id="sha256:" + "c" * 64,
            platform="linux/arm64",
            lock_path=".task/bundle.lock.json",
            artifact_directory="artifacts/task/cmd_test",
        )

    monkeypatch.setattr(lifecycle, "init_bundle", succeed)
    result = runner.invoke(
        app,
        [
            "init",
            "bundle",
            "--rebuild",
            "--no-cache",
            "--platform",
            "linux/arm64",
            "--keep-build-context",
            "--json",
            "--no-colour",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout)["status"] == "initialized"
    assert captured == InitOptions(
        rebuild=True,
        no_cache=True,
        platform="linux/arm64",
        keep_build_context=True,
    )


def test_init_json_error_is_structured(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(bundle: Path, options: InitOptions) -> None:
        del bundle, options
        raise TaskBundleError(
            ErrorCode.LOCK_MISMATCH,
            "Lock is stale.",
            ErrorContext(
                phase="lock-freshness",
                expected="Current identities",
                actual="bundle_input_digest",
                corrective_action="Run task init --rebuild.",
            ),
        )

    monkeypatch.setattr(lifecycle, "init_bundle", fail)
    result = runner.invoke(app, ["init", "bundle", "--json"])

    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "failed"
    assert payload["error"]["code"] == "LOCK_MISMATCH"
    assert "Traceback" not in result.stdout


def _validation_result(status: ValidationStatus) -> ValidationResult:
    now = datetime.now(UTC)
    return ValidationResult(
        command_id="cmd_validate",
        validation_id="val_" + "a" * 32,
        task_id="task",
        command_status="succeeded",
        validation_status=status,
        bundle_input_digest="sha256:" + "a" * 64,
        task_image_id="sha256:" + "b" * 64,
        runtime_policy_digest="sha256:" + "c" * 64,
        harness_digest="sha256:" + "d" * 64,
        selector_digest="sha256:" + "e" * 64,
        test_patch_sha256="sha256:" + "f" * 64,
        golden_patch_sha256="sha256:" + "0" * 64,
        repeat_count=2,
        started_at=now,
        finished_at=now,
        baseline=PhaseSummary(
            phase=EvaluationPhase.BASELINE,
            repeat_count=2,
            outcome="accepted",
            pass_to_pass_matched=1,
            pass_to_pass_total=1,
            fail_to_pass_matched=1,
            fail_to_pass_total=1,
            duration_ms=10,
        ),
        golden=PhaseSummary(
            phase=EvaluationPhase.GOLDEN,
            repeat_count=2,
            outcome="accepted" if status == ValidationStatus.VALID else "rejected",
            pass_to_pass_matched=1,
            pass_to_pass_total=1,
            fail_to_pass_matched=(
                1 if status == ValidationStatus.VALID else 0
            ),
            fail_to_pass_total=1,
            duration_ms=10,
        ),
        evaluations=(),
        artifact_directory="artifacts/task/cmd_validate",
        artifact_paths=("report.json", "report.md"),
        cleanup_complete=True,
    )


def test_validate_help_lists_only_supported_phase_4_options() -> None:
    result = runner.invoke(app, ["validate", "--help"])

    assert result.exit_code == 0
    for option in ("--repeat", "--keep-containers", "--json", "--no-colour"):
        assert option in result.stdout
    assert "--solver" not in result.stdout
    assert "--candidate" not in result.stdout


def test_validate_json_forwards_options_and_uses_outcome_exit_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: ValidationOptions | None = None

    def validate(bundle: Path, options: ValidationOptions) -> ValidationResult:
        nonlocal captured
        del bundle
        captured = options
        return _validation_result(ValidationStatus.INVALID_GOLDEN)

    monkeypatch.setattr(lifecycle, "validate_bundle", validate)
    result = runner.invoke(
        app,
        [
            "validate",
            "bundle",
            "--repeat",
            "2",
            "--keep-containers",
            "--json",
            "--no-colour",
        ],
    )

    assert result.exit_code == 4
    assert json.loads(result.stdout)["validation_status"] == "invalid_golden"
    assert captured == ValidationOptions(repeat=2, keep_containers=True)
    assert "\x1b[" not in result.stdout


def test_validate_infrastructure_error_exits_three_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(bundle: Path, options: ValidationOptions) -> ValidationResult:
        del bundle, options
        raise TaskBundleError(
            ErrorCode.TEST_RUNNER_ERROR,
            "Runner failed.",
            ErrorContext(
                phase="baseline",
                expected="Structured test results",
                actual="exit 7",
                corrective_action="Inspect runner logs.",
            ),
        )

    monkeypatch.setattr(lifecycle, "validate_bundle", fail)
    result = runner.invoke(app, ["validate", "bundle", "--json"])

    assert result.exit_code == 3
    assert json.loads(result.stdout)["error"]["code"] == "TEST_RUNNER_ERROR"
    assert "Traceback" not in result.stdout
