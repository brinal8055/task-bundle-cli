import json
import re
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn

from task_bundle.errors import ErrorCode, ErrorContext, TaskBundleError
from task_bundle.image.docker import DockerRunner
from task_bundle.image.models import ImageInspection

_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
_PROTECTED_SOURCE_PATH = PurePosixPath("/opt/task/repo")


def inspect_image(
    runner: DockerRunner,
    reference: str,
    *,
    expected_platform: str | None = None,
    expected_labels: dict[str, str] | None = None,
) -> ImageInspection:
    result = runner.run(
        ("image", "inspect", "--format", "{{json .}}", reference),
        cwd=Path.cwd(),
        timeout_seconds=30,
        error_code=ErrorCode.IMAGE_INSPECT_ERROR,
        phase="image-inspect",
        description=f"inspect task image {reference}",
    )
    inspection = _parse_inspection(reference, result.stdout)
    _reject_source_volume_conflicts(inspection)
    if expected_platform is not None and inspection.platform != expected_platform:
        _inspection_error(
            ErrorCode.PLATFORM_MISMATCH,
            "Built image platform does not match the requested platform.",
            expected_platform,
            inspection.platform,
            "Rebuild for the configured platform and verify emulation support.",
        )
    if expected_labels is not None:
        mismatches = {
            name: {"expected": value, "actual": inspection.labels.get(name)}
            for name, value in expected_labels.items()
            if inspection.labels.get(name) != value
        }
        if mismatches:
            raise TaskBundleError(
                ErrorCode.IMAGE_IDENTITY_ERROR,
                "Built image is missing required identity labels.",
                ErrorContext(
                    phase="image-inspect",
                    expected="All task identity labels to match the build request",
                    actual=f"{len(mismatches)} label mismatch(es)",
                    corrective_action="Rebuild the image from the generated context.",
                    details={"mismatches": mismatches},
                ),
            )
    if reference not in inspection.repo_tags:
        _inspection_error(
            ErrorCode.IMAGE_IDENTITY_ERROR,
            "Task image tag does not resolve to the inspected image.",
            reference,
            ", ".join(inspection.repo_tags) or "no repository tags",
            "Rebuild the deterministic task image tag.",
        )
    return inspection


def inspect_image_if_present(
    runner: DockerRunner,
    reference: str,
) -> ImageInspection | None:
    result = runner.run(
        ("image", "inspect", "--format", "{{json .}}", reference),
        cwd=Path.cwd(),
        timeout_seconds=30,
        error_code=ErrorCode.IMAGE_INSPECT_ERROR,
        phase="image-inspect",
        description=f"inspect existing task image {reference}",
        check=False,
    )
    if result.exit_code != 0:
        return None
    inspection = _parse_inspection(reference, result.stdout)
    _reject_source_volume_conflicts(inspection)
    return inspection


def _parse_inspection(reference: str, output: str) -> ImageInspection:
    try:
        raw: Any = json.loads(output)
    except json.JSONDecodeError as error:
        _inspection_error(
            ErrorCode.IMAGE_INSPECT_ERROR,
            "Docker image inspection output is malformed.",
            "A JSON image object",
            str(error),
            "Use a supported Docker CLI and daemon.",
        )
    if not isinstance(raw, dict):
        _inspection_error(
            ErrorCode.IMAGE_INSPECT_ERROR,
            "Docker image inspection output has an unexpected shape.",
            "A JSON image object",
            type(raw).__name__,
            "Use a supported Docker CLI and daemon.",
        )
    image_id = raw.get("Id")
    if not isinstance(image_id, str) or _IMAGE_ID.fullmatch(image_id) is None:
        _invalid_field("Id")
    os_name = raw.get("Os")
    architecture = raw.get("Architecture")
    if not isinstance(os_name, str) or not os_name:
        _invalid_field("Os")
    if not isinstance(architecture, str) or not architecture:
        _invalid_field("Architecture")
    variant_value = raw.get("Variant")
    variant = variant_value if isinstance(variant_value, str) and variant_value else None
    platform = f"{os_name}/{architecture}"
    if variant is not None:
        platform += f"/{variant}"
    config = raw.get("Config")
    if not isinstance(config, dict):
        config = {}
    labels_raw = config.get("Labels")
    labels = (
        {str(key): str(value) for key, value in labels_raw.items()}
        if isinstance(labels_raw, dict)
        else {}
    )
    declared_volumes = _declared_volumes(config.get("Volumes"))
    size = raw.get("Size")
    if not isinstance(size, int) or size < 0:
        size = 0
    return ImageInspection(
        image_id=image_id,
        reference=reference,
        repo_tags=_string_tuple(raw.get("RepoTags")),
        repo_digests=_string_tuple(raw.get("RepoDigests")),
        os=os_name,
        architecture=architecture,
        variant=variant,
        platform=platform,
        created=raw.get("Created") if isinstance(raw.get("Created"), str) else None,
        configured_user=(config.get("User") if isinstance(config.get("User"), str) else None),
        working_directory=(
            config.get("WorkingDir") if isinstance(config.get("WorkingDir"), str) else None
        ),
        declared_volumes=declared_volumes,
        labels=labels,
        size_bytes=size,
    )


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(sorted(item for item in value if isinstance(item, str)))


def _declared_volumes(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, dict):
        _volume_error(
            "Docker Config.Volumes must be an object.",
            details={"protected_path": _PROTECTED_SOURCE_PATH.as_posix()},
        )
    normalized: set[str] = set()
    malformed: list[str] = []
    for raw in value:
        if not isinstance(raw, str):
            malformed.append(type(raw).__name__)
            continue
        try:
            normalized.add(_normalize_container_path(raw))
        except ValueError:
            malformed.append(raw)
    if malformed:
        _volume_error(
            "Docker image declares malformed volume paths.",
            details={
                "malformed_volume_paths": sorted(malformed),
                "protected_path": _PROTECTED_SOURCE_PATH.as_posix(),
            },
        )
    return tuple(sorted(normalized))


def _normalize_container_path(value: str) -> str:
    if not value or "\0" in value or not value.startswith("/"):
        raise ValueError("volume path must be absolute")
    components = value.split("/")
    if any(component in {".", ".."} for component in components):
        raise ValueError("volume path contains unresolved components")
    normalized_components = [component for component in components if component]
    normalized = "/" + "/".join(normalized_components)
    path = PurePosixPath(normalized)
    if not path.is_absolute() or path.as_posix() != normalized:
        raise ValueError("volume path is not normalized")
    return normalized


def _reject_source_volume_conflicts(inspection: ImageInspection) -> None:
    protected_parts = _PROTECTED_SOURCE_PATH.parts
    conflicts = [
        volume
        for volume in inspection.declared_volumes
        if _paths_overlap(PurePosixPath(volume).parts, protected_parts)
    ]
    if conflicts:
        _volume_error(
            "Docker image volumes overlap the protected task source.",
            details={
                "conflicting_volume_paths": conflicts,
                "protected_path": _PROTECTED_SOURCE_PATH.as_posix(),
            },
        )


def _paths_overlap(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    shared = min(len(left), len(right))
    return left[:shared] == right[:shared]


def _volume_error(message: str, *, details: dict[str, object]) -> NoReturn:
    raise TaskBundleError(
        ErrorCode.IMAGE_SOURCE_VOLUME_CONFLICT,
        message,
        ErrorContext(
            phase="image-inspect",
            expected=(
                "No declared Docker volume at, above, or below /opt/task/repo"
            ),
            actual="The image volume declaration can shadow verified source bytes.",
            corrective_action=(
                "Remove or relocate the conflicting VOLUME declaration and rebuild."
            ),
            details=details,
        ),
    )


def _invalid_field(name: str) -> NoReturn:
    _inspection_error(
        ErrorCode.IMAGE_INSPECT_ERROR,
        "Docker image inspection output is incomplete.",
        f"A valid {name} field",
        "The field is absent or invalid.",
        "Use a supported Docker CLI and daemon.",
    )


def _inspection_error(
    code: ErrorCode,
    message: str,
    expected: str,
    actual: str,
    corrective_action: str,
) -> NoReturn:
    raise TaskBundleError(
        code,
        message,
        ErrorContext(
            phase="image-inspect",
            expected=expected,
            actual=actual,
            corrective_action=corrective_action,
        ),
    )
