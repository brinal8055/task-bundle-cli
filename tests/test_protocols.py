from collections.abc import Sequence
from pathlib import Path

from task_bundle.models import EvaluationPlan, NormalizedResult
from task_bundle.protocols import (
    BuildRequest,
    ContainerRequest,
    CopyRequest,
    ProcessResult,
    Runtime,
    Solver,
    SolverRequest,
    SolverResult,
)
from task_bundle.protocols import TestAdapter as Adapter


class FakeRuntime:
    def build_image(self, request: BuildRequest) -> str:
        return "sha256:image"

    def create_container(self, request: ContainerRequest) -> str:
        return "container"

    def execute(
        self, container_id: str, argv: Sequence[str], timeout_seconds: int
    ) -> ProcessResult:
        return ProcessResult(tuple(argv), 0, "", "")

    def copy_to(self, request: CopyRequest) -> None:
        return None

    def copy_from(self, request: CopyRequest) -> None:
        return None

    def remove_container(self, container_id: str) -> None:
        return None


class FakeSolver:
    def solve(self, request: SolverRequest) -> SolverResult:
        return SolverResult(0, "", "")


class FakeAdapter:
    def create_plan(self, plan: EvaluationPlan, destination: Path) -> None:
        return None

    def parse_results(self, result_file: Path) -> NormalizedResult:
        raise NotImplementedError


def test_fakes_conform_to_extension_protocols() -> None:
    runtime: Runtime = FakeRuntime()
    solver: Solver = FakeSolver()
    adapter: Adapter = FakeAdapter()

    assert isinstance(runtime, Runtime)
    assert isinstance(solver, Solver)
    assert isinstance(adapter, Adapter)
