import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from task_bundle.bundle.loader import LoadedBundle
from task_bundle.database import Database
from task_bundle.errors import ErrorCode, ErrorContext, TaskBundleError
from task_bundle.image.lock import LOCK_RELATIVE_PATH, load_bundle_lock
from task_bundle.image.service import InitOptions, InitService
from task_bundle.source.persistence import (
    SOURCE_MANIFEST_RELATIVE_PATH,
    SOURCE_SNAPSHOT_RELATIVE_PATH,
)
from task_bundle.source.service import MaterializedSource
from tests.bundle_helpers import create_bundle, read_task, write_task
from tests.image_helpers import (
    FakeDockerRunner,
    StaticSourceFactory,
    all_artifact_bytes,
)


def _service(
    tmp_path: Path,
    source: StaticSourceFactory,
    docker: FakeDockerRunner,
) -> tuple[InitService, Database]:
    database = Database(tmp_path / "state" / "task.db")
    service = InitService(
        database=database,
        cli_version="test",
        source_factory=source,
        docker_factory=lambda home: docker,
    )
    return service, database


def test_init_builds_inspects_smokes_locks_and_records(tmp_path: Path) -> None:
    bundle = create_bundle(tmp_path / "bundle")
    source = StaticSourceFactory(tmp_path / "source")
    docker = FakeDockerRunner()
    service, database = _service(tmp_path, source, docker)

    result = service.run(bundle, InitOptions(no_cache=True))

    assert result.status == "initialized"
    assert result.image_id == "sha256:" + f"{1:064x}"
    assert result.lock_path == LOCK_RELATIVE_PATH.as_posix()
    assert load_bundle_lock(bundle / LOCK_RELATIVE_PATH).image_id == result.image_id
    assert (bundle / SOURCE_MANIFEST_RELATIVE_PATH).is_file()
    assert (bundle / SOURCE_SNAPSHOT_RELATIVE_PATH).is_file()
    assert docker.context_top_level == ("Dockerfile", "env", "repo")
    assert "repo/README.md" in docker.context_paths
    assert not any("evaluation" in path or ".task" in path for path in docker.context_paths)
    build = next(command for command in docker.commands if command[0] == "build")
    assert "--no-cache" in build
    smoke = next(command for command in docker.commands if command[0] == "create")
    for required in (
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
    ):
        assert required in smoke
    artifact_root = bundle / result.artifact_directory
    assert (artifact_root / "report.json").is_file()
    assert (artifact_root / "build/image-inspect.json").is_file()
    assert (artifact_root / "smoke/result.json").is_file()
    assert not any(path.name.startswith("task-bundle-build-") for path in tmp_path.iterdir())
    with database.connect() as connection:
        command = connection.execute(
            "SELECT * FROM commands WHERE id = ?", (result.command_id,)
        ).fetchone()
        events = connection.execute(
            "SELECT event_type FROM command_events WHERE command_id = ? ORDER BY id",
            (result.command_id,),
        ).fetchall()
    assert command["command_status"] == "succeeded"
    assert command["image_id"] == result.image_id
    assert events[-1]["event_type"] == "COMMAND_FINISHED"


def test_repeated_current_init_does_not_fetch_or_rebuild(tmp_path: Path) -> None:
    bundle = create_bundle(tmp_path / "bundle")
    source = StaticSourceFactory(tmp_path / "source")
    docker = FakeDockerRunner()
    service, _ = _service(tmp_path, source, docker)

    first = service.run(bundle, InitOptions())
    second = service.run(bundle, InitOptions())

    assert first.status == "initialized"
    assert second.status == "already_initialized"
    assert source.calls == 1
    assert docker.build_count == 1
    assert second.image_id == first.image_id


def test_stale_lock_requires_rebuild_and_rebuild_replaces_it(tmp_path: Path) -> None:
    bundle = create_bundle(tmp_path / "bundle")
    source = StaticSourceFactory(tmp_path / "source")
    docker = FakeDockerRunner()
    service, _ = _service(tmp_path, source, docker)
    first = service.run(bundle, InitOptions())
    (bundle / "public/description.md").write_text("changed\n", encoding="utf-8")

    with pytest.raises(TaskBundleError) as caught:
        service.run(bundle, InitOptions())

    assert caught.value.code == ErrorCode.LOCK_MISMATCH
    assert source.calls == 1
    rebuilt = service.run(bundle, InitOptions(rebuild=True))
    assert rebuilt.status == "initialized"
    assert rebuilt.image_id != first.image_id
    assert source.calls == 2
    assert docker.build_count == 2


def test_changed_tag_identity_is_stale(tmp_path: Path) -> None:
    bundle = create_bundle(tmp_path / "bundle")
    source = StaticSourceFactory(tmp_path / "source")
    docker = FakeDockerRunner()
    service, _ = _service(tmp_path, source, docker)
    result = service.run(bundle, InitOptions())
    docker.images[result.image_reference]["Id"] = "sha256:" + "f" * 64

    with pytest.raises(TaskBundleError) as caught:
        service.run(bundle, InitOptions())

    assert caught.value.code == ErrorCode.LOCK_MISMATCH
    assert "image_id" in caught.value.context.actual


def test_build_failure_preserves_redacted_command_and_logs_without_lock(
    tmp_path: Path,
) -> None:
    bundle = create_bundle(tmp_path / "bundle")
    source = StaticSourceFactory(tmp_path / "source")
    docker = FakeDockerRunner(fail_on="build")
    service, database = _service(tmp_path, source, docker)

    with pytest.raises(TaskBundleError) as caught:
        service.run(bundle, InitOptions())

    assert caught.value.code == ErrorCode.IMAGE_BUILD_ERROR
    assert not (bundle / LOCK_RELATIVE_PATH).exists()
    artifact_bytes = all_artifact_bytes(bundle)
    assert b"VERSION=<redacted>" in artifact_bytes
    assert b"VERSION=1" not in artifact_bytes
    artifact_root = next((bundle / "artifacts/example-task").iterdir())
    assert (artifact_root / "failure/docker.stderr.log").is_file()
    with database.connect() as connection:
        command = connection.execute(
            "SELECT command_status FROM commands ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
    assert command["command_status"] == "failed"


def test_failed_rebuild_preserves_previous_lock_bytes(tmp_path: Path) -> None:
    bundle = create_bundle(tmp_path / "bundle")
    source = StaticSourceFactory(tmp_path / "source")
    docker = FakeDockerRunner()
    service, _ = _service(tmp_path, source, docker)
    service.run(bundle, InitOptions())
    lock_path = bundle / LOCK_RELATIVE_PATH
    previous_lock = lock_path.read_bytes()
    (bundle / "public/description.md").write_text("changed\n", encoding="utf-8")
    docker.fail_on = "build"

    with pytest.raises(TaskBundleError):
        service.run(bundle, InitOptions(rebuild=True))

    assert lock_path.read_bytes() == previous_lock


def test_smoke_failure_always_removes_container_and_writes_no_lock(
    tmp_path: Path,
) -> None:
    bundle = create_bundle(tmp_path / "bundle")
    source = StaticSourceFactory(tmp_path / "source")
    docker = FakeDockerRunner(fail_on="start")
    service, _ = _service(tmp_path, source, docker)

    with pytest.raises(TaskBundleError) as caught:
        service.run(bundle, InitOptions())

    assert caught.value.code == ErrorCode.SMOKE_CHECK_ERROR
    assert any(command[0] == "rm" for command in docker.commands)
    assert not (bundle / LOCK_RELATIVE_PATH).exists()
    artifact_root = next((bundle / "artifacts/example-task").iterdir())
    assert (artifact_root / "smoke/docker-command.json").is_file()


def test_source_failure_is_recorded_with_artifacts_and_without_lock(
    tmp_path: Path,
) -> None:
    bundle = create_bundle(tmp_path / "bundle")
    docker = FakeDockerRunner()
    backing_source = StaticSourceFactory(tmp_path / "source")

    @contextmanager
    def failing_source(bundle_config: LoadedBundle) -> Iterator[MaterializedSource]:
        with backing_source(bundle_config) as materialized:
            raise TaskBundleError(
                ErrorCode.SOURCE_FETCH_ERROR,
                "Source fetch failed.",
                ErrorContext(
                    phase="source-fetch",
                    expected="Exact public commit",
                    actual="Exit code 128",
                    corrective_action="Check repository availability.",
                    details={"stderr": "safe fetch error"},
                ),
            )
            yield materialized

    service = InitService(
        database=Database(tmp_path / "task.db"),
        cli_version="test",
        source_factory=failing_source,
        docker_factory=lambda home: docker,
    )

    with pytest.raises(TaskBundleError) as caught:
        service.run(bundle, InitOptions())

    assert caught.value.code == ErrorCode.SOURCE_FETCH_ERROR
    assert not (bundle / LOCK_RELATIVE_PATH).exists()
    artifact_root = next((bundle / "artifacts/example-task").iterdir())
    failure = json.loads((artifact_root / "failure/report.json").read_text())
    assert failure["error"]["details"]["stderr"] == "safe fetch error"


def test_secret_like_build_argument_is_rejected_before_docker_and_not_logged(
    tmp_path: Path,
) -> None:
    bundle = create_bundle(tmp_path / "bundle")
    mapping = read_task(bundle)
    mapping["environment"]["build"]["build_args"] = {"API_TOKEN": "top-secret"}
    write_task(bundle, mapping)
    source = StaticSourceFactory(tmp_path / "source")
    docker = FakeDockerRunner()
    docker_factory_called = False

    def docker_factory(home: Path) -> FakeDockerRunner:
        nonlocal docker_factory_called
        docker_factory_called = True
        return docker

    service = InitService(
        database=Database(tmp_path / "task.db"),
        cli_version="test",
        source_factory=source,
        docker_factory=docker_factory,
    )

    with pytest.raises(TaskBundleError) as caught:
        service.run(bundle, InitOptions())

    assert caught.value.code == ErrorCode.BUILD_CONFIG_ERROR
    assert not docker_factory_called
    assert b"top-secret" not in all_artifact_bytes(bundle)
    assert not (bundle / LOCK_RELATIVE_PATH).exists()


def test_keep_context_and_base_image_wrapper(tmp_path: Path) -> None:
    bundle = create_bundle(tmp_path / "bundle")
    mapping = read_task(bundle)
    reference = f"busybox@sha256:{'a' * 64}"
    mapping["environment"] = {
        "type": "base_image",
        "image": reference,
        "platform": "linux/amd64",
    }
    write_task(bundle, mapping)
    source = StaticSourceFactory(tmp_path / "source")
    docker = FakeDockerRunner()
    service, _ = _service(tmp_path, source, docker)

    result = service.run(bundle, InitOptions(keep_build_context=True))

    assert result.build_context_path is not None
    kept = bundle / result.build_context_path
    assert kept.is_dir()
    assert (kept / "Dockerfile").read_text().startswith(f"FROM {reference}\n")
    assert (kept / "repo/README.md").is_file()
    assert docker.context_top_level == ("Dockerfile", "env", "repo")


def test_platform_mismatch_fails_before_lock(tmp_path: Path) -> None:
    bundle = create_bundle(tmp_path / "bundle")
    source = StaticSourceFactory(tmp_path / "source")
    docker = FakeDockerRunner(actual_platform="linux/arm64")
    service, _ = _service(tmp_path, source, docker)

    with pytest.raises(TaskBundleError) as caught:
        service.run(bundle, InitOptions())

    assert caught.value.code == ErrorCode.PLATFORM_MISMATCH
    assert not (bundle / LOCK_RELATIVE_PATH).exists()


def test_report_json_matches_result(tmp_path: Path) -> None:
    bundle = create_bundle(tmp_path / "bundle")
    source = StaticSourceFactory(tmp_path / "source")
    docker = FakeDockerRunner()
    service, _ = _service(tmp_path, source, docker)

    result = service.run(bundle, InitOptions())
    report = json.loads((bundle / result.artifact_directory / "report.json").read_text())

    assert report["command_id"] == result.command_id
    assert report["image_id"] == result.image_id
