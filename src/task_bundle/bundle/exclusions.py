from pathlib import PurePosixPath

EXCLUDED_DIRECTORY_NAMES = {
    ".task",
    "artifacts",
    "__pycache__",
    "generated-context",
    "generated-build-context",
    ".generated-context",
}
EXCLUDED_FILE_SUFFIXES = {".pyc", ".pyo", ".db", ".sqlite", ".sqlite3"}
ATOMIC_SNAPSHOT_PREFIX = ".bundle.snapshot."


def is_generated_path(path: PurePosixPath) -> bool:
    if any(part in EXCLUDED_DIRECTORY_NAMES for part in path.parts):
        return True
    return (
        path.suffix.lower() in EXCLUDED_FILE_SUFFIXES
        or path.name.startswith(ATOMIC_SNAPSHOT_PREFIX)
    )


def is_runtime_owned_path(path: PurePosixPath) -> bool:
    return any(
        part in {".task", "artifacts", "generated-context", "generated-build-context"}
        for part in path.parts
    )
