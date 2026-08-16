"""Notifications derived from canonical session state."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Protocol

from diagnostics import record as AUDIT
from dashboard import config, prefs
from dashboard.activity import DashboardSessionService
from dashboard.application import DashboardNotificationState
from notify import channels, presence
from domain.ids import SessionId
from engine.projections import SessionQueries, TabState

NOTIFICATION_KINDS = {
    "awaiting_attention": "asking",
    "awaiting_response": "done",
}


class NotificationApplication(Protocol):
    queries: SessionQueries
    dashboard_sessions: DashboardSessionService
    dashboard_notification_state: DashboardNotificationState


@dataclass
class PendingNotification:
    session_id: SessionId
    state: TabState
    kind: str
    project: str
    title: str
    due_at: float

    def payload(self) -> dict:
        return {
            "session_id": str(self.session_id),
            "state": self.state,
            "kind": self.kind,
            "project": self.project,
            "title": self.title,
        }


@dataclass
class DeliveredNotification:
    session_id: SessionId
    state: TabState
    handle: dict


class Notifier:
    """Publish and deliver transitions into canonical attention states."""

    def __init__(self, application: NotificationApplication) -> None:
        self.application = application
        self.notification_state = application.dashboard_notification_state
        self.previous_states: dict[SessionId, TabState | None] | None = None
        self.pending: dict[SessionId, PendingNotification] = {}
        self.delivered: dict[SessionId, DeliveredNotification] = {}

    def scan(self) -> None:
        items = tuple(
            item
            for item in self.application.dashboard_sessions.sessions()
            if item.terminal.window_id is not None
        )
        current_states = {
            item.session.session_id: self.application.queries.tab_state(
                item.session.session_id
            )
            for item in items
        }
        if self.previous_states is None:
            self.previous_states = current_states
            return

        items_by_session = {item.session.session_id: item for item in items}
        now = time.monotonic()
        all_session_ids = set(self.previous_states) | set(current_states)
        for session_id in all_session_ids:
            previous = self.previous_states.get(session_id)
            current = current_states.get(session_id)
            if previous == current:
                continue
            self._resolve(session_id, current)
            kind = NOTIFICATION_KINDS.get(current)
            item = items_by_session.get(session_id)
            if kind is not None and item is not None:
                self._schedule(item, current, kind, now)
        for session_id, delivered in list(self.delivered.items()):
            if current_states.get(session_id) != delivered.state:
                self._resolve(session_id, current_states.get(session_id))
        self.previous_states = current_states
        self._deliver_due(current_states, now)

    def _schedule(self, item, state: TabState, kind: str, now: float) -> None:
        session_id = item.session.session_id
        if not prefs.notify_enabled() or prefs.notify_muted(str(session_id)):
            return
        project = os.path.basename(item.project_directory) or str(session_id)
        title = item.session.title or ""
        self.notification_state.publish_notification(
            str(session_id), kind, project, title
        )
        delay = config.NOTIFICATION_DELAY_SECONDS
        if kind == "done":
            delay = max(delay, config.NOTIFICATION_SETTLE_SECONDS)
        self.pending[session_id] = PendingNotification(
            session_id,
            state,
            kind,
            project,
            title,
            now + delay,
        )

    def _deliver_due(
        self,
        current_states: dict[SessionId, TabState | None],
        now: float,
    ) -> None:
        for session_id, notification in list(self.pending.items()):
            if current_states.get(session_id) != notification.state:
                self.pending.pop(session_id, None)
                continue
            if now < notification.due_at:
                continue
            if presence.web_viewing(str(session_id)) or presence.device_active():
                self.pending.pop(session_id, None)
                AUDIT.state_file(
                    "",
                    "",
                    "notification-suppressed",
                    {
                        "session_id": str(session_id),
                        "kind": notification.kind,
                        "reason": "browser-present",
                    },
                )
                continue
            self.pending.pop(session_id, None)
            payload = notification.payload()
            target, subscriptions, decision = presence.route()
            AUDIT.state_file(
                "",
                "",
                "notification-route",
                dict(
                    decision,
                    session_id=str(session_id),
                    kind=notification.kind,
                ),
            )
            handle = None
            if subscriptions and config.NOTIFY_WEBPUSH:
                handle = channels.webpush.send_alert(
                    payload,
                    subscriptions,
                    self._attention_count(current_states),
                )
            elif config.NOTIFY_TELEGRAM:
                handle = channels.telegram.send_alert(payload, "no-browser")
            if handle is not None:
                self.delivered[session_id] = DeliveredNotification(
                    session_id,
                    notification.state,
                    handle,
                )

    def _resolve(self, session_id: SessionId, current: TabState | None) -> None:
        self.pending.pop(session_id, None)
        delivered = self.delivered.get(session_id)
        if delivered is None or delivered.state == current:
            return
        outcome = channels.retract(delivered.handle, "state-changed")
        if outcome != channels.PENDING:
            self.delivered.pop(session_id, None)

    @staticmethod
    def _attention_count(states: dict[SessionId, TabState | None]) -> int:
        return sum(state in NOTIFICATION_KINDS for state in states.values())

    def run(self) -> None:
        while True:
            try:
                self.scan()
            except Exception:
                AUDIT.error("", "dashboard notifier", {})
            time.sleep(config.GLOBAL_REFRESH_SECONDS)
