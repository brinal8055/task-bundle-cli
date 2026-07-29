from collections.abc import Callable
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from task_bundle import __version__, lifecycle
from task_bundle.errors import TaskBundleError

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


def _render_error(error: TaskBundleError) -> None:
    console.print(f"[bold red]{error}[/bold red]")
    console.print(f"\n[bold]Code:[/bold] {error.code}")
    console.print(f"[bold]Phase:[/bold] {error.context.phase}")
    console.print(f"[bold]Expected:[/bold] {error.context.expected}")
    console.print(f"[bold]Actual:[/bold] {error.context.actual}")
    if error.context.path is not None:
        console.print(f"[bold]Path:[/bold] {error.context.path}")
    if error.context.artifact is not None:
        console.print(f"[bold]Artifact:[/bold] {error.context.artifact}")
    console.print(f"[bold]Fix:[/bold] {error.context.corrective_action}")


def _invoke(operation: Callable[..., object], *args: object) -> None:
    try:
        operation(*args)
    except TaskBundleError as error:
        _render_error(error)
        raise typer.Exit(code=error.exit_code) from None


@app.command()
def init(bundle: Annotated[Path, typer.Argument(help="Path to the task bundle.")]) -> None:
    """Validate a bundle, build its task image, and write its lockfile."""
    _invoke(lifecycle.init_bundle, bundle)


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
