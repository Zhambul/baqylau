"""The list page's own state: what you set, what you spent, what is waiting.

Everything here is state the BROWSER owns rather than the session — the
new-session form you half-filled, the directories you hid, the devices you
subscribed for push — held in the preferences store and answered as one
snapshot so the page never has to stitch six calls together.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from dashboard import prefs
from dashboard.services.models import DashboardSessionListItem
from dashboard.services.notices import DashboardNotificationNotice, DashboardNotificationState
from dashboard.services.sessions import DashboardSessionService
from domain.ids import SessionId
from harness.models import UsageRow


@dataclass(frozen=True)
class GlobalNotificationState:
    enabled: bool
    latest: DashboardNotificationNotice | None


@dataclass(frozen=True)
class NewSessionPreferences:
    working_directory: str | None
    harness: str | None
    model: str | None
    effort: str | None


@dataclass(frozen=True)
class NewSessionDraft:
    working_directory: str
    text: str
    sequence: float


@dataclass(frozen=True)
class DashboardLimits:
    upload_bytes: int
    rename_characters: int
    presence_seconds: float


@dataclass(frozen=True)
class GlobalPreferences:
    new_session: NewSessionPreferences
    new_session_drafts: tuple[NewSessionDraft, ...]
    hidden_directories: dict[str, float]
    limits: DashboardLimits


@dataclass(frozen=True)
class BrowserPushSubscription:
    endpoint: str
    public_key: str
    authentication_secret: str
    device_id: str
    device_label: str | None


@dataclass(frozen=True)
class BrowserPresence:
    device_id: str
    session_id: SessionId | None
    away: bool


@dataclass(frozen=True)
class GlobalApplicationSnapshot:
    sessions: tuple[DashboardSessionListItem, ...]
    usage_rows: tuple[UsageRow, ...]
    notifications: GlobalNotificationState
    preferences: GlobalPreferences


class UsageReader(Protocol):
    def usage_rows(self) -> tuple[UsageRow, ...]: ...


class GlobalApplicationService:
    def __init__(
        self,
        sessions: DashboardSessionService,
        usage: UsageReader,
        state: DashboardNotificationState,
    ) -> None:
        self.sessions = sessions
        self.usage = usage
        self.state = state

    def snapshot(self) -> GlobalApplicationSnapshot:
        from core.daemon import contract as daemon_contract
        from dashboard import config
        from notify import presence

        new_session = prefs.get("new-session", {})
        drafts = prefs.new_session_drafts()
        return GlobalApplicationSnapshot(
            sessions=self.sessions.sessions(),
            usage_rows=self.usage.usage_rows(),
            notifications=GlobalNotificationState(
                enabled=prefs.notify_enabled(),
                latest=self.state.notification(),
            ),
            preferences=GlobalPreferences(
                new_session=NewSessionPreferences(
                    working_directory=new_session.get("working_directory") or None,
                    harness=new_session.get("harness") or None,
                    model=new_session.get("model") or None,
                    effort=new_session.get("effort") or None,
                ),
                new_session_drafts=tuple(
                    NewSessionDraft(
                        working_directory=working_directory,
                        text=record["text"],
                        sequence=float(record["sequence"]),
                    )
                    for working_directory, record in sorted(drafts.items())
                ),
                hidden_directories={
                    str(path): float(hidden_at)
                    for path, hidden_at in prefs.hidden_dirs().items()
                },
                limits=DashboardLimits(
                    upload_bytes=daemon_contract.UPLOAD_MAX,
                    rename_characters=config.RENAME_CHARACTER_LIMIT,
                    presence_seconds=presence.VIEW_LIFETIME_SECONDS,
                ),
            ),
        )

    def set_notifications_enabled(self, enabled: bool) -> None:
        prefs.set_notify_enabled(enabled)

    def save_new_session_preferences(
        self,
        working_directory: str | None,
        harness: str | None,
        model: str | None,
        effort: str | None,
    ) -> None:
        record = {}
        if working_directory:
            record["working_directory"] = working_directory
        if harness:
            record["harness"] = harness
        if model:
            record["model"] = model
        if effort:
            record["effort"] = effort
        if not prefs.set("new-session", record):
            raise RuntimeError("new-session preferences were not saved")

    def save_new_session_draft(
        self,
        working_directory: str,
        text: str,
        sequence: float,
    ) -> bool:
        record = prefs.set_new_session_draft(
            working_directory,
            text if text.strip() else "",
            sequence,
        )
        return not bool(record.get("stale"))

    def hide_directory(self, working_directory: str) -> dict[str, float]:
        live = [
            item
            for item in self.sessions.sessions()
            if item.project_directory == working_directory
            and item.terminal.window_id is not None
        ]
        if live:
            raise ValueError("cannot hide a directory with an active session")
        import time

        return prefs.hide_dir(working_directory, time.time())

    def register_push_subscription(
        self,
        subscription: BrowserPushSubscription,
    ) -> None:
        prefs.add_push_subscription(
            {
                "endpoint": subscription.endpoint,
                "keys": {
                    "p256dh": subscription.public_key,
                    "auth": subscription.authentication_secret,
                },
            },
            device=subscription.device_id,
            label=subscription.device_label,
        )

    @staticmethod
    def report_presence(report: BrowserPresence) -> None:
        from notify import presence

        session_id = str(report.session_id) if report.session_id is not None else None
        if report.away:
            presence.mark_away(report.device_id, session_id)
            return
        presence.mark_device(report.device_id)
        if session_id:
            presence.mark_viewing(session_id)
