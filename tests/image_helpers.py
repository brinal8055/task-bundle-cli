import json
from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime
from pathlib import Path

from task_bundle.bundle.loader import LoadedBundle
from task_bundle.errors import ErrorCode, ErrorContext, TaskBundleError
from task_bundle.image.docker import DockerCommandResult
from task_bundle.image.models import DockerEnvironment
from task_bundle.models import ResolvedSource
from task_bundle.source.manifest import build_source_manifest, source_manifest_digest
from task_bundle.source.service import MaterializedSource
from task_bundle.source.validation import normalize_repository_url


class StaticSourceFactory:
    def __init__(self, root: Path, *, tree_sha: str = "b" * 40) -> None:
        if not root.exists():
            root.mkdir()
            (root / "README.md").write_text("source\n", encoding="utf-8")
            (root / "bin").mkdir()
            tool = root / "bin" / "tool"
            tool.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            tool.chmod(0o755)
            (root / "tool-link").symlink_to("bin/tool")
        self.root = root
        self.tree_sha = tree_sha
        self.calls = 0

    def __call__(
        self,
        bundle: LoadedBundle,
    ) -> AbstractContextManager[MaterializedSource]:
        return self._open(bundle)

    @contextmanager
    def _open(self, bundle: LoadedBundle) -> Iterator[MaterializedSource]:
        self.calls += 1
        manifest = build_source_manifest(self.root)
        digest = source_manifest_digest(manifest)
        files = [entry for entry in manifest.entries if entry.type == "file"]
        resolved = ResolvedSource(
            repository_url=normalize_repository_url(bundle.task.repository.url),
            requested_commit=bundle.task.repository.commit.lower(),
            resolved_commit=bundle.task.repository.commit.lower(),
            tree_sha=self.tree_sha,
            source_tree_digest=digest,
            source_entry_count=len(manifest.entries),
            source_total_bytes=sum(entry.size for entry in files),
            symlink_count=sum(entry.type == "symlink" for entry in manifest.entries),
            executable_file_count=sum(
                entry.type == "file" and entry.mode == "0755" for entry in manifest.entries
            ),
            git_executable="/usr/bin/git",
            git_version="test",
            created_at=datetime.now(UTC),
        )
        yield MaterializedSource(
            root=self.root,
            resolved=resolved,
            manifest=manifest,
            fetch_stdout="fetch ok\n",
            fetch_stderr="",
        )


class FakeDockerRunner:
    def __init__(
        self,
        *,
        fail_on: str | None = None,
        actual_platform: str | None = None,
    ) -> None:
        self.environment_info = DockerEnvironment(
            executable="/usr/local/bin/docker",
            client_version="28.0.0",
            server_version="28.0.0",
            host_os="linux",
            host_architecture="amd64",
            rootless=False,
        )
        self.fail_on = fail_on
        self.actual_platform = actual_platform
        self.commands: list[tuple[str, ...]] = []
        self.images: dict[str, dict[str, object]] = {}
        self.last_result: DockerCommandResult | None = None
        self.last_failure_result: DockerCommandResult | None = None
        self.build_count = 0
        self.context_top_level: tuple[str, ...] = ()
        self.context_paths: tuple[str, ...] = ()
        self.dockerfile = b""

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        timeout_seconds: int,
        error_code: ErrorCode,
        phase: str,
        description: str,
        check: bool = True,
        redact: Sequence[str] = (),
        timeout_code: ErrorCode | None = None,
    ) -> DockerCommandResult:
        del cwd, timeout_seconds, timeout_code
        command = tuple(args)
        self.commands.append(command)
        operation = command[0]
        if self.fail_on == operation:
            return self._result(
                stdout=f"failed output {self._build_arg_value(command)}",
                stderr="simulated Docker failure",
                exit_code=7,
                check=check,
                error_code=error_code,
                phase=phase,
                description=description,
                redact=redact,
            )
        if operation == "build":
            self._capture_build(command)
            return self._result(
                stdout=f"built with {self._build_arg_value(command)}\n",
                redact=redact,
            )
        if command[:2] == ("image", "inspect"):
            reference = command[-1]
            image = self.images.get(reference)
            if image is None:
                return self._result(
                    stderr="No such image",
                    exit_code=1,
                    check=check,
                    error_code=error_code,
                    phase=phase,
                    description=description,
                )
            return self._result(stdout=json.dumps(image))
        if operation == "create":
            return self._result(stdout=f"{'c' * 64}\n")
        if operation == "start":
            return self._result(stdout="smoke ok\n")
        if operation == "rm":
            return self._result(stdout=command[-1])
        return self._result()

    def _capture_build(self, command: tuple[str, ...]) -> None:
        self.build_count += 1
        tag = command[command.index("--tag") + 1]
        platform = command[command.index("--platform") + 1]
        labels: dict[str, str] = {}
        for index, value in enumerate(command):
            if value == "--label":
                name, label_value = command[index + 1].split("=", 1)
                labels[name] = label_value
        context = Path(command[-1])
        self.context_top_level = tuple(sorted(path.name for path in context.iterdir()))
        self.context_paths = tuple(
            sorted(
                path.relative_to(context).as_posix()
                for path in context.rglob("*")
                if not path.is_dir()
            )
        )
        self.dockerfile = (context / "Dockerfile").read_bytes()
        image_id = f"sha256:{self.build_count:064x}"
        actual = self.actual_platform or platform
        os_name, architecture, *variant = actual.split("/")
        self.images[tag] = {
            "Id": image_id,
            "RepoTags": [tag],
            "RepoDigests": [],
            "Os": os_name,
            "Architecture": architecture,
            "Variant": variant[0] if variant else "",
            "Created": "2026-07-29T00:00:00Z",
            "Config": {
                "User": "1000:1000",
                "WorkingDir": "/workspace/repo",
                "Labels": labels,
            },
            "Size": 1024,
        }

    def _result(
        self,
        *,
        stdout: str = "",
        stderr: str = "",
        exit_code: int = 0,
        check: bool = True,
        error_code: ErrorCode = ErrorCode.IMAGE_ERROR,
        phase: str = "docker",
        description: str = "run Docker",
        redact: Sequence[str] = (),
    ) -> DockerCommandResult:
        for secret in redact:
            if secret:
                stdout = stdout.replace(secret, "[REDACTED]")
                stderr = stderr.replace(secret, "[REDACTED]")
        result = DockerCommandResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            duration_ms=10,
            output_truncated=False,
        )
        self.last_result = result
        if exit_code != 0:
            self.last_failure_result = result
        if check and exit_code != 0:
            raise TaskBundleError(
                error_code,
                f"Docker failed while attempting to {description}.",
                ErrorContext(
                    phase=phase,
                    expected="Docker command success",
                    actual=f"Exit code {exit_code}: {stderr}",
                    corrective_action="Review Docker artifacts.",
                ),
            )
        return result

    @staticmethod
    def _build_arg_value(command: tuple[str, ...]) -> str:
        if "--build-arg" not in command:
            return ""
        return command[command.index("--build-arg") + 1]


def all_artifact_bytes(bundle: Path) -> bytes:
    artifacts = bundle / "artifacts"
    if not artifacts.exists():
        return b""
    return b"\n".join(
        path.read_bytes()
        for path in artifacts.rglob("*")
        if path.is_file() and not path.is_symlink()
    )
