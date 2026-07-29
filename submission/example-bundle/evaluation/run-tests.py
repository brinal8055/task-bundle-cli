#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

PLAN = Path("/evaluation/input/plan.json")
OUTPUT = Path("/evaluation/output/results.json")
ROOT = Path("/workspace/repo")
CANARY = "TB_HIDDEN_CANARY_6F2F5A64B88D"


def main() -> int:
    started = datetime.now(UTC)
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    selectors = [
        *(item["selector"] for item in plan["pass_to_pass"]),
        *(item["selector"] for item in plan["fail_to_pass"]),
    ]
    if (ROOT / ".task_bundle_hidden_canary").read_text().strip() != CANARY:
        raise RuntimeError("hidden evaluator canary is missing")
    statuses = {
        "synthetic::readme": (ROOT / "README").is_file(),
        "synthetic::answer": (
            (ROOT / "answer.txt").is_file()
            and (ROOT / "answer.txt").read_bytes() == b"42\n"
        ),
    }
    tests = [
        {
            "requested_selector": selector,
            "observed_id": selector,
            "status": "passed" if statuses.get(selector, False) else "failed",
            "duration_ms": 0,
        }
        for selector in selectors
    ]
    result = {
        "schema_version": "1",
        "framework": "synthetic-python",
        "harness_status": "completed",
        "collection_succeeded": True,
        "execution_started": True,
        "command": ["python", "/evaluation/harness/run-tests.py"],
        "started_at": started.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "exit_code": 0 if all(item["status"] == "passed" for item in tests) else 1,
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
