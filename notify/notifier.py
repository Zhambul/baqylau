"""Notifications derived from canonical session state."""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Any

from audit.recorder import AuditRecorder
from core.repository import RepositoryQueries
from dashboard import config
from dashboard.services.notices import DashboardNotificationState
from notify import channels
from notify.presence import Presence
from domain.ids import SessionId
from domain.sessiondata import ActorStatus, SessionData
from repository.contract.session_data import SessionDataRepository
from terminal.adapter import TerminalAdapter
from repository.contract.preferences import (
    NotificationSettingRepository,
    PushSigningKeyRepository,
    PushSubscriptionRepository,
)

# The two states worth interrupting you for. `awaiting_background` is DELIBERATELY
# not among them: a turn that ended while its own background job still runs has not
# finished producing what you would come back to read, and an alert then is early.
# Being unmapped is not a gap — the lookup returning None is how a state says "no
# alert", the same way `idle` and `working` do.
#
# It became reachable when background work stopped being ended by its own launch
# (engine/projections/tabstate.py), so its consequence is new: a session stays
# unalerted until the JOB reports its end, and one that never reports one stays
# unalerted for the rest of the session. The alert that DOES fire is the one for
# the next thing that asks you something.
NOTIFICATION_KINDS = {
    "awaiting_attention": "asking",
    "awaiting_response": "done",
}


@dataclass
class PendingNotification:
    session_id: SessionId
    state: ActorStatus
    kind: str
    project: str
    title: str
    due_at: float
    pushed: bool = False

    def payload(self) -> dict[str, str]:
        return {
            "session_id": str(self.session_id),
            "state": self.state,
            "kind": self.kind,
            "project": self.project,
            "title": self.title,
        }


@dataclass(frozen=True)
class _Alertable:
    """One attended session, as the notifier reads it: who it is, what it is
    called, and the one word that decides whether to interrupt you."""

    session_id: SessionId
    title: str
    project: str
    status: ActorStatus | None


def _lead_status(session_data: SessionData) -> ActorStatus | None:
    """The session's own status, which is its LEAD actor's.

    A tab shows a session and a session shows its lead: a subagent asking itself
    a question is not the session asking you one.
    """
    for actor in session_data.actors:
        if actor.actor_id == session_data.session.lead_actor_id:
            return actor.status
    return None


@dataclass
class DeliveredNotification:
    session_id: SessionId
    state: ActorStatus
    handle: dict[str, Any]  # loose: notification payload, wave 2 gives it a real shape
    delivered_at: float


class Notifier:
    """Publish and deliver transitions into canonical attention states."""

    def __init__(
        self,
        session_data_repository: SessionDataRepository,
        terminal_adapter: TerminalAdapter,
        repository_queries: RepositoryQueries,
        dashboard_notification_state: DashboardNotificationState,
        notification_setting_repository: NotificationSettingRepository,
        push_subscription_repository: PushSubscriptionRepository,
        push_signing_key_repository: PushSigningKeyRepository,
        presence: Presence,
        audit_recorder: AuditRecorder,
    ) -> None:
        self.read_model = session_data_repository
        self.terminal = terminal_adapter
        self.repositories = repository_queries
        self.notification_state = dashboard_notification_state
        self.notification_settings = notification_setting_repository
        self.push_subscriptions = push_subscription_repository
        self.push_signing_keys = push_signing_key_repository
        self.presence = presence
        self.audit = audit_recorder
        # One query per pass, not one per armed session.
        self._muted: frozenset[SessionId] = frozenset()
        self.previous_states: dict[SessionId, ActorStatus | None] | None = None
        self.pending: dict[SessionId, PendingNotification] = {}
        self.delivered: dict[SessionId, list[DeliveredNotification]] = {}

    def scan(self) -> None:
        # ATTENDED sessions only: a notification is a nudge back to a window,
        # and a parked session has none to nudge you to.
        items = tuple(
            _Alertable(
                session_id=data.session.session_id,
                title=data.session.title or "",
                project=os.path.basename(
                    self.repositories.project_directory(data.session.working_directory) or ""
                )
                or str(data.session.session_id),
                status=_lead_status(data),
            )
            for data in self.read_model.visible()
            if self.terminal.window_for_session(data.session.session_id) is not None
        )
        current_states = {item.session_id: item.status for item in items}
        self._muted = self.notification_settings.muted_session_ids()
        if self.previous_states is None:
            self.previous_states = current_states
            return

        items_by_session = {item.session_id: item for item in items}
        now = time.monotonic()
        badge = self._attention_count(current_states)
        all_session_ids = set(self.previous_states) | set(current_states)
        for session_id in all_session_ids:
            previous = self.previous_states.get(session_id)
            current = current_states.get(session_id)
            if previous == current:
                continue
            self._resolve(session_id, current, now, badge)
            # A session that dropped out of current_states has nothing left to
            # notify ABOUT — it was only here so _resolve above could retract a
            # standing alert. The kind lookup below already returned None for
            # it (None is not a key), so this only says out loud what the
            # sequence was doing.
            if current is None:
                continue
            kind = NOTIFICATION_KINDS.get(current)
            item = items_by_session.get(session_id)
            if kind is not None and item is not None:
                self._schedule(item, current, kind, now)
        for session_id, delivered in list(self.delivered.items()):
            if any(item.state != current_states.get(session_id) for item in delivered):
                self._resolve(
                    session_id,
                    current_states.get(session_id),
                    now,
                    badge,
                )
        self.previous_states = current_states
        self._deliver_due(current_states, now)

    def _schedule(self, _alertable: _Alertable, state: ActorStatus, kind: str, now: float) -> None:
        session_id = _alertable.session_id
        if not self.notification_settings.alerting_enabled() or session_id in self._muted:
            return
        project = _alertable.project
        title = _alertable.title
        self.notification_state.publish_notification(session_id, kind, project, title)
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
        current_states: dict[SessionId, ActorStatus | None],
        now: float,
    ) -> None:
        for session_id, notification in list(self.pending.items()):
            if current_states.get(session_id) != notification.state:
                self.pending.pop(session_id, None)
                continue
            if now < notification.due_at:
                continue
            if notification.pushed:
                # Stage 2: the routed browser push has had its chance. If the
                # session still needs attention, Telegram is the nudge on a
                # different channel. A state transition already removed this
                # pending record in _resolve.
                self.pending.pop(session_id, None)
                if self.presence.web_viewing(session_id):
                    continue
                if config.NOTIFY_TELEGRAM:
                    self._track(
                        notification,
                        channels.telegram.send_alert(
                            notification.payload(), "escalation"
                        ),
                    )
                continue
            if self.presence.web_viewing(session_id) or self.presence.device_active():
                self.pending.pop(session_id, None)
                self.audit.state_file(
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
            payload = notification.payload()
            target, subscriptions, decision = self.presence.route(self.push_subscriptions)
            self.audit.state_file(
                "",
                "",
                "notification-route",
                dict(
                    decision,
                    session_id=str(session_id),
                    kind=notification.kind,
                ),
            )
            push_handle = None
            if subscriptions and config.NOTIFY_WEBPUSH:
                push_handle = channels.webpush.send_alert(
                    payload,
                    subscriptions,
                    self._attention_count(current_states),
                    push_signing_key_repository=self.push_signing_keys,
                    push_subscription_repository=self.push_subscriptions,
                )
            if push_handle is not None:
                self._track(notification, push_handle)
                if config.NOTIFY_TELEGRAM_ALWAYS:
                    self.pending.pop(session_id, None)
                    if config.NOTIFY_TELEGRAM:
                        self._track(
                            notification,
                            channels.telegram.send_alert(payload, "always"),
                        )
                elif config.NOTIFY_TELEGRAM:
                    notification.pushed = True
                    notification.due_at = now + config.ESCALATION_DELAY_SECONDS
                else:
                    self.pending.pop(session_id, None)
                continue

            self.pending.pop(session_id, None)
            if config.NOTIFY_TELEGRAM:
                if target == "terminal":
                    reason = "terminal"
                elif subscriptions:
                    reason = "push-off"
                else:
                    reason = "no-device"
                self._track(
                    notification,
                    channels.telegram.send_alert(payload, reason),
                )

    def _track(
        self,
        pending_notification: PendingNotification,
        handle: dict[str, Any] | None,  # loose: notification payload, wave 2 gives it a real shape
    ) -> None:
        if handle is None:
            return
        self.delivered.setdefault(pending_notification.session_id, []).append(
            DeliveredNotification(
                pending_notification.session_id,
                pending_notification.state,
                handle,
                time.monotonic(),
            )
        )
        self._enforce_sent_cap()

    def _resolve(
        self,
        session_id: SessionId,
        current: ActorStatus | None,
        now: float,
        badge: int = 0,
    ) -> None:
        self.pending.pop(session_id, None)
        delivered = self.delivered.get(session_id) or []
        remaining = []
        for notification in delivered:
            if notification.state == current:
                remaining.append(notification)
                continue
            age = now - notification.delivered_at
            if age >= config.RETRACTION_LIFETIME_SECONDS:
                self._audit_retraction(notification, "expired", age)
                continue
            outcome = channels.retract(
                notification.handle,
                "state-changed",
                badge=badge,
                push_signing_key_repository=self.push_signing_keys,
                push_subscription_repository=self.push_subscriptions,
            )
            if outcome in (channels.PENDING, channels.FAILED):
                remaining.append(notification)
            else:
                self._audit_retraction(notification, outcome, age)
        if remaining:
            self.delivered[session_id] = remaining
        else:
            self.delivered.pop(session_id, None)

    def _audit_retraction(
        self,
        delivered_notification: DeliveredNotification,
        outcome: str,
        age: float,
        reason: str = "state-changed",
    ) -> None:
        self.audit.state_file(
            "",
            "",
            "notify-retract",
            {
                "session_id": str(delivered_notification.session_id),
                "channel": delivered_notification.handle.get("ch"),
                "kind": delivered_notification.handle.get("kind"),
                "reason": reason,
                "outcome": outcome,
                "age_seconds": round(max(0.0, age), 3),
            },
        )

    def _enforce_sent_cap(self) -> None:
        excess = sum(map(len, self.delivered.values())) - config.SENT_CAP
        if excess <= 0:
            return
        oldest = sorted(
            (
                (notification.delivered_at, session_id, notification)
                for session_id, delivered in self.delivered.items()
                for notification in delivered
            ),
            key=lambda item: item[0],
        )[:excess]
        for _, session_id, notification in oldest:
            delivered = self.delivered.get(session_id) or []
            if notification not in delivered:
                continue
            delivered.remove(notification)
            self._audit_retraction(
                notification,
                "capacity-expired",
                time.monotonic() - notification.delivered_at,
                "capacity",
            )
            if not delivered:
                self.delivered.pop(session_id, None)

    @staticmethod
    def _attention_count(states: dict[SessionId, ActorStatus | None]) -> int:
        return sum(state in NOTIFICATION_KINDS for state in states.values())

    def run(self, stop: threading.Event) -> None:
        """One pass per refresh interval until asked to stop. The wait IS the
        sleep, so a shutdown does not have to outlast one."""
        while not stop.is_set():
            try:
                self.scan()
            except Exception:
                self.audit.error("", "dashboard notifier", {})
            stop.wait(config.GLOBAL_REFRESH_SECONDS)
