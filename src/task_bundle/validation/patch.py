import shlex
from pathlib import Path, PurePosixPath
from typing import NoReturn

from task_bundle.errors import ErrorCode, ErrorContext, TaskBundleError
from task_bundle.models import EvaluationPhase

MAX_PATCH_BYTES = 20_971_520


def validate_patch(
    path: Path,
    *,
    phase: EvaluationPhase,
    repeat_index: int,
    golden: bool,
) -> bytes:
    code = ErrorCode.GOLDEN_PATCH_APPLY_ERROR if golden else ErrorCode.TEST_PATCH_APPLY_ERROR
    try:
        payload = path.read_bytes()
    except OSError as error:
        _patch_error(
            code,
            "Trusted patch could not be read.",
            str(error),
            phase,
            repeat_index,
            path,
        )
    validate_patch_bytes(
        payload,
        code=code,
        phase=phase,
        repeat_index=repeat_index,
        artifact=path,
        max_bytes=MAX_PATCH_BYTES,
    )
    return payload


def validate_patch_bytes(
    payload: bytes,
    *,
    code: ErrorCode,
    phase: EvaluationPhase,
    repeat_index: int,
    artifact: Path,
    max_bytes: int,
    allow_empty: bool = False,
) -> frozenset[str]:
    if len(payload) > max_bytes:
        _patch_error(
            code,
            "Trusted patch exceeds the validation size limit.",
            f"{len(payload)} bytes",
            phase,
            repeat_index,
            artifact,
        )
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        _patch_error(
            code,
            "Trusted patch headers are not UTF-8.",
            str(error),
            phase,
            repeat_index,
            artifact,
        )
    if allow_empty and not payload:
        return frozenset()
    paths = _patch_paths(text, code, phase, repeat_index, artifact)
    if not paths:
        _patch_error(
            code,
            "Trusted patch contains no file paths.",
            "No diff paths were found.",
            phase,
            repeat_index,
            artifact,
        )
    return frozenset(paths)


def _patch_paths(
    text: str,
    code: ErrorCode,
    phase: EvaluationPhase,
    repeat_index: int,
    artifact: Path,
) -> set[str]:
    paths: set[str] = set()
    diff_headers = 0
    for line in text.splitlines():
        candidates: tuple[str, ...] = ()
        if line.startswith("diff --git "):
            diff_headers += 1
            if "\\" in line:
                _patch_error(
                    code,
                    "Patch diff header contains an unsupported escaped path.",
                    line[:200],
                    phase,
                    repeat_index,
                    artifact,
                )
            try:
                fields = shlex.split(line)
            except ValueError as error:
                _patch_error(
                    code,
                    "Patch header is malformed.",
                    str(error),
                    phase,
                    repeat_index,
                    artifact,
                )
            if len(fields) != 4:
                _patch_error(
                    code,
                    "Patch diff header is malformed.",
                    line[:200],
                    phase,
                    repeat_index,
                    artifact,
                )
            candidates = (fields[2], fields[3])
        elif line.startswith(("rename from ", "rename to ", "copy from ", "copy to ")):
            _patch_error(
                code,
                "Patch rename and copy metadata is unsupported.",
                line[:200],
                phase,
                repeat_index,
                artifact,
            )
        elif line.startswith(("--- ", "+++ ")):
            value = line[4:].split("\t", 1)[0]
            if value != "/dev/null":
                if "\\" in value:
                    _patch_error(
                        code,
                        "Patch file header contains an unsupported escaped path.",
                        line[:200],
                        phase,
                        repeat_index,
                        artifact,
                    )
                try:
                    decoded = shlex.split(value)
                except ValueError as error:
                    _patch_error(
                        code,
                        "Patch file header is malformed.",
                        str(error),
                        phase,
                        repeat_index,
                        artifact,
                    )
                if len(decoded) != 1:
                    _patch_error(
                        code,
                        "Patch file header is malformed.",
                        line[:200],
                        phase,
                        repeat_index,
                        artifact,
                    )
                candidates = (decoded[0],)
        for candidate in candidates:
            normalized = candidate
            if normalized.startswith(("a/", "b/")):
                normalized = normalized[2:]
            _validate_patch_path(normalized, code, phase, repeat_index, artifact)
            paths.add(normalized)
    if diff_headers == 0:
        _patch_error(
            code,
            "Trusted patch contains no Git diff header.",
            "A `diff --git` header is required.",
            phase,
            repeat_index,
            artifact,
        )
    return paths


def _validate_patch_path(
    value: str,
    code: ErrorCode,
    phase: EvaluationPhase,
    repeat_index: int,
    artifact: Path,
) -> None:
    logical = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or logical.is_absolute()
        or value in {".", ".."}
        or ".." in logical.parts
        or logical.as_posix() != value
        or ".git" in logical.parts
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        _patch_error(
            code,
            "Trusted patch contains an unsafe path.",
            value[:200],
            phase,
            repeat_index,
            artifact,
        )


def _patch_error(
    code: ErrorCode,
    message: str,
    actual: str,
    phase: EvaluationPhase,
    repeat_index: int,
    artifact: Path,
) -> NoReturn:
    raise TaskBundleError(
        code,
        message,
        ErrorContext(
            phase=phase.value,
            expected="A bounded Git binary patch with normalized repository-relative paths",
            actual=actual,
            corrective_action="Regenerate the trusted patch without unsafe paths.",
            artifact=artifact,
            details={"repeat_index": repeat_index},
        ),
    )
