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


def _selectors(plan: dict[str, Any]) -> list[str]:
    return [
        *(item["selector"] for item in plan["pass_to_pass"]),
        *(item["selector"] for item in plan["fail_to_pass"]),
    ]


def _parse_case(report: Path, selector: str) -> tuple[str, str | None, bool]:
    try:
        root = ET.parse(report).getroot()
    except (ET.ParseError, OSError) as error:
        return "missing", str(error)[:MAX_MESSAGE], False
    cases = list(root.iter("testcase"))
    if len(cases) != 1:
        return (
            "missing",
            f"expected one JUnit testcase for {selector}, observed {len(cases)}",
            False,
        )
    case = cases[0]
    for tag, status in (("error", "error"), ("failure", "failed"), ("skipped", "skipped")):
        child = case.find(tag)
        if child is not None:
            message = "\n".join(
                value for value in (child.get("message"), child.text) if value
            )
            return status, message[:MAX_MESSAGE] or None, True
    return "passed", None, True


def main() -> int:
    started = datetime.now(UTC)
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    tests: list[dict[str, Any]] = []
    collection_succeeded = True
    execution_started = False
    exit_codes: list[int] = []
    for index, selector in enumerate(_selectors(plan)):
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
            env={**os.environ, "PYTEST_ADDOPTS": ""},
        )
        execution_started = True
        exit_codes.append(completed.returncode)
        status, message, mapped = _parse_case(report, selector)
        if not mapped or completed.returncode in {2, 3, 4, 5}:
            collection_succeeded = False
            if message is None:
                message = (completed.stderr or completed.stdout)[:MAX_MESSAGE]
        duration = int((datetime.now(UTC) - before).total_seconds() * 1000)
        tests.append(
            {
                "requested_selector": selector,
                "observed_id": selector if mapped else None,
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
    return 0 if collection_succeeded else 2


if __name__ == "__main__":
    raise SystemExit(main())
