# Copyright (c) 2026 Zhambyl Yermagambet
"""Commit a concurrent fact while the snapshot reader's transaction is open."""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from extensions.models.interpretations import StoredCanonicalFact
from repository.impl.sqlite.interpretations import SqliteInterpretationRepository
from tests.extension_host import interpretation_snapshot_fixture as fixture


@dataclass
class SnapshotWriter:
    """Use a second real database connection without a wait or a timing assumption."""

    store: SqliteInterpretationRepository
    decode: Callable[[sqlite3.Row], StoredCanonicalFact]
    written: bool = False

    def __call__(self, row: sqlite3.Row) -> StoredCanonicalFact:
        """Write once before the open reader finishes decoding its first body.

        Returns:
            The unchanged stored body through the production codec.

        """
        if not self.written:
            fixture.seed(self.store, (fixture.fact("concurrent"),))
            self.written = True
        return self.decode(row)
