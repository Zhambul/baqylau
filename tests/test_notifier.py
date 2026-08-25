"""Notification scanning uses one terminal snapshot for all sessions."""

from __future__ import annotations

from collections.abc import Iterable
from types import SimpleNamespace
from typing import Any, cast

from dashboard.services.notices import DashboardNotificationState
from domain.ids import ActorId, HarnessName, SessionId
from domain.sessiondata import LifecycleState, SessionFacts
from notify.notifier import Notifier
from repository.contract.session_data import SessionLead


def _session_data(value: str) -> SessionLead:
    session_id = SessionId(value)
    return SessionLead(
        session=SessionFacts(
            session_id=session_id,
            harness=HarnessName.CODEX,
            state=LifecycleState.RUNNING,
            working_directory="/work",
            started_at=1.0,
            lead_actor_id=ActorId(f"{value}:lead"),
        ),
        lead=None,
    )


class CountingTerminal:
    def __init__(self) -> None:
        self.calls = 0
        self.requested: tuple[SessionId, ...] = ()

    def live_sessions(self, session_ids: Iterable[SessionId]) -> frozenset[SessionId]:
        self.calls += 1
        self.requested = tuple(session_ids)
        return frozenset(self.requested)


def test_notification_scan_reads_one_terminal_snapshot_for_all_sessions():
    visible = (_session_data("session-one"), _session_data("session-two"))
    terminal = CountingTerminal()
    notifier = Notifier(
        cast(Any, SimpleNamespace(lead_sessions=lambda: visible)),
        cast(Any, terminal),
        cast(Any, SimpleNamespace(project_directory=lambda path: path)),
        DashboardNotificationState(),
        cast(Any, SimpleNamespace(muted_session_ids=lambda: frozenset())),
        cast(Any, object()),
        cast(Any, object()),
        cast(Any, object()),
        cast(Any, object()),
    )

    notifier.scan()

    assert terminal.calls == 1
    assert terminal.requested == (SessionId("session-one"), SessionId("session-two"))
