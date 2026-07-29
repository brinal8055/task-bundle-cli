import os
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from task_bundle.errors import ErrorCode
from task_bundle.source.git import (
    GitCommandResult,
    SystemGitRunner,
)


@dataclass(frozen=True, slots=True)
class GitFixture:
    root: Path
    commit: str
    tree: str


def create_git_repository(
    root: Path,
    *,
    symlink: bool = True,
    gitlink: bool = False,
    gitmodules_only: bool = False,
    export_attributes: bool = False,
) -> GitFixture:
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Task Bundle Tests")
    _git(root, "config", "user.email", "tests@example.invalid")
    (root / "README.md").write_text("example\n", encoding="utf-8")
    (root / "bin").mkdir()
    script = root / "bin/tool"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)
    if symlink:
        (root / "tool-link").symlink_to("bin/tool")
    if gitmodules_only:
        (root / ".gitmodules").write_text(
            '[submodule "unused"]\n\tpath = unused\n',
            encoding="utf-8",
        )
    if export_attributes:
        (root / ".gitattributes").write_text(
            "archive-only.txt export-ignore\n"
            "literal-format.txt export-subst\n"
            "literal-ident.txt ident\n"
            "literal-lf.txt eol=crlf\n",
            encoding="utf-8",
        )
        (root / "archive-only.txt").write_text("must remain\n", encoding="utf-8")
        (root / "literal-format.txt").write_text(
            "$Format:%H$\n",
            encoding="utf-8",
        )
        (root / "literal-ident.txt").write_text("$Id$\n", encoding="utf-8")
        (root / "literal-lf.txt").write_bytes(b"line\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "fixture")
    commit = _git(root, "rev-parse", "HEAD")
    if gitlink:
        _git(root, "update-index", "--add", "--cacheinfo", f"160000,{commit},vendor/sub")
        _git(root, "commit", "-q", "-m", "add gitlink")
        commit = _git(root, "rev-parse", "HEAD")
    tree = _git(root, "rev-parse", f"{commit}^{{tree}}")
    return GitFixture(root=root, commit=commit, tree=tree)


class LocalFetchGitRunner:
    def __init__(self, delegate: SystemGitRunner, repository: Path) -> None:
        self.delegate = delegate
        self.repository = repository
        self.installation = delegate.installation

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        timeout_seconds: int,
        error_code: ErrorCode,
        phase: str,
        description: str,
    ) -> GitCommandResult:
        rewritten = list(args)
        if "fetch" in rewritten:
            for index, value in enumerate(rewritten):
                if value.startswith("https://fixture.invalid/"):
                    rewritten[index] = str(self.repository)
            rewritten[0:0] = ["-c", "protocol.file.allow=always"]
        return self.delegate.run(
            rewritten,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            error_code=error_code,
            phase=phase,
            description=description,
        )

    def write_blob(
        self,
        *,
        object_repository: Path,
        object_id: str,
        destination: Path,
        timeout_seconds: int,
    ) -> None:
        self.delegate.write_blob(
            object_repository=object_repository,
            object_id=object_id,
            destination=destination,
            timeout_seconds=timeout_seconds,
        )


def local_fetch_runner(home: Path, repository: Path) -> LocalFetchGitRunner:
    return LocalFetchGitRunner(SystemGitRunner.create(home), repository)


def _git(root: Path, *args: str) -> str:
    environment = {
        "PATH": os.environ.get("PATH", os.defpath),
        "HOME": str(root),
        "LC_ALL": "C",
        "LANG": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
    }
    result = subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", *args],
        cwd=root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        shell=False,
    )
    return result.stdout.strip()
