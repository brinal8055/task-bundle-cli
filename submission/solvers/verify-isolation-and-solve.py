#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path

FORBIDDEN_MARKERS = (
    b"TB_" + b"HIDDEN_CANARY_6F2F5A64B88D",
    b"source_" + b"record_sha256",
    b"pass_" + b"to_pass",
    b"fail_" + b"to_pass",
)
FORBIDDEN_NAMES = {
    "test.patch",
    "golden.patch",
    "task.yaml",
    "bundle.snapshot.json",
    "bundle.lock.json",
    "task.db",
}


def inspect_tree(root: Path) -> None:
    if not root.exists():
        return
    for path in root.rglob("*"):
        if path.name.lower() in FORBIDDEN_NAMES:
            raise RuntimeError(f"forbidden filename visible: {path}")
        if not path.is_file() or path.is_symlink():
            continue
        try:
            payload = path.read_bytes()
        except (OSError, PermissionError):
            continue
        if any(marker in payload for marker in FORBIDDEN_MARKERS):
            raise RuntimeError(f"forbidden content visible: {path}")


def main() -> None:
    for root in (Path("/task"), Path("/workspace"), Path("/tmp")):
        inspect_tree(root)
    visible = "\0".join(
        [*os.environ.keys(), *os.environ.values(), *os.sys.argv]
    ).encode()
    if any(marker in visible for marker in FORBIDDEN_MARKERS):
        raise RuntimeError("forbidden marker visible in environment or argv")
    for path in (
        Path("/evaluation"),
        Path("/var/run/docker.sock"),
        Path("/root/.ssh"),
        Path("/root/.docker"),
    ):
        try:
            visible = path.exists()
        except PermissionError:
            visible = False
        if visible:
            raise RuntimeError(f"forbidden host surface visible: {path}")
    Path("answer.txt").write_text("42\n", encoding="utf-8")


if __name__ == "__main__":
    main()
