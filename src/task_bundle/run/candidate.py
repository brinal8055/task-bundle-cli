import os
import subprocess
from pathlib import Path
from typing import NoReturn

from task_bundle.bundle.canonical import sha256_digest
from task_bundle.errors import ErrorCode, ErrorContext, TaskBundleError
from task_bundle.models import EvaluationPhase, SolverConfig, SourceManifest
from task_bundle.run.filesystem import (
    build_filesystem_manifest,
    copy_manifest_tree,
    manifests_equal,
    read_manifest_file,
)
from task_bundle.run.models import (
    CandidateTree,
    FilesystemManifest,
    ManifestSymlink,
)
from task_bundle.source.git import detect_git, sanitized_git_environment
from task_bundle.validation.patch import MAX_PATCH_BYTES, validate_patch_bytes

_GIT_CONFIG = (
    "core.hooksPath=/dev/null",
    "core.autocrlf=false",
    "core.eol=lf",
    "core.filemode=true",
    "core.symlinks=true",
    "credential.helper=",
)


class CandidateBuilder:
    def __init__(self, temporary_root: Path) -> None:
        self.home = temporary_root / "git-home"
        self.environment = sanitized_git_environment(self.home)
        self.git = detect_git(self.environment).executable
        self.repository = temporary_root / "objects.git"
        self._run(("init", "--bare", str(self.repository)), cwd=temporary_root)

    def build(
        self,
        *,
        baseline_root: Path,
        baseline_manifest: FilesystemManifest,
        candidate_root: Path,
        candidate_manifest: FilesystemManifest,
        expected_baseline_tree: str,
        solver: SolverConfig,
    ) -> tuple[CandidateTree, bytes]:
        baseline_tree = self._write_tree(
            baseline_root,
            baseline_manifest,
            self.repository.parent / "baseline.index",
        )
        if baseline_tree != expected_baseline_tree:
            _candidate_error(
                ErrorCode.CANDIDATE_TREE_MISMATCH,
                "Reconstructed baseline tree does not match the locked Git tree.",
                f"expected {expected_baseline_tree}, observed {baseline_tree}",
            )
        candidate_tree = self._write_tree(
            candidate_root,
            candidate_manifest,
            self.repository.parent / "candidate.index",
        )
        patch = self._run(
            (
                "diff-tree",
                "--no-commit-id",
                "--binary",
                "--full-index",
                "--no-renames",
                "-r",
                baseline_tree,
                candidate_tree,
            ),
            cwd=self.repository,
            binary_output=True,
        )
        assert isinstance(patch, bytes)
        changed_paths = _changed_paths(baseline_manifest, candidate_manifest)
        candidate = CandidateTree(
            baseline_tree_sha=baseline_tree,
            candidate_tree_sha=candidate_tree,
            candidate_patch_sha256=sha256_digest(patch),
            candidate_patch_size=len(patch),
            changed_paths=changed_paths,
        )
        self._round_trip(
            baseline_root=baseline_root,
            baseline_manifest=baseline_manifest,
            candidate_manifest=candidate_manifest,
            patch=patch,
            solver=solver,
        )
        return candidate, patch

    def _write_tree(
        self,
        root: Path,
        manifest: FilesystemManifest,
        index: Path,
    ) -> str:
        index.unlink(missing_ok=True)
        environment = {**self.environment, "GIT_INDEX_FILE": str(index)}
        for entry in manifest.entries:
            source = root / Path(entry.path)
            if isinstance(entry, ManifestSymlink):
                try:
                    target = os.readlink(source)
                    if target != entry.target:
                        raise OSError("symlink target changed during tree construction")
                    payload = target.encode()
                except (OSError, UnicodeEncodeError) as error:
                    _candidate_error(
                        ErrorCode.CANDIDATE_TREE_ERROR,
                        "Candidate symlink could not be hashed.",
                        f"{entry.path}: {error}",
                    )
                mode = "120000"
            else:
                payload = read_manifest_file(
                    root,
                    entry,
                    phase="candidate-tree",
                    error_code=ErrorCode.CANDIDATE_TREE_ERROR,
                )
                mode = "100755" if entry.mode == "0755" else "100644"
            object_id = self._run(
                ("hash-object", "-w", "--stdin"),
                cwd=self.repository,
                input_bytes=payload,
            )
            assert isinstance(object_id, str)
            self._run(
                (
                    "update-index",
                    "--add",
                    "--cacheinfo",
                    f"{mode},{object_id.strip()},{entry.path}",
                ),
                cwd=self.repository,
                environment=environment,
            )
        tree = self._run(
            ("write-tree",),
            cwd=self.repository,
            environment=environment,
        )
        assert isinstance(tree, str)
        return tree.strip()

    def _round_trip(
        self,
        *,
        baseline_root: Path,
        baseline_manifest: FilesystemManifest,
        candidate_manifest: FilesystemManifest,
        patch: bytes,
        solver: SolverConfig,
    ) -> None:
        roundtrip = self.repository.parent / "roundtrip"
        copy_manifest_tree(
            baseline_root,
            roundtrip,
            baseline_manifest,
            phase="candidate-roundtrip",
            error_code=ErrorCode.CANDIDATE_PATCH_ROUNDTRIP_ERROR,
        )
        if patch:
            self._run(
                ("apply", "--binary", "--whitespace=nowarn", "-"),
                cwd=roundtrip,
                input_bytes=patch,
                error_code=ErrorCode.CANDIDATE_PATCH_ROUNDTRIP_ERROR,
            )
        rebuilt = build_filesystem_manifest(
            roundtrip,
            phase="candidate-roundtrip",
            error_code=ErrorCode.CANDIDATE_PATCH_ROUNDTRIP_ERROR,
            allow_symlinks=True,
            max_files=solver.max_context_files,
            max_total_bytes=solver.max_context_bytes,
            max_file_bytes=solver.max_context_bytes,
        )
        if not manifests_equal(rebuilt, candidate_manifest):
            _candidate_error(
                ErrorCode.CANDIDATE_PATCH_ROUNDTRIP_ERROR,
                "Candidate patch does not reconstruct the exported workspace.",
                "Round-trip manifest differs from the solver export manifest.",
            )

    def _run(
        self,
        args: tuple[str, ...],
        *,
        cwd: Path,
        input_bytes: bytes | None = None,
        binary_output: bool = False,
        environment: dict[str, str] | None = None,
        error_code: ErrorCode = ErrorCode.CANDIDATE_TREE_ERROR,
    ) -> str | bytes:
        command = [self.git]
        for setting in _GIT_CONFIG:
            command.extend(("-c", setting))
        command.extend(args)
        try:
            result = subprocess.run(
                command,
                cwd=cwd,
                env=environment or self.environment,
                input=input_bytes,
                capture_output=True,
                shell=False,
                timeout=120,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            _candidate_error(error_code, "Trusted Git plumbing failed.", str(error))
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace").strip()[:2000]
            _candidate_error(
                error_code,
                "Trusted Git plumbing rejected the candidate.",
                f"exit {result.returncode}: {stderr}",
            )
        if binary_output:
            return result.stdout
        return result.stdout.decode("ascii").strip()


def verify_baseline_manifest(
    exported: FilesystemManifest,
    locked: SourceManifest,
) -> None:
    exported_rows = tuple(entry.model_dump(mode="json") for entry in exported.entries)
    locked_rows = tuple(entry.model_dump(mode="json") for entry in locked.entries)
    if exported_rows != locked_rows:
        _candidate_error(
            ErrorCode.CANDIDATE_TREE_MISMATCH,
            "Image baseline source does not match its locked source manifest.",
            "Exported /opt/task/repo differs from .task/source.manifest.json.",
        )


def enforce_patch_policy(
    *,
    candidate: CandidateTree,
    patch: bytes,
    candidate_manifest: FilesystemManifest,
    hidden_patch: bytes,
    solver: SolverConfig,
) -> None:
    if len(patch) > solver.max_patch_bytes:
        _candidate_error(
            ErrorCode.CANDIDATE_PATCH_TOO_LARGE,
            "Candidate patch exceeds the configured size limit.",
            f"{len(patch)} bytes exceeds {solver.max_patch_bytes}",
        )
    if len(candidate.changed_paths) > solver.max_changed_files:
        _candidate_error(
            ErrorCode.PATCH_POLICY_ERROR,
            "Candidate changes too many files.",
            f"{len(candidate.changed_paths)} files exceeds {solver.max_changed_files}",
        )
    if candidate_manifest.entry_count > solver.max_context_files:
        _candidate_error(
            ErrorCode.CANDIDATE_FILE_LIMIT_ERROR,
            "Candidate tree contains too many entries.",
            str(candidate_manifest.entry_count),
        )
    if candidate_manifest.total_bytes > solver.max_context_bytes:
        _candidate_error(
            ErrorCode.CANDIDATE_FILE_LIMIT_ERROR,
            "Candidate tree exceeds the configured byte limit.",
            str(candidate_manifest.total_bytes),
        )
    parsed = validate_patch_bytes(
        patch,
        code=ErrorCode.PATCH_POLICY_ERROR,
        phase=EvaluationPhase.CANDIDATE,
        repeat_index=1,
        artifact=Path("solver/candidate.patch"),
        max_bytes=solver.max_patch_bytes,
        allow_empty=True,
    )
    if parsed != frozenset(candidate.changed_paths):
        _candidate_error(
            ErrorCode.PATCH_POLICY_ERROR,
            "Candidate patch paths do not match the exported tree changes.",
            "Generated patch and manifest path sets differ.",
        )
    hidden_paths = validate_patch_bytes(
        hidden_patch,
        code=ErrorCode.PATCH_POLICY_ERROR,
        phase=EvaluationPhase.CANDIDATE,
        repeat_index=1,
        artifact=Path("hidden-test-patch"),
        max_bytes=MAX_PATCH_BYTES,
    )
    conflicts = tuple(sorted(parsed & hidden_paths))
    if conflicts:
        raise TaskBundleError(
            ErrorCode.PATCH_CONFLICT,
            "Candidate changes overlap hidden-test-owned paths.",
            ErrorContext(
                phase="patch-policy",
                expected="No candidate path to overlap a hidden-test patch path",
                actual=", ".join(conflicts),
                corrective_action="Remove changes to the reported protected paths.",
                details={"conflicting_paths": list(conflicts)},
            ),
        )


def _changed_paths(
    baseline: FilesystemManifest,
    candidate: FilesystemManifest,
) -> tuple[str, ...]:
    before = {entry.path: entry for entry in baseline.entries}
    after = {entry.path: entry for entry in candidate.entries}
    return tuple(
        sorted(
            path
            for path in before.keys() | after.keys()
            if before.get(path) != after.get(path)
        )
    )


def _candidate_error(code: ErrorCode, message: str, actual: str) -> NoReturn:
    raise TaskBundleError(
        code,
        message,
        ErrorContext(
            phase="candidate-extraction",
            expected="A safe exact Git tree and round-trippable binary patch",
            actual=actual[:2000],
            corrective_action="Inspect solver export and candidate artifacts.",
        ),
    )
