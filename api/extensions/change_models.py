# Copyright (c) 2026 Zhambyl Yermagambet
"""Typed extension change frames and their snapshot selection."""

from baqylau_extension_api.models.records import RecordState
from pydantic import BaseModel, Field


class ExtensionChangeQuery(BaseModel):
    """Select one owned change stream by its exact snapshot cursor."""

    scope: str
    history_revision: str = "default"
    projection_generation: str = "default"
    cursor: int = Field(default=0, ge=0)


class ExtensionChangeFrame(BaseModel):
    """Carry every record changed at one committed boundary."""

    records: tuple[RecordState, ...]
    cursor: int


class ExtensionChangeReset(BaseModel):
    """Tell a client to restart from the active snapshot."""

    history_revision: str
    projection_generation: str
    cursor: int


class ExtensionChangeError(BaseModel):
    """Report a change stream that failed after its headers were sent."""

    error: str
