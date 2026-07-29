from pathlib import Path
from typing import NoReturn

from task_bundle.errors import ErrorCode, ErrorContext, TaskBundleError


def _pending(command: str) -> NoReturn:
    raise TaskBundleError(
        code=ErrorCode.CONFIG_ERROR,
        message=f"The {command} lifecycle is not implemented yet.",
        context=ErrorContext(
            phase=command,
            expected=f"Phase implementing the {command} lifecycle",
            actual="Phase 0 foundation",
            corrective_action="Complete the corresponding implementation phase.",
        ),
    )


def init_bundle(bundle: Path) -> NoReturn:
    _pending("init")


def validate_bundle(bundle: Path) -> NoReturn:
    _pending("validate")


def run_bundle(bundle: Path) -> NoReturn:
    _pending("run")


def show_command(command_id: str) -> NoReturn:
    _pending("show")
