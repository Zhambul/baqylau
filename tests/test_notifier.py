# Copyright (c) 2026 Zhambyl Yermagambet
"""Notification scanning uses one terminal snapshot for all sessions."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

from dashboard.services.notices import DashboardNotificationState
from domain import ids as domain_ids, lifecycle, session_state
from notify import notifier as notifier_module, presence as presence_module
from repository.contract.session_data import SessionDataRepository, SessionLead

if TYPE_CHECKING:
    from collections.abc import Iterable

    from audit.recorder import AuditRecorder
    from core.repository import RepositoryQueries
    from repository.contract.preferences import (
        NotificationSettingRepository,
        PushSigningKeyRepository,
        PushSubscriptionRepository,
    )
    from terminal.adapter import TerminalAdapter


CODEX_HARNESS = domain_ids.HarnessName("codex")


def _session_data(session_id_text: str) -> SessionLead:
    session_id = domain_ids.SessionId(session_id_text)
    return SessionLead(
        session=session_state.SessionFacts(
            session_id=session_id,
            harness=CODEX_HARNESS,
            state=lifecycle.LifecycleState.RUNNING,
            working_directory="/work",
            started_at=1.0,
            lead_actor_id=domain_ids.ActorId(f"{session_id_text}:lead"),
        ),
        lead=None,
    )


class CountingTerminal:
    """Represent counting terminal."""

    def __init__(self) -> None:
        """Initialize the object."""
        self.calls = 0
        self.requested: tuple[domain_ids.SessionId, ...] = ()

    def live_sessions(self, session_ids: Iterable[domain_ids.SessionId]) -> frozenset[domain_ids.SessionId]:
        """Record a live-session query.

        Returns:
            All requested session identifiers.

        """
        self.calls += 1
        self.requested = tuple(session_ids)
        return frozenset(self.requested)


def test_notice_scan_reads_one_terminal_snapshot() -> None:
    """Verify notification scan reads one terminal snapshot for all sessions."""
    visible = (_session_data("session-one"), _session_data("session-two"))
    terminal = CountingTerminal()
    notifier = notifier_module.Notifier(
        notifier_module.NotifierDependencies(
            session_data_repository=cast("SessionDataRepository", SimpleNamespace(lead_sessions=lambda: visible)),
            terminal_adapter=cast("TerminalAdapter", terminal),
            repository_queries=cast("RepositoryQueries", SimpleNamespace(project_directory=lambda path: path)),
            dashboard_notification_state=DashboardNotificationState(),
            notification_setting_repository=cast(
                "NotificationSettingRepository",
                SimpleNamespace(muted_session_ids=frozenset),
            ),
            push_subscription_repository=cast("PushSubscriptionRepository", object()),
            push_signing_key_repository=cast("PushSigningKeyRepository", object()),
            presence=cast("presence_module.Presence", object()),
            audit_recorder=cast("AuditRecorder", object()),
        ),
    )

    notifier.scan()

    assert terminal.calls == 1
    assert terminal.requested == (domain_ids.SessionId("session-one"), domain_ids.SessionId("session-two"))


def test_dashboard_notice_state_publishes_each() -> None:
    """Verify dashboard notification state publishes each new notice."""
    changes = []
    state = DashboardNotificationState(lambda: changes.append("changed"))

    state.publish_notification(domain_ids.SessionId("session-one"), "done", "baqylau", "finished")
    state.publish_notification(domain_ids.SessionId("session-two"), "asking", "api", "needs you")

    assert changes == ["changed", "changed"]
    notice = state.notification()
    assert notice is not None
    assert notice.revision == len(changes)
