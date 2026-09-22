# Copyright (c) 2026 Zhambyl Yermagambet
"""Feed entry bodies owned by one extension projection."""

from dataclasses import dataclass

from domain.entry_base import EntryBody
from domain.ids import CanonicalEventId


@dataclass(frozen=True)
class ExtensionSchemaIdentity:
    """Identify the feature-owned schema of one derived document."""

    owner: str
    name: str
    version: int
    digest: str


@dataclass(frozen=True)
class ExtensionEntryBody(EntryBody):
    """Record one feature-owned derived entry without its stored fact."""

    owner: str
    entry_type: str
    source_event_id: CanonicalEventId
    schema_ref: ExtensionSchemaIdentity
    document: str
