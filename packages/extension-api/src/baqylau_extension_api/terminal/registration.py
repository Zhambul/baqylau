# Copyright (c) 2026 Zhambyl Yermagambet
"""Check terminal requests and actions against the data-only manifest."""

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.manifest.views import TerminalView
from baqylau_extension_api.operations import documents
from baqylau_extension_api.schemas import SchemaSet
from baqylau_extension_api.terminal.models import TerminalViewRequest


def validate_presentation_request(
    manifest: ExtensionManifest, schemas: SchemaSet, request: TerminalViewRequest,
) -> None:
    """Reject an undeclared view or invalid snapshot before feature code runs.

    Raises:
        ExtensionContractError: If scope, owner, or document registration is invalid.

    """
    binding = request.binding
    if binding.extension_id != manifest.extension_id:
        message = "terminal view belongs to another extension"
        raise ExtensionContractError(message)
    view = _declared_view(manifest, binding.view_id)
    if binding.snapshot.scope.kind not in view.scopes:
        message = "terminal view or scope is not declared"
        raise ExtensionContractError(message)
    documents.validate_settings(manifest, request.settings, schemas)
    _validate_documents(schemas, request)


def _validate_documents(schemas: SchemaSet, request: TerminalViewRequest) -> None:
    for document in (request.state, request.document):
        if document is not None:
            schemas.validate(document)
    if request.state is not None and request.state.schema_ref.owner != request.binding.extension_id:
        message = "terminal view state must use its extension's schema"
        raise ExtensionContractError(message)


def _declared_view(manifest: ExtensionManifest, view_id: str) -> TerminalView:
    for view in manifest.contributions.terminal:
        if view.view_id == view_id:
            return view
    message = "terminal view is not declared"
    raise ExtensionContractError(message)
