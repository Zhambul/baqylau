"""Which turn is open, per actor.

Claude Code announces no turn boundary of its own: its Stop hook says a turn
ENDED and nothing at all says one began, so every fact it reports used to carry
no turn. The user's PROMPT is the boundary — a turn is the answer to something
somebody asked — so a prompt opens one and everything until the Stop rides it.

Kept per actor because a subagent's turns are its own, and per session because
one translator serves every session in the process.

Two deliberate limits. An ordinary prompt that arrives while a turn is open
does not open a second one: an injection is part of that turn. A queued prompt
with `promptSource=queued` is different. Claude can write it before the native
interrupt marker, although it starts the work after the interrupt. That prompt
replaces the open turn, and the later marker still belongs to the replaced turn.
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
        self._queued_interrupts: list[OpenTurn] = []

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

    def replace_for_queued_prompt(self, raw_event: RawEvent) -> TurnId | None:
        """Close the old turn before Claude's queued prompt starts a new one."""
        turn_id = self.close(raw_event)
        if turn_id is not None:
            self._queued_interrupts.append(
                OpenTurn(raw_event.session_id, raw_event.actor_id, turn_id)
            )
        return turn_id

    def interrupted(self, raw_event: RawEvent) -> tuple[TurnId | None, bool]:
        """Return the interrupted turn and whether its abort fact exists."""
        session_id, actor_id = self._key(raw_event)
        index = next(
            (
                index
                for index, opened in enumerate(self._queued_interrupts)
                if opened.session_id == session_id and opened.actor_id == actor_id
            ),
            None,
        )
        if index is not None:
            return self._queued_interrupts.pop(index).turn_id, True
        return self.close(raw_event), False

    def release_session(self, session_id: SessionId) -> None:
        """Release any turn that outlived a finished native session."""
        self._open = [
            opened for opened in self._open if opened.session_id != session_id
        ]
        self._queued_interrupts = [
            opened
            for opened in self._queued_interrupts
            if opened.session_id != session_id
        ]
