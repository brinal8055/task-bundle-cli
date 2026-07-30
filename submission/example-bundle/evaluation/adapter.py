#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PLAN = Path("/evaluation/input/plan.json")
CAPTURED = Path("/evaluation/trusted/executions.json")
ROOT = Path("/workspace/repo")
CANARY = "TB_HIDDEN_CANARY_6F2F5A64B88D"


def _selectors(plan: dict[str, Any]) -> list[str]:
    return [
        *(item["selector"] for item in plan["pass_to_pass"]),
        *(item["selector"] for item in plan["fail_to_pass"]),
    ]


def _build_plan() -> int:
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    executions = [
        {
            "execution_id": f"selector-{index:03d}",
            "requested_selectors": [selector],
            "argv": [
                "python",
                "/evaluation/harness/adapter.py",
                "run-selector",
                selector,
            ],
            "timeout_seconds": plan["timeout_seconds"],
        }
        for index, selector in enumerate(_selectors(plan), start=1)
    ]
    print(json.dumps({"schema_version": "2", "executions": executions}))
    return 0


def _run_selector(selector: str) -> int:
    if (ROOT / ".task_bundle_hidden_canary").read_text().strip() != CANARY:
        return 2
    statuses = {
        "synthetic::readme": (ROOT / "README").is_file(),
        "synthetic::answer": (
            (ROOT / "answer.txt").is_file()
            and (ROOT / "answer.txt").read_bytes() == b"42\n"
        ),
    }
    return 0 if statuses.get(selector, False) else 1


def _parse_result() -> int:
    captured = json.loads(CAPTURED.read_text(encoding="utf-8"))["executions"]
    tests: list[dict[str, Any]] = []
    collection_succeeded = True
    for execution in captured:
        selectors = execution["requested_selectors"]
        if len(selectors) != 1:
            raise ValueError("synthetic adapter requires one selector per execution")
        if execution["stdout_truncated"] or execution["stderr_truncated"]:
            raise ValueError("captured test output was truncated")
        selector = selectors[0]
        if execution["timed_out"]:
            status = "timeout"
        elif execution["exit_code"] == 0:
            status = "passed"
        elif execution["exit_code"] == 1:
            status = "failed"
        else:
            status = "error"
            collection_succeeded = False
        tests.append(
            {
                "requested_selector": selector,
                "observed_id": selector,
                "status": status,
                "duration_ms": execution["duration_ms"],
                "message": (execution["stderr"] or execution["stdout"])[:16_384]
                or None,
            }
        )
    result = {
        "schema_version": "1",
        "framework": "synthetic-python-exit-code",
        "harness_status": (
            "completed" if collection_succeeded else "collection_failed"
        ),
        "collection_succeeded": collection_succeeded,
        "execution_started": bool(captured),
        "command": ["python", "/evaluation/harness/adapter.py", "run-selector"],
        "started_at": captured[0]["started_at"],
        "finished_at": captured[-1]["finished_at"],
        "exit_code": max(
            (
                execution["exit_code"]
                for execution in captured
                if execution["exit_code"] is not None
            ),
            default=0,
        ),
        "tests": tests,
    }
    print(json.dumps(result, separators=(",", ":")))
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        return 2
    if sys.argv[1] == "build-plan":
        return _build_plan()
    if sys.argv[1] == "run-selector" and len(sys.argv) == 3:
        return _run_selector(sys.argv[2])
    if sys.argv[1] == "parse-result":
        return _parse_result()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
