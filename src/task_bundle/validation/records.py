import json
import sqlite3
from datetime import UTC, datetime

from task_bundle.database import Database
from task_bundle.errors import ErrorCode, ErrorContext, TaskBundleError
from task_bundle.models import CommandStatus
from task_bundle.validation.models import ValidationResult, ValidationStatus


class ValidationStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    def finish(self, result: ValidationResult) -> None:
        try:
            with self.database.connect() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO validations (
                        command_id, bundle_digest, image_id, runtime_policy_digest,
                        harness_digest, selector_digest, outcome, validation_key,
                        repeat_count, started_at, finished_at, test_patch_digest,
                        golden_patch_digest
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        result.command_id,
                        result.bundle_input_digest,
                        result.task_image_id,
                        result.runtime_policy_digest,
                        result.harness_digest,
                        result.selector_digest,
                        result.validation_status.value,
                        result.validation_id,
                        result.repeat_count,
                        _timestamp(result.started_at),
                        _timestamp(result.finished_at),
                        result.test_patch_sha256,
                        result.golden_patch_sha256,
                    ),
                )
                validation_row_id = cursor.lastrowid
                if validation_row_id is None:
                    raise sqlite3.DatabaseError("validation insert returned no row ID")
                for evaluation in result.evaluations:
                    evaluation_cursor = connection.execute(
                        """
                        INSERT INTO evaluations (
                            command_id, phase, harness_status, duration_ms,
                            runner_exit_code, patch_digest, outcome, validation_id,
                            repeat_index, evaluation_status, container_id,
                            workspace_id, evaluation_storage_id, test_patch_digest,
                            golden_patch_digest, cleaned_up
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            result.command_id,
                            evaluation.phase.value,
                            evaluation.harness_status.value,
                            evaluation.duration_ms,
                            evaluation.runner_exit_code,
                            evaluation.golden_patch_sha256
                            or evaluation.test_patch_sha256,
                            evaluation.outcome,
                            validation_row_id,
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
                    evaluation_row_id = evaluation_cursor.lastrowid
                    if evaluation_row_id is None:
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
                                evaluation_row_id,
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
                connection.execute(
                    """
                    UPDATE commands
                    SET command_status = ?, outcome_status = ?, finished_at = ?,
                        image_id = ?, exit_code = ?, message = ?
                    WHERE id = ?
                    """,
                    (
                        CommandStatus.SUCCEEDED.value,
                        result.validation_status.value,
                        _timestamp(result.finished_at),
                        result.task_image_id,
                        0 if result.validation_status == ValidationStatus.VALID else 4,
                        _message(result.validation_status),
                        result.command_id,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO command_events (
                        command_id, event_type, created_at, details_json
                    ) VALUES (?, 'COMMAND_FINISHED', ?, ?)
                    """,
                    (
                        result.command_id,
                        _timestamp(result.finished_at),
                        json.dumps(
                            {
                                "status": CommandStatus.SUCCEEDED.value,
                                "outcome": result.validation_status.value,
                                "exit_code": (
                                    0
                                    if result.validation_status == ValidationStatus.VALID
                                    else 4
                                ),
                            },
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    ),
                )
        except sqlite3.Error as error:
            raise TaskBundleError(
                ErrorCode.DATABASE_ERROR,
                "Validation lifecycle could not be persisted.",
                ErrorContext(
                    phase="database",
                    expected="An atomic validation, evaluation, and result transaction",
                    actual=str(error),
                    corrective_action="Check database permissions and schema compatibility.",
                    path=self.database.path,
                ),
            ) from error

    def matching_success_exists(self, validation_id: str) -> bool:
        try:
            with self.database.connect() as connection:
                row = connection.execute(
                    """
                    SELECT 1 FROM validations
                    WHERE validation_key = ? AND outcome = 'valid'
                    LIMIT 1
                    """,
                    (validation_id,),
                ).fetchone()
        except sqlite3.Error as error:
            raise TaskBundleError(
                ErrorCode.DATABASE_ERROR,
                "Successful validation identity could not be queried.",
                ErrorContext(
                    phase="database",
                    expected="A readable validation index",
                    actual=str(error),
                    corrective_action="Check database permissions and schema compatibility.",
                    path=self.database.path,
                ),
            ) from error
        return row is not None


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _message(status: ValidationStatus) -> str:
    if status == ValidationStatus.VALID:
        return "Baseline and golden validation passed."
    return f"Validation completed with outcome {status.value}."
