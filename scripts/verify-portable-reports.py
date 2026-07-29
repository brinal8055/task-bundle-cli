#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "submission/reports"
REQUIRED_JSON = (
    "real-task-init-report.json",
    "real-task-validation-report.json",
    "real-task-noop-run-report.json",
    "real-task-resolved-run-report.json",
)
FORBIDDEN_KEYS = {
    "credential",
    "docker_config",
    "environment_dump",
    "password",
    "secret",
    "token",
}
LOCAL_PATH_MARKERS = ("/Users/", "/home/", "/private/", "\\Users\\")


def _walk(value: Any, *, source: Path) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in FORBIDDEN_KEYS:
                raise ValueError(f"{source.name}: forbidden portable-report key {key!r}")
            _walk(item, source=source)
    elif isinstance(value, list):
        for item in value:
            _walk(item, source=source)
    elif isinstance(value, str) and any(marker in value for marker in LOCAL_PATH_MARKERS):
        raise ValueError(f"{source.name}: local absolute path is not portable")


def _load(name: str) -> dict[str, Any]:
    path = REPORTS / name
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{name}: report root must be an object")
    _walk(payload, source=path)
    return payload


def main() -> int:
    reports = {name: _load(name) for name in REQUIRED_JSON}
    for path in sorted(REPORTS.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        _walk(payload, source=path)

    init = reports["real-task-init-report.json"]
    validation = reports["real-task-validation-report.json"]
    noop = reports["real-task-noop-run-report.json"]
    resolved = reports["real-task-resolved-run-report.json"]
    identities = {
        report["bundle_input_digest"] for report in (init, validation, noop, resolved)
    }
    images = {report["image_id"] for report in (init, validation, noop, resolved)}
    if len(identities) != 1 or len(images) != 1:
        raise ValueError("real-task reports do not share one bundle and image identity")
    if init["exit_code"] != 0 or init["outcome"] != "initialized":
        raise ValueError("portable init report is not successful")
    if validation["exit_code"] != 0 or validation["outcome"] != "valid":
        raise ValueError("portable validation report is not successful")
    if noop["exit_code"] != 1 or noop["resolved"] is not False:
        raise ValueError("portable no-op report is not the expected unresolved result")
    if resolved["exit_code"] != 0 or resolved["resolved"] is not True:
        raise ValueError("portable resolved report is not successful")
    if noop["validation_id"] != validation["validation_id"]:
        raise ValueError("no-op report references a different validation")
    if resolved["validation_id"] != validation["validation_id"]:
        raise ValueError("resolved report references a different validation")
    for path in (
        REPORTS / "real-task-selection.md",
        REPORTS / "real-task-command-evidence.md",
        REPORTS / "final-cleanup-audit.md",
    ):
        text = path.read_text(encoding="utf-8")
        if any(marker in text for marker in LOCAL_PATH_MARKERS):
            raise ValueError(f"{path.name}: local absolute path is not portable")
    print(f"portable reports verified: {len(tuple(REPORTS.glob('*.json')))} JSON files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
