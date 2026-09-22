# Copyright (c) 2026 Zhambyl Yermagambet
"""Check dependent confirmation through the public JSON request boundary."""

from pathlib import Path

import pytest

from sdk.client_extension_lifecycle import preview_request
from sdk.transport import ApiFailureError
from tests.extension_host import (
    lifecycle_dependency_fixture as dependencies,
    lifecycle_http_fixture as fixture,
    process_fixture,
)


def test_http_confirms_required_dependents(tmp_path: Path) -> None:
    """A JSON array confirms the exact removal set; an optional consumer stays active."""
    dependencies.write_graph(tmp_path)
    with process_fixture.running_catalog(tmp_path) as client:
        for owner in (dependencies.BASE, dependencies.CHILD, dependencies.LEAF, dependencies.OPTIONAL):
            request = fixture.lifecycle_request(client, owner, "enable", owner)
            admitted = client.extensions.lifecycle.change(owner, request)
            assert fixture.wait_operation(client, admitted.operation.operation_id).status == "succeeded"
        request = fixture.lifecycle_request(client, dependencies.BASE, "disable", "remove-provider")
        assert client.extensions.lifecycle.preview(dependencies.BASE, preview_request(request)).affected_extensions == (
            dependencies.LEAF, dependencies.CHILD, dependencies.BASE,
        )
        with pytest.raises(ApiFailureError, match=r"409.*exact required dependents"):
            client.extensions.lifecycle.change(dependencies.BASE, request)
        request = request.model_copy(update={"confirmed_dependents": (dependencies.LEAF, dependencies.CHILD)})
        admitted = client.extensions.lifecycle.change(dependencies.BASE, request)
        fixture.wait_operation(client, admitted.operation.operation_id)
        selected = client.extensions.lifecycle.state().committed_packages
        assert tuple(package.extension_info.extension_id for package in selected) == (dependencies.OPTIONAL,)
