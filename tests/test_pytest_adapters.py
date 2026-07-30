import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

ROOT = Path(__file__).parents[1]
ADAPTERS = (
    ROOT / "bundles/swebench-pro-ansible-d9f186/evaluation/adapter.py",
    ROOT / "bundles/swebench-pro-openlibrary/evaluation/adapter.py",
)
SELECTOR_ONE = "tests/test_api.py::test_one"
SELECTOR_TWO = "tests/test_api.py::test_two"


@pytest.fixture(params=ADAPTERS, ids=("ansible", "openlibrary"))
def adapter(request: pytest.FixtureRequest) -> ModuleType:
    path = request.param
    spec = importlib.util.spec_from_file_location(
        f"task_bundle_test_adapter_{path.parent.parent.name}",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _event(
    adapter: ModuleType,
    selector: str,
    *,
    when: str = "call",
    outcome: str = "passed",
    wasxfail: str | None = None,
) -> str:
    value = {
        "type": "test_report",
        "nodeid": selector,
        "when": when,
        "outcome": outcome,
        "duration_ms": 2,
        "wasxfail": wasxfail,
    }
    return cast(str, adapter.EVENT_PREFIX) + json.dumps(
        value,
        separators=(",", ":"),
    )


def _captured(
    stdout: str,
    *,
    selectors: list[str] | None = None,
    exit_code: int | None = 0,
    timed_out: bool = False,
    stdout_truncated: bool = False,
    stderr_truncated: bool = False,
) -> dict[str, Any]:
    requested = selectors or [SELECTOR_ONE]
    return {
        "schema_version": "1",
        "executions": [
            {
                "execution_id": "pytest-group-001",
                "requested_selectors": requested,
                "argv": ["pytest", *requested],
                "exit_code": exit_code,
                "timed_out": timed_out,
                "stdout": stdout,
                "stderr": "",
                "stdout_truncated": stdout_truncated,
                "stderr_truncated": stderr_truncated,
                "started_at": "2026-07-30T08:00:00Z",
                "finished_at": "2026-07-30T08:00:01Z",
                "duration_ms": 1000,
                "candidate_processes_terminated": True,
            }
        ],
    }


@pytest.mark.parametrize(
    ("when", "outcome", "wasxfail", "exit_code", "status"),
    [
        ("call", "passed", None, 0, "passed"),
        ("call", "failed", None, 1, "failed"),
        ("setup", "failed", None, 1, "error"),
        ("teardown", "failed", None, 1, "error"),
        ("setup", "skipped", None, 0, "skipped"),
        ("call", "skipped", "expected failure", 0, "xfailed"),
        ("call", "passed", "expected failure", 0, "xpassed"),
        ("call", "failed", "strict xpass", 1, "xpassed"),
    ],
)
def test_pytest_adapter_maps_machine_readable_statuses(
    adapter: ModuleType,
    when: str,
    outcome: str,
    wasxfail: str | None,
    exit_code: int,
    status: str,
) -> None:
    lines = []
    if when == "teardown":
        lines.append(_event(adapter, SELECTOR_ONE))
    lines.append(
        _event(
            adapter,
            SELECTOR_ONE,
            when=when,
            outcome=outcome,
            wasxfail=wasxfail,
        )
    )

    result = adapter._normalized_result(
        _captured("\n".join(lines), exit_code=exit_code)
    )

    assert result["tests"][0]["status"] == status
    assert result["tests"][0]["observed_id"] == SELECTOR_ONE


def test_pytest_adapter_supports_grouped_selectors(adapter: ModuleType) -> None:
    stdout = "\n".join(
        (
            _event(adapter, SELECTOR_ONE),
            _event(adapter, SELECTOR_TWO),
        )
    )

    result = adapter._normalized_result(
        _captured(stdout, selectors=[SELECTOR_ONE, SELECTOR_TWO])
    )

    assert [item["requested_selector"] for item in result["tests"]] == [
        SELECTOR_ONE,
        SELECTOR_TWO,
    ]


def test_pytest_adapter_accepts_pytest_progress_before_event(
    adapter: ModuleType,
) -> None:
    stdout = "F" + _event(adapter, SELECTOR_ONE, outcome="failed")

    result = adapter._normalized_result(_captured(stdout, exit_code=1))

    assert result["tests"][0]["status"] == "failed"


@pytest.mark.parametrize(
    "stdout",
    [
        "",
        "TASK_BUNDLE_PYTEST_EVENT {",
        "ordinary pytest summary only",
    ],
    ids=("empty", "malformed-event", "no-machine-readable-event"),
)
def test_pytest_adapter_rejects_missing_or_malformed_output(
    adapter: ModuleType,
    stdout: str,
) -> None:
    with pytest.raises(ValueError):
        adapter._normalized_result(_captured(stdout))


@pytest.mark.parametrize(
    ("selectors", "lines"),
    [
        (
            [SELECTOR_ONE],
            (SELECTOR_ONE, SELECTOR_ONE),
        ),
        (
            [SELECTOR_ONE],
            (SELECTOR_ONE, SELECTOR_TWO),
        ),
        (
            [SELECTOR_ONE, SELECTOR_TWO],
            (SELECTOR_ONE,),
        ),
        (
            [SELECTOR_ONE, SELECTOR_TWO],
            (SELECTOR_ONE, SELECTOR_ONE, SELECTOR_TWO),
        ),
        (
            [SELECTOR_ONE, SELECTOR_TWO],
            (SELECTOR_ONE, SELECTOR_TWO, "tests/test_api.py::test_extra"),
        ),
    ],
    ids=(
        "duplicate-single",
        "unexpected-single",
        "missing-grouped",
        "duplicate-grouped",
        "unexpected-grouped",
    ),
)
def test_pytest_adapter_rejects_selector_mapping_errors(
    adapter: ModuleType,
    selectors: list[str],
    lines: tuple[str, ...],
) -> None:
    stdout = "\n".join(_event(adapter, selector) for selector in lines)

    with pytest.raises(ValueError):
        adapter._normalized_result(_captured(stdout, selectors=selectors))


@pytest.mark.parametrize(
    ("stdout_truncated", "stderr_truncated"),
    [(True, False), (False, True)],
)
def test_pytest_adapter_fails_closed_on_truncation(
    adapter: ModuleType,
    stdout_truncated: bool,
    stderr_truncated: bool,
) -> None:
    stdout = _event(adapter, SELECTOR_ONE)

    with pytest.raises(ValueError, match="truncated"):
        adapter._normalized_result(
            _captured(
                stdout,
                stdout_truncated=stdout_truncated,
                stderr_truncated=stderr_truncated,
            )
        )


@pytest.mark.parametrize("summary", ["1 skipped", "1 xfailed"])
def test_pytest_adapter_does_not_trust_truncated_passing_summary(
    adapter: ModuleType,
    summary: str,
) -> None:
    with pytest.raises(ValueError, match="truncated"):
        adapter._normalized_result(
            _captured(
                summary,
                stdout_truncated=True,
            )
        )


def test_pytest_adapter_distinguishes_timeout(adapter: ModuleType) -> None:
    result = adapter._normalized_result(
        _captured("", exit_code=None, timed_out=True)
    )

    assert result["harness_status"] == "timed_out"
    assert result["tests"][0]["status"] == "timeout"


@pytest.mark.parametrize(
    ("stdout", "exit_code"),
    [
        (
            "TASK_BUNDLE_PYTEST_EVENT "
            '{"type":"collection_error","nodeid":"tests/test_api.py"}',
            2,
        ),
        ("", 2),
    ],
)
def test_pytest_adapter_rejects_collection_failure(
    adapter: ModuleType,
    stdout: str,
    exit_code: int,
) -> None:
    with pytest.raises(ValueError, match=r"collection|harness"):
        adapter._normalized_result(_captured(stdout, exit_code=exit_code))


@pytest.mark.parametrize(
    ("stdout", "exit_code"),
    [
        pytest.param(
            lambda module: _event(module, SELECTOR_ONE, outcome="failed"),
            0,
            id="zero-with-failure",
        ),
        pytest.param(
            lambda module: _event(module, SELECTOR_ONE),
            1,
            id="one-with-pass",
        ),
    ],
)
def test_pytest_adapter_rejects_ambiguous_exit_status(
    adapter: ModuleType,
    stdout: Any,
    exit_code: int,
) -> None:
    with pytest.raises(ValueError, match="exit status"):
        adapter._normalized_result(
            _captured(stdout(adapter), exit_code=exit_code)
        )
