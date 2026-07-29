import posixpath
import re
from pathlib import PurePosixPath
from urllib.parse import SplitResult, urlsplit, urlunsplit

from task_bundle.errors import ErrorCode, ErrorContext, TaskBundleError

_FULL_SHA = re.compile(r"^[0-9a-fA-F]{40}$")


def validate_repository_url(value: str) -> str:
    try:
        return normalize_repository_url(value)
    except ValueError as error:
        raise TaskBundleError(
            ErrorCode.SOURCE_URL_ERROR,
            "Repository URL is not an allowed public HTTPS URL.",
            ErrorContext(
                phase="source-url",
                expected="A credential-free public HTTPS Git repository URL",
                actual=str(error),
                corrective_action="Use an HTTPS repository URL without credentials or parameters.",
            ),
        ) from error


def normalize_repository_url(value: str) -> str:
    if not value or any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("URL is empty or contains control characters")
    if value.startswith("ext::"):
        raise ValueError("Git remote-helper syntax is not allowed")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise ValueError(f"URL cannot be parsed: {error}") from error
    if parsed.scheme.lower() != "https":
        raise ValueError("scheme must be exactly https")
    if parsed.hostname is None:
        raise ValueError("host is required")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("embedded credentials are not allowed")
    if parsed.query:
        raise ValueError("query strings are not allowed")
    if parsed.fragment:
        raise ValueError("fragments are not allowed")
    path = parsed.path.rstrip("/")
    if path in {"", "/"}:
        raise ValueError("repository path is required")
    hostname = parsed.hostname.lower()
    if ":" in hostname:
        hostname = f"[{hostname}]"
    netloc = hostname if port is None else f"{hostname}:{port}"
    normalized = SplitResult("https", netloc, path, "", "")
    return urlunsplit(normalized)


def validate_commit_sha(value: str) -> str:
    try:
        return normalize_commit_sha(value)
    except ValueError as error:
        raise TaskBundleError(
            ErrorCode.SOURCE_COMMIT_FORMAT_ERROR,
            "Repository commit is not an exact full SHA.",
            ErrorContext(
                phase="source-commit",
                expected="Exactly 40 hexadecimal characters",
                actual=str(error),
                corrective_action="Pin the task to a complete commit object ID.",
            ),
        ) from error


def normalize_commit_sha(value: str) -> str:
    if not _FULL_SHA.fullmatch(value):
        raise ValueError("commit must contain exactly 40 hexadecimal characters")
    return value.lower()


def validate_symlink_target(path: str, target: str) -> None:
    if not target or "\\" in target or PurePosixPath(target).is_absolute():
        raise ValueError("symlink target must be non-empty and relative")
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(path), target))
    if resolved == ".." or resolved.startswith("../") or resolved.startswith("/"):
        raise ValueError("symlink target escapes the source root")
