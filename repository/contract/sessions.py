"""The `sessions` table: one writer, and the reads every surface makes."""

from __future__ import annotations

from typing import Protocol

from domain.ids import HarnessName, SessionId
from harness.models import Session


class SessionRepository(Protocol):
    """Sessions are read-models of committed facts.

    The one writer is the interpreter's session-upsert reaction, which derives
    birth from the session's own `session.started` fact and keeps the two live
    columns current from later raw events.
    """

    def save(self, harness: HarnessName, session: Session) -> None:
        """Upsert: identity columns written once, live columns overwritten."""
        ...

    def find(self, session_id: SessionId) -> Session | None: ...

    def watchable(self) -> tuple[Session, ...]:
        """Every session without a committed finish, newest first.

        No count limit by design: liveness is a raw event question, never a
        quota. The session lifecycle is maintained by database triggers in the
        same transaction as canonical start and finish facts.
        """
        ...
