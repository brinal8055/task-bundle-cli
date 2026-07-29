import os
from pathlib import Path

from task_bundle.bundle.loader import load_bundle
from task_bundle.image.context import create_build_context
from task_bundle.models import DockerfileEnvironment
from tests.bundle_helpers import create_bundle
from tests.image_helpers import StaticSourceFactory


def test_generated_context_is_physical_minimal_and_deterministic(tmp_path: Path) -> None:
    bundle_path = create_bundle(tmp_path / "bundle")
    generated = bundle_path / "environment/context" / ".task"
    generated.mkdir()
    (generated / "must-not-stage").write_text("runtime\n", encoding="utf-8")
    artifacts = bundle_path / "environment/context" / "artifacts"
    artifacts.mkdir()
    (artifacts / "must-not-stage").write_text("runtime\n", encoding="utf-8")
    bundle = load_bundle(bundle_path)
    source_factory = StaticSourceFactory(tmp_path / "source")

    with source_factory(bundle) as source:
        with create_build_context(
            bundle,
            source,
            command_id="cmd_first",
            keep=False,
        ) as first:
            first_root = first.root
            first_manifest = first.manifest
            first_metadata = first.metadata
            assert tuple(sorted(path.name for path in first.root.iterdir())) == (
                "Dockerfile",
                "env",
                "repo",
            )
            assert (first.root / "repo/tool-link").is_symlink()
            assert os.readlink(first.root / "repo/tool-link") == "bin/tool"
            assert (first.root / "repo/bin/tool").stat().st_mode & 0o111
            assert (first.root / "env/tool.conf").is_file()
            assert not (first.root / "env/.task").exists()
            assert not (first.root / "env/artifacts").exists()
            assert isinstance(bundle.task.environment, DockerfileEnvironment)
            assert (first.root / "Dockerfile").read_bytes() == (
                bundle.root / bundle.task.environment.dockerfile
            ).read_bytes()
            repo_entries = tuple(
                entry.model_copy(update={"path": entry.path.removeprefix("repo/")})
                for entry in first.manifest.entries
                if entry.path.startswith("repo/")
            )
            assert repo_entries == source.manifest.entries
            assert (
                first.metadata.repository_source_digest
                == source.resolved.source_tree_digest
            )
        assert not first_root.exists()

        with create_build_context(
            bundle,
            source,
            command_id="cmd_second",
            keep=False,
        ) as second:
            assert second.manifest == first_manifest
            assert second.metadata.context_digest == first_metadata.context_digest
            assert (
                second.metadata.environment_context_digest
                == first_metadata.environment_context_digest
            )
