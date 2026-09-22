# Copyright (c) 2026 Zhambyl Yermagambet
"""Check terminal input schemas and view registration before presentation."""

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest.settings import SettingsDefinition
from baqylau_extension_api.models.documents import EncodedDocument
from baqylau_extension_api.schemas import SchemaSet
from baqylau_extension_api.terminal.registration import validate_presentation_request

from tests.extension_api import samples, terminal_samples


@pytest.mark.parametrize("change", [
    {"extension_id": "peer"}, {"view_id": "test.sample.absent"},
])
def test_request_requires_registered_owner(change: dict[str, str]) -> None:
    """Reject other owners and unknown view IDs before calling feature code."""
    request = terminal_samples.view_request()
    request = request.model_copy(update={"binding": request.binding.model_copy(update=change)})
    with pytest.raises(ExtensionContractError):
        validate_presentation_request(terminal_samples.load_request().manifest, SchemaSet(()), request)


def test_request_rejects_undeclared_scope() -> None:
    """Do not invent a session when a view is installation-scoped."""
    request = terminal_samples.view_request()
    snapshot = request.binding.snapshot.model_copy(update={"scope": samples.SESSION})
    binding = request.binding.model_copy(update={"snapshot": snapshot})
    request = request.model_copy(update={"binding": binding})
    with pytest.raises(ExtensionContractError, match="scope is not declared"):
        validate_presentation_request(terminal_samples.load_request().manifest, SchemaSet(()), request)


def test_terminal_request_checks_settings_schema() -> None:
    """Require effective settings when the package declares a settings schema."""
    manifest = terminal_samples.command_manifest()
    document = EncodedDocument(schema_ref=terminal_samples.schema_definition().reference, json_text='"default"')
    settings = SettingsDefinition(defaults=document, scopes=("installation",))
    manifest = manifest.model_copy(update={"settings": settings})
    with pytest.raises(ExtensionContractError, match="settings do not match"):
        validate_presentation_request(manifest, SchemaSet(manifest.schemas), terminal_samples.view_request())
    request = terminal_samples.view_request().model_copy(update={"settings": document})
    validate_presentation_request(manifest, SchemaSet(manifest.schemas), request)


def test_request_rejects_undeclared_settings() -> None:
    """Reject settings when a package has no settings definition."""
    request = terminal_samples.view_request().model_copy(update={"settings": samples.encoded_document()})
    with pytest.raises(ExtensionContractError, match="settings are not declared"):
        validate_presentation_request(terminal_samples.load_request().manifest, SchemaSet(()), request)


def test_terminal_state_cannot_use_a_peer_schema() -> None:
    """Allow registered peer data for display, but keep view state package-owned."""
    manifest = terminal_samples.load_request().manifest
    schemas = SchemaSet((samples.schema_definition(),))
    request = terminal_samples.view_request().model_copy(update={"document": samples.encoded_document()})
    validate_presentation_request(manifest, schemas, request)
    with pytest.raises(ExtensionContractError, match="state must use"):
        validate_presentation_request(manifest, schemas, request.model_copy(update={
            "state": samples.encoded_document(),
        }))


def test_terminal_document_is_schema_validated() -> None:
    """Do not let a type-correct encoded envelope bypass document validation."""
    manifest = terminal_samples.load_request().manifest
    request = terminal_samples.view_request().model_copy(update={"document": samples.encoded_document("42")})
    with pytest.raises(ExtensionContractError):
        validate_presentation_request(manifest, SchemaSet((samples.schema_definition(),)), request)
