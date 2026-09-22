# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep source progress and original observations inside one repository operation."""

from typing import Protocol

from baqylau_extension_api.models.base import Identifier

from extensions.models.source_reads import SourceCheckpoint, SourceKey, SourceReadCommit, SourceReadOutcome


class ExtensionSourceRepository(Protocol):
    """Read captured progress and atomically accept a complete source result."""

    def source_checkpoint(self, key: SourceKey) -> SourceCheckpoint:
        """Read exact committed progress, or explicit revision zero for a new source."""
        ...

    def record_source_read(self, request: SourceReadCommit) -> SourceReadOutcome:
        """Commit original rows, pending input, the read record, and source progress together."""
        ...

    def find_source_read(self, runtime_revision: Identifier, call_id: Identifier) -> SourceReadCommit | None:
        """Read a complete accepted source call without requiring its worker to run."""
        ...
