import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from task_bundle import __version__, lifecycle
from task_bundle.cli import app
from task_bundle.errors import ErrorCode, ErrorContext, TaskBundleError
from task_bundle.image.models import InitResult
from task_bundle.image.service import InitOptions

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
