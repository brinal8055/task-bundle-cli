import json
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import BaseModel

from task_bundle.bundle.canonical import sha256_digest
from task_bundle.bundle.snapshot import write_bytes_atomic
from task_bundle.errors import ErrorCode, ErrorContext, TaskBundleError
from task_bundle.image.records import CommandStore
from task_bundle.image.validation import task_path_component


class ArtifactWriter:
    def __init__(
        self,
        *,
        bundle_root: Path,
        task_id: str,
        command_id: str,
        store: CommandStore,
    ) -> None:
        self.bundle_root = bundle_root
        self.command_id = command_id
        self.store = store
        self.root = bundle_root / "artifacts" / task_path_component(task_id) / command_id
        try:
            self.root.mkdir(parents=True, exist_ok=False)
        except OSError as error:
            raise TaskBundleError(
                ErrorCode.ARTIFACT_WRITE_ERROR,
                "Command artifact directory could not be created.",
                ErrorContext(
                    phase="artifacts",
                    expected="A new writable command artifact directory",
                    actual=str(error),
                    corrective_action="Check bundle permissions and command ID collisions.",
                    path=self.root,
                ),
            ) from error

    def write_model(
        self,
        relative: str,
        value: BaseModel,
        artifact_type: str,
    ) -> Path:
        return self.write_json(
            relative,
            value.model_dump(mode="json", exclude_none=False),
            artifact_type,
        )

    def write_json(
        self,
        relative: str,
        value: dict[str, Any] | list[Any],
        artifact_type: str,
    ) -> Path:
        payload = (
            json.dumps(
                value,
                sort_keys=True,
                indent=2,
                ensure_ascii=False,
            ).encode()
            + b"\n"
        )
        return self.write_bytes(relative, payload, artifact_type)

    def write_text(
        self,
        relative: str,
        value: str,
        artifact_type: str,
    ) -> Path:
        return self.write_bytes(relative, value.encode(), artifact_type)

    def write_bytes(
        self,
        relative: str,
        payload: bytes,
        artifact_type: str,
    ) -> Path:
        destination = self._destination(relative)
        write_bytes_atomic(
            payload,
            destination,
            error_code=ErrorCode.ARTIFACT_WRITE_ERROR,
            phase="artifacts",
            message="Command artifact could not be written atomically.",
        )
        relative_to_bundle = destination.relative_to(self.bundle_root).as_posix()
        self.store.artifact(
            self.command_id,
            artifact_type=artifact_type,
            relative_path=relative_to_bundle,
            sha256=sha256_digest(payload),
            size_bytes=len(payload),
        )
        return destination

    def _destination(self, relative: str) -> Path:
        logical = PurePosixPath(relative)
        if (
            not relative
            or logical.is_absolute()
            or ".." in logical.parts
            or logical.as_posix() != relative
        ):
            raise TaskBundleError(
                ErrorCode.ARTIFACT_WRITE_ERROR,
                "Artifact path is unsafe.",
                ErrorContext(
                    phase="artifacts",
                    expected="A normalized relative artifact path",
                    actual=relative,
                    corrective_action="Use a command-owned relative artifact path.",
                ),
            )
        return self.root / Path(relative)
