import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from task_bundle.database import Database
from task_bundle.errors import ErrorCode, ErrorContext, TaskBundleError
from task_bundle.models import CommandStatus


class CommandStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    def start(
        self,
        *,
        command_id: str,
        task_id: str,
        bundle_path: Path,
        command_type: str = "init",
        started_at: datetime | None = None,
    ) -> None:
        timestamp = _timestamp(started_at)
        self._write(
            """
            INSERT INTO commands (
                id, task_id, command_type, command_status, started_at, bundle_path
            ) VALUES (?, ?, ?, 'running', ?, ?)
            """,
            (
                command_id,
                task_id,
                command_type,
                timestamp,
                str(bundle_path.resolve(strict=False)),
            ),
        )
        self.event(command_id, "COMMAND_STARTED", {"command_type": command_type})

    def update_identity(
        self,
        command_id: str,
        *,
        task_id: str,
        bundle_digest: str,
    ) -> None:
        self._write(
            "UPDATE commands SET task_id = ?, bundle_digest = ? WHERE id = ?",
            (task_id, bundle_digest, command_id),
        )

    def set_artifact_root(self, command_id: str, relative_path: str) -> None:
        self._write(
            "UPDATE commands SET artifact_root = ? WHERE id = ?",
            (relative_path, command_id),
        )

    def event(
        self,
        command_id: str,
        event_type: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        self._write(
            """
            INSERT INTO command_events (
                command_id, event_type, created_at, details_json
            ) VALUES (?, ?, ?, ?)
            """,
            (
                command_id,
                event_type,
                _timestamp(),
                json.dumps(details or {}, sort_keys=True, separators=(",", ":")),
            ),
        )

    def artifact(
        self,
        command_id: str,
        *,
        artifact_type: str,
        relative_path: str,
        sha256: str,
        size_bytes: int,
    ) -> None:
        self._write(
            """
            INSERT INTO artifacts (
                command_id, artifact_type, relative_path, sha256, size_bytes
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (command_id, artifact_type, relative_path, sha256, size_bytes),
        )

    def finish(
        self,
        command_id: str,
        *,
        status: CommandStatus,
        outcome: str,
        exit_code: int,
        image_id: str | None,
        message: str,
    ) -> None:
        self._write(
            """
            UPDATE commands
            SET command_status = ?, outcome_status = ?, finished_at = ?,
                image_id = ?, exit_code = ?, message = ?
            WHERE id = ?
            """,
            (
                status.value,
                outcome,
                _timestamp(),
                image_id,
                exit_code,
                message,
                command_id,
            ),
        )
        self.event(
            command_id,
            "COMMAND_FINISHED",
            {
                "status": status.value,
                "outcome": outcome,
                "exit_code": exit_code,
            },
        )

    def _write(self, statement: str, parameters: tuple[object, ...]) -> None:
        try:
            with self.database.connect() as connection:
                connection.execute(statement, parameters)
        except sqlite3.Error as error:
            raise TaskBundleError(
                ErrorCode.DATABASE_ERROR,
                "Command lifecycle could not be persisted.",
                ErrorContext(
                    phase="database",
                    expected="A writable compatible SQLite command database",
                    actual=str(error),
                    corrective_action="Check database permissions and schema compatibility.",
                    path=self.database.path,
                ),
            ) from error


def _timestamp(value: datetime | None = None) -> str:
    timestamp = value or datetime.now(UTC)
    return timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z")
