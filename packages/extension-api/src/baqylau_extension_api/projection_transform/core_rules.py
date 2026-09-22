# Copyright (c) 2026 Zhambyl Yermagambet
"""Apply core identity and lifecycle protection to projection operations."""

from baqylau_extension_api.core.aggregate import CoreAggregateState
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.projection_changes import (
    CoreActorChange,
    CoreEntryChange,
    CoreSessionChange,
    ProjectionChange,
)
from baqylau_extension_api.projection_transform import protected


def validate_core_replacement(original: ProjectionChange, replacement: ProjectionChange) -> None:
    """Keep protected core fields while allowing valid visible data changes."""
    if isinstance(original, CoreSessionChange) and isinstance(replacement, CoreSessionChange):
        protected.require_session_execution(original.session, replacement.session)
    elif isinstance(original, CoreActorChange) and isinstance(replacement, CoreActorChange):
        protected.require_actor_execution(original.actor, replacement.actor)
    elif isinstance(original, CoreEntryChange) and isinstance(replacement, CoreEntryChange):
        _require_entry_origin(original, replacement)


def validate_core_drop(before: CoreAggregateState, original: ProjectionChange) -> None:
    """Permit a display-only drop but retain required state transitions."""
    if isinstance(original, CoreSessionChange):
        protected.require_session_execution(before.session, original.session)
    elif isinstance(original, CoreActorChange):
        previous = next((
            actor for actor in before.actors if actor.actor_id == original.actor.actor_id
        ), None)
        protected.require_actor_execution(previous, original.actor)


def validate_core_addition(before: CoreAggregateState, addition: ProjectionChange) -> None:
    """Only update existing core rows; use canonical events to create actors or sessions."""
    validate_core_drop(before, addition)


def _require_entry_origin(expected: CoreEntryChange, actual: CoreEntryChange) -> None:
    expected_origin = (
        expected.source_event_id, expected.entry.entry_id, expected.entry.session_id, expected.entry.actor_id,
        expected.entry.parent_actor_id, expected.entry.turn_id,
    )
    actual_origin = (
        actual.source_event_id, actual.entry.entry_id, actual.entry.session_id, actual.entry.actor_id,
        actual.entry.parent_actor_id, actual.entry.turn_id,
    )
    if expected_origin != actual_origin:
        message = "projection replacement cannot change core feed identity or source references"
        raise ExtensionContractError(message)
