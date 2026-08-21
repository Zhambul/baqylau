"""Any dataclass or pydantic model of ours, as the bytes it is stored or
carried as, and back.

Not only the canonical fact: the engine's own raw events — an output chunk, a
process exit, an interrupt mark — are documents too. They used to be a dict
literal at the writer and a field-by-field read at the translator, twice per
document, with nothing holding the two halves together.
"""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import TypeAdapter, ValidationError

DocumentType = TypeVar("DocumentType")


class StoredDocumentError(ValueError):
    """A stored document does not match the shape it claims, in either
    direction: an encode that fails validation, or a decode of bytes that were
    never a valid instance of the shape asked for."""


def encode_document(value: object) -> bytes:  # loose: stored value of no fixed kind, wave 2 gives it a real shape
    adapter: TypeAdapter[Any] = TypeAdapter(type(value))
    return adapter.dump_json(value)


def decode_document(shape: type[DocumentType], encoded: bytes | str) -> DocumentType:
    """The inverse, against the shape the caller expects."""
    adapter: TypeAdapter[DocumentType] = TypeAdapter(shape)
    try:
        return adapter.validate_json(encoded)
    except ValidationError as error:
        raise StoredDocumentError(f"not a {shape.__name__}: {error}") from error
