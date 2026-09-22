# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep absent, inactive, incompatible, and undeclared services distinct."""

from dataclasses import replace

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.services import ServiceResolved

from tests.extension_api import service_checks, service_samples as fixtures


def test_resolution_exposes_only_public_queries() -> None:
    """Metadata lookup can run without an execution grant but cannot expose hidden reads."""
    host = service_checks.test_host()
    response = host.access.resolve_service(fixtures.resolve_request())
    assert isinstance(response, ServiceResolved)
    assert tuple(query.name for query in response.queries) == (f"{fixtures.BETA}.read",)
    assert not host.target.received


@pytest.mark.parametrize("change", ["not_installed", "not_enabled", "not_provided", "incompatible"])
def test_service_unavailability_is_typed(change: str) -> None:
    """A missing optional service is not a successful empty value."""
    host = service_checks.test_host()
    provider = host.providers.entries[0]
    if change == "not_installed":
        host.providers.entries = ()
    if change == "not_enabled":
        host.providers.replace(replace(provider, environment=None))
    if change == "not_provided":
        contributions = provider.manifest.contributions.model_copy(update={"services": ()})
        manifest = provider.manifest.model_copy(update={"contributions": contributions})
        host.providers.replace(replace(provider, manifest=manifest))
    if change == "incompatible":
        _change_version(host)
    response = host.access.resolve_service(fixtures.resolve_request())
    assert response.status == "unavailable"
    assert response.reason == change
    assert not host.target.received


def test_undeclared_service_access_is_rejected() -> None:
    """A valid peer identity does not grant access to an undeclared public service."""
    host = service_checks.test_host()
    request = fixtures.resolve_request()
    binding = request.binding.model_copy(update={"service_id": f"{fixtures.BETA}.other"})
    with pytest.raises(ExtensionContractError, match="not declared"):
        host.access.resolve_service(request.model_copy(update={"binding": binding}))


def _change_version(host: service_checks.ServiceTestHost) -> None:
    provider = host.providers.entries[0]
    contributions = provider.manifest.contributions
    service = contributions.services[0].model_copy(update={"version": "2.0.0"})
    contributions = contributions.model_copy(update={"services": (service,)})
    manifest = provider.manifest.model_copy(update={"contributions": contributions})
    host.providers.replace(replace(provider, manifest=manifest))
