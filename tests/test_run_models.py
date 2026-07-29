from pathlib import Path

import pytest
from pydantic import ValidationError

from task_bundle.run.models import RunOptions, SolverType


def test_solver_options_are_strictly_scoped() -> None:
    assert RunOptions(solver=SolverType.NOOP).command == ()
    assert RunOptions(
        solver=SolverType.PATCH,
        patch=Path("candidate.patch"),
    ).patch == Path("candidate.patch")
    assert RunOptions(
        solver=SolverType.COMMAND,
        solver_context=Path("solver"),
        command=("python", "/task/solver/solve.py"),
    ).command == ("python", "/task/solver/solve.py")

    invalid = (
        {"solver": SolverType.NOOP, "patch": Path("candidate.patch")},
        {"solver": SolverType.PATCH},
        {"solver": SolverType.PATCH, "patch": Path("p"), "command": ("x",)},
        {"solver": SolverType.COMMAND},
        {"solver": SolverType.COMMAND, "command": ("",)},
    )
    for values in invalid:
        with pytest.raises(ValidationError):
            RunOptions.model_validate(values)
