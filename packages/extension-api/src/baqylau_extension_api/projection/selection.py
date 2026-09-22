# Copyright (c) 2026 Zhambyl Yermagambet
"""Validate pure record-key selection and exact host capture coverage."""

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.models.projections import ProjectionReadSet, ProjectionRequest, ProjectionSelectionRequest
from baqylau_extension_api.models.records import MissingRecord, RecordState
from baqylau_extension_api.projection import boundaries, records
from baqylau_extension_api.schemas import SchemaSet

MAX_READ_SET_BYTES = 1_048_576


def validate_read_set(request: ProjectionSelectionRequest, response: ProjectionReadSet) -> ProjectionReadSet:
    """Keep selected keys unique, owned, and attached to the exact prior snapshot.

    Returns:
        The complete checked read selection.

    Raises:
        ExtensionContractError: If binding, keys, owner, or scope is invalid.

    """
    checked_request = boundaries.validate_selection_boundary(request)
    checked = ProjectionReadSet.model_validate(response)
    _require_read_set_size(checked)
    if checked.binding != checked_request.binding:
        message = "projection read set must keep its exact processing and snapshot binding"
        raise ExtensionContractError(message)
    if len(set(checked.keys)) != len(checked.keys):
        message = "projection read set keys must be unique"
        raise ExtensionContractError(message)
    context = checked.binding.context
    foreign_keys = any(
        key.owner != context.extension_id or key.scope != context.scope
        for key in checked.keys
    )
    if foreign_keys:
        message = "projection read set keys must have the context owner and scope"
        raise ExtensionContractError(message)
    return checked


def validate_read_registrations(manifest: ExtensionManifest, schemas: SchemaSet, response: ProjectionReadSet) -> None:
    """Require all selected keys to name declared owned collections."""
    for key in response.keys:
        records.validate_prior_record(manifest, schemas, MissingRecord(key=key))


def capture_projection_request(
    request: ProjectionSelectionRequest, selected: ProjectionReadSet, captured: tuple[RecordState, ...],
) -> ProjectionRequest:
    """Assemble only complete host reads from the selected snapshot.

    The host must read all keys at this snapshot, or start a new selection if
    the active boundary changed. Missing rows require explicit MissingRecord.

    Returns:
        A request that covers every selected key once, in selection order.

    Raises:
        ExtensionContractError: If captured keys differ from the requested keys.

    """
    checked = validate_read_set(request, selected)
    if tuple(record.key for record in captured) != checked.keys:
        message = "captured projection records must cover the exact selected keys in order"
        raise ExtensionContractError(message)
    return boundaries.validate_request_boundary(ProjectionRequest(
        binding=request.binding, events=request.events, prior_records=captured,
    ))


def _require_read_set_size(response: ProjectionReadSet) -> None:
    if len(response.model_dump_json().encode("utf-8")) > MAX_READ_SET_BYTES:
        message = "projection read set exceeds its encoded size limit"
        raise ExtensionContractError(message)
