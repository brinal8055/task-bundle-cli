#!/usr/bin/env python3
from __future__ import annotations

import ast
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROW_INDEX = 407
INSTANCE_ID = (
    "instance_ansible__ansible-d9f1866249756efc264b00ff7497e92c11a9885f-"
    "v0f01c69f1e2528b935359cfe578530722bca2c59"
)
EXPECTED_DIGEST = "d9ac34c26a511a63954f1dd21f9cfea6eea56b8a96437fee2d9ab47aded9d994"
BUNDLE = Path(__file__).resolve().parents[1] / "bundles/swebench-pro-ansible-d9f186"


def _record(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("JSON root must be an object")
    if "rows" not in payload:
        return payload
    rows = payload["rows"]
    if not isinstance(rows, list) or len(rows) != 1:
        raise ValueError("Dataset Server response must contain exactly one row")
    entry = rows[0]
    if not isinstance(entry, dict) or entry.get("row_idx") != ROW_INDEX:
        raise ValueError(f"Dataset Server response must contain row {ROW_INDEX}")
    record = entry.get("row")
    if not isinstance(record, dict):
        raise ValueError("Dataset Server response omitted the row object")
    return record


def _normalized_public(value: Any) -> bytes:
    if not isinstance(value, str):
        raise ValueError("public text field must be a JSON string")
    decoded = json.loads(value)
    if not isinstance(decoded, str):
        raise ValueError("public text field must decode to a string")
    return (decoded.rstrip("\n") + "\n").encode()


def _check_file(path: str, expected: bytes) -> None:
    actual = (BUNDLE / path).read_bytes()
    if actual != expected:
        raise ValueError(f"{path} does not match the immutable source record")


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {Path(sys.argv[0]).name} RECORD.json", file=sys.stderr)
        return 2
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    record = _record(payload)
    canonical = json.dumps(
        record,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    digest = hashlib.sha256(canonical).hexdigest()
    if digest != EXPECTED_DIGEST:
        raise ValueError(f"source-record digest mismatch: sha256:{digest}")
    if record.get("instance_id") != INSTANCE_ID:
        raise ValueError("source-record instance ID mismatch")
    if record.get("base_commit") != "59ca05b70994b07a9507f61a0871146a4991b262":
        raise ValueError("source-record base commit mismatch")
    for field, path in (
        ("problem_statement", "public/description.md"),
        ("requirements", "public/requirements.md"),
        ("interface", "public/interface.md"),
    ):
        _check_file(path, _normalized_public(record.get(field)))
    for field, path in (
        ("test_patch", "evaluation/hidden/test.patch"),
        ("patch", "evaluation/hidden/golden.patch"),
    ):
        value = record.get(field)
        if not isinstance(value, str):
            raise ValueError(f"{field} must be a string")
        _check_file(path, value.encode())
    fail_to_pass = ast.literal_eval(record.get("fail_to_pass", ""))
    pass_to_pass = ast.literal_eval(record.get("pass_to_pass", ""))
    if fail_to_pass != [
        "test/units/module_utils/common/validation/"
        "test_check_type_dict.py::test_check_type_dict_fail"
    ]:
        raise ValueError("FAIL_TO_PASS selector mismatch")
    if pass_to_pass != [
        "test/units/module_utils/common/validation/"
        "test_check_type_dict.py::test_check_type_dict"
    ]:
        raise ValueError("PASS_TO_PASS selector mismatch")
    print(f"verified row {ROW_INDEX}: sha256:{digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
