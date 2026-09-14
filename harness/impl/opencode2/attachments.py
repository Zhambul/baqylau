# Copyright (c) 2026 Zhambyl Yermagambet
"""Encode staged files for the native prompt hook."""

import base64
from pathlib import Path

from pydantic import BaseModel, Field

from harness.models.controls import AttachmentReference


class NativeAttachment(BaseModel):
    """Use the native file attachment input."""

    uri: str
    name: str
    media_type: str | None = Field(default=None, serialization_alias="mediaType")


class AttachmentPrompt(BaseModel):
    """Carry one prompt and its selected local files to the native plugin."""

    text: str
    files: tuple[NativeAttachment, ...]


def prompt(
    text: str | None,
    attachments: tuple[AttachmentReference, ...],
) -> str:
    """Encode a native attachment operation for the owned terminal.

    Returns:
        Plain text without attachments, or an encoded attachment input.

    """
    if not attachments:
        return text or ""
    message = AttachmentPrompt(text=text or "", files=tuple(
        NativeAttachment(
            uri=Path(attachment.local_path).as_uri(), name=attachment.display_name, media_type=attachment.media_type,
        )
        for attachment in attachments
    ))
    serialized = message.model_dump_json(by_alias=True, exclude_none=True)
    encoded = base64.b64encode(serialized.encode()).decode()
    return f"/baqylau-attach {encoded}"
