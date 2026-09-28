# Copyright (c) 2026 Zhambyl Yermagambet
"""Read the working directory of each stored session."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from domain.ids import ActorId, HarnessName, SessionId


@dataclass(frozen=True)
class SessionRow:
    """One session's identity and working directory."""

    session_id: SessionId
    lead_actor_id: ActorId
    harness: HarnessName
    working_directory: str


class SessionRows(Protocol):
    """Read the sessions that have a working directory, newest first."""

    def session_rows(self) -> tuple[SessionRow, ...]:
        """Read every session with a working directory, newest first."""
        ...
