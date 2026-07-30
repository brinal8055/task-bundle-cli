#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "submission/reports"
SUPPORT_MATRIX = ROOT / "submission/support-matrix.json"
REQUIRED_JSON = (
    "real-task-init-report.json",
    "real-task-validation-report.json",
    "real-task-noop-run-report.json",
    "real-task-resolved-run-report.json",
)
SYNTHETIC_JSON = (
    "synthetic/noop-unresolved.json",
    "synthetic/golden-resolved.json",
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


def _validate_synthetic(
    report: dict[str, Any],
    *,
    expected_exit: int,
    resolved: bool,
    solver_type: str,
    candidate_outcome: str,
) -> None:
    if report["task_id"] != "synthetic-go-calculator":
        raise ValueError("synthetic report has the wrong task")
    if report["exit_code"] != expected_exit or report["resolved"] is not resolved:
        raise ValueError("synthetic report has the wrong resolved classification")
    if report["command_status"] != "succeeded":
        raise ValueError("synthetic report does not describe a completed command")
    if report["solver"]["type"] != solver_type:
        raise ValueError("synthetic report has the wrong solver")
    baseline = report["baseline"]
    candidate = report["candidate"]
    if baseline["outcome"] != "accepted" or candidate["outcome"] != candidate_outcome:
        raise ValueError("synthetic report has the wrong baseline/candidate outcome")
    for phase in (baseline, candidate):
        results = phase["results"]
        groups = [result["group"] for result in results]
        if groups.count("pass_to_pass") != 1 or groups.count("fail_to_pass") != 2:
            raise ValueError("synthetic report does not show complete selector groups")
    if report["cleanup_complete"] is not True:
        raise ValueError("synthetic report does not prove cleanup")


def main() -> int:
    reports = {name: _load(name) for name in REQUIRED_JSON}
    synthetic = {name: _load(name) for name in SYNTHETIC_JSON}
    for path in sorted(REPORTS.rglob("*.json")):
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

    _validate_synthetic(
        synthetic["synthetic/noop-unresolved.json"],
        expected_exit=1,
        resolved=False,
        solver_type="noop",
        candidate_outcome="rejected",
    )
    _validate_synthetic(
        synthetic["synthetic/golden-resolved.json"],
        expected_exit=0,
        resolved=True,
        solver_type="patch",
        candidate_outcome="accepted",
    )
    synthetic_identities = {
        report["identity"]["bundle_input_digest"] for report in synthetic.values()
    }
    synthetic_images = {report["identity"]["task_image_id"] for report in synthetic.values()}
    synthetic_validations = {
        report["identity"]["validation_id"] for report in synthetic.values()
    }
    if (
        len(synthetic_identities) != 1
        or len(synthetic_images) != 1
        or len(synthetic_validations) != 1
    ):
        raise ValueError("synthetic reports do not share one verified identity")

    support = json.loads(SUPPORT_MATRIX.read_text(encoding="utf-8"))
    _walk(support, source=SUPPORT_MATRIX)
    if support["schema_version"] != "1":
        raise ValueError("support matrix schema is unsupported")
    if support["source"] != {
        "public_https_git": True,
        "submodules": False,
        "private_git": False,
    }:
        raise ValueError("support matrix source capabilities are inaccurate")
    if support["security"] != {
        "pre_finalisation_hidden_isolation": True,
        "candidate_writable_final_results": False,
        "cryptographic_in_process_result_integrity": False,
    }:
        raise ValueError("support matrix security capabilities are inaccurate")

    for path in (
        REPORTS / "real-task-selection.md",
        REPORTS / "real-task-command-evidence.md",
        REPORTS / "final-cleanup-audit.md",
    ):
        text = path.read_text(encoding="utf-8")
        if any(marker in text for marker in LOCAL_PATH_MARKERS):
            raise ValueError(f"{path.name}: local absolute path is not portable")
    print(f"portable reports verified: {len(tuple(REPORTS.rglob('*.json')))} JSON files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
