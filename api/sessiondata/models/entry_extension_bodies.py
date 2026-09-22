# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide extension entry body responses."""

from pydantic import BaseModel


class ExtensionSchemaRefResponse(BaseModel):
    """Identify the feature-owned schema of one derived document."""

    owner: str
    name: str
    version: int
    digest: str


class ExtensionBodyResponse(BaseModel):
    """Record one feature-owned derived entry without its stored fact."""

    owner: str
    entry_type: str
    source_event_id: str
    schema_ref: ExtensionSchemaRefResponse
    document: str
