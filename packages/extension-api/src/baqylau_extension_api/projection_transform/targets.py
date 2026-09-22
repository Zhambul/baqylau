# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep write targets distinct from operation identities and feed positions."""

from baqylau_extension_api.models.projection_changes import (
    CoreActorChange,
    CoreEntryChange,
    CoreSessionChange,
    ExtensionEntryChange,
    ProjectionChange,
)
from baqylau_extension_api.projection_identity import ProjectionEntryIdentity, projected_entry_id


def change_target(change: ProjectionChange) -> tuple[str, str]:
    """Return a stable typed-row key for conflict detection.

    Returns:
        A row category and its complete logical identity.

    """
    if isinstance(change, CoreSessionChange):
        return "session", change.session.session_id
    if isinstance(change, CoreActorChange):
        return "actor", change.actor.actor_id
    if isinstance(change, CoreEntryChange):
        return "entry", change.entry.entry_id
    if isinstance(change, ExtensionEntryChange):
        return "entry", projected_entry_id(ProjectionEntryIdentity(
            extension_id=change.owner, scope=change.scope,
            source_event_id=change.entry.source_event_id, entry_key=change.entry.entry_key,
        ))
    return "record", change.write.key.model_dump_json()
