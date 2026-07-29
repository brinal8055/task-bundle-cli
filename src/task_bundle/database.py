import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 1

_MIGRATION_1 = """
CREATE TABLE commands (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    command_type TEXT NOT NULL,
    command_status TEXT NOT NULL,
    outcome_status TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    bundle_digest TEXT,
    image_id TEXT,
    exit_code INTEGER
);

CREATE TABLE command_events (
    id INTEGER PRIMARY KEY,
    command_id TEXT NOT NULL REFERENCES commands(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    created_at TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE validations (
    id INTEGER PRIMARY KEY,
    command_id TEXT NOT NULL REFERENCES commands(id) ON DELETE CASCADE,
    bundle_digest TEXT NOT NULL,
    image_id TEXT NOT NULL,
    runtime_policy_digest TEXT NOT NULL,
    harness_digest TEXT NOT NULL,
    selector_digest TEXT NOT NULL,
    outcome TEXT NOT NULL
);

CREATE TABLE solver_runs (
    id INTEGER PRIMARY KEY,
    command_id TEXT NOT NULL REFERENCES commands(id) ON DELETE CASCADE,
    solver_type TEXT NOT NULL,
    command_json TEXT,
    context_digest TEXT,
    duration_ms INTEGER,
    patch_digest TEXT,
    changed_paths_json TEXT,
    outcome TEXT NOT NULL
);

CREATE TABLE evaluations (
    id INTEGER PRIMARY KEY,
    command_id TEXT NOT NULL REFERENCES commands(id) ON DELETE CASCADE,
    phase TEXT NOT NULL,
    harness_status TEXT NOT NULL,
    duration_ms INTEGER,
    runner_exit_code INTEGER,
    patch_digest TEXT,
    outcome TEXT NOT NULL
);

CREATE TABLE test_results (
    id INTEGER PRIMARY KEY,
    evaluation_id INTEGER NOT NULL REFERENCES evaluations(id) ON DELETE CASCADE,
    test_group TEXT NOT NULL,
    requested_selector TEXT NOT NULL,
    observed_id TEXT,
    expected_status TEXT NOT NULL,
    actual_status TEXT NOT NULL,
    duration_ms INTEGER,
    failure_message TEXT
);

CREATE TABLE artifacts (
    id INTEGER PRIMARY KEY,
    command_id TEXT NOT NULL REFERENCES commands(id) ON DELETE CASCADE,
    artifact_type TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL
);
"""
_MIGRATIONS = (_MIGRATION_1,)


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA busy_timeout = 5000")
            self._migrate(connection)
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _migrate(self, connection: sqlite3.Connection) -> None:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            message = f"Unsupported database schema version {version}; expected {SCHEMA_VERSION}"
            raise RuntimeError(message)
        for target_version in range(version + 1, SCHEMA_VERSION + 1):
            script = _MIGRATIONS[target_version - 1]
            try:
                connection.executescript(
                    "BEGIN IMMEDIATE;\n"
                    f"{script}\n"
                    f"PRAGMA user_version = {target_version};\n"
                    "COMMIT;"
                )
            except sqlite3.Error:
                connection.rollback()
                raise
