# Copyright (c) 2026 Zhambyl Yermagambet
"""Check projection scope, ordered input, and captured record revisions."""

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest import rules
from baqylau_extension_api.models.projections import ProjectionRequest, ProjectionSelectionRequest
from baqylau_extension_api.projection.binding import validate_binding

MAX_PROJECTION_REQUEST_BYTES = 8_388_608


def validate_request_boundary(request: ProjectionRequest) -> ProjectionRequest:
    """Reject inconsistent snapshots before a projector can read them.

    Returns:
        A revalidated request with distinct source and projection cursors.

    """
    checked = ProjectionRequest.model_validate(request)
    validate_binding(checked.binding)
    _validate_events(checked)
    _validate_records(checked)
    _require_size(checked.model_dump_json())
    return checked


def validate_selection_boundary(request: ProjectionSelectionRequest) -> ProjectionSelectionRequest:
    """Validate selected facts before record keys are known.

    Returns:
        An immutable fact selection without live record state.

    """
    checked = ProjectionSelectionRequest.model_validate(request)
    validate_binding(checked.binding)
    _validate_events(checked)
    _require_size(checked.model_dump_json())
    return checked


def _validate_events(request: ProjectionSelectionRequest) -> None:
    binding = request.binding
    cursors = tuple(stored.cursor for stored in request.events)
    if tuple(sorted(set(cursors))) != cursors:
        message = "projection fact cursors must be unique and increasing"
        raise ExtensionContractError(message)
    if any(not binding.after_input_cursor < cursor <= binding.context.input_cursor for cursor in cursors):
        message = "projection fact cursor is outside the selected input boundary"
        raise ExtensionContractError(message)
    if any(stored.fact.scope != binding.context.scope for stored in request.events):
        message = "projection facts must have the context scope"
        raise ExtensionContractError(message)
    rules.require_unique((stored.fact.event_id for stored in request.events), "projection fact identities")


def _validate_records(request: ProjectionRequest) -> None:
    keys = tuple(record.key for record in request.prior_records)
    if len(set(keys)) != len(keys):
        message = "projection prior record keys must be unique"
        raise ExtensionContractError(message)
    if any(record.revision > request.binding.snapshot.commit_cursor for record in request.prior_records):
        message = "prior record revision exceeds the projection snapshot"
        raise ExtensionContractError(message)
    if any(key.scope != request.binding.context.scope for key in keys):
        message = "projection prior records must have the context scope"
        raise ExtensionContractError(message)


def _require_size(encoded: str) -> None:
    if len(encoded.encode("utf-8")) > MAX_PROJECTION_REQUEST_BYTES:
        message = "projection request exceeds its encoded size limit"
        raise ExtensionContractError(message)
