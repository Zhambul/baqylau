# Copyright (c) 2026 Zhambyl Yermagambet
"""Store each distinct journal body once and name it by its content digest."""

import hashlib
from dataclasses import dataclass
from typing import Literal

from baqylau_extension_api.models import canonical, content, documents, events
from baqylau_extension_api.models.base import Digest, Revision, WireModel
from pydantic import TypeAdapter

BodyKind = Literal[
    "canonical_fact",
    "raw_input",
    "content_bundle",
    "prior_state",
    "encoded_document",
]

CANONICAL_FACT_KIND: BodyKind = "canonical_fact"
RAW_INPUT_KIND: BodyKind = "raw_input"
CONTENT_BUNDLE_KIND: BodyKind = "content_bundle"
PRIOR_STATE_KIND: BodyKind = "prior_state"
ENCODED_DOCUMENT_KIND: BodyKind = "encoded_document"


class BodyRef(WireModel):
    """Name one immutable body by its kind, digest, and exact encoded length."""

    kind: BodyKind
    digest: Digest
    byte_length: Revision


@dataclass(frozen=True)
class StoredBody:
    """Keep one immutable body reference with its exact encoded bytes."""

    ref: BodyRef
    encoded: bytes


type BodyList = list[StoredBody] | tuple[StoredBody, ...]


class BodyStore:
    """Intern typed values by content digest and count each distinct body once.

    The store holds one version for each distinct checked value. A journal
    admits the bytes of every body it names, even when another journal
    already stores the same digest. Physical database deduplication and
    per-interpretation admission are separate concerns.
    """

    def __init__(self) -> None:
        """Start an empty store for one journal."""
        self._bodies: list[StoredBody] = []

    def intern(self, body: WireModel, kind: BodyKind) -> BodyRef:
        """Store one typed value once and return its reference.

        Returns:
            The reference to the exact encoded value.

        """
        encoded = body.model_dump_json().encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        existing = _find(self._bodies, digest)
        if existing is not None:
            _require_same(existing, kind, encoded)
            return existing.ref
        ref = BodyRef(kind=kind, digest=digest, byte_length=len(encoded))
        self._bodies.append(StoredBody(ref=ref, encoded=encoded))
        return ref

    def adopt(self, ref: BodyRef, encoded: bytes) -> None:
        """Load one stored body and check its digest, kind, and length.

        Raises:
            ValueError: If the stored bytes do not match the reference.

        """
        digest = hashlib.sha256(encoded).hexdigest()
        if digest != ref.digest or len(encoded) != ref.byte_length:
            message = "stored journal body does not match its reference"
            raise ValueError(message)
        existing = _find(self._bodies, digest)
        if existing is not None:
            _require_same(existing, ref.kind, encoded)
            return
        self._bodies.append(StoredBody(ref=ref, encoded=encoded))

    def checkpoint(self) -> int:
        """Return a position that restores this store after a rejected value.

        Returns:
            The current number of stored bodies.

        """
        return len(self._bodies)

    def rollback(self, checkpoint: int) -> None:
        """Remove every body that arrived after one checkpoint."""
        self._bodies = self._bodies[:checkpoint]

    def bodies(self) -> tuple[StoredBody, ...]:
        """Read every distinct body in digest order.

        Returns:
            References with their exact encoded bytes.

        """
        return tuple(sorted(self._bodies, key=lambda body: body.ref.digest))

    def byte_length(self) -> int:
        """Count the encoded bytes of every distinct body once.

        Returns:
            The complete body byte count for this journal.

        """
        return sum(len(body.encoded) for body in self._bodies)


class BodyResolver:
    """Decode stored bodies through their typed references."""

    def __init__(self, store: BodyStore) -> None:
        """Read one store without changing its bytes."""
        self._store = store

    def resolve_fact(self, ref: BodyRef) -> canonical.CanonicalFact:
        """Decode one referenced canonical fact.

        Returns:
            The exact stored fact.

        """
        return _decode(self._store, ref, "canonical_fact", TypeAdapter(canonical.CanonicalFact))

    def resolve_raw_input(self, ref: BodyRef) -> events.RawInput:
        """Decode one referenced raw input.

        Returns:
            The exact stored raw input.

        """
        return _decode(self._store, ref, "raw_input", TypeAdapter(events.RawInput))

    def resolve_content(self, ref: BodyRef) -> content.ContentBundle:
        """Decode one referenced content snapshot.

        Returns:
            The exact stored content bundle.

        """
        return _decode(self._store, ref, "content_bundle", TypeAdapter(content.ContentBundle))

    def resolve_prior(self, ref: BodyRef) -> canonical.CoreStateSnapshot:
        """Decode one referenced prior-state snapshot.

        Returns:
            The exact stored prior state.

        """
        return _decode(self._store, ref, "prior_state", TypeAdapter(canonical.CoreStateSnapshot))

    def resolve_document(self, ref: BodyRef) -> documents.EncodedDocument:
        """Decode one referenced encoded document.

        Returns:
            The exact stored document.

        """
        return _decode(self._store, ref, "encoded_document", TypeAdapter(documents.EncodedDocument))


def _decode[Decoded](store: BodyStore, ref: BodyRef, kind: BodyKind, adapter: TypeAdapter[Decoded]) -> Decoded:
    stored = _find(store.bodies(), ref.digest)
    if stored is None or ref.kind != kind:
        message = "journal body reference has no matching stored body"
        raise ValueError(message)
    if stored.ref.kind != kind:
        message = "journal body reference has no matching stored body"
        raise ValueError(message)
    if stored.ref.byte_length != ref.byte_length:
        message = "journal body reference has no matching stored body"
        raise ValueError(message)
    return adapter.validate_json(stored.encoded)


def _find(bodies: BodyList, digest: str) -> StoredBody | None:
    for body in bodies:
        if body.ref.digest == digest:
            return body
    return None


def _require_same(stored: StoredBody, kind: BodyKind, encoded: bytes) -> None:
    if stored.ref.kind != kind or stored.encoded != encoded:
        message = "one body digest names two different values"
        raise ValueError(message)
