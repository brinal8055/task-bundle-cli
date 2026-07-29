import json
from collections.abc import Callable
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from task_bundle import __version__, lifecycle
from task_bundle.errors import TaskBundleError
from task_bundle.image.models import InitResult
from task_bundle.image.service import InitOptions
from task_bundle.run.models import RunResult, ShowResult, SolverType
from task_bundle.validation.models import ValidationResult, ValidationStatus
from task_bundle.validation.service import ValidationOptions

app = typer.Typer(
    name="task",
    help="Build and evaluate reproducible coding-agent task bundles.",
    no_args_is_help=True,
)
console = Console()


def version_callback(value: bool) -> None:
    if value:
        console.print(__version__)
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool | None,
        typer.Option("--version", callback=version_callback, is_eager=True, help="Show version."),
    ] = None,
) -> None:
    """Build and evaluate reproducible coding-agent task bundles."""


def _render_error(error: TaskBundleError, output: Console) -> None:
    output.print(f"[bold red]{error}[/bold red]")
    output.print(f"\n[bold]Code:[/bold] {error.code}")
    output.print(f"[bold]Phase:[/bold] {error.context.phase}")
    output.print(f"[bold]Expected:[/bold] {error.context.expected}")
    output.print(f"[bold]Actual:[/bold] {error.context.actual}")
    if error.context.path is not None:
        output.print(f"[bold]Path:[/bold] {error.context.path}")
    if error.context.artifact is not None:
        output.print(f"[bold]Artifact:[/bold] {error.context.artifact}")
    output.print(f"[bold]Fix:[/bold] {error.context.corrective_action}")


def _invoke[ResultT](
    operation: Callable[..., ResultT],
    *args: object,
    output: Console = console,
    json_errors: bool = False,
) -> ResultT:
    try:
        return operation(*args)
    except TaskBundleError as error:
        if json_errors:
            typer.echo(
                json.dumps(
                    {
                        "status": "failed",
                        "error": {
                            "code": error.code.value,
                            "message": str(error),
                            "phase": error.context.phase,
                            "expected": error.context.expected,
                            "actual": error.context.actual,
                            "corrective_action": error.context.corrective_action,
                        },
                    },
                    sort_keys=True,
                )
            )
        else:
            _render_error(error, output)
        raise typer.Exit(code=error.exit_code) from None
    except KeyboardInterrupt:
        raise typer.Exit(code=130) from None


@app.command()
def init(
    bundle: Annotated[Path, typer.Argument(help="Path to the task bundle.")],
    rebuild: Annotated[
        bool,
        typer.Option("--rebuild", help="Replace a stale or current image and lock."),
    ] = False,
    no_cache: Annotated[
        bool,
        typer.Option("--no-cache", help="Disable Docker build cache."),
    ] = False,
    platform: Annotated[
        str | None,
        typer.Option("--platform", help="Override the configured Docker platform."),
    ] = None,
    keep_build_context: Annotated[
        bool,
        typer.Option(
            "--keep-build-context",
            help="Keep the generated context under .task/build-contexts/.",
        ),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit a machine-readable JSON result."),
    ] = False,
    no_colour: Annotated[
        bool,
        typer.Option("--no-colour", help="Disable coloured output."),
    ] = False,
) -> None:
    """Validate a bundle, build its task image, and write its lockfile."""
    output = Console(no_color=no_colour)
    result = _invoke(
        lifecycle.init_bundle,
        bundle,
        InitOptions(
            rebuild=rebuild,
            no_cache=no_cache,
            platform=platform,
            keep_build_context=keep_build_context,
        ),
        output=output,
        json_errors=json_output,
    )
    if json_output:
        typer.echo(result.model_dump_json(indent=2))
    else:
        _render_init_result(result, output)


def _render_init_result(result: InitResult, output: Console) -> None:
    status = (
        "Task image initialized."
        if result.status == "initialized"
        else "Task image and lock are already current."
    )
    output.print(f"[bold green]{status}[/bold green]")
    output.print(f"Command: {result.command_id}")
    output.print(f"Image: {result.image_reference}")
    output.print(f"Image ID: {result.image_id}")
    output.print(f"Platform: {result.platform}")
    output.print(f"Lock: {result.lock_path}")
    output.print(f"Artifacts: {result.artifact_directory}")
    if result.build_context_path is not None:
        output.print(f"Build context: {result.build_context_path}")
    for warning in result.warnings:
        output.print(f"[yellow]Warning: {warning}[/yellow]")


@app.command()
def validate(
    bundle: Annotated[Path, typer.Argument(help="Path to the task bundle.")],
    repeat: Annotated[
        int | None,
        typer.Option("--repeat", min=1, help="Override the configured repeat count."),
    ] = None,
    keep_containers: Annotated[
        bool,
        typer.Option(
            "--keep-containers",
            help="Retain evaluator containers and hidden inputs for debugging.",
        ),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit a machine-readable JSON result."),
    ] = False,
    no_colour: Annotated[
        bool,
        typer.Option("--no-colour", help="Disable coloured output."),
    ] = False,
) -> None:
    """Validate baseline and golden task behavior."""
    output = Console(no_color=no_colour)
    result = _invoke(
        lifecycle.validate_bundle,
        bundle,
        ValidationOptions(repeat=repeat, keep_containers=keep_containers),
        output=output,
        json_errors=json_output,
    )
    if json_output:
        typer.echo(result.model_dump_json(indent=2))
    else:
        _render_validation_result(result, output)
    if result.validation_status != ValidationStatus.VALID:
        raise typer.Exit(code=4)


def _render_validation_result(result: ValidationResult, output: Console) -> None:
    colour = "green" if result.validation_status == ValidationStatus.VALID else "red"
    output.print(
        f"[bold {colour}]Validation: {result.validation_status.value.upper()}[/bold {colour}]"
    )
    output.print(
        "Baseline: "
        f"P2P {result.baseline.pass_to_pass_matched}/"
        f"{result.baseline.pass_to_pass_total}, "
        f"F2P {result.baseline.fail_to_pass_matched}/"
        f"{result.baseline.fail_to_pass_total}"
    )
    if result.golden is not None:
        output.print(
            "Golden: "
            f"P2P {result.golden.pass_to_pass_matched}/"
            f"{result.golden.pass_to_pass_total}, "
            f"F2P {result.golden.fail_to_pass_matched}/"
            f"{result.golden.fail_to_pass_total}"
        )
    output.print(f"Command: {result.command_id}")
    output.print(f"Validation: {result.validation_id}")
    output.print(f"Artifacts: {result.artifact_directory}")
    for warning in result.warnings:
        output.print(f"[yellow]Warning: {warning}[/yellow]")


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def run(
    ctx: typer.Context,
    bundle: Annotated[Path, typer.Argument(help="Path to the task bundle.")],
    solver: Annotated[
        SolverType,
        typer.Option("--solver", help="Solver type: noop, patch, or command."),
    ],
    patch: Annotated[
        Path | None,
        typer.Option("--patch", help="Candidate patch for the patch solver."),
    ] = None,
    solver_context: Annotated[
        Path | None,
        typer.Option(
            "--solver-context",
            help="Read-only context directory for the command solver.",
        ),
    ] = None,
    keep_containers: Annotated[
        bool,
        typer.Option(
            "--keep-containers",
            help="Retain solver and evaluator containers for debugging.",
        ),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit a machine-readable JSON result."),
    ] = False,
    no_colour: Annotated[
        bool,
        typer.Option("--no-colour", help="Disable coloured output."),
    ] = False,
) -> None:
    """Run a solver and evaluate its candidate patch."""
    output = Console(no_color=no_colour)
    result = _invoke(
        lifecycle.run_bundle,
        bundle,
        solver,
        patch,
        solver_context,
        tuple(ctx.args),
        keep_containers,
        output=output,
        json_errors=json_output,
    )
    if json_output:
        typer.echo(result.model_dump_json(indent=2))
    else:
        _render_run_result(result, output)
    if not result.resolved:
        raise typer.Exit(code=1)


def _render_run_result(result: RunResult, output: Console) -> None:
    colour = "green" if result.resolved else "yellow"
    label = "RESOLVED" if result.resolved else "UNRESOLVED"
    output.print(f"[bold {colour}]Result: {label}[/bold {colour}]")
    output.print(
        "Baseline preflight: "
        f"P2P {result.baseline_preflight.pass_to_pass_matched}/"
        f"{result.baseline_preflight.pass_to_pass_total}, "
        f"F2P {result.baseline_preflight.fail_to_pass_matched}/"
        f"{result.baseline_preflight.fail_to_pass_total}"
    )
    output.print(
        f"Solver: {result.solver.solver_type.value} ({result.solver.status.value})"
    )
    output.print(
        "Candidate: "
        f"P2P {result.candidate_summary.pass_to_pass_matched}/"
        f"{result.candidate_summary.pass_to_pass_total}, "
        f"F2P {result.candidate_summary.fail_to_pass_matched}/"
        f"{result.candidate_summary.fail_to_pass_total}"
    )
    output.print(f"Changed files: {len(result.candidate_tree.changed_paths)}")
    output.print(f"Patch: {result.candidate_tree.candidate_patch_sha256}")
    output.print(f"Command: {result.command_id}")
    output.print(f"Artifacts: {result.artifact_directory}")
    for warning in result.warnings:
        output.print(f"[yellow]Warning: {warning}[/yellow]")


@app.command()
def show(
    command_id: Annotated[str, typer.Argument(help="Stable command ID.")],
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable command details."),
    ] = False,
    events: Annotated[
        bool,
        typer.Option("--events", help="Include ordered lifecycle events."),
    ] = False,
    tests: Annotated[
        bool,
        typer.Option("--tests", help="Include persisted per-selector results."),
    ] = False,
    no_colour: Annotated[
        bool,
        typer.Option("--no-colour", help="Disable coloured output."),
    ] = False,
) -> None:
    """Show a persisted init, validate, or run command."""
    output = Console(no_color=no_colour)
    result = _invoke(
        lifecycle.show_command,
        command_id,
        events,
        tests,
        output=output,
        json_errors=json_output,
    )
    if json_output:
        typer.echo(result.model_dump_json(indent=2))
    else:
        _render_show_result(result, output)


def _render_show_result(result: ShowResult, output: Console) -> None:
    command = result.command
    output.print(f"[bold]Command {command['id']}[/bold]")
    output.print(f"Type: {command['command_type']}")
    output.print(f"Status: {command['command_status']}")
    if command.get("evaluation_status") is not None:
        output.print(f"Evaluation: {command['evaluation_status']}")
    if command.get("resolved") is not None:
        output.print(f"Resolved: {bool(command['resolved'])}")
    if result.solver is not None:
        output.print(
            f"Solver: {result.solver['solver_type']} ({result.solver['status']})"
        )
        changed = json.loads(str(result.solver.get("changed_paths_json") or "[]"))
        output.print(f"Changed files: {len(changed)}")
        if result.solver.get("patch_digest") is not None:
            output.print(f"Patch: {result.solver['patch_digest']}")
    if result.evaluations:
        for evaluation in result.evaluations:
            output.print(
                f"{str(evaluation['phase']).title()}: {evaluation['outcome']} "
                f"({evaluation['matched_count']}/{evaluation['test_count']} matched)"
            )
    if command.get("artifact_root") is not None:
        output.print(f"Artifacts: {command['artifact_root']}")
    if result.events:
        output.print("Events:")
        for event in result.events:
            output.print(f"  {event['created_at']}  {event['event_type']}")
    if result.tests:
        output.print("Tests:")
        for test in result.tests:
            output.print(
                f"  {test['phase']} {test['requested_selector']}: "
                f"{test['actual_status']}"
            )
