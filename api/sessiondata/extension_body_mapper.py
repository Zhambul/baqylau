# Copyright (c) 2026 Zhambyl Yermagambet
"""Map extension entry bodies to API responses."""

from __future__ import annotations

from typing import TYPE_CHECKING

from api.sessiondata.models import entry as entry_models, entry_extension_bodies as extension_models
from domain import entry_extensions as extension_bodies

if TYPE_CHECKING:
    from domain import entry_base


def map_body(entry_body: entry_base.EntryBody) -> entry_models.EntryBodyResponse | None:
    """Return the API response for an extension entry body.

    Returns:
        The API response for an extension entry body.

    """
    if not isinstance(entry_body, extension_bodies.ExtensionEntryBody):
        return None
    schema_ref = entry_body.schema_ref
    return extension_models.ExtensionBodyResponse(
        owner=entry_body.owner,
        entry_type=entry_body.entry_type,
        source_event_id=str(entry_body.source_event_id),
        schema_ref=extension_models.ExtensionSchemaRefResponse(
            owner=schema_ref.owner,
            name=schema_ref.name,
            version=schema_ref.version,
            digest=schema_ref.digest,
        ),
        document=entry_body.document,
    )
