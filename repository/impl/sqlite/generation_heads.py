# Copyright (c) 2026 Zhambyl Yermagambet
"""Read which projection generation of an owner is live."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import sqlite3

DEFAULT_GENERATION = "default"
_HEAD_SQL = "SELECT generation FROM extension_projection_heads WHERE owner=?"


def active_generation(connection_handle: sqlite3.Connection, owner: str) -> str:
    """Read one owner's active projection generation.

    Returns:
        The head generation, or the default before the first switch.

    """
    row = connection_handle.execute(_HEAD_SQL, (owner,)).fetchone()
    return DEFAULT_GENERATION if row is None else str(row["generation"])


def is_candidate(connection_handle: sqlite3.Connection, owner: str, generation: str) -> bool:
    """Tell whether a generation is not the owner's live one.

    Returns:
        True when writes of this generation go to the mirror tables.

    """
    return generation != active_generation(connection_handle, owner)
