# Copyright (c) 2026 Zhambyl Yermagambet
"""Carry bounded immutable bytes across the process boundary."""

import base64
import binascii
import hashlib
from typing import Annotated, Self

from pydantic import Field, model_validator

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.base import WireModel
from baqylau_extension_api.models.documents import ContentReference

MAX_CONTENT_BYTES = 1_048_576
MAX_BATCH_BYTES = 8 * MAX_CONTENT_BYTES


class ContentBlob(WireModel):
    """Supply exact bytes with a checked reference, not a host file path."""

    reference: ContentReference
    base64_text: Annotated[str, Field(max_length=4 * ((MAX_CONTENT_BYTES + 2) // 3))]

    @model_validator(mode="after")
    def validate_bytes(self) -> Self:
        """Check encoding, byte count, and digest.

        Returns:
            The verified content snapshot.

        Raises:
            ValueError: If the content does not match its reference.

        """
        decoded = decode_base64(self.base64_text)
        if len(decoded) > MAX_CONTENT_BYTES or len(decoded) != self.reference.byte_length:
            message = "content byte length is invalid"
            raise ValueError(message)
        if hashlib.sha256(decoded).hexdigest() != self.reference.digest:
            message = "content digest does not match its reference"
            raise ValueError(message)
        return self


class ContentBundle(WireModel):
    """Provide a bounded set of immutable content objects for one call."""

    blobs: Annotated[tuple[ContentBlob, ...], Field(max_length=1000)] = ()

    @model_validator(mode="after")
    def validate_bundle(self) -> Self:
        """Reject ambiguous references and oversized batch content.

        Returns:
            The verified content set.

        Raises:
            ValueError: If identities repeat or total bytes exceed the limit.

        """
        identities = {blob.reference.content_id for blob in self.blobs}
        if len(identities) != len(self.blobs):
            message = "content identities must be unique in a bundle"
            raise ValueError(message)
        if sum(blob.reference.byte_length for blob in self.blobs) > MAX_BATCH_BYTES:
            message = "content bundle exceeds the byte limit"
            raise ValueError(message)
        return self

    def resolve(self, reference: ContentReference) -> ContentBlob:
        """Read exact supplied content without a live host callback.

        Returns:
            The matching immutable content object.

        Raises:
            ExtensionContractError: If the exact reference is not supplied.

        """
        for blob in self.blobs:
            if blob.reference == reference:
                return blob
        message = "content reference is not present in the supplied snapshot"
        raise ExtensionContractError(message)


def decode_base64(encoded: str) -> bytes:
    """Decode only canonical ASCII base64, including empty content.

    Returns:
        The exact source bytes.

    Raises:
        ValueError: If the text is not canonical base64.

    """
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        message = "content must use valid base64"
        raise ValueError(message) from exc
    if base64.b64encode(decoded).decode("ascii") != encoded:
        message = "content must use canonical base64"
        raise ValueError(message)
    return decoded


def encode_content(source: bytes, media_type: str = "application/octet-stream") -> ContentBlob:
    """Build a content snapshot from exact source bytes.

    Returns:
        A validated, content-addressed object.

    """
    digest = hashlib.sha256(source).hexdigest()
    framed = f"{media_type}\x00{digest}"
    identity = hashlib.sha256(framed.encode("utf-8")).hexdigest()
    return ContentBlob(
        reference=ContentReference(
            content_id=f"content:{identity}", media_type=media_type, byte_length=len(source), digest=digest,
        ),
        base64_text=base64.b64encode(source).decode("ascii"),
    )
