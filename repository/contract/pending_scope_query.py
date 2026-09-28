# Copyright (c) 2026 Zhambyl Yermagambet
"""Name one fact consumer for a pending-scope read."""

from dataclasses import dataclass


@dataclass(frozen=True)
class PendingScopeQuery:
    """Name one consumer and the scope kinds it declares."""

    owner: str
    scope_kinds: frozenset[str]
    history_revision: str
    generation: str
    limit: int
