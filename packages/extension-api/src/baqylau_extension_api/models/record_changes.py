# Copyright (c) 2026 Zhambyl Yermagambet
"""Propose record writes without assigning host-owned commit revisions."""

from typing import Annotated, Literal

from pydantic import Field

from baqylau_extension_api.models.base import Revision, WireModel
from baqylau_extension_api.models.documents import EncodedDocument
from baqylau_extension_api.models.records import RecordKey
from baqylau_extension_api.terminal.text import DisplayText


class PutRecord(WireModel):
    """Insert, replace, or restore one captured key at its expected revision."""

    operation: Literal["put"] = "put"
    key: RecordKey
    expected_revision: Revision
    document: EncodedDocument
    summary: DisplayText


class DeleteRecord(WireModel):
    """Delete an existing row without losing its last committed revision."""

    operation: Literal["delete"] = "delete"
    key: RecordKey
    expected_revision: Annotated[int, Field(ge=1)]


type RecordChange = Annotated[PutRecord | DeleteRecord, Field(discriminator="operation")]
