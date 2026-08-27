"""A command's output file, while we are still following it.

One row of the follow list, as a value rather than as a database row. The
reader that turns the file into a raw event takes one of these; it used to take a
live `sqlite3.Row`, which is why it could not be built in a test without a
database.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum

from domain.ids import ActorId, HarnessName, SessionId, ShellId
from domain.values import ShellFollowUntil


class ShellFollowState(StrEnum):
    ACTIVE = "active"
    FINISHING = "finishing"


def shell_output_source_key(source_path: str) -> str:
    """Return the stable identity of one file followed for a shell."""
    return hashlib.sha256(source_path.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ShellOutputFollowing:
    session_id: SessionId
    shell_id: ShellId
    harness: HarnessName
    actor_id: ActorId
    parent_actor_id: ActorId | None
    source_path: str
    chunk_source_type: str
    delete_source: bool
    initial_size: int
    initial_modified_at: int
    wait_for_source_change: bool
    until: ShellFollowUntil
    state: ShellFollowState
    created_at: float

    @property
    def finishing(self) -> bool:
        return self.state == ShellFollowState.FINISHING
