# Copyright (c) 2026 Zhambyl Yermagambet
"""Use migration scopes directly, including a repository without a session."""

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.migrations import requests
from baqylau_extension_api.models.migrations import RecordMigrationRequest
from baqylau_extension_api.models.scopes import (
    ExtensionScope,
    InstallationScope,
    RepositoryScope,
    SessionScope,
    WorkspaceScope,
)
from baqylau_extension_api.schemas import SchemaSet

from tests.extension_api import migration_samples as fixtures

SCOPE = "scope"


@pytest.mark.parametrize(SCOPE, [
    InstallationScope(), WorkspaceScope(workspace_id="workspace"),
    RepositoryScope(repository_id="repository", worktree="/project", git_directory="/project/.git"),
    SessionScope(session_id="session", actor_id="actor", harness="harness"),
])
def test_all_declared_scopes_are_supported(scope: ExtensionScope) -> None:
    """The requested scope needs no synthetic session or actor."""
    binding = fixtures.binding().model_copy(update={SCOPE: scope})
    manifest = _scoped_manifest(scope)
    schemas = SchemaSet(manifest.schemas)
    request = fixtures.settings_request().model_copy(update={"binding": binding})
    assert requests.validate_settings_request(manifest, schemas, request) == request
    records = _scoped_records(scope)
    assert requests.validate_records_request(manifest, schemas, records) == records


def test_undeclared_scope_is_rejected() -> None:
    """Settings and collection declarations restrict the supported data scopes."""
    scope = WorkspaceScope(workspace_id="workspace")
    manifest = fixtures.manifest()
    binding = fixtures.binding().model_copy(update={SCOPE: scope})
    settings = fixtures.settings_request().model_copy(update={"binding": binding})
    with pytest.raises(ExtensionContractError, match=SCOPE):
        requests.validate_settings_request(manifest, SchemaSet(manifest.schemas), settings)
    with pytest.raises(ExtensionContractError, match=SCOPE):
        requests.validate_records_request(manifest, SchemaSet(manifest.schemas), _scoped_records(scope))


def test_snapshot_scope_must_match_records() -> None:
    """A cursor for another scope cannot authorize this batch."""
    request = fixtures.records_request()
    snapshot = request.source_snapshot.model_copy(update={SCOPE: WorkspaceScope(workspace_id="other")})
    manifest = fixtures.manifest()
    with pytest.raises(ExtensionContractError, match="snapshot changed scope"):
        requests.validate_records_request(manifest, SchemaSet(manifest.schemas), request.model_copy(update={
            "source_snapshot": snapshot,
        }))


def _scoped_manifest(scope: ExtensionScope) -> ExtensionManifest:
    manifest = fixtures.manifest()
    assert manifest.settings is not None
    collection = manifest.contributions.collections[0].model_copy(update={
        "scopes": (scope.kind,),
    })
    return manifest.model_copy(update={
        "settings": manifest.settings.model_copy(update={"scopes": (scope.kind,)}),
        "contributions": manifest.contributions.model_copy(update={"collections": (collection,)}),
    })


def _scoped_records(scope: ExtensionScope) -> RecordMigrationRequest:
    request = fixtures.records_request()
    return request.model_copy(update={
        "binding": request.binding.model_copy(update={SCOPE: scope}),
        "source_snapshot": request.source_snapshot.model_copy(update={SCOPE: scope}),
        "records": tuple(record.model_copy(update={
            "key": record.key.model_copy(update={SCOPE: scope}),
        }) for record in request.records),
    })
