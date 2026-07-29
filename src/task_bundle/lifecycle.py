from pathlib import Path
from typing import NoReturn

from task_bundle import __version__
from task_bundle.database import Database
from task_bundle.errors import ErrorCode, ErrorContext, TaskBundleError
from task_bundle.image.models import InitResult
from task_bundle.image.service import InitOptions, InitService
from task_bundle.validation.models import ValidationResult
from task_bundle.validation.service import ValidationOptions, ValidationService


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


def init_bundle(bundle: Path, options: InitOptions | None = None) -> InitResult:
    database = Database(Path.home() / ".task-bundle" / "task.db")
    service = InitService(database=database, cli_version=__version__)
    return service.run(bundle, options or InitOptions())


def validate_bundle(
    bundle: Path,
    options: ValidationOptions | None = None,
) -> ValidationResult:
    database = Database(Path.home() / ".task-bundle" / "task.db")
    service = ValidationService(database=database, cli_version=__version__)
    return service.run(bundle, options or ValidationOptions())


def run_bundle(bundle: Path) -> NoReturn:
    _pending("run")


def show_command(command_id: str) -> NoReturn:
    _pending("show")
