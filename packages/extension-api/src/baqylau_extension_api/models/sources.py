# Copyright (c) 2026 Zhambyl Yermagambet
"""Describe declared sources without exposing host readers or storage handles."""

from typing import Annotated

from pydantic import Field

from baqylau_extension_api.models.base import ExtensionId, Identifier, NonemptyText, Revision, WireModel
from baqylau_extension_api.models.documents import EncodedDocument
from baqylau_extension_api.models.observations import MAX_OBSERVATIONS
from baqylau_extension_api.models.scopes import ExtensionScope
from baqylau_extension_api.paths import AbsolutePath

MAX_SOURCES = 128
MAX_SOURCE_WATCHES = 64
SourceDeadline = Annotated[float, Field(ge=0)]


class SourceBinding(WireModel):
    """Identify one source call for a host-selected scope and runtime."""

    extension_id: ExtensionId
    scope: ExtensionScope
    runtime_revision: Identifier
    call_id: Identifier


class SourceContext(WireModel):
    """Supply captured settings for live source discovery and reads."""

    binding: SourceBinding
    settings_revision: Revision
    settings: EncodedDocument | None = None


class SourceDescriptor(WireModel):
    """Select a stable source and event-driven wake conditions.

    The source identity changes when an old resume position is no longer valid.
    Paths are watch requests, not permission to read host-owned content.
    """

    source_identity: Identifier
    source_type: Identifier
    watch_paths: Annotated[tuple[AbsolutePath, ...], Field(max_length=MAX_SOURCE_WATCHES)] = ()
    next_due_at: SourceDeadline | None = None
    state: EncodedDocument | None = None


class SourceReadRequest(WireModel):
    """Read after committed progress without changing it inside the worker."""

    context: SourceContext
    source: SourceDescriptor
    after_position: NonemptyText | None = None
    limit: Annotated[int, Field(ge=1, le=MAX_OBSERVATIONS)] = 100


class SourceReleaseRequest(WireModel):
    """Release one source or all sources owned by the selected scope."""

    binding: SourceBinding
    reason: NonemptyText
    source_identity: Identifier | None = None
