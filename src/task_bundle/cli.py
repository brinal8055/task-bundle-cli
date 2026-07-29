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
def validate(bundle: Annotated[Path, typer.Argument(help="Path to the task bundle.")]) -> None:
    """Validate baseline and golden task behavior."""
    _invoke(lifecycle.validate_bundle, bundle)


@app.command()
def run(bundle: Annotated[Path, typer.Argument(help="Path to the task bundle.")]) -> None:
    """Run a solver and evaluate its candidate patch."""
    _invoke(lifecycle.run_bundle, bundle)


@app.command()
def show(command_id: Annotated[str, typer.Argument(help="Stable command ID.")]) -> None:
    """Show a persisted command and its lifecycle."""
    _invoke(lifecycle.show_command, command_id)
