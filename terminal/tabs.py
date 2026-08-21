"""The session tab's colour, painted from the actor's status.

The one thing the terminal does in response to the read model rather than to a
gesture: a tab is a status light, and the status is what the writers just
committed. So this is an `AppliedActorListener` — it is told the actor rows one
event produced, after they were written — and NOT a canonical event reaction. A
reaction runs before the writers, where the status is still the previous one.

It paints only what changed. A repaint per event would be a terminal round trip
four times a second per session, for a colour that changes a handful of times a
turn.
"""

from __future__ import annotations

from collections.abc import Sequence

from domain.ids import ActorId, SessionId
from domain.sessiondata import ActorFacts, ActorStatus
from engine.sessiondata.contract import AppliedActorListener
from repository.contract.sessions import SessionRepository
from terminal.adapter import TerminalAdapter
from terminal.theme import tab_appearance


class TabColorPainter(AppliedActorListener):
    """One tab per session, coloured by its LEAD actor.

    A tab shows a session, and a session shows its lead: a subagent turning red
    because it asked ITSELF something is not the session asking you anything.
    """

    def __init__(self, terminal_adapter: TerminalAdapter, session_repository: SessionRepository) -> None:
        self._terminal = terminal_adapter
        self._sessions = session_repository
        self._painted: dict[SessionId, ActorStatus | None] = {}

    def applied(self, session_id: SessionId, actors: Sequence[ActorFacts]) -> None:
        lead = self._lead(session_id, actors)
        if lead is None:
            return
        if session_id in self._painted and self._painted[session_id] == lead.status:
            return
        self._painted[session_id] = lead.status
        if lead.status is None:
            # A finished session shows no state, so its tab shows no colour.
            self._terminal.clear_session_tab(session_id)
            return
        self._terminal.paint_session_tab(session_id, tab_appearance(lead.status))

    def _lead(
        self, session_id: SessionId, actors: Sequence[ActorFacts]
    ) -> ActorFacts | None:
        """The lead actor among the rows this event wrote, if it wrote one.

        The lead's identity comes from the sessions row rather than the aggregate
        because that row is what control routing already keys on, and it is
        written by the interpreter before any of this runs.
        """
        session = self._sessions.find(session_id)
        lead_actor_id: ActorId | None = session.lead_actor_id if session is not None else None
        if lead_actor_id is None:
            return None
        return next((actor for actor in actors if actor.actor_id == lead_actor_id), None)
