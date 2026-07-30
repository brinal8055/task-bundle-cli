import json
import os
import textwrap
from pathlib import Path

import pytest

from task_bundle.bundle.loader import load_bundle
from task_bundle.database import Database
from task_bundle.errors import ErrorCode, TaskBundleError
from task_bundle.image.docker import SystemDockerRunner
from task_bundle.image.lock import LOCK_RELATIVE_PATH
from task_bundle.image.service import InitOptions, InitService
from task_bundle.image.validation import task_image_reference
from task_bundle.run.models import RunOptions, SolverType
from task_bundle.run.service import RunService
from task_bundle.validation.service import ValidationOptions, ValidationService
from tests.bundle_helpers import create_bundle, read_task, write_task
from tests.image_helpers import StaticSourceFactory
from tests.test_real_docker_run import _source_tree_sha
from tests.test_real_docker_validation import _create_python_validation_bundle


def test_real_docker_candidate_cannot_forge_final_result_when_configured(
    tmp_path: Path,
) -> None:
    base_image = os.environ.get("TASK_BUNDLE_REAL_DOCKER_PYTHON_BASE")
    if not base_image:
        pytest.skip(
            "set TASK_BUNDLE_REAL_DOCKER_PYTHON_BASE to a local digest-pinned "
            "Python image containing Git"
        )
    platform = os.environ.get("TASK_BUNDLE_REAL_DOCKER_PLATFORM", "linux/amd64")
    bundle, source = _create_python_validation_bundle(
        tmp_path,
        base_image=base_image,
        platform=platform,
    )
    mapping = read_task(bundle)
    mapping["evaluation"]["repeat"] = 1
    write_task(bundle, mapping)
    attack = bundle / "candidate.patch"
    attack.write_text(_result_forgery_patch())
    tree_sha = _source_tree_sha(source, tmp_path / "source-tree")
    database = Database(tmp_path / "task.db")
    runners: list[SystemDockerRunner] = []

    def docker_factory(home: Path) -> SystemDockerRunner:
        runner = SystemDockerRunner.create(home)
        runners.append(runner)
        return runner

    image_reference: str | None = None
    try:
        initialized = InitService(
            database=database,
            cli_version="test",
            source_factory=StaticSourceFactory(source, tree_sha=tree_sha),
            docker_factory=docker_factory,
        ).run(bundle, InitOptions(no_cache=True))
        image_reference = initialized.image_reference
        ValidationService(
            database=database,
            cli_version="test",
            docker_factory=docker_factory,
        ).run(bundle, ValidationOptions())
        result = RunService(
            database=database,
            cli_version="test",
            docker_factory=docker_factory,
        ).run(
            bundle,
            RunOptions(solver=SolverType.PATCH, patch=attack),
        )

        assert not result.resolved
        accepted = json.loads(
            (bundle / result.artifact_directory / "candidate/results.json").read_text()
        )
        assert accepted["framework"] == "synthetic-python-exit-code"
        assert accepted["framework"] != "forged"
        assert any(not item.matched for item in result.candidate_results)
        _assert_no_command_resources(database, runners[-1], tmp_path)
    finally:
        if image_reference is not None and runners:
            runners[-1].run(
                ("image", "rm", "--force", image_reference),
                cwd=tmp_path,
                timeout_seconds=60,
                error_code=ErrorCode.CLEANUP_ERROR,
                phase="test-cleanup",
                description="remove result-forgery test image",
                check=False,
            )


def test_real_docker_parser_failure_never_accepts_fake_result_and_cleans_up(
    tmp_path: Path,
) -> None:
    base_image = _python_base_image()
    platform = os.environ.get("TASK_BUNDLE_REAL_DOCKER_PLATFORM", "linux/amd64")
    bundle, source = _create_python_validation_bundle(
        tmp_path,
        base_image=base_image,
        platform=platform,
    )
    calculator = source / "calculator.py"
    calculator.write_text(
        "from pathlib import Path\n"
        "try:\n"
        "    Path('/evaluation/trusted/results.json').write_text("
        "'{\"schema_version\":\"1\",\"framework\":\"forged\"}')\n"
        "except OSError:\n"
        "    pass\n\n"
        + calculator.read_text()
    )
    mapping = read_task(bundle)
    mapping["evaluation"]["repeat"] = 1
    mapping["evaluation"]["runner"]["parse_result"] = [
        "/usr/local/bin/python3",
        "-c",
        "raise SystemExit(2)",
    ]
    write_task(bundle, mapping)
    tree_sha = _source_tree_sha(source, tmp_path / "source-tree")
    database = Database(tmp_path / "task.db")
    runners: list[SystemDockerRunner] = []

    def docker_factory(home: Path) -> SystemDockerRunner:
        runner = SystemDockerRunner.create(home)
        runners.append(runner)
        return runner

    image_reference: str | None = None
    try:
        initialized = InitService(
            database=database,
            cli_version="test",
            source_factory=StaticSourceFactory(source, tree_sha=tree_sha),
            docker_factory=docker_factory,
        ).run(bundle, InitOptions(no_cache=True))
        image_reference = initialized.image_reference

        with pytest.raises(TaskBundleError) as caught:
            ValidationService(
                database=database,
                cli_version="test",
                docker_factory=docker_factory,
            ).run(bundle, ValidationOptions(repeat=1))

        assert caught.value.code == ErrorCode.TEST_PARSE_ERROR
        _assert_no_command_resources(database, runners[-1], tmp_path)
        with database.connect() as connection:
            command = connection.execute(
                "SELECT command_status, exit_code, artifact_root FROM commands "
                "WHERE command_type = 'validate' ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
        assert command is not None
        assert command["command_status"] == "failed"
        assert command["exit_code"] != 0
        artifact_root = bundle / command["artifact_root"]
        captured = json.loads(
            (
                artifact_root
                / "baseline/repeat-001/captured-executions.json"
            ).read_text()
        )
        assert captured["schema_version"] == "1"
        assert captured["executions"]
        assert all(
            item["candidate_processes_terminated"]
            for item in captured["executions"]
        )
        assert all("framework" not in item for item in captured["executions"])
        assert (
            artifact_root / "baseline/repeat-001/runner.stdout.log"
        ).is_file()
        assert not (
            artifact_root / "baseline/repeat-001/results.json"
        ).exists()
        assert (artifact_root / "failure/report.json").is_file()
    finally:
        if image_reference is not None and runners:
            _remove_image(runners[-1], image_reference, tmp_path)


@pytest.mark.parametrize(
    ("case", "mutation", "detail_key", "path"),
    [
        (
            "probe-content",
            "RUN printf 'mutated\\n' > /opt/task/repo/a-probe.txt",
            "changed_paths",
            "a-probe.txt",
        ),
        (
            "non-probe-content",
            "RUN printf 'mutated\\n' > /opt/task/repo/z-target.txt",
            "changed_paths",
            "z-target.txt",
        ),
        (
            "addition",
            "RUN printf 'added\\n' > /opt/task/repo/added.txt",
            "added_paths",
            "added.txt",
        ),
        (
            "deletion",
            "RUN rm /opt/task/repo/z-target.txt",
            "removed_paths",
            "z-target.txt",
        ),
        (
            "executable-mode",
            "RUN chmod +x /opt/task/repo/mode.sh",
            "mode_changed_paths",
            "mode.sh",
        ),
        (
            "file-to-symlink",
            "RUN rm /opt/task/repo/z-target.txt && "
            "ln -s a-probe.txt /opt/task/repo/z-target.txt",
            "type_changed_paths",
            "z-target.txt",
        ),
        (
            "symlink-to-file",
            "RUN rm /opt/task/repo/source-link && "
            "printf 'regular\\n' > /opt/task/repo/source-link",
            "type_changed_paths",
            "source-link",
        ),
        (
            "symlink-target",
            "RUN rm /opt/task/repo/source-link && "
            "ln -s a-probe.txt /opt/task/repo/source-link",
            "changed_paths",
            "source-link",
        ),
    ],
)
def test_real_docker_init_rejects_complete_source_mutation_matrix_when_configured(
    tmp_path: Path,
    case: str,
    mutation: str,
    detail_key: str,
    path: str,
) -> None:
    base_image = _python_base_image()
    platform = os.environ.get("TASK_BUNDLE_REAL_DOCKER_PLATFORM", "linux/amd64")
    bundle, source = _create_image_source_bundle(
        tmp_path,
        base_image=base_image,
        platform=platform,
        task_id=f"image-source-{case}",
        mutation=mutation,
    )
    database = Database(tmp_path / "task.db")
    runner = SystemDockerRunner.create(tmp_path / "docker-home")
    service = InitService(
        database=database,
        cli_version="test",
        source_factory=StaticSourceFactory(source),
        docker_factory=lambda home: runner,
    )
    image_reference = _image_reference(bundle, platform)
    try:
        with pytest.raises(TaskBundleError) as failure:
            service.run(bundle, InitOptions(no_cache=True))
        assert failure.value.code == ErrorCode.IMAGE_SOURCE_MISMATCH
        assert failure.value.context.details is not None
        observed_paths = failure.value.context.details.get(detail_key)
        assert isinstance(observed_paths, list)
        assert path in observed_paths
        assert not (bundle / LOCK_RELATIVE_PATH).exists()
        _assert_no_command_resources(database, runner, tmp_path)
    finally:
        _remove_image(runner, image_reference, tmp_path)


@pytest.mark.parametrize(
    ("case", "mutation", "expected_path"),
    [
        (
            "git-file",
            "RUN printf 'forbidden\\n' > /opt/task/repo/.git",
            ".git",
        ),
        (
            "git-directory",
            "RUN mkdir /opt/task/repo/.git && "
            "printf 'forbidden\\n' > /opt/task/repo/.git/config",
            ".git",
        ),
    ],
)
def test_real_docker_init_rejects_git_metadata_when_configured(
    tmp_path: Path,
    case: str,
    mutation: str,
    expected_path: str,
) -> None:
    base_image = _python_base_image()
    platform = os.environ.get("TASK_BUNDLE_REAL_DOCKER_PLATFORM", "linux/amd64")
    bundle, source = _create_image_source_bundle(
        tmp_path,
        base_image=base_image,
        platform=platform,
        task_id=f"image-source-{case}",
        mutation=mutation,
    )
    database = Database(tmp_path / "task.db")
    runner = SystemDockerRunner.create(tmp_path / "docker-home")
    image_reference = _image_reference(bundle, platform)
    try:
        with pytest.raises(TaskBundleError) as caught:
            InitService(
                database=database,
                cli_version="test",
                source_factory=StaticSourceFactory(source),
                docker_factory=lambda home: runner,
            ).run(bundle, InitOptions(no_cache=True))

        assert caught.value.code == ErrorCode.IMAGE_SOURCE_MISMATCH
        assert caught.value.context.path is not None
        assert caught.value.context.path.as_posix().startswith(expected_path)
        assert "unsafe or unsupported" in caught.value.context.actual
        assert not (bundle / LOCK_RELATIVE_PATH).exists()
        _assert_no_command_resources(database, runner, tmp_path)
    finally:
        _remove_image(runner, image_reference, tmp_path)


@pytest.mark.parametrize(
    ("volume", "conflicts"),
    [
        ("/", True),
        ("/opt", True),
        ("/opt/task", True),
        ("/opt/task/repo", True),
        ("/opt/task/repo/cache", True),
        ("/data", False),
    ],
)
def test_real_docker_init_rejects_source_volume_shadowing_when_configured(
    tmp_path: Path,
    volume: str,
    conflicts: bool,
) -> None:
    base_image = _python_base_image()
    platform = os.environ.get("TASK_BUNDLE_REAL_DOCKER_PLATFORM", "linux/amd64")
    case = volume.strip("/").replace("/", "-") or "root"
    bundle, source = _create_image_source_bundle(
        tmp_path,
        base_image=base_image,
        platform=platform,
        task_id=f"image-volume-{case}",
        mutation=f"VOLUME {volume}",
    )
    database = Database(tmp_path / "task.db")
    runner = SystemDockerRunner.create(tmp_path / "docker-home")
    image_reference = _image_reference(bundle, platform)
    try:
        service = InitService(
            database=database,
            cli_version="test",
            source_factory=StaticSourceFactory(source),
            docker_factory=lambda home: runner,
        )
        if conflicts:
            with pytest.raises(TaskBundleError) as caught:
                service.run(bundle, InitOptions(no_cache=True))
            assert caught.value.code == ErrorCode.IMAGE_SOURCE_VOLUME_CONFLICT
            assert caught.value.context.details is not None
            assert volume in caught.value.context.details[
                "conflicting_volume_paths"
            ]
            assert not (bundle / LOCK_RELATIVE_PATH).exists()
        else:
            result = service.run(bundle, InitOptions(no_cache=True))
            assert result.status == "initialized"
            assert (bundle / LOCK_RELATIVE_PATH).is_file()
        _assert_no_command_resources(database, runner, tmp_path)
    finally:
        _remove_image(runner, image_reference, tmp_path)


def test_real_docker_init_accepts_unchanged_complete_source_when_configured(
    tmp_path: Path,
) -> None:
    base_image = _python_base_image()
    platform = os.environ.get("TASK_BUNDLE_REAL_DOCKER_PLATFORM", "linux/amd64")
    bundle, source = _create_image_source_bundle(
        tmp_path,
        base_image=base_image,
        platform=platform,
        task_id="image-source-unchanged",
    )
    database = Database(tmp_path / "task.db")
    runner = SystemDockerRunner.create(tmp_path / "docker-home")
    image_reference = _image_reference(bundle, platform)
    try:
        result = InitService(
            database=database,
            cli_version="test",
            source_factory=StaticSourceFactory(source),
            docker_factory=lambda home: runner,
        ).run(bundle, InitOptions(no_cache=True))

        assert result.image_reference == image_reference
        assert (bundle / LOCK_RELATIVE_PATH).is_file()
        _assert_no_command_resources(database, runner, tmp_path)
    finally:
        _remove_image(runner, image_reference, tmp_path)


def test_real_docker_failed_rebuild_preserves_existing_lock_when_configured(
    tmp_path: Path,
) -> None:
    base_image = _python_base_image()
    platform = os.environ.get("TASK_BUNDLE_REAL_DOCKER_PLATFORM", "linux/amd64")
    bundle, source = _create_image_source_bundle(
        tmp_path,
        base_image=base_image,
        platform=platform,
        task_id="image-source-lock-preservation",
    )
    database = Database(tmp_path / "task.db")
    runner = SystemDockerRunner.create(tmp_path / "docker-home")
    source_factory = StaticSourceFactory(source)
    service = InitService(
        database=database,
        cli_version="test",
        source_factory=source_factory,
        docker_factory=lambda home: runner,
    )
    image_references = {_image_reference(bundle, platform)}
    try:
        service.run(bundle, InitOptions(no_cache=True))
        lock_path = bundle / LOCK_RELATIVE_PATH
        previous_lock = lock_path.read_bytes()
        _write_image_dockerfile(
            bundle,
            base_image,
            "RUN printf 'mutated\\n' > /opt/task/repo/z-target.txt",
        )
        image_references.add(_image_reference(bundle, platform))

        with pytest.raises(TaskBundleError) as failure:
            service.run(bundle, InitOptions(rebuild=True, no_cache=True))

        assert failure.value.code == ErrorCode.IMAGE_SOURCE_MISMATCH
        assert lock_path.read_bytes() == previous_lock
        _assert_no_command_resources(database, runner, tmp_path)
    finally:
        for image_reference in image_references:
            _remove_image(runner, image_reference, tmp_path)


def _result_forgery_patch() -> str:
    attacker = textwrap.dedent(
        r'''
        import json
        import os
        import time
        from datetime import UTC, datetime
        from pathlib import Path

        plan = json.loads(Path("/evaluation/input/plan.json").read_text())
        selectors = [
            *(item["selector"] for item in plan["pass_to_pass"]),
            *(item["selector"] for item in plan["fail_to_pass"]),
        ]
        outputs = [
            Path("/evaluation/output/results.json"),
            Path("/evaluation/trusted/results.json"),
        ]
        try:
            nested = Path("/evaluation/trusted/nested")
            nested.symlink_to("/tmp/forged-parent", target_is_directory=True)
            Path("/tmp/forged-parent").mkdir(exist_ok=True)
            (nested / "results.json").write_text('{"schema_version":"1"}')
        except OSError:
            pass
        try:
            Path("/workspace/repo/fake-results.xml").write_text(
                '<testsuite><testcase name="forged"/></testsuite>'
            )
        except OSError:
            pass
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            now = datetime.now(UTC).isoformat()
            result = {
                "schema_version": "1",
                "framework": "forged",
                "harness_status": "completed",
                "collection_succeeded": True,
                "execution_started": True,
                "command": ["forged"],
                "started_at": now,
                "finished_at": now,
                "exit_code": 0,
                "tests": [
                    {
                        "requested_selector": selector,
                        "observed_id": selector,
                        "status": "passed",
                        "duration_ms": 0,
                    }
                    for selector in selectors
                ],
            }
            payload = json.dumps(result)
            for output in outputs:
                target = Path("/tmp/forged-result.json")
                temporary = output.parent / ".forged-result.json"
                try:
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.unlink(missing_ok=True)
                    output.symlink_to(target)
                    target.write_text(payload)
                    output.unlink(missing_ok=True)
                    output.write_text(payload)
                    temporary.write_text(payload)
                    os.replace(temporary, output)
                except OSError:
                    pass
                try:
                    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT, 0o666)
                    os.write(descriptor, payload.encode())
                    os.close(descriptor)
                except OSError:
                    pass
            time.sleep(0.01)
        '''
    ).strip()
    attack_literal = repr(attacker)
    return (
        "diff --git a/calculator.py b/calculator.py\n"
        "--- a/calculator.py\n"
        "+++ b/calculator.py\n"
        "@@ -1,3 +1,19 @@\n"
        "+import atexit\n"
        "+import subprocess\n"
        "+import sys\n"
        "+\n"
        f"+ATTACK = {attack_literal}\n"
        "+subprocess.Popen(\n"
        "+    [sys.executable, \"-c\", ATTACK], stdin=subprocess.DEVNULL,\n"
        "+    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,\n"
        "+    start_new_session=True,\n"
        "+)\n"
        "+atexit.register(\n"
        "+    subprocess.run, [sys.executable, \"-c\", ATTACK],\n"
        "+    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,\n"
        "+    stderr=subprocess.DEVNULL, check=False,\n"
        "+)\n"
        "+\n"
        " def add(a: int, b: int) -> int:\n"
        "     return a - b\n"
        " \n"
    )


def _python_base_image() -> str:
    base_image = os.environ.get("TASK_BUNDLE_REAL_DOCKER_PYTHON_BASE")
    if not base_image:
        pytest.skip(
            "set TASK_BUNDLE_REAL_DOCKER_PYTHON_BASE to a local digest-pinned "
            "Python image containing Git"
        )
    return base_image


def _create_image_source_bundle(
    tmp_path: Path,
    *,
    base_image: str,
    platform: str,
    task_id: str,
    mutation: str | None = None,
) -> tuple[Path, Path]:
    bundle = create_bundle(tmp_path / "bundle")
    source = tmp_path / "source"
    source.mkdir()
    (source / "a-probe.txt").write_text("probe\n")
    (source / "mode.sh").write_text("#!/bin/sh\nexit 0\n")
    (source / "z-target.txt").write_text("original\n")
    (source / "source-link").symlink_to("z-target.txt")
    _write_image_dockerfile(bundle, base_image, mutation)
    mapping = read_task(bundle)
    mapping["task"]["id"] = task_id
    mapping["environment"]["platform"] = platform
    mapping["environment"]["build"] = {"network": False}
    write_task(bundle, mapping)
    return bundle, source


def _write_image_dockerfile(
    bundle: Path,
    base_image: str,
    mutation: str | None,
) -> None:
    lines = [
        f"FROM {base_image}",
        "COPY repo/ /opt/task/repo/",
    ]
    if mutation is not None:
        lines.append(mutation)
    lines.extend(
        [
            "WORKDIR /workspace/repo",
            "ENTRYPOINT []",
        ]
    )
    (bundle / "environment/Dockerfile").write_text("\n".join(lines) + "\n")


def _image_reference(bundle: Path, platform: str) -> str:
    loaded = load_bundle(bundle)
    return task_image_reference(
        loaded.task.task.id,
        loaded.bundle_input_digest,
        platform,
    )


def _assert_no_command_resources(
    database: Database,
    runner: SystemDockerRunner,
    cwd: Path,
) -> None:
    with database.connect() as connection:
        command_ids = [
            str(row["id"])
            for row in connection.execute("SELECT id FROM commands").fetchall()
        ]
    for command_id in command_ids:
        containers = runner.run(
            (
                "ps",
                "-aq",
                "--filter",
                f"label=io.task-bundle.command-id={command_id}",
            ),
            cwd=cwd,
            timeout_seconds=30,
            error_code=ErrorCode.CLEANUP_ERROR,
            phase="test-cleanup",
            description="list integrity-test containers",
        )
        assert not containers.stdout.strip()
        volumes = runner.run(
            (
                "volume",
                "ls",
                "-q",
                "--filter",
                f"label=io.task-bundle.command-id={command_id}",
            ),
            cwd=cwd,
            timeout_seconds=30,
            error_code=ErrorCode.CLEANUP_ERROR,
            phase="test-cleanup",
            description="list integrity-test volumes",
        )
        assert not volumes.stdout.strip()


def _remove_image(
    runner: SystemDockerRunner,
    image_reference: str,
    cwd: Path,
) -> None:
    runner.run(
        ("image", "rm", "--force", image_reference),
        cwd=cwd,
        timeout_seconds=60,
        error_code=ErrorCode.CLEANUP_ERROR,
        phase="test-cleanup",
        description="remove integrity-test image",
        check=False,
    )
