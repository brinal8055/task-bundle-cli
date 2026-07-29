import re
from pathlib import PurePosixPath
from typing import NoReturn

from task_bundle.errors import ErrorCode, ErrorContext, TaskBundleError
from task_bundle.models import RuntimeSettings

_BASE_IMAGE = re.compile(
    r"^(?:[a-z0-9](?:[a-z0-9._:-]*[a-z0-9])?/)*"
    r"[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?@sha256:([0-9a-fA-F]{64})$"
)
_PLATFORM = re.compile(r"^[a-z0-9]+/[a-z0-9_]+(?:/[a-z0-9._-]+)?$")
_BUILD_ARG_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SUSPICIOUS_BUILD_ARG = re.compile(
    r"(?:TOKEN|PASSWORD|PASSWD|SECRET|API_KEY|PRIVATE_KEY|CREDENTIAL|"
    r"(?:^|_)AUTH(?:_|$)|AUTHORIZATION)",
    re.IGNORECASE,
)
_NUMERIC_USER = re.compile(r"^[1-9][0-9]*:[1-9][0-9]*$")
_SAFE_TMPFS = re.compile(r"^/[A-Za-z0-9._/-]+(?::[A-Za-z0-9_=.,+-]+)?$")
_SAFE_CONTAINER_PATH = re.compile(r"^/[A-Za-z0-9._/-]+$")


def validate_base_image_reference(value: str) -> str:
    if (
        not value
        or any(character.isspace() or ord(character) < 32 for character in value)
        or any(character in value for character in (";", "|", "`", "$", "\\"))
        or value.count("@") != 1
        or _BASE_IMAGE.fullmatch(value) is None
    ):
        _config_error(
            "Base image reference is unsafe or not digest-pinned.",
            "A lowercase Docker reference ending in @sha256:<64 hex>",
            "The configured base image does not meet the immutable-reference policy.",
            "Use a credential-free lowercase registry/repository digest reference.",
        )
    name, digest = value.rsplit("@sha256:", 1)
    return f"{name}@sha256:{digest.lower()}"


def validate_platform(value: str) -> str:
    normalized = value.lower()
    if value != normalized or _PLATFORM.fullmatch(normalized) is None:
        _config_error(
            "Image platform is invalid.",
            "A normalized platform such as linux/amd64 or linux/arm64/v8",
            value,
            "Use a lowercase Docker OS/architecture[/variant] platform.",
        )
    return normalized


def validate_task_id_for_build(value: str) -> None:
    if len(value) > 128 or any(ord(character) < 32 or ord(character) == 127 for character in value):
        _config_error(
            "Task ID cannot be represented safely in image metadata.",
            "At most 128 printable characters",
            "The configured task ID contains control data or is too long.",
            "Use a short printable task ID.",
        )


def validate_build_args(build_args: dict[str, str]) -> None:
    for name, value in build_args.items():
        if _BUILD_ARG_NAME.fullmatch(name) is None:
            _config_error(
                "Docker build argument name is invalid.",
                "A shell-independent identifier",
                name,
                "Rename the build argument using letters, digits, and underscores.",
            )
        if _SUSPICIOUS_BUILD_ARG.search(name):
            _config_error(
                "Secret-like Docker build arguments are forbidden.",
                "Build arguments that cannot carry credentials or secrets",
                f"Rejected build argument name: {name}",
                "Remove secret material from the image build.",
            )
        if "\0" in value or any(
            ord(character) < 32 and character not in {"\t", "\n"} for character in value
        ):
            _config_error(
                "Docker build argument value contains unsupported control data.",
                "A text build argument value without NUL or control bytes",
                f"Rejected value for build argument {name}",
                "Use a plain text non-secret build argument value.",
            )


def runtime_policy_from_settings(settings: RuntimeSettings) -> dict[str, object]:
    validate_runtime_settings(settings)
    return {
        "user": settings.user,
        "working_directory": settings.working_directory,
        "network": settings.network,
        "timeout_seconds": settings.timeout_seconds,
        "cpus": float(settings.cpus),
        "memory_mb": settings.memory_mb,
        "pids_limit": settings.pids_limit,
        "read_only_root": settings.read_only_root,
        "tmpfs": tuple(settings.tmpfs),
    }


def validate_runtime_settings(settings: RuntimeSettings) -> None:
    if _NUMERIC_USER.fullmatch(settings.user) is None:
        _config_error(
            "Runtime user must be a non-root numeric uid:gid.",
            "A numeric user such as 1000:1000",
            settings.user,
            "Configure a non-zero numeric uid and gid.",
        )
    working_directory = PurePosixPath(settings.working_directory)
    if (
        not working_directory.is_absolute()
        or ".." in working_directory.parts
        or working_directory.as_posix() != settings.working_directory
        or _SAFE_CONTAINER_PATH.fullmatch(settings.working_directory) is None
    ):
        _config_error(
            "Runtime working directory is invalid.",
            "A normalized absolute POSIX path",
            settings.working_directory,
            "Use a path such as /workspace/repo.",
        )
    for specification in settings.tmpfs:
        if _SAFE_TMPFS.fullmatch(specification) is None:
            _config_error(
                "Runtime tmpfs specification is invalid.",
                "An absolute container path with optional safe options",
                specification,
                "Use a value such as /tmp:size=512m.",
            )


def task_image_reference(task_id: str, bundle_digest: str, platform: str) -> str:
    slug = task_path_component(task_id)
    platform_slug = platform.replace("/", "-").replace("_", "-")
    return f"task-bundle/{slug}:{bundle_digest.removeprefix('sha256:')[:16]}-{platform_slug}"


def task_path_component(task_id: str) -> str:
    slug = re.sub(r"[^a-z0-9._-]+", "-", task_id.lower()).strip("._-")
    slug = re.sub(r"[._-]+", "-", slug)
    if not slug:
        slug = "task"
    slug = slug[:80].rstrip("._-") or "task"
    return slug


def _config_error(
    message: str,
    expected: str,
    actual: str,
    corrective_action: str,
) -> NoReturn:
    raise TaskBundleError(
        ErrorCode.BUILD_CONFIG_ERROR,
        message,
        ErrorContext(
            phase="build-config",
            expected=expected,
            actual=actual,
            corrective_action=corrective_action,
        ),
    )
