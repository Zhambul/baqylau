# Copyright (c) 2026 Zhambyl Yermagambet
"""Name the read model that one session's facts fold into."""

from dataclasses import dataclass

from domain.actor_state import ActorFacts
from domain.entries import SessionEntry
from domain.session_state import SessionFacts


@dataclass(frozen=True)
class FoldedEntry:
    """Keep one folded feed row with its commit boundary and position."""

    commit_cursor: int
    position: int
    entry: SessionEntry


@dataclass(frozen=True)
class FoldedSession:
    """Keep the read model that a history's facts fold into for one session."""

    session: SessionFacts | None
    actors: tuple[ActorFacts, ...]
    entries: tuple[FoldedEntry, ...]
    cursor: int
