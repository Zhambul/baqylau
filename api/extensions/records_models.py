# Copyright (c) 2026 Zhambyl Yermagambet
"""Typed extension record pages."""

from baqylau_extension_api.models.records import RecordState
from pydantic import BaseModel


class ExtensionRecordPageQuery(BaseModel):
    """Select one record page by its exact scope and continuation key."""

    scope: str
    after: str = ""
    limit: int = 50


class ExtensionRecordPageResponse(BaseModel):
    """Describe one ordered record page and its exact continuation key."""

    records: tuple[RecordState, ...]
    next_key: str | None
