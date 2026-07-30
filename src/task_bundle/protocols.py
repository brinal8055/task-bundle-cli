from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from task_bundle.models import (
    CapturedTestExecutions,
    EvaluationPlan,
    NormalizedResult,
    TestExecutionPlan,
)


@dataclass(frozen=True, slots=True)
class ProcessResult:
    argv: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str


@dataclass(frozen=True, slots=True)
class BuildRequest:
    context: Path
    tag: str
    platform: str
    timeout_seconds: int


@dataclass(frozen=True, slots=True)
class ContainerRequest:
    image: str
    name: str
    command: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CopyRequest:
    container_id: str
    source: Path
    destination: Path


@runtime_checkable
class Runtime(Protocol):
    def build_image(self, request: BuildRequest) -> str: ...

    def create_container(self, request: ContainerRequest) -> str: ...

    def execute(
        self, container_id: str, argv: Sequence[str], timeout_seconds: int
    ) -> ProcessResult: ...

    def copy_to(self, request: CopyRequest) -> None: ...

    def copy_from(self, request: CopyRequest) -> None: ...

    def remove_container(self, container_id: str) -> None: ...


@dataclass(frozen=True, slots=True)
class SolverRequest:
    workspace: Path
    public_context: Path
    timeout_seconds: int


@dataclass(frozen=True, slots=True)
class SolverResult:
    exit_code: int
    stdout: str
    stderr: str


@runtime_checkable
class Solver(Protocol):
    def solve(self, request: SolverRequest) -> SolverResult: ...


@runtime_checkable
class TestAdapter(Protocol):
    def create_plan(self, plan: EvaluationPlan) -> TestExecutionPlan: ...

    def parse_results(
        self,
        plan: TestExecutionPlan,
        captured: CapturedTestExecutions,
    ) -> NormalizedResult: ...
