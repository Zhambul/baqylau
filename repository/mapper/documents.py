"""Any dataclass or pydantic model of ours, as the bytes it is stored or
carried as, and back.

Not only the canonical fact: the engine's own raw events — an output chunk, a
process exit, an interrupt mark — are documents too. They used to be a dict
literal at the writer and a field-by-field read at the translator, twice per
document, with nothing holding the two halves together.
"""

from __future__ import annotations

from collections.abc import Hashable
from functools import cache
from typing import Any, TypeVar, cast

from pydantic import TypeAdapter, ValidationError

DocumentType = TypeVar("DocumentType")


class StoredDocumentError(ValueError):
    """A stored document does not match the shape it claims, in either
    direction: an encode that fails validation, or a decode of bytes that were
    never a valid instance of the shape asked for."""


@cache
def _adapter(shape: Hashable) -> TypeAdapter[Any]:
    """Build one adapter for each stored document type."""
    return TypeAdapter(cast(Any, shape))


def encode_document(value: DocumentType) -> bytes:
    """`value`'s own runtime type IS the shape to validate and dump against —
    the whole reason this takes a type parameter instead of `object`: the
    adapter it builds is for exactly the caller's type, not a generic one."""
    adapter = _adapter(cast(Hashable, type(value)))
    return adapter.dump_json(value)


def decode_document(shape: type[DocumentType], encoded: bytes | str) -> DocumentType:
    """The inverse, against the shape the caller expects."""
    adapter = _adapter(cast(Hashable, shape))
    try:
        return cast(DocumentType, adapter.validate_json(encoded))
    except ValidationError as error:
        raise StoredDocumentError(f"not a {shape.__name__}: {error}") from error
