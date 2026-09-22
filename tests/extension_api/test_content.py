# Copyright (c) 2026 Zhambyl Yermagambet
"""Check exact immutable byte delivery and resource bounds."""

import pytest
from baqylau_extension_api.models.content import (
    MAX_CONTENT_BYTES,
    ContentBlob,
    ContentBundle,
    decode_base64,
    encode_content,
)
from pydantic import ValidationError

SOURCE = b"hello"


@pytest.mark.parametrize("source", [b"", SOURCE, b"\x00\xff\xfe", "Жамбыл".encode()])
def test_content_preserves_exact_bytes(source: bytes) -> None:
    """Preserve binary and Unicode bytes with deterministic identity."""
    blob = encode_content(source)
    assert ContentBlob.model_validate_json(blob.model_dump_json()) == blob
    assert decode_base64(blob.base64_text) == source
    assert blob == encode_content(source)


@pytest.mark.parametrize("encoded", ["not base64", "Жамбыл", "aGVsbG8=\n", "aGVsbG9=", "===="])
def test_content_rejects_invalid_base64(encoded: str) -> None:
    """Reject non-ASCII, whitespace, extra padding, and nonzero trailing bits."""
    with pytest.raises(ValidationError, match="base64"):
        ContentBlob(reference=encode_content(SOURCE).reference, base64_text=encoded)


@pytest.mark.parametrize("field", ["digest", "byte_length"])
def test_content_checks_reference(field: str) -> None:
    """Reject a content reference that does not describe the supplied bytes."""
    blob = encode_content(SOURCE)
    wrong = encode_content(b"other bytes").reference
    invalid = blob.reference.model_copy(update={field: getattr(wrong, field)})
    with pytest.raises(ValidationError, match="content"):
        ContentBlob(reference=invalid, base64_text=blob.base64_text)


def test_bundle_rejects_ambiguous_identities() -> None:
    """Do not choose one of two content objects by response order."""
    blob = encode_content(SOURCE)
    with pytest.raises(ValidationError, match="unique"):
        ContentBundle(blobs=(blob, blob))


def test_individual_content_is_bounded() -> None:
    """Stop oversized content before a process request is accepted."""
    with pytest.raises(ValidationError):
        encode_content(b"x" * (MAX_CONTENT_BYTES + 1))


def test_batch_bytes_are_bounded() -> None:
    """Bound aggregate bytes, not only the number of content objects."""
    blobs = tuple(
        encode_content(bytes([index]) * MAX_CONTENT_BYTES)
        for index in range(9)
    )
    with pytest.raises(ValidationError, match="byte limit"):
        ContentBundle(blobs=blobs)


def test_media_type_is_part_of_content_identity() -> None:
    """Keep two valid media descriptions unambiguous in one snapshot."""
    text = encode_content(SOURCE, "text/plain")
    binary = encode_content(SOURCE)
    assert text.reference.digest == binary.reference.digest
    assert text.reference.content_id != binary.reference.content_id
    assert ContentBundle(blobs=(text, binary)).resolve(text.reference) == text
