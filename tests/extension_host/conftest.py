# Copyright (c) 2026 Zhambyl Yermagambet
"""Build dependency fixtures once per test process without a network or a cache."""

from pathlib import Path

import pytest

from tests.extension_host.wheel_fixture import build_wheels


@pytest.fixture(scope="session")
def runtime_wheels(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Supply real test wheel bytes from installed dependencies.

    Returns:
        The complete SDK test wheelhouse for this test process.

    """
    return build_wheels(tmp_path_factory.mktemp("extension-wheels"))
