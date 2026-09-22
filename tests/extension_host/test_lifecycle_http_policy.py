# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep readonly management, exact request revisions, and private values observable."""

from pathlib import Path

import pytest

from sdk.client_extension_lifecycle import preview_request
from sdk.transport import ApiFailureError
from tests.extension_host import (
    lifecycle_dependency_fixture as dependencies,
    lifecycle_http_fixture as fixture,
    package_fixture,
    process_fixture,
)

OWNER = package_fixture.OWNER


def test_read_only_allows_preview_but_no_mutation(tmp_path: Path) -> None:
    """The real daemon denies lifecycle changes and rescans before either can write."""
    package_fixture.write_package(tmp_path / "packages", web=True)
    with process_fixture.running_catalog(tmp_path, read_only=True) as client:
        request = fixture.lifecycle_request(client, OWNER, "enable", "read-only")
        assert client.extensions.lifecycle.state().read_only
        assert client.extensions.lifecycle.preview(OWNER, preview_request(request)).affected_extensions == (OWNER,)
        with pytest.raises(ApiFailureError, match=r"403.*read-only"):
            client.extensions.lifecycle.change(OWNER, request)
        with pytest.raises(ApiFailureError, match=r"403.*read-only"):
            client.extensions.rescan(client.extensions.catalog().revision)
        assert not client.extensions.lifecycle.state().committed_packages


def test_http_rejects_stale_selected_state(tmp_path: Path) -> None:
    """A client cannot change another runtime by reusing an obsolete state revision."""
    package_fixture.write_package(tmp_path / "packages", web=True)
    with process_fixture.running_catalog(tmp_path) as client:
        request = fixture.lifecycle_request(client, OWNER, "enable", "stale")
        request = request.model_copy(update={"expected_revision": 0})
        with pytest.raises(ApiFailureError, match=r"409.*revision changed"):
            client.extensions.lifecycle.change(OWNER, request)
        assert not client.extensions.lifecycle.state().committed_packages


def test_runtime_and_operation_hide_settings(tmp_path: Path) -> None:
    """Defaults and private candidate values do not appear in lifecycle read responses."""
    dependencies.write_settings_package(tmp_path)
    with process_fixture.running_catalog(tmp_path) as client:
        request = fixture.lifecycle_request(client, OWNER, "enable", "settings-private")
        admitted = client.extensions.lifecycle.change(OWNER, request)
        finished = fixture.wait_operation(client, admitted.operation.operation_id)
        assert finished.status == "succeeded"
        assert dependencies.PRIVATE_DEFAULT not in admitted.model_dump_json()
        assert dependencies.PRIVATE_DEFAULT not in finished.model_dump_json()
        assert dependencies.PRIVATE_DEFAULT not in client.extensions.lifecycle.state().model_dump_json()
