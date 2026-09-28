# Copyright (c) 2026 Zhambyl Yermagambet
"""Row shapes for session facts and their published session data."""

from __future__ import annotations

from dataclasses import dataclass

from domain import ids as domain_ids


@dataclass(frozen=True)
class SessionRow:
    """Represent session row."""

    session_id: domain_ids.SessionId
    lead_actor_id: domain_ids.ActorId
    harness: domain_ids.HarnessName
    source_reference: str
    working_directory: str | None
    project_directory: str | None
    terminal_window_id: domain_ids.WindowId | None
    harness_process_id: int | None
    created_at: float


@dataclass(frozen=True)
class SessionDataRow:
    """Represent session data row."""

    session_id: domain_ids.SessionId
    revision: int
    payload: str


@dataclass(frozen=True)
class SessionDataActorRow:
    """Represent session data actor row."""

    session_id: domain_ids.SessionId
    actor_id: domain_ids.ActorId
    revision: int
    payload: str


@dataclass(frozen=True)
class SessionEntryRow:
    """Represent session entry row."""

    cursor: int
    entry_id: domain_ids.CanonicalEventId
    session_id: domain_ids.SessionId
    entry_type: str
    actor_id: domain_ids.ActorId
    parent_actor_id: domain_ids.ActorId | None
    turn_id: domain_ids.TurnId | None
    occurred_at: float | None
    summary: str | None
    payload: str
    commit_cursor: int = 0
