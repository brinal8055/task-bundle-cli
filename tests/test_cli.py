from pathlib import Path

import pytest
from typer.testing import CliRunner

from task_bundle import __version__, lifecycle
from task_bundle.cli import app
from task_bundle.errors import ErrorCode, ErrorContext, TaskBundleError

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
    def fail(bundle: Path) -> None:
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
