# Copyright (c) 2026 Zhambyl Yermagambet
"""Count the switches that rewrite rows a session stream client already has."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import sqlite3


def revision(connection_handle: sqlite3.Connection) -> int:
    """Read the view revision in the caller's transaction.

    Returns:
        The number of switches so far.

    """
    row = connection_handle.execute("SELECT revision FROM read_model_views WHERE id=1").fetchone()
    return 0 if row is None else int(row["revision"])


def bump(connection_handle: sqlite3.Connection) -> None:
    """Count one switch in the caller's transaction."""
    connection_handle.execute(
        "INSERT INTO read_model_views(id, revision) VALUES(1, 1) ON CONFLICT(id) DO UPDATE SET revision=revision+1",
    )
