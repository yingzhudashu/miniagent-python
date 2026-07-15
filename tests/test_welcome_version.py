"""Welcome banner version must match package __version__ (dynamic setuptools metadata)."""

import miniagent
from miniagent.assistant.engine.welcome import get_version


def test_get_version_matches_package_version() -> None:
    assert get_version() == miniagent.__version__
    assert get_version()  # non-empty string
