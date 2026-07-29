from importlib.metadata import version

from task_bundle import __version__


def test_package_and_cli_versions_agree() -> None:
    assert version("task-bundle-cli") == __version__
