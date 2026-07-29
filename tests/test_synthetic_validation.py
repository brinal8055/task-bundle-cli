import shutil
import subprocess
from pathlib import Path

from task_bundle.bundle.loader import load_bundle
from tests.synthetic_validation import create_synthetic_validation_bundle


def test_synthetic_validation_bundle_and_patch_order_are_valid(tmp_path: Path) -> None:
    bundle, source = create_synthetic_validation_bundle(
        tmp_path,
        base_image=f"golang@sha256:{'a' * 64}",
        platform="linux/amd64",
    )
    loaded = load_bundle(bundle)

    assert loaded.task.evaluation.prepare is None
    assert loaded.task.evaluation.repeat == 2
    assert len(loaded.task.evaluation.fail_to_pass) == 2

    for phase, patches in (
        ("baseline", ("test.patch",)),
        ("golden", ("golden.patch", "test.patch")),
    ):
        workspace = tmp_path / phase
        shutil.copytree(source, workspace)
        _git(workspace, "init", "-q")
        _git(workspace, "config", "core.hooksPath", "/dev/null")
        _git(workspace, "add", "-A")
        for patch in patches:
            patch_path = bundle / "evaluation/hidden" / patch
            _git(workspace, "apply", "--check", "--index", "--binary", str(patch_path))
            _git(workspace, "apply", "--index", "--binary", str(patch_path))

    assert "return a - b" in (tmp_path / "baseline/calculator.go").read_text()
    assert "return a + b" in (tmp_path / "golden/calculator.go").read_text()
    assert (tmp_path / "baseline/calculator_hidden_test.go").is_file()
    assert (tmp_path / "golden/calculator_hidden_test.go").is_file()


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        shell=False,
    )
