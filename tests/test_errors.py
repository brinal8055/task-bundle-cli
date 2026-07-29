from task_bundle.errors import ErrorCode, ErrorContext, TaskBundleError, exit_code_for_error


def test_every_error_code_maps_to_a_stable_exit_code() -> None:
    exit_codes = {code: exit_code_for_error(code) for code in ErrorCode}

    assert set(exit_codes) == set(ErrorCode)
    assert set(exit_codes.values()) <= {2, 3, 4, 5, 6}
    assert exit_codes[ErrorCode.CONFIG_ERROR] == 2
    assert exit_codes[ErrorCode.BUILD_CONFIG_ERROR] == 2
    assert exit_codes[ErrorCode.CONTAINER_ERROR] == 3
    assert exit_codes[ErrorCode.GOLDEN_VALIDATION_ERROR] == 4
    assert exit_codes[ErrorCode.SOLVER_TIMEOUT] == 5
    assert exit_codes[ErrorCode.PATCH_CONFLICT] == 6


def test_error_exposes_actionable_context() -> None:
    error = TaskBundleError(
        ErrorCode.CONFIG_ERROR,
        "Invalid bundle.",
        ErrorContext(
            phase="bundle",
            expected="Valid task configuration",
            actual="Unknown field",
            corrective_action="Remove the unknown field.",
        ),
    )

    assert error.exit_code == 2
    assert error.context.corrective_action == "Remove the unknown field."
