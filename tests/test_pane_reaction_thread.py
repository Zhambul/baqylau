# Copyright (c) 2026 Zhambyl Yermagambet
"""Open and close panes on their own thread, so that the engine never waits for the terminal."""

from __future__ import annotations

import threading
from dataclasses import replace
from typing import TYPE_CHECKING
from unittest.mock import Mock

from audit.failures import FailureContext
from domain.event_session import SessionFinished
from domain.outcomes import Outcome
from terminal.panes.reaction_thread import PaneReactionThread
from tests import foundation_test_events

if TYPE_CHECKING:
    from domain.event_base import CanonicalEvent, EventPayload

STARTED = foundation_test_events.session_started_event()
FINISHED = replace(STARTED, payload=SessionFinished(Outcome.SUCCEEDED, None))
MESSAGE = foundation_test_events.canonical_message()


class _SlowPanes:
    """Wait for a release before each pane action, like a slow terminal."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.facts: list[CanonicalEvent[EventPayload]] = []

    def react(self, canonical_event: CanonicalEvent[EventPayload]) -> None:
        self.release.wait()
        self.facts.append(canonical_event)


def test_react_returns_before_the_terminal_acts() -> None:
    """Queue pane facts in order, and skip the facts that do not open or close panes."""
    panes = _SlowPanes()
    thread = PaneReactionThread(panes, Mock())

    thread.react(STARTED)
    thread.react(MESSAGE)
    thread.react(FINISHED)
    assert panes.facts == []

    panes.release.set()
    thread.flush()
    assert panes.facts == [STARTED, FINISHED]
    thread.close()


def test_failed_pane_action_is_audited() -> None:
    """Audit a failed pane action in the same way the reaction loop does, and run the next fact."""
    panes = Mock()
    panes.react.side_effect = [RuntimeError("the terminal did not answer"), None]
    audit = Mock()
    thread = PaneReactionThread(panes, audit)

    thread.react(STARTED)
    thread.react(FINISHED)
    thread.close()

    assert [called.args[0] for called in panes.react.call_args_list] == [STARTED, FINISHED]
    failure = FailureContext(session_id=STARTED.session_id, event_id=STARTED.event_id, cursor=STARTED.cursor)
    audit.error.assert_called_once_with(str(STARTED.session_id), "reactions (Mock)", failure)
