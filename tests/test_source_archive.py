import io
import os
import tarfile
from pathlib import Path

import pytest

from task_bundle.errors import ErrorCode, TaskBundleError
from task_bundle.source.archive import extract_source_archive

TarMember = tuple[tarfile.TarInfo, bytes | None]


def _write_tar(path: Path, members: list[TarMember]) -> None:
    with tarfile.open(path, "w") as archive:
        for member, content in members:
            archive.addfile(member, io.BytesIO(content) if content is not None else None)


def _file(name: str, content: bytes = b"content", mode: int = 0o644) -> TarMember:
    member = tarfile.TarInfo(name)
    member.type = tarfile.REGTYPE
    member.size = len(content)
    member.mode = mode
    return member, content


def _symlink(name: str, target: str) -> TarMember:
    member = tarfile.TarInfo(name)
    member.type = tarfile.SYMTYPE
    member.linkname = target
    return member, None


def test_safe_archive_extracts_files_modes_and_internal_symlink(tmp_path: Path) -> None:
    archive = tmp_path / "source.tar"
    _write_tar(
        archive,
        [
            _file("src/tool", b"#!/bin/sh\n", 0o755),
            _symlink("bin/tool", "../src/tool"),
        ],
    )

    destination = tmp_path / "source"
    extract_source_archive(archive, destination)

    assert (destination / "src/tool").read_bytes() == b"#!/bin/sh\n"
    assert os.access(destination / "src/tool", os.X_OK)
    assert os.readlink(destination / "bin/tool") == "../src/tool"
    assert not (destination / ".git").exists()


@pytest.mark.parametrize(
    "member",
    [
        _file("../escape"),
        _file("/absolute"),
        _file("nested/../../escape"),
    ],
)
def test_archive_traversal_is_rejected(tmp_path: Path, member: TarMember) -> None:
    archive = tmp_path / "source.tar"
    _write_tar(archive, [member])

    with pytest.raises(TaskBundleError) as caught:
        extract_source_archive(archive, tmp_path / "source")

    assert caught.value.code == ErrorCode.SOURCE_ARCHIVE_UNSAFE


@pytest.mark.parametrize("kind", [tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE])
def test_unsupported_archive_member_is_rejected(
    tmp_path: Path, kind: bytes
) -> None:
    info = tarfile.TarInfo("unsafe")
    info.type = kind
    info.linkname = "target"
    archive = tmp_path / "source.tar"
    _write_tar(archive, [(info, None)])

    with pytest.raises(TaskBundleError) as caught:
        extract_source_archive(archive, tmp_path / "source")

    assert caught.value.code == ErrorCode.SOURCE_ARCHIVE_UNSAFE


@pytest.mark.parametrize("target", ["/etc/passwd", "../../../../outside"])
def test_unsafe_symlink_target_is_rejected(tmp_path: Path, target: str) -> None:
    archive = tmp_path / "source.tar"
    _write_tar(archive, [_symlink("bin/tool", target)])

    with pytest.raises(TaskBundleError) as caught:
        extract_source_archive(archive, tmp_path / "source")

    assert caught.value.code == ErrorCode.SOURCE_SYMLINK_UNSAFE


def test_member_nested_beneath_symlink_is_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "source.tar"
    _write_tar(archive, [_symlink("linked", "target"), _file("linked/file")])

    with pytest.raises(TaskBundleError) as caught:
        extract_source_archive(archive, tmp_path / "source")

    assert caught.value.code == ErrorCode.SOURCE_ARCHIVE_UNSAFE


def test_git_metadata_member_is_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "source.tar"
    _write_tar(archive, [_file(".git/config")])

    with pytest.raises(TaskBundleError):
        extract_source_archive(archive, tmp_path / "source")
