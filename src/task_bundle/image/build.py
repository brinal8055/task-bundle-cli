from dataclasses import dataclass
from pathlib import Path

from task_bundle.bundle.loader import LoadedBundle
from task_bundle.errors import ErrorCode
from task_bundle.image.context import BuildContext
from task_bundle.image.docker import DockerCommandResult, DockerRunner
from task_bundle.image.models import DockerCommandRecord
from task_bundle.image.validation import validate_build_args

LABEL_TASK_ID = "org.task-bundle.task-id"
LABEL_BUNDLE_DIGEST = "org.task-bundle.bundle-digest"
LABEL_SOURCE_COMMIT = "org.task-bundle.source-commit"
LABEL_SOURCE_DIGEST = "org.task-bundle.source-digest"
LABEL_CONTEXT_DIGEST = "org.task-bundle.build-context-digest"
LABEL_CLI_VERSION = "org.task-bundle.cli-version"


@dataclass(frozen=True, slots=True)
class ImageBuildOutcome:
    result: DockerCommandResult
    command: DockerCommandRecord
    labels: dict[str, str]


def required_image_labels(
    bundle: LoadedBundle,
    context: BuildContext,
    cli_version: str,
) -> dict[str, str]:
    return {
        LABEL_TASK_ID: bundle.task.task.id,
        LABEL_BUNDLE_DIGEST: bundle.bundle_input_digest,
        LABEL_SOURCE_COMMIT: bundle.task.repository.commit.lower(),
        LABEL_SOURCE_DIGEST: context.metadata.repository_source_digest,
        LABEL_CONTEXT_DIGEST: context.metadata.context_digest,
        LABEL_CLI_VERSION: cli_version,
    }


def build_image(
    runner: DockerRunner,
    *,
    bundle: LoadedBundle,
    context: BuildContext,
    image_reference: str,
    platform: str,
    no_cache: bool,
    cli_version: str,
) -> ImageBuildOutcome:
    settings = bundle.task.environment.build
    validate_build_args(settings.build_args)
    labels = required_image_labels(bundle, context, cli_version)
    args: list[str] = [
        "build",
        "--platform",
        platform,
        "--network",
        "default" if settings.network else "none",
    ]
    if no_cache or settings.no_cache:
        args.append("--no-cache")
    for name in sorted(settings.build_args):
        args.extend(("--build-arg", f"{name}={settings.build_args[name]}"))
    for name, value in sorted(labels.items()):
        args.extend(("--label", f"{name}={value}"))
    args.extend(
        (
            "--tag",
            image_reference,
            "--file",
            str(context.root / "Dockerfile"),
            str(context.root),
        )
    )
    command = build_command_record(
        bundle=bundle,
        labels=labels,
        image_reference=image_reference,
        platform=platform,
        no_cache=no_cache,
    )
    result = runner.run(
        args,
        cwd=Path.cwd(),
        timeout_seconds=settings.timeout_seconds,
        error_code=ErrorCode.IMAGE_BUILD_ERROR,
        phase="image-build",
        description=f"build task image {image_reference}",
        redact=(*settings.build_args.values(), str(context.root)),
        timeout_code=ErrorCode.BUILD_TIMEOUT,
    )
    return ImageBuildOutcome(result=result, command=command, labels=labels)


def build_command_record(
    *,
    bundle: LoadedBundle,
    labels: dict[str, str],
    image_reference: str,
    platform: str,
    no_cache: bool,
) -> DockerCommandRecord:
    settings = bundle.task.environment.build
    args: list[str] = [
        "docker",
        "build",
        "--platform",
        platform,
        "--network",
        "default" if settings.network else "none",
    ]
    if no_cache or settings.no_cache:
        args.append("--no-cache")
    for name in sorted(settings.build_args):
        args.extend(("--build-arg", f"{name}=<redacted>"))
    for name, value in sorted(labels.items()):
        args.extend(("--label", f"{name}={value}"))
    args.extend(
        (
            "--tag",
            image_reference,
            "--file",
            "<build-context>/Dockerfile",
            "<build-context>",
        )
    )
    return DockerCommandRecord(
        phase="image-build",
        argv=tuple(args),
        timeout_seconds=settings.timeout_seconds,
    )
