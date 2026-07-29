from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

BundleFactory = Callable[[Path], Path]


def task_mapping() -> dict[str, Any]:
    return {
        "schema_version": "1",
        "task": {"id": "example-task", "title": "Example task"},
        "repository": {
            "url": "https://example.com/repository.git",
            "commit": "a" * 40,
            "submodules": False,
        },
        "public": {
            "description": "public/description.md",
            "requirements": "public/requirements.md",
            "interface": "public/interface.md",
        },
        "environment": {
            "type": "dockerfile",
            "dockerfile": "environment/Dockerfile",
            "context": "environment/context",
            "platform": "linux/amd64",
            "build": {"build_args": {"VERSION": "1"}},
        },
        "evaluation": {
            "test_patch": "evaluation/hidden/test.patch",
            "golden_patch": "evaluation/hidden/golden.patch",
            "prepare": {
                "command": ["/evaluation/harness/prepare.sh"],
                "network": False,
            },
            "runner": {
                "command": ["/evaluation/harness/run-tests.sh"],
                "result_file": "/evaluation/output/results.json",
                "result_schema_version": "1",
            },
            "pass_to_pass": [{"selector": "tests/test_api.py::test_existing"}],
            "fail_to_pass": [
                {
                    "selector": "tests/test_api.py::test_create",
                    "baseline_statuses": ["failed"],
                }
            ],
        },
    }


def write_task(root: Path, mapping: dict[str, Any], *, sort_keys: bool = False) -> None:
    (root / "task.yaml").write_text(
        yaml.safe_dump(mapping, sort_keys=sort_keys),
        encoding="utf-8",
    )


def read_task(root: Path) -> dict[str, Any]:
    loaded = yaml.safe_load((root / "task.yaml").read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def create_bundle(root: Path) -> Path:
    for directory in (
        "public",
        "environment/context",
        "evaluation/hidden",
    ):
        (root / directory).mkdir(parents=True, exist_ok=True)
    files = {
        "public/description.md": "Implement the requested behavior.\n",
        "public/requirements.md": "Preserve existing behavior.\n",
        "public/interface.md": "No interface changes.\n",
        "environment/Dockerfile": "FROM scratch\nCOPY repo/ /workspace/repo/\n",
        "environment/context/tool.conf": "enabled=true\n",
        "evaluation/prepare.sh": "#!/bin/sh\nexit 0\n",
        "evaluation/run-tests.sh": "#!/bin/sh\nexit 0\n",
        "evaluation/parse-results.py": "print('parse')\n",
        "evaluation/hidden/test.patch": "test patch\n",
        "evaluation/hidden/golden.patch": "golden patch\n",
    }
    for relative, content in files.items():
        (root / relative).write_text(content, encoding="utf-8")
    (root / "evaluation/prepare.sh").chmod(0o755)
    (root / "evaluation/run-tests.sh").chmod(0o755)
    write_task(root, task_mapping())
    return root
