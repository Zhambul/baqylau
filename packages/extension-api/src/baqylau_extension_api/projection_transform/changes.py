# Copyright (c) 2026 Zhambyl Yermagambet
"""Validate full projection write proposals, scopes, and source links."""

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest import rules
from baqylau_extension_api.models.canonical import CanonicalFact, CoreFact
from baqylau_extension_api.models.projection_changes import (
    CoreActorChange,
    CoreEntryChange,
    CoreSessionChange,
    ExtensionEntryChange,
    ProjectionChange,
)
from baqylau_extension_api.models.projection_transforms import ProjectionTransformRequest
from baqylau_extension_api.models.scopes import SessionScope
from baqylau_extension_api.projection_transform import records, state, targets
from baqylau_extension_api.schemas import SchemaSet


def validate_changes(
    request: ProjectionTransformRequest, changes: tuple[ProjectionChange, ...], schemas: SchemaSet,
) -> None:
    """Reject conflicting write targets before returning any part of a proposal.

    Raises:
        ExtensionContractError: If several changes write the same logical row.

    """
    rules.require_unique((change.change_id for change in changes), "projection change identities")
    keys = tuple(targets.change_target(change) for change in changes)
    if len(set(keys)) != len(keys):
        message = "projection changes must not write the same target twice"
        raise ExtensionContractError(message)
    for change in changes:
        _validate_change(request, change, schemas)
    records.validate_record_proposals(request, changes, schemas)
    state.validate_proposed_state(request.binding.context.scope, request.before_core, changes)


def _validate_change(request: ProjectionTransformRequest, change: ProjectionChange, schemas: SchemaSet) -> None:
    scope = request.binding.context.scope
    if isinstance(change, CoreSessionChange):
        state.validate_session_scope(scope, change.session)
    elif isinstance(change, CoreActorChange):
        state.validate_actor_scope(scope, change.actor)
    elif isinstance(change, CoreEntryChange):
        _validate_core_entry(request, change)
    elif isinstance(change, ExtensionEntryChange):
        _validate_extension_entry(request, change, schemas)


def _validate_core_entry(request: ProjectionTransformRequest, change: CoreEntryChange) -> None:
    scope = request.binding.context.scope
    if not isinstance(scope, SessionScope) or change.entry.session_id != scope.session_id:
        message = "core feed change must keep the selected session"
        raise ExtensionContractError(message)
    source = source_fact(request, change.source_event_id)
    if not isinstance(source, CoreFact):
        message = "core feed change requires a captured core source fact"
        raise ExtensionContractError(message)
    expected = source.scope.actor_id, source.parent_actor_id, source.turn_id
    actual = change.entry.actor_id, change.entry.parent_actor_id, change.entry.turn_id
    if actual != expected:
        message = "core feed change must keep its source actor, parent, and turn"
        raise ExtensionContractError(message)


def _validate_extension_entry(
    request: ProjectionTransformRequest, change: ExtensionEntryChange, schemas: SchemaSet,
) -> None:
    if (
        change.scope != request.binding.context.scope
        or change.entry.document.schema_ref.owner != change.owner
    ):
        message = "extension feed change must keep its scope and schema owner"
        raise ExtensionContractError(message)
    rules.require_owned((change.entry.entry_type,), change.owner)
    source_fact(request, change.entry.source_event_id)
    schemas.validate(change.entry.document)


def source_fact(request: ProjectionTransformRequest, source_event_id: str) -> CanonicalFact:
    """Resolve a feed row's cause only from the supplied committed facts.

    Returns:
        The immutable captured cause.

    Raises:
        ExtensionContractError: If a proposed row refers to an unknown source.

    """
    for stored in request.events:
        if stored.fact.event_id == source_event_id:
            return stored.fact
    message = "projection feed change must refer to a captured source fact"
    raise ExtensionContractError(message)
