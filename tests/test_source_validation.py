import pytest

from task_bundle.errors import ErrorCode, TaskBundleError
from task_bundle.source.validation import validate_commit_sha, validate_repository_url


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            "HTTPS://GitHub.COM/Owner/Repository.git/",
            "https://github.com/Owner/Repository.git",
        ),
        (
            "https://git.example.com/Group/Repository",
            "https://git.example.com/Group/Repository",
        ),
    ],
)
def test_valid_public_repository_url_is_normalized(value: str, expected: str) -> None:
    assert validate_repository_url(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "https:///owner/repo.git",
        "https://github.com",
        "https://github.com/",
        "https://user@github.com/owner/repo.git",
        "https://user:password@github.com/owner/repo.git",
        "https://github.com/owner/repo.git?token=secret",
        "https://github.com/owner/repo.git#fragment",
        "file:///tmp/repo",
        "/tmp/repo",
        "../repo",
        "ssh://git@github.com/owner/repo.git",
        "git@github.com:owner/repo.git",
        "git://github.com/owner/repo.git",
        "ext::sh -c echo",
        "https://github.com/owner/repo.git\n",
    ],
)
def test_unsafe_repository_urls_are_rejected(value: str) -> None:
    with pytest.raises(TaskBundleError) as caught:
        validate_repository_url(value)

    assert caught.value.code == ErrorCode.SOURCE_URL_ERROR


def test_repository_path_case_is_preserved() -> None:
    normalized = validate_repository_url("https://EXAMPLE.com/Owner/MixedCase.git")

    assert normalized == "https://example.com/Owner/MixedCase.git"


def test_valid_commit_is_lowercased() -> None:
    assert validate_commit_sha("A" * 40) == "a" * 40


@pytest.mark.parametrize(
    "value",
    [
        "abc123",
        "main",
        "v1.0",
        "HEAD",
        f"{'a' * 39}^",
        f"{'a' * 39}~",
        "refs/heads/main",
        f"{'a' * 39} ",
        "g" * 40,
    ],
)
def test_non_exact_commits_are_rejected(value: str) -> None:
    with pytest.raises(TaskBundleError) as caught:
        validate_commit_sha(value)

    assert caught.value.code == ErrorCode.SOURCE_COMMIT_FORMAT_ERROR
