# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep readonly settings and stale writes consistent at the actual HTTP boundary."""

from pathlib import Path

import pytest

from sdk.transport import ApiFailureError
from tests import terminal_pty_waits
from tests.extension_host import (
    lifecycle_dependency_fixture as packages,
    lifecycle_http_fixture as lifecycle,
    package_fixture,
    process_fixture,
    settings_http_fixture as fixture,
)

OWNER = package_fixture.OWNER


def test_http_read_only_settings_remain_readable(tmp_path: Path) -> None:
    """The direct write route applies policy even before a browser page exists."""
    packages.write_settings_package(tmp_path)
    with process_fixture.running_catalog(tmp_path, read_only=True) as client:
        selected = fixture.request(client, OWNER, "denied", '"not saved"')
        assert client.extensions.settings.read(OWNER).read_only
        with pytest.raises(ApiFailureError, match=r"403.*read-only"):
            client.extensions.settings.change(OWNER, selected)
        assert client.extensions.settings.read(OWNER).settings.settings_revision == 0


def test_http_stale_settings_keep_saved_values(tmp_path: Path) -> None:
    """A second saved form cannot overwrite the first accepted edit."""
    packages.write_settings_package(tmp_path)
    with process_fixture.running_catalog(tmp_path) as client:
        terminal_pty_waits.wait_until(lambda: client.extensions.lifecycle.state().phase == "running")
        first = fixture.request(client, OWNER, "first", '"first form"')
        stale = fixture.request(client, OWNER, "stale", '"stale form"')
        admitted = client.extensions.settings.change(OWNER, first)
        lifecycle.wait_operation(client, admitted.operation.operation_id)
        with pytest.raises(ApiFailureError, match=r"409.*revision changed"):
            client.extensions.settings.change(OWNER, stale)
        assert client.extensions.settings.read(OWNER).settings.effective == first.document


def test_http_operation_omits_settings_document(tmp_path: Path) -> None:
    """Only the explicit settings read returns values, not general operation or state reads."""
    packages.write_settings_package(tmp_path)
    with process_fixture.running_catalog(tmp_path) as client:
        terminal_pty_waits.wait_until(lambda: client.extensions.lifecycle.state().phase == "running")
        selected = fixture.request(client, OWNER, "private-settings", '"private-override-marker"')
        admitted = client.extensions.settings.change(OWNER, selected)
        finished = lifecycle.wait_operation(client, admitted.operation.operation_id)
        assert "private-override-marker" not in admitted.model_dump_json()
        assert "private-override-marker" not in finished.model_dump_json()
        assert "private-override-marker" not in client.extensions.lifecycle.state().model_dump_json()


def test_http_settings_reads_are_not_cached(tmp_path: Path) -> None:
    """Settings documents must not remain in a shared browser response cache."""
    packages.write_settings_package(tmp_path)
    with process_fixture.running_catalog(tmp_path) as client:
        transport = client.extensions.settings.transport
        response = transport.client.get(f"/api/extensions/{OWNER}/settings")
        response.raise_for_status()
        assert response.headers["Cache-Control"] == "no-store"
