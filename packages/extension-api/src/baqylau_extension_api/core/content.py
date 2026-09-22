# Copyright (c) 2026 Zhambyl Yermagambet
"""Preserve text and external structured content in public core facts."""

from typing import Literal

from pydantic import field_validator

from baqylau_extension_api import schema_documents
from baqylau_extension_api.core.base import CoreModel
from baqylau_extension_api.models.documents import DocumentText


class TextContent(CoreModel):
    """Keep the text and its declared display type."""

    text: str
    media_type: Literal["text/plain", "text/markdown"] = "text/plain"


class StructuredContent(CoreModel):
    """Keep an external tool document as encoded text."""

    json_text: DocumentText

    @field_validator("json_text")
    @classmethod
    def validate_json_text(cls, encoded: str) -> str:
        """Check syntax without changing external document bytes.

        Returns:
            The same finite JSON text.

        """
        schema_documents.decode_json(encoded)
        return encoded


type Content = TextContent | StructuredContent
