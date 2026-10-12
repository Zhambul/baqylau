# Copyright (c) 2026 Zhambyl Yermagambet
"""Open and close session panes on their own thread, in the order of the facts.

Opening the panes of a new session asks the terminal about ten times. With a
real terminal it took about one second (measured 1.06 s, seven window reads).
The engine has one ordered worker, so while that reaction waited, the engine
read no harness record and translated no raw event. The first tool hook of a
new session then came before its prompt, and its command had no turn. Now the
reaction loop only puts the fact on a queue, and one thread runs the pane
reaction for each fact in the order the facts came.
"""

from __future__ import annotations

import queue
import threading
from typing import TYPE_CHECKING

from audit.failures import FailureContext
from harness.contract import CanonicalEventReaction
from terminal.panes.reaction import PANE_FACTS

if TYPE_CHECKING:
    from audit.failures import ErrorRecorder
    from domain.event_base import CanonicalEvent, EventPayload


class PaneReactionThread(CanonicalEventReaction):
    """Run one pane reaction on one thread, so that the engine never waits for the terminal."""

    def __init__(self, pane_canonical_event_reaction: CanonicalEventReaction, error_recorder: ErrorRecorder) -> None:
        """Start the pane thread."""
        self._reaction = pane_canonical_event_reaction
        self._audit = error_recorder
        # None asks the thread to stop after the facts before it.
        self._facts: queue.Queue[CanonicalEvent[EventPayload] | None] = queue.Queue()
        self._thread = threading.Thread(target=self._react_until_closed, name="baqylau-panes", daemon=True)
        self._thread.start()

    def react(self, canonical_event: CanonicalEvent[EventPayload]) -> None:
        """Queue a fact that opens or closes panes, and return before the terminal acts."""
        if isinstance(canonical_event.payload, PANE_FACTS):
            self._facts.put(canonical_event)

    def flush(self) -> None:
        """Wait until the pane reaction ran for each fact that was queued before this call."""
        self._facts.join()

    def close(self) -> None:
        """Run the pane reaction for the queued facts, then stop the thread."""
        self._facts.put(None)
        self._thread.join()

    def _react_until_closed(self) -> None:
        closed = False
        while not closed:
            canonical_event = self._facts.get()
            closed = canonical_event is None
            if canonical_event is not None:
                self._react(canonical_event)
            self._facts.task_done()

    def _react(self, canonical_event: CanonicalEvent[EventPayload]) -> None:
        try:
            self._reaction.react(canonical_event)
        except Exception:  # noqa: BLE001 -- A failed pane action must not stop the pane thread; it is audited.
            self._audit.error(
                str(canonical_event.session_id),
                f"reactions ({type(self._reaction).__name__})",
                FailureContext(
                    session_id=canonical_event.session_id,
                    event_id=canonical_event.event_id,
                    cursor=canonical_event.cursor,
                ),
            )
