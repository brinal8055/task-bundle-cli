import shutil
import subprocess
from pathlib import Path

import pytest

from task_bundle.errors import ErrorCode, TaskBundleError
from task_bundle.models import EvaluationPhase
from task_bundle.validation.patch import validate_patch


def _write_patch(path: Path, relative: str) -> Path:
    path.write_text(
        f"diff --git a/{relative} b/{relative}\n"
        f"--- a/{relative}\n"
        f"+++ b/{relative}\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n",
        encoding="utf-8",
    )
    return path


def test_patch_accepts_normalized_binary_capable_git_diff(tmp_path: Path) -> None:
    patch = _write_patch(tmp_path / "test.patch", "tests/test_api.py")

    assert validate_patch(
        patch,
        phase=EvaluationPhase.BASELINE,
        repeat_index=1,
        golden=False,
    ) == patch.read_bytes()


@pytest.mark.parametrize(
    "relative",
    [
        "../escape",
        "/absolute",
        ".git/config",
        "nested/.git/hooks/pre-commit",
        r"windows\path",
    ],
)
def test_patch_rejects_unsafe_paths(tmp_path: Path, relative: str) -> None:
    patch = _write_patch(tmp_path / "test.patch", relative)

    with pytest.raises(TaskBundleError) as caught:
        validate_patch(
            patch,
            phase=EvaluationPhase.BASELINE,
            repeat_index=2,
            golden=False,
        )

    assert caught.value.code == ErrorCode.TEST_PATCH_APPLY_ERROR
    assert caught.value.context.details == {"repeat_index": 2}


def test_golden_patch_uses_phase_specific_error(tmp_path: Path) -> None:
    patch = _write_patch(tmp_path / "golden.patch", ".git/config")

    with pytest.raises(TaskBundleError) as caught:
        validate_patch(
            patch,
            phase=EvaluationPhase.GOLDEN,
            repeat_index=1,
            golden=True,
        )

    assert caught.value.code == ErrorCode.GOLDEN_PATCH_APPLY_ERROR


def test_binary_patch_and_executable_mode_change_are_preserved(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    binary = repository / "fixture.bin"
    binary.write_bytes(b"\x00\xffold\n")
    _git(repository, "init", "-q")
    _git(repository, "config", "user.name", "Task Bundle Tests")
    _git(repository, "config", "user.email", "tests@example.invalid")
    _git(repository, "add", "-A")
    _git(repository, "commit", "-q", "-m", "baseline")
    target = tmp_path / "target"
    shutil.copytree(repository, target)
    _git(target, "update-index", "--refresh")
    binary.write_bytes(b"\x00\xfenew\x80\n")
    binary.chmod(0o755)
    patch = tmp_path / "binary.patch"
    patch.write_bytes(_git_bytes(repository, "diff", "--binary", "HEAD"))

    validate_patch(
        patch,
        phase=EvaluationPhase.GOLDEN,
        repeat_index=1,
        golden=True,
    )
    _git(target, "apply", "--check", "--index", "--binary", str(patch))
    _git(target, "apply", "--index", "--binary", str(patch))

    assert (target / "fixture.bin").read_bytes() == b"\x00\xfenew\x80\n"
    assert (target / "fixture.bin").stat().st_mode & 0o111


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        shell=False,
    )


def _git_bytes(root: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        shell=False,
    ).stdout
