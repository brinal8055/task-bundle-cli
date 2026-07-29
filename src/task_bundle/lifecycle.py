from pathlib import Path

from pydantic import ValidationError

from task_bundle import __version__
from task_bundle.database import Database
from task_bundle.errors import ErrorCode, ErrorContext, TaskBundleError
from task_bundle.image.models import InitResult
from task_bundle.image.service import InitOptions, InitService
from task_bundle.run.models import RunOptions, RunResult, ShowResult, SolverType
from task_bundle.run.records import RunStore
from task_bundle.run.service import RunService
from task_bundle.validation.models import ValidationResult
from task_bundle.validation.service import ValidationOptions, ValidationService


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


def run_bundle(
    bundle: Path,
    solver: SolverType,
    patch: Path | None,
    solver_context: Path | None,
    command: tuple[str, ...],
    keep_containers: bool,
) -> RunResult:
    try:
        options = RunOptions(
            solver=solver,
            patch=patch,
            solver_context=solver_context,
            command=command,
            keep_containers=keep_containers,
        )
    except ValidationError as error:
        raise TaskBundleError(
            ErrorCode.SOLVER_CONFIGURATION_ERROR,
            "Solver options are invalid.",
            ErrorContext(
                phase="solver-config",
                expected="Options matching the selected noop, patch, or command solver",
                actual="; ".join(item["msg"] for item in error.errors(include_url=False)),
                corrective_action="Use `task run --help` and correct solver-specific options.",
            ),
        ) from error
    database = Database(Path.home() / ".task-bundle" / "task.db")
    return RunService(database=database, cli_version=__version__).run(bundle, options)


def show_command(
    command_id: str,
    include_events: bool = False,
    include_tests: bool = False,
) -> ShowResult:
    database = Database(Path.home() / ".task-bundle" / "task.db")
    return RunStore(database).show(
        command_id,
        include_events=include_events,
        include_tests=include_tests,
    )
