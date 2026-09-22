# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep peer reads inside declared operations and host-owned call authority."""

from dataclasses import replace

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.scopes import InstallationScope, WorkspaceScope
from baqylau_extension_api.models.services import ServiceQueryRequest
from baqylau_extension_api.runtime.host_context import host_call_scope
from pydantic import ValidationError

from tests.extension_api import service_checks, service_samples as fixtures

SETTINGS_REVISION = 7


def test_peer_query_uses_captured_host_settings() -> None:
    """The consumer cannot choose the target's settings revision."""
    host = service_checks.test_host()
    provider = host.providers.entries[0]
    host.providers.replace(replace(provider, settings_revision=SETTINGS_REVISION))
    with host.access.calls.root(fixtures.environment(fixtures.ALPHA), InstallationScope(), 3):
        response = host.access.query_service(fixtures.service_query())
    assert response.status == "available"
    assert response.settings_revision == SETTINGS_REVISION
    assert host.target.received[0].settings_revision == SETTINGS_REVISION
    assert host.target.received[0].settings is None
    assert response.result.binding.extension_id == fixtures.BETA


@pytest.mark.parametrize("change", ["private", "foreign", "schema", "scope"])
def test_rejected_peer_query_does_not_run(change: str) -> None:
    """Reject hidden reads, undeclared services, bad arguments, and changed scopes."""
    host = service_checks.test_host()
    request = fixtures.service_query()
    if change == "private":
        request = request.model_copy(update={"query_id": f"{fixtures.BETA}.private"})
    if change == "foreign":
        binding = request.binding.model_copy(update={"service_id": f"{fixtures.BETA}.hidden"})
        request = request.model_copy(update={"binding": binding})
    if change == "schema":
        request = request.model_copy(update={
            "arguments": request.arguments.model_copy(update={"json_text": "42"}),
        })
    if change == "scope":
        binding = request.binding.model_copy(update={"scope": WorkspaceScope(workspace_id="other")})
        request = request.model_copy(update={"binding": binding})
    with (
        host.access.calls.root(fixtures.environment(fixtures.ALPHA), InstallationScope(), 3),
        pytest.raises(ExtensionContractError),
    ):
        host.access.query_service(request)
    assert not host.target.received


@pytest.mark.parametrize("reference", [None, "forged-call"])
def test_peer_query_cannot_forge_host_authority(reference: str | None) -> None:
    """An absent or invented correlation ID grants no service query access."""
    host = service_checks.test_host()
    with host_call_scope(reference), pytest.raises(ExtensionContractError):
        host.access.query_service(fixtures.service_query())
    assert not host.target.received


def test_peer_query_rejects_stale_service_version() -> None:
    """A replaced runtime needs a fresh resolution before any read runs."""
    host = service_checks.test_host()
    request = fixtures.service_query()
    revision = request.service_revision.model_copy(update={"runtime_revision": "previous"})
    with host.access.calls.root(fixtures.environment(fixtures.ALPHA), InstallationScope(), 3):
        response = host.access.query_service(request.model_copy(update={"service_revision": revision}))
    assert response.status == "unavailable"
    assert response.reason == "stale_handle"
    assert not host.target.received


@pytest.mark.parametrize("field", ["settings", "settings_revision", "caller", "route"])
def test_peer_query_cannot_supply_host_fields(field: str) -> None:
    """Feature payloads cannot choose peer settings or forge their call route."""
    with pytest.raises(ValidationError):
        ServiceQueryRequest.model_validate(fixtures.service_query().model_copy(update={field: "forged"}))
