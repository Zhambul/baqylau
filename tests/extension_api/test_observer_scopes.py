# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep observer work inside its declared scope without synthetic sessions."""

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.scopes import ExtensionScope, InstallationScope, RepositoryScope, WorkspaceScope
from baqylau_extension_api.observers import requests, results
from baqylau_extension_api.schemas import SchemaSet

from tests.extension_api import observer_samples as fixtures

SCOPE = "scope"


@pytest.mark.parametrize(SCOPE, [
    InstallationScope(), fixtures.SESSION_SCOPE, WorkspaceScope(workspace_id="workspace"),
    RepositoryScope(repository_id="repository", worktree="/project", git_directory="/project/.git"),
])
def test_observer_supports_all_scopes(scope: ExtensionScope) -> None:
    """Each scope has a matching trigger and output identity."""
    request = fixtures.request()
    binding = request.binding.model_copy(update={SCOPE: scope})
    fact = request.event.fact.model_copy(update={SCOPE: scope})
    request = request.model_copy(update={
        "binding": binding, "event": request.event.model_copy(update={"fact": fact}),
    })
    manifest = fixtures.manifest()
    assert requests.validate_observation_request(manifest, SchemaSet(manifest.schemas), request) == request
    _validate_output(scope)


def test_undeclared_observer_scope_is_rejected() -> None:
    """A matching job owner does not grant an undeclared input scope."""
    manifest = fixtures.manifest()
    processing = manifest.contributions.processing
    selection = processing[0].model_copy(update={"scopes": ("session",)})
    manifest = manifest.model_copy(update={"contributions": manifest.contributions.model_copy(update={
        "processing": (selection, *processing[1:]),
    })})
    with pytest.raises(ExtensionContractError, match=SCOPE):
        requests.validate_observation_request(manifest, SchemaSet(manifest.schemas), fixtures.request())


def test_selected_core_trigger_is_valid() -> None:
    """Observers can receive the same closed core fact union as projectors."""
    manifest = fixtures.manifest()
    request = fixtures.core_request()
    assert requests.validate_observation_request(manifest, SchemaSet(manifest.schemas), request) == request


def _validate_output(scope: ExtensionScope) -> None:
    response = fixtures.succeeded()
    binding = response.binding.model_copy(update={SCOPE: scope})
    response = response.model_copy(update={
        "binding": binding, "observations": (response.observations[0].model_copy(update={SCOPE: scope}),),
    })
    manifest = fixtures.manifest()
    assert results.validate_observation_result(binding, response) == response
    results.validate_observation_documents(manifest, SchemaSet(manifest.schemas), response)
