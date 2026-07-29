import os
import socket
import tempfile
from pathlib import Path

import pytest

from task_bundle.bundle.paths import PathKind, resolve_bundle_path
from task_bundle.errors import ErrorCode, TaskBundleError
from tests.bundle_helpers import BundleFactory


def test_valid_file_and_directory_are_normalized(
    tmp_path: Path, bundle_factory: BundleFactory
) -> None:
    root = bundle_factory(tmp_path / "bundle")

    file = resolve_bundle_path(root, "public/../public/description.md", "file")
    directory = resolve_bundle_path(root, "environment/context", "directory")

    assert file.relative == "public/description.md"
    assert directory.relative == "environment/context"


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("missing.txt", "file"),
        ("/etc/passwd", "file"),
        ("../outside.txt", "file"),
        ("public/../../outside.txt", "file"),
        ("public/description.md", "directory"),
        ("public", "file"),
        ("", "file"),
    ],
)
def test_invalid_paths_are_rejected(
    tmp_path: Path,
    bundle_factory: BundleFactory,
    configured: str,
    expected: PathKind,
) -> None:
    root = bundle_factory(tmp_path / "bundle")

    with pytest.raises(TaskBundleError) as caught:
        resolve_bundle_path(root, configured, expected)

    assert caught.value.code == ErrorCode.BUNDLE_PATH_ERROR


def test_symlink_escape_is_rejected(tmp_path: Path, bundle_factory: BundleFactory) -> None:
    root = bundle_factory(tmp_path / "bundle")
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    (root / "public/link.md").symlink_to(outside)

    with pytest.raises(TaskBundleError) as caught:
        resolve_bundle_path(root, "public/link.md", "file")

    assert caught.value.code == ErrorCode.BUNDLE_PATH_ERROR


def test_fifo_is_not_a_regular_file(tmp_path: Path, bundle_factory: BundleFactory) -> None:
    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFOs are not supported on this platform")
    root = bundle_factory(tmp_path / "bundle")
    fifo = root / "public/input.fifo"
    os.mkfifo(fifo)

    with pytest.raises(TaskBundleError):
        resolve_bundle_path(root, "public/input.fifo", "file")


def test_socket_is_not_a_regular_file(
    bundle_factory: BundleFactory,
) -> None:
    with tempfile.TemporaryDirectory(prefix="tb-", dir="/private/tmp") as temporary:
        root = bundle_factory(Path(temporary) / "bundle")
        socket_path = root / "public/input.sock"
        server = socket.socket(socket.AF_UNIX)
        try:
            try:
                server.bind(str(socket_path))
            except OSError as error:
                pytest.skip(f"Unix sockets unavailable in test environment: {error}")
            with pytest.raises(TaskBundleError):
                resolve_bundle_path(root, "public/input.sock", "file")
        finally:
            server.close()


def test_generated_path_cannot_be_explicit_input(
    tmp_path: Path, bundle_factory: BundleFactory
) -> None:
    root = bundle_factory(tmp_path / "bundle")
    generated = root / ".task/input.txt"
    generated.parent.mkdir()
    generated.write_text("generated", encoding="utf-8")

    with pytest.raises(TaskBundleError):
        resolve_bundle_path(root, ".task/input.txt", "file")
