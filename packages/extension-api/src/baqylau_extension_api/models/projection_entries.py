# Copyright (c) 2026 Zhambyl Yermagambet
"""Describe extension-owned feed rows without changing canonical facts."""

from baqylau_extension_api.models.base import Identifier, OpaqueId, WireModel
from baqylau_extension_api.models.documents import EncodedDocument
from baqylau_extension_api.terminal.text import DisplayText


class ProjectedEntry(WireModel):
    """Propose one stable row linked to an input fact in this request."""

    entry_key: OpaqueId
    source_event_id: OpaqueId
    entry_type: Identifier
    document: EncodedDocument
    summary: DisplayText
    occurred_at: float | None = None
