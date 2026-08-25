"""Which turn is open, per actor.

Claude Code announces no turn boundary of its own: its Stop hook says a turn
ENDED and nothing at all says one began, so every fact it reports used to carry
no turn. The user's PROMPT is the boundary — a turn is the answer to something
somebody asked — so a prompt opens one and everything until the Stop rides it.

Kept per actor because a subagent's turns are its own, and per session because
one translator serves every session in the process.

Two deliberate limits. A prompt that arrives while a turn is open does not open
a second one: an injection is part of the turn it interrupted. And a turn that
ends without a Stop — an interrupt whose acknowledgement no harness raw event
ever confirmed, whose `turn.aborted` is produced by the engine rather than by
this translator — stays open here; the facts after it ride a turn that is over.
"""

from __future__ import annotations

from dataclasses import dataclass

from domain.ids import ActorId, SessionId, TurnId
from harness.models import RawEvent


@dataclass(frozen=True)
class OpenTurn:
    session_id: SessionId
    actor_id: ActorId
    turn_id: TurnId


class TurnSemantics:
    def __init__(self) -> None:
        self._open: list[OpenTurn] = []

    @staticmethod
    def _key(raw_event: RawEvent) -> tuple[SessionId, ActorId]:
        return raw_event.session_id, raw_event.actor_id

    def begin(self, raw_event: RawEvent, turn_id: TurnId) -> bool:
        """Open a turn, unless one already is. True when this prompt started it."""
        session_id, actor_id = self._key(raw_event)
        if any(
            opened.session_id == session_id and opened.actor_id == actor_id
            for opened in self._open
        ):
            return False
        self._open.append(OpenTurn(session_id, actor_id, turn_id))
        return True

    def current(self, raw_event: RawEvent) -> TurnId | None:
        session_id, actor_id = self._key(raw_event)
        return next(
            (
                opened.turn_id
                for opened in self._open
                if opened.session_id == session_id and opened.actor_id == actor_id
            ),
            None,
        )

    def close(self, raw_event: RawEvent) -> TurnId | None:
        session_id, actor_id = self._key(raw_event)
        index = next(
            (
                index
                for index, opened in enumerate(self._open)
                if opened.session_id == session_id and opened.actor_id == actor_id
            ),
            None,
        )
        return self._open.pop(index).turn_id if index is not None else None

    def release_session(self, session_id: SessionId) -> None:
        """Release any turn that outlived a finished native session."""
        self._open = [
            opened for opened in self._open if opened.session_id != session_id
        ]
