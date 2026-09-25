# Copyright (c) 2026 Zhambyl Yermagambet
"""Read captured extension record states and paged record rows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from baqylau_extension_api.models import records

if TYPE_CHECKING:
    from collections.abc import Sequence

    from baqylau_extension_api.models import scopes


@dataclass(frozen=True)
class ExtensionRecordPage:
    """Keep one ordered record page and its exact continuation key."""

    records: tuple[records.RecordState, ...]
    next_key: str | None


@dataclass(frozen=True)
class ExtensionRecordChanges:
    """Keep every record changed at one committed boundary."""

    changes: tuple[records.RecordState, ...]
    next_cursor: int


class RecordStateReader(Protocol):
    """Capture feature-owned record keys of one generation, including explicit absent rows."""

    def record_states(self, keys: Sequence[records.RecordKey]) -> tuple[records.RecordState, ...]:
        """Return the captured states in the supplied key order."""
        ...


class ExtensionRecordRepository(RecordStateReader, Protocol):
    """Read the live generation's feature-owned records."""

    def record_page(
        self, owner: str, collection: str, scope: scopes.ExtensionScope, after_key: str, limit: int,
    ) -> ExtensionRecordPage:
        """Read one ordered record page in its exact scope."""
        ...

    def record_changes(
        self,
        owner: str,
        scope: scopes.ExtensionScope,
        projection_generation: str,
        after_cursor: int,
    ) -> ExtensionRecordChanges:
        """Read every record change at the next committed boundary after a cursor."""
        ...
