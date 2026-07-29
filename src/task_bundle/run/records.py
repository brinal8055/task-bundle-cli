import json
import sqlite3
from datetime import UTC, datetime

from task_bundle.database import Database
from task_bundle.errors import ErrorCode, ErrorContext, TaskBundleError
from task_bundle.models import CommandStatus
from task_bundle.run.models import (
    CandidateTree,
    PatchPolicyStatus,
    RunEvaluationStatus,
    ShowResult,
    SolverExecutionResult,
    SolverStatus,
    SolverType,
)
from task_bundle.validation.models import EvaluationRecord


class RunStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    def set_artifact_root(self, command_id: str, relative_path: str) -> None:
        self._write(
            "UPDATE commands SET artifact_root = ? WHERE id = ?",
            (relative_path, command_id),
        )

    def record_evaluation(
        self,
        command_id: str,
        evaluation: EvaluationRecord,
        *,
        patch_digest: str,
    ) -> None:
        try:
            with self.database.connect() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO evaluations (
                        command_id, phase, harness_status, duration_ms,
                        runner_exit_code, patch_digest, outcome, repeat_index,
                        evaluation_status, container_id, workspace_id,
                        evaluation_storage_id, test_patch_digest,
                        golden_patch_digest, cleaned_up
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        command_id,
                        evaluation.phase.value,
                        evaluation.harness_status.value,
                        evaluation.duration_ms,
                        evaluation.runner_exit_code,
                        patch_digest,
                        evaluation.outcome,
                        evaluation.repeat_index,
                        evaluation.status.value,
                        evaluation.container_id,
                        evaluation.workspace_id,
                        evaluation.evaluation_storage_id,
                        evaluation.test_patch_sha256,
                        evaluation.golden_patch_sha256,
                        int(evaluation.cleaned_up),
                    ),
                )
                evaluation_id = cursor.lastrowid
                if evaluation_id is None:
                    raise sqlite3.DatabaseError("evaluation insert returned no row ID")
                for test in evaluation.selector_results:
                    connection.execute(
                        """
                        INSERT INTO test_results (
                            evaluation_id, test_group, requested_selector,
                            observed_id, expected_status, actual_status,
                            duration_ms, failure_message
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            evaluation_id,
                            test.group.value,
                            test.requested_selector,
                            test.observed_id,
                            json.dumps(
                                [status.value for status in test.expected_statuses],
                                separators=(",", ":"),
                            ),
                            test.actual_status.value,
                            test.duration_ms,
                            test.message,
                        ),
                    )
        except sqlite3.Error as error:
            self._database_error("Run evaluation evidence could not be persisted.", error)

    def record_solver(
        self,
        command_id: str,
        execution: SolverExecutionResult,
        *,
        validation_id: str,
        candidate: CandidateTree | None,
        patch_policy: PatchPolicyStatus,
    ) -> None:
        self._write(
            """
            INSERT INTO solver_runs (
                command_id, solver_type, command_json, context_digest,
                duration_ms, patch_digest, changed_paths_json, outcome,
                status, validation_key, container_id, started_at, finished_at,
                exit_code, timed_out, baseline_tree_sha, candidate_tree_sha,
                patch_policy_status, workspace_export_status, cleaned_up
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                command_id,
                execution.solver_type.value,
                json.dumps(execution.argv, separators=(",", ":")),
                execution.context_digest,
                execution.duration_ms,
                None if candidate is None else candidate.candidate_patch_sha256,
                json.dumps(
                    [] if candidate is None else candidate.changed_paths,
                    separators=(",", ":"),
                ),
                execution.status.value,
                execution.status.value,
                validation_id,
                execution.container_id,
                _timestamp(execution.started_at),
                _timestamp(execution.finished_at),
                execution.exit_code,
                int(execution.timed_out),
                None if candidate is None else candidate.baseline_tree_sha,
                None if candidate is None else candidate.candidate_tree_sha,
                patch_policy.value,
                execution.workspace_export_status,
                int(execution.cleaned_up),
            ),
        )

    def update_solver_candidate(
        self,
        command_id: str,
        *,
        candidate: CandidateTree,
        patch_policy: PatchPolicyStatus,
    ) -> None:
        self._write(
            """
            UPDATE solver_runs
            SET patch_digest = ?, changed_paths_json = ?,
                baseline_tree_sha = ?, candidate_tree_sha = ?,
                patch_policy_status = ?
            WHERE command_id = ?
            """,
            (
                candidate.candidate_patch_sha256,
                json.dumps(candidate.changed_paths, separators=(",", ":")),
                candidate.baseline_tree_sha,
                candidate.candidate_tree_sha,
                patch_policy.value,
                command_id,
            ),
        )

    def record_solver_not_run(
        self,
        command_id: str,
        *,
        solver_type: SolverType,
        validation_id: str,
    ) -> None:
        self._write(
            """
            INSERT INTO solver_runs (
                command_id, solver_type, command_json, changed_paths_json,
                outcome, status, validation_key, timed_out,
                patch_policy_status, workspace_export_status, cleaned_up
            ) VALUES (?, ?, '[]', '[]', ?, ?, ?, 0, ?, 'not_run', 1)
            """,
            (
                command_id,
                solver_type.value,
                SolverStatus.NOT_RUN.value,
                SolverStatus.NOT_RUN.value,
                validation_id,
                PatchPolicyStatus.NOT_RUN.value,
            ),
        )

    def record_solver_error(
        self,
        command_id: str,
        *,
        solver_type: SolverType,
        argv: tuple[str, ...],
        context_digest: str | None,
        validation_id: str,
        timed_out: bool,
        container_id: str | None,
        cleaned_up: bool,
    ) -> None:
        now = _timestamp()
        status = SolverStatus.TIMED_OUT if timed_out else SolverStatus.FAILED
        self._write(
            """
            INSERT INTO solver_runs (
                command_id, solver_type, command_json, context_digest,
                changed_paths_json, outcome, status, validation_key,
                container_id, started_at, finished_at, timed_out, patch_policy_status,
                workspace_export_status, cleaned_up
            ) VALUES (?, ?, ?, ?, '[]', ?, ?, ?, ?, ?, ?, ?, ?, 'not_run', ?)
            """,
            (
                command_id,
                solver_type.value,
                json.dumps(argv, separators=(",", ":")),
                context_digest,
                status.value,
                status.value,
                validation_id,
                container_id,
                now,
                now,
                int(timed_out),
                PatchPolicyStatus.NOT_RUN.value,
                int(cleaned_up),
            ),
        )

    def finish(
        self,
        command_id: str,
        *,
        status: CommandStatus,
        evaluation_status: RunEvaluationStatus,
        resolved: bool | None,
        exit_code: int,
        image_id: str | None,
        message: str,
        outcome: str | None = None,
    ) -> None:
        timestamp = _timestamp()
        details = {
            "status": status.value,
            "outcome": outcome or evaluation_status.value,
            "evaluation_status": evaluation_status.value,
            "resolved": resolved,
            "exit_code": exit_code,
        }
        try:
            with self.database.connect() as connection:
                connection.execute(
                    """
                    UPDATE commands
                    SET command_status = ?, outcome_status = ?, evaluation_status = ?,
                        resolved = ?, finished_at = ?, image_id = ?, exit_code = ?,
                        message = ?
                    WHERE id = ?
                    """,
                    (
                        status.value,
                        outcome or evaluation_status.value,
                        evaluation_status.value,
                        None if resolved is None else int(resolved),
                        timestamp,
                        image_id,
                        exit_code,
                        message,
                        command_id,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO command_events (
                        command_id, event_type, created_at, details_json
                    ) VALUES (?, 'COMMAND_FINISHED', ?, ?)
                    """,
                    (
                        command_id,
                        timestamp,
                        json.dumps(details, sort_keys=True, separators=(",", ":")),
                    ),
                )
        except sqlite3.Error as error:
            self._database_error("Run finalization could not be persisted.", error)

    def event(
        self,
        command_id: str,
        event_type: str,
        details: dict[str, object] | None = None,
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

    def show(
        self,
        command_id: str,
        *,
        include_events: bool,
        include_tests: bool,
    ) -> ShowResult:
        try:
            with self.database.connect() as connection:
                command = connection.execute(
                    "SELECT * FROM commands WHERE id = ?",
                    (command_id,),
                ).fetchone()
                if command is None:
                    raise TaskBundleError(
                        ErrorCode.COMMAND_NOT_FOUND,
                        "Command was not found.",
                        ErrorContext(
                            phase="show",
                            expected="A persisted init, validate, or run command ID",
                            actual=command_id,
                            corrective_action="Use a command ID printed by the CLI.",
                        ),
                    )
                solver = connection.execute(
                    "SELECT * FROM solver_runs WHERE command_id = ? ORDER BY id LIMIT 1",
                    (command_id,),
                ).fetchone()
                evaluations = connection.execute(
                    "SELECT * FROM evaluations WHERE command_id = ? ORDER BY id",
                    (command_id,),
                ).fetchall()
                all_tests = connection.execute(
                    """
                    SELECT t.*, e.phase
                    FROM test_results t
                    JOIN evaluations e ON e.id = t.evaluation_id
                    WHERE e.command_id = ?
                    ORDER BY t.id
                    """,
                    (command_id,),
                ).fetchall()
                tests = (
                    all_tests
                    if include_tests
                    else tuple(row for row in all_tests if not _test_matched(row))
                )
                events = (
                    connection.execute(
                        """
                        SELECT event_type, created_at, details_json
                        FROM command_events WHERE command_id = ? ORDER BY id
                        """,
                        (command_id,),
                    ).fetchall()
                    if include_events
                    else ()
                )
                artifacts = connection.execute(
                    """
                    SELECT artifact_type, relative_path, sha256, size_bytes
                    FROM artifacts WHERE command_id = ? ORDER BY id
                    """,
                    (command_id,),
                ).fetchall()
        except TaskBundleError:
            raise
        except sqlite3.Error as error:
            self._database_error("Command details could not be queried.", error)
        return ShowResult(
            command=_row_dict(command),
            solver=None if solver is None else _row_dict(solver),
            evaluations=tuple(
                _evaluation_dict(row, all_tests) for row in evaluations
            ),
            tests=tuple(_row_dict(row) for row in tests),
            events=tuple(_event_dict(row) for row in events),
            artifacts=tuple(_row_dict(row) for row in artifacts),
        )

    def _write(self, statement: str, parameters: tuple[object, ...]) -> None:
        try:
            with self.database.connect() as connection:
                connection.execute(statement, parameters)
        except sqlite3.Error as error:
            self._database_error("Run lifecycle could not be persisted.", error)

    def _database_error(self, message: str, error: sqlite3.Error) -> None:
        raise TaskBundleError(
            ErrorCode.DATABASE_ERROR,
            message,
            ErrorContext(
                phase="database",
                expected="A writable compatible SQLite run database",
                actual=str(error),
                corrective_action="Check database permissions and schema compatibility.",
                path=self.database.path,
            ),
        ) from error


def _timestamp(value: datetime | None = None) -> str:
    timestamp = value or datetime.now(UTC)
    return timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _row_dict(row: sqlite3.Row) -> dict[str, object]:
    return dict(zip(row.keys(), row, strict=True))


def _event_dict(row: sqlite3.Row) -> dict[str, object]:
    return {
        "event_type": row["event_type"],
        "created_at": row["created_at"],
        "details": json.loads(row["details_json"]),
    }


def _test_matched(row: sqlite3.Row) -> bool:
    expected = json.loads(row["expected_status"])
    return row["actual_status"] in expected


def _evaluation_dict(
    row: sqlite3.Row,
    tests: tuple[sqlite3.Row, ...] | list[sqlite3.Row],
) -> dict[str, object]:
    value = _row_dict(row)
    selected = [test for test in tests if test["evaluation_id"] == row["id"]]
    value["test_count"] = len(selected)
    value["matched_count"] = sum(_test_matched(test) for test in selected)
    return value
