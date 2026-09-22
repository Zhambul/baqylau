# Copyright (c) 2026 Zhambyl Yermagambet
"""Bound prior-state capture without hiding whether facts were omitted."""

from dataclasses import dataclass, field
from typing import Annotated

from baqylau_extension_api.models import base, canonical, scopes
from pydantic import Field

MAX_PRIOR_FACTS = 1000
MAX_PRIOR_BYTES = 1_048_576
MIN_PRIOR_BYTES = 128


class PriorStateRequest(base.WireModel):
    """Bind one bounded read to the exact history, scope, and accepted head."""

    history_revision: base.Identifier
    scope: scopes.ExtensionScope
    expected_canonical_cursor: base.Revision
    max_facts: Annotated[int, Field(ge=1, le=MAX_PRIOR_FACTS)] = MAX_PRIOR_FACTS
    max_bytes: Annotated[int, Field(ge=MIN_PRIOR_BYTES, le=MAX_PRIOR_BYTES)] = MAX_PRIOR_BYTES


@dataclass
class SnapshotCapture:
    """Retain only a bounded ordered prefix, with space for the complete wire representation."""

    request: PriorStateRequest
    facts: list[canonical.CommittedFact] = field(default_factory=list, init=False)
    byte_length: int = field(init=False)

    def __post_init__(self) -> None:
        """Reserve the larger incomplete header, including the surrounding array."""
        encoded = self.finish(complete=False).model_dump_json().encode("utf-8")
        self.byte_length = len(encoded)

    def append(self, fact: canonical.CommittedFact) -> bool:
        """Check the complete UTF-8 fact representation before retaining it.

        Returns:
            True when the next fact fits both limits.

        """
        encoded = fact.model_dump_json().encode("utf-8")
        size = len(encoded) + bool(self.facts)
        exceeds_bytes = self.byte_length + size > self.request.max_bytes
        if len(self.facts) >= self.request.max_facts or exceeds_bytes:
            return False
        self.byte_length += size
        self.facts.append(fact)
        return True

    def finish(self, *, complete: bool) -> canonical.CoreStateSnapshot:
        """Freeze the accepted prefix and its explicit coverage claim.

        Returns:
            A snapshot whose full encoded representation fits the requested bound.

        """
        return canonical.CoreStateSnapshot(
            after_cursor=self.request.expected_canonical_cursor, facts=tuple(self.facts), complete=complete,
        )
