# Copyright (c) 2026 Zhambyl Yermagambet
"""A package that declares no settings has no settings resource."""

from pathlib import Path

import pytest

from sdk.transport import ApiFailureError
from tests.extension_host import package_fixture, process_fixture


def test_http_undeclared_settings_are_not_found(tmp_path: Path) -> None:
    """The settings page reads a 404 as "no settings", and not as a failed request."""
    package_fixture.write_package(tmp_path / "packages")
    with (
        process_fixture.running_catalog(tmp_path) as client,
        pytest.raises(ApiFailureError, match=r"404.*no settings declaration"),
    ):
        client.extensions.settings.read(package_fixture.OWNER)
