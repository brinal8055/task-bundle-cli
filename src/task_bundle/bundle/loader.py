from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from task_bundle.bundle.canonical import canonical_json_bytes, sha256_digest
from task_bundle.bundle.manifest import build_input_manifest
from task_bundle.bundle.paths import resolve_bundle_path
from task_bundle.bundle.yaml_loader import load_yaml_mapping
from task_bundle.errors import ErrorCode, ErrorContext, TaskBundleError
from task_bundle.models import (
    BaseImageEnvironment,
    DockerfileEnvironment,
    EvaluationInputDigests,
    InputManifestEntry,
    PublicFiles,
    TaskConfig,
)


@dataclass(frozen=True, slots=True)
class LoadedBundle:
    root: Path
    task: TaskConfig
    canonical_config: bytes
    canonical_config_sha256: str
    input_manifest: tuple[InputManifestEntry, ...]
    bundle_input_digest: str
    evaluation_inputs: EvaluationInputDigests


def load_bundle(bundle_root: Path) -> LoadedBundle:
    root = _resolve_bundle_root(bundle_root)
    task_path = resolve_bundle_path(root, "task.yaml", "file")
    raw = load_yaml_mapping(task_path.absolute)
    try:
        task = TaskConfig.model_validate(raw)
    except ValidationError as error:
        raise TaskBundleError(
            ErrorCode.BUNDLE_SCHEMA_ERROR,
            "Task configuration does not match schema version 1.",
            ErrorContext(
                phase="bundle-schema",
                expected="A valid strict task configuration",
                actual=f"{error.error_count()} validation error(s)",
                corrective_action="Correct the reported task.yaml fields.",
                path=Path("task.yaml"),
                details={"errors": error.errors(include_url=False)},
            ),
        ) from error

    normalized, files, trees = _normalize_and_collect_paths(root, task)
    canonical_config = canonical_json_bytes(normalized)
    canonical_digest = sha256_digest(canonical_config)
    manifest = build_input_manifest(root, files, trees, execution_trees={"evaluation"})
    evaluation_inputs = _evaluation_digests(normalized, manifest)
    digest_document: dict[str, Any] = {
        "schema_version": "1",
        "canonical_config_sha256": canonical_digest,
        "inputs": [entry.model_dump(mode="json") for entry in manifest],
    }
    bundle_digest = sha256_digest(canonical_json_bytes(digest_document))
    return LoadedBundle(
        root=root,
        task=normalized,
        canonical_config=canonical_config,
        canonical_config_sha256=canonical_digest,
        input_manifest=manifest,
        bundle_input_digest=bundle_digest,
        evaluation_inputs=evaluation_inputs,
    )


def _resolve_bundle_root(bundle_root: Path) -> Path:
    try:
        root = bundle_root.resolve(strict=True)
    except OSError as error:
        raise TaskBundleError(
            ErrorCode.BUNDLE_NOT_FOUND,
            "Bundle root does not exist.",
            ErrorContext(
                phase="bundle-load",
                expected="An existing task bundle directory",
                actual=str(error),
                corrective_action="Provide the path to an existing task bundle.",
                path=bundle_root,
            ),
        ) from error
    if not root.is_dir():
        raise TaskBundleError(
            ErrorCode.BUNDLE_NOT_FOUND,
            "Bundle root is not a directory.",
            ErrorContext(
                phase="bundle-load",
                expected="A task bundle directory",
                actual="The supplied path is not a directory",
                corrective_action="Provide the directory containing task.yaml.",
                path=bundle_root,
            ),
        )
    return root


def _normalize_and_collect_paths(
    root: Path,
    task: TaskConfig,
) -> tuple[TaskConfig, set[str], set[str]]:
    files: set[str] = set()
    trees: set[str] = set()

    description = _file(root, task.public.description, files)
    requirements = _optional_file(root, task.public.requirements, files)
    interface = _optional_file(root, task.public.interface, files)
    public = PublicFiles(
        description=description,
        requirements=requirements,
        interface=interface,
    )

    environment: DockerfileEnvironment | BaseImageEnvironment
    if isinstance(task.environment, DockerfileEnvironment):
        dockerfile = _file(root, task.environment.dockerfile, files)
        context = _directory(root, task.environment.context, trees)
        environment = task.environment.model_copy(
            update={"dockerfile": dockerfile, "context": context}
        )
    elif isinstance(task.environment, BaseImageEnvironment):
        environment = task.environment
    else:
        raise AssertionError("Pydantic returned an unsupported environment model")

    test_patch = _file(root, task.evaluation.test_patch, files)
    golden_patch = _file(root, task.evaluation.golden_patch, files)
    evaluation_root = _directory(root, "evaluation", trees)
    evaluation = task.evaluation.model_copy(
        update={"test_patch": test_patch, "golden_patch": golden_patch}
    )

    normalized = task.model_copy(
        update={"public": public, "environment": environment, "evaluation": evaluation}
    )
    if evaluation_root != "evaluation":
        raise AssertionError("Evaluation root normalization changed unexpectedly")
    return normalized, files, trees


def _file(root: Path, configured: str, files: set[str]) -> str:
    resolved = resolve_bundle_path(root, configured, "file")
    files.add(resolved.relative)
    return resolved.relative


def _optional_file(root: Path, configured: str | None, files: set[str]) -> str | None:
    return None if configured is None else _file(root, configured, files)


def _directory(root: Path, configured: str, trees: set[str]) -> str:
    resolved = resolve_bundle_path(root, configured, "directory")
    trees.add(resolved.relative)
    return resolved.relative


def _evaluation_digests(
    task: TaskConfig,
    manifest: tuple[InputManifestEntry, ...],
) -> EvaluationInputDigests:
    by_path = {entry.path: entry for entry in manifest}
    test_patch = by_path[task.evaluation.test_patch]
    golden_patch = by_path[task.evaluation.golden_patch]
    harness = [
        entry.model_dump(mode="json")
        for entry in manifest
        if entry.path.startswith("evaluation/")
        and not entry.path.startswith("evaluation/hidden/")
    ]
    selector_document = {
        "pass_to_pass": [
            item.model_dump(mode="json") for item in task.evaluation.pass_to_pass
        ],
        "fail_to_pass": [
            item.model_dump(mode="json") for item in task.evaluation.fail_to_pass
        ],
    }
    return EvaluationInputDigests(
        test_patch_sha256=test_patch.sha256,
        golden_patch_sha256=golden_patch.sha256,
        harness_sha256=sha256_digest(canonical_json_bytes({"inputs": harness})),
        selectors_sha256=sha256_digest(canonical_json_bytes(selector_document)),
    )
