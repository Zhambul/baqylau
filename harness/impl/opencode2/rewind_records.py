# Copyright (c) 2026 Zhambyl Yermagambet
"""Read and write the native rewind boundary."""

from pydantic import BaseModel, Field

from domain.ids import MessageId


class RevertRequest(BaseModel):
    """Encode the native message boundary and file choice."""

    message_id: MessageId = Field(serialization_alias="messageID")
    files: bool


class RevertBoundary(BaseModel):
    """Read the boundary accepted by the native server."""

    message_id: MessageId = Field(alias="messageID")


class RevertResponse(BaseModel):
    """Read the native revert response."""

    boundary: RevertBoundary = Field(alias="data")
