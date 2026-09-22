# Copyright (c) 2026 Zhambyl Yermagambet
"""Check rejected source writes against a complete logical database snapshot."""

import pytest

from extensions.models.source_reads import SourceReadCommit
from tests import sqlite_migration_fixture as snapshots
from tests.extension_host.source_read_fixture import SourceCase


def require_rejected(case: SourceCase, request: SourceReadCommit, match: str) -> None:
    """Require the selected error and no change to any table, index, or cursor."""
    before = snapshots.snapshot(case.store.database)
    with pytest.raises(ValueError, match=match):
        case.store.record_source_read(request)
    assert snapshots.snapshot(case.store.database) == before
