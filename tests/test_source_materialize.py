import pytest

from task_bundle.errors import ErrorCode, TaskBundleError
from task_bundle.source.materialize import validate_tree_listing

_OID = "a" * 40


def _record(path: str, mode: str = "100644", object_type: str = "blob") -> str:
    return f"{mode} {object_type} {_OID}\t{path}\0"


@pytest.mark.parametrize(
    "listing",
    [
        _record("../escape"),
        _record("/absolute"),
        _record("nested\\.git"),
        _record(".git/config"),
        _record("line\nbreak"),
    ],
)
def test_unsafe_tree_paths_are_rejected(listing: str) -> None:
    with pytest.raises(TaskBundleError) as caught:
        validate_tree_listing(listing)

    assert caught.value.code == ErrorCode.SOURCE_TREE_UNSAFE


@pytest.mark.parametrize(
    "listing",
    [
        _record("README") + _record("readme"),
        _record("entry") + _record("entry/child"),
        _record("same") + _record("same"),
    ],
)
def test_duplicate_type_and_case_collisions_are_rejected(listing: str) -> None:
    with pytest.raises(TaskBundleError) as caught:
        validate_tree_listing(listing)

    assert caught.value.code == ErrorCode.SOURCE_TREE_UNSAFE


def test_supported_tree_entries_are_sorted() -> None:
    entries = validate_tree_listing(
        _record("tool-link", "120000") + _record("bin/tool", "100755") + _record("README.md")
    )

    assert [entry.path for entry in entries] == ["README.md", "bin/tool", "tool-link"]


def test_special_git_mode_is_rejected() -> None:
    with pytest.raises(TaskBundleError) as caught:
        validate_tree_listing(_record("socket", "140000"))

    assert caught.value.code == ErrorCode.SOURCE_TREE_UNSAFE


def test_gitlinks_remain_visible_for_structured_service_rejection() -> None:
    entries = validate_tree_listing(_record("vendor/sub", "160000", "commit"))

    assert entries[0].mode == "160000"
    assert entries[0].path == "vendor/sub"
