from pathlib import Path

import pytest

from tests.bundle_helpers import BundleFactory, create_bundle


@pytest.fixture
def bundle_factory() -> BundleFactory:
    def factory(root: Path) -> Path:
        return create_bundle(root)

    return factory
