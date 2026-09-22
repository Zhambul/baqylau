# Copyright (c) 2026 Zhambyl Yermagambet
"""Tag complete proposed core and extension writes for pure transformation."""

from typing import Annotated, Literal

from pydantic import Field

from baqylau_extension_api.core.actor_state import CoreActorFacts
from baqylau_extension_api.core.entries import CoreSessionEntry
from baqylau_extension_api.core.session_state import CoreSessionFacts
from baqylau_extension_api.models.base import ExtensionId, OpaqueId, WireModel
from baqylau_extension_api.models.projection_entries import ProjectedEntry
from baqylau_extension_api.models.record_changes import RecordChange
from baqylau_extension_api.models.scopes import ExtensionScope


class ProjectionChangeModel(WireModel):
    """Give each proposed write a stable identity within its processing batch."""

    change_id: OpaqueId


class CoreSessionChange(ProjectionChangeModel):
    """Propose the complete next session row."""

    kind: Literal["session"] = "session"
    session: CoreSessionFacts


class CoreActorChange(ProjectionChangeModel):
    """Propose the complete next actor row."""

    kind: Literal["actor"] = "actor"
    actor: CoreActorFacts


class CoreEntryChange(ProjectionChangeModel):
    """Add a core feed row linked to one captured canonical fact."""

    kind: Literal["core_entry"] = "core_entry"
    source_event_id: OpaqueId
    entry: CoreSessionEntry


class ExtensionEntryChange(ProjectionChangeModel):
    """Add an owned extension feed row without a host commit position."""

    kind: Literal["extension_entry"] = "extension_entry"
    owner: ExtensionId
    scope: ExtensionScope
    entry: ProjectedEntry


class ExtensionRecordChange(ProjectionChangeModel):
    """Propose an owned record write with a captured expected revision."""

    kind: Literal["record"] = "record"
    write: RecordChange


type ProjectionChange = Annotated[
    CoreSessionChange | CoreActorChange | CoreEntryChange | ExtensionEntryChange | ExtensionRecordChange,
    Field(discriminator="kind"),
]
