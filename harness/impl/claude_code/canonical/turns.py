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

from domain.ids import TurnId
from harness.models import RawEvent


class TurnSemantics:
    def __init__(self) -> None:
        self._open: dict[tuple[str, str], TurnId] = {}

    @staticmethod
    def _key(raw_event: RawEvent) -> tuple[str, str]:
        return (str(raw_event.session_id), str(raw_event.actor_id))

    def begin(self, raw_event: RawEvent, turn_id: TurnId) -> bool:
        """Open a turn, unless one already is. True when this prompt started it."""
        key = self._key(raw_event)
        if key in self._open:
            return False
        self._open[key] = turn_id
        return True

    def current(self, raw_event: RawEvent) -> TurnId | None:
        return self._open.get(self._key(raw_event))

    def close(self, raw_event: RawEvent) -> TurnId | None:
        return self._open.pop(self._key(raw_event), None)
