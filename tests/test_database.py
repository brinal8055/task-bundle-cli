import sqlite3
from pathlib import Path

import pytest

from task_bundle import database as database_module
from task_bundle.database import SCHEMA_VERSION, Database


def test_database_migration_is_idempotent(tmp_path: Path) -> None:
    database = Database(tmp_path / "state" / "task.db")

    with database.connect() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        version = connection.execute("PRAGMA user_version").fetchone()[0]

    with database.connect():
        pass

    assert version == SCHEMA_VERSION
    assert {
        "commands",
        "command_events",
        "validations",
        "solver_runs",
        "evaluations",
        "test_results",
        "artifacts",
    } <= tables


def test_database_rolls_back_failed_transaction(tmp_path: Path) -> None:
    database = Database(tmp_path / "task.db")

    with pytest.raises(sqlite3.IntegrityError), database.connect() as connection:
        connection.execute(
            """
            INSERT INTO commands (
                id, task_id, command_type, command_status, started_at
            ) VALUES ('cmd_1', 'task_1', 'init', 'running', '2026-07-29T00:00:00Z')
            """
        )
        connection.execute(
            """
            INSERT INTO commands (
                id, task_id, command_type, command_status, started_at
            ) VALUES ('cmd_1', 'task_1', 'init', 'running', '2026-07-29T00:00:00Z')
            """
        )

    with database.connect() as connection:
        count = connection.execute("SELECT COUNT(*) FROM commands").fetchone()[0]

    assert count == 0


def test_foreign_keys_are_enabled_on_every_connection(tmp_path: Path) -> None:
    database = Database(tmp_path / "task.db")

    with pytest.raises(sqlite3.IntegrityError), database.connect() as connection:
        connection.execute(
            """
            INSERT INTO command_events (
                command_id, event_type, created_at
            ) VALUES ('missing', 'COMMAND_STARTED', '2026-07-29T00:00:00Z')
            """
        )


def test_failed_migration_leaves_no_partial_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken_migration = "CREATE TABLE partial (id INTEGER); INVALID SQL;"
    monkeypatch.setattr(database_module, "_MIGRATIONS", (broken_migration,))
    database = Database(tmp_path / "task.db")

    with pytest.raises(sqlite3.Error), database.connect():
        pass

    connection = sqlite3.connect(database.path)
    try:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        partial = connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'partial'"
        ).fetchone()
    finally:
        connection.close()

    assert version == 0
    assert partial is None
