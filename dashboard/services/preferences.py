"""What YOU set, as opposed to what a session did.

Six writes, and every one of them is state the BROWSER owns rather than the
session: the new-session form you half-filled, the directories you hid, the
devices you subscribed for push, whether you are looking. None of it is derived
from a fact, which is why it is a store and not a fold — and why this survived
the read path it used to sit beside.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from core.daemon import contract as daemon_contract
from core.repository import RepositoryQueries
from dashboard import config
from dashboard.services.notices import DashboardNotificationNotice, DashboardNotificationState
from domain.ids import DeviceId, HarnessName, SessionId
from domain.preferences import (
    NewSessionDraft as StoredNewSessionDraft,
    NewSessionPreferences as StoredNewSessionPreferences,
    PushSubscription,
)
from harness.models import UsageRow
from notify import presence
from notify.presence import Presence
from repository.contract.preferences import (
    HiddenDirectoryRepository,
    NewSessionRepository,
    NotificationSettingRepository,
    PushSubscriptionRepository,
)
from repository.contract.session_data import SessionDataRepository
from terminal.adapter import TerminalAdapter


class UsageReader(Protocol):
    """The account fuel gauges. A Protocol because the thing that reads them is
    the usage worker's state, which this tier may not import."""

    def usage_rows(self) -> tuple[UsageRow, ...]: ...

# The launch form is opened against a handful of projects in practice; an
# unbounded table would gain a row per directory ever typed into.
NEW_SESSION_DRAFT_LIMIT = 24


@dataclass(frozen=True)
class GlobalNotificationState:
    enabled: bool
    latest: DashboardNotificationNotice | None


@dataclass(frozen=True)
class DashboardLimits:
    upload_bytes: int
    rename_characters: int
    presence_seconds: float


@dataclass(frozen=True)
class NewSessionPreferences:
    working_directory: str | None
    harness: HarnessName | None
    model: str | None
    effort: str | None


@dataclass(frozen=True)
class NewSessionDraft:
    working_directory: str
    text: str
    sequence: float


@dataclass(frozen=True)
class ApplicationPreferences:
    """Everything the list page owns, in one answer.

    One read rather than five, because the page needs all of it before it can
    paint anything and the repo already retired the field-specific routes this
    would otherwise grow back into.
    """

    new_session: NewSessionPreferences
    new_session_drafts: tuple[NewSessionDraft, ...]
    hidden_directories: dict[str, float]
    limits: DashboardLimits
    notifications: GlobalNotificationState
    usage_rows: tuple[UsageRow, ...]


@dataclass(frozen=True)
class BrowserPushSubscription:
    endpoint: str
    public_key: str
    authentication_secret: str
    device_id: DeviceId
    device_label: str | None


@dataclass(frozen=True)
class BrowserPresence:
    device_id: DeviceId
    session_id: SessionId | None
    away: bool


class ApplicationPreferenceService:
    def __init__(
        self,
        session_data_repository: SessionDataRepository,
        terminal_adapter: TerminalAdapter,
        repository_queries: RepositoryQueries,
        usage_reader: UsageReader,
        dashboard_notification_state: DashboardNotificationState,
        new_session_repository: NewSessionRepository,
        notification_setting_repository: NotificationSettingRepository,
        hidden_directory_repository: HiddenDirectoryRepository,
        push_subscription_repository: PushSubscriptionRepository,
        presence: Presence,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.session_data_repository = session_data_repository
        self.terminal_adapter = terminal_adapter
        self.repository_queries = repository_queries
        self.usage_reader = usage_reader
        self.dashboard_notification_state = dashboard_notification_state
        self.new_session_repository = new_session_repository
        self.notification_setting_repository = notification_setting_repository
        self.hidden_directory_repository = hidden_directory_repository
        self.push_subscription_repository = push_subscription_repository
        self.presence = presence
        self.clock = clock

    def snapshot(self) -> ApplicationPreferences:
        """What the page owns, read back. No session rows: those are the read
        model's, and they arrive on /sessionData."""
        new_session = self.new_session_repository.preferences()
        return ApplicationPreferences(
            new_session=NewSessionPreferences(
                working_directory=new_session.working_directory if new_session else None,
                harness=new_session.harness if new_session else None,
                model=new_session.model if new_session else None,
                effort=new_session.effort if new_session else None,
            ),
            new_session_drafts=tuple(
                NewSessionDraft(
                    working_directory=draft.working_directory,
                    text=draft.text,
                    sequence=draft.sequence,
                )
                for draft in self.new_session_repository.drafts()
            ),
            hidden_directories={
                entry.working_directory: entry.hidden_at for entry in self.hidden_directory_repository.hidden()
            },
            limits=DashboardLimits(
                upload_bytes=daemon_contract.UPLOAD_MAX,
                rename_characters=config.RENAME_CHARACTER_LIMIT,
                presence_seconds=presence.VIEW_LIFETIME_SECONDS,
            ),
            notifications=GlobalNotificationState(
                enabled=self.notification_setting_repository.alerting_enabled(),
                latest=self.dashboard_notification_state.notification(),
            ),
            usage_rows=self.usage_reader.usage_rows(),
        )

    def set_notifications_enabled(self, enabled: bool) -> None:
        self.notification_setting_repository.set_alerting_enabled(enabled)

    def save_new_session_preferences(
        self,
        working_directory: str | None,
        harness: HarnessName | None,
        model: str | None,
        effort: str | None,
    ) -> None:
        self.new_session_repository.save_preferences(
            StoredNewSessionPreferences(
                working_directory=working_directory or None,
                harness=harness or None,
                model=model or None,
                effort=effort or None,
            )
        )

    def save_new_session_draft(
        self,
        working_directory: str,
        text: str,
        sequence: float,
    ) -> bool:
        written = self.new_session_repository.save_draft(
            StoredNewSessionDraft(
                working_directory,
                text if text.strip() else "",
                sequence,
            ),
            NEW_SESSION_DRAFT_LIMIT,
        )
        return not written.stale

    def hide_directory(self, working_directory: str) -> dict[str, float]:
        # A directory with a session somebody is attending is a directory they
        # are working in; hiding it would take the row out from under them.
        live = [
            data
            for data in self.session_data_repository.visible()
            if self.repository_queries.project_directory(data.session.working_directory) == working_directory
            and self.terminal_adapter.window_for_session(data.session.session_id) is not None
        ]
        if live:
            raise ValueError("cannot hide a directory with an active session")
        self.hidden_directory_repository.hide(working_directory, self.clock())
        return {entry.working_directory: entry.hidden_at for entry in self.hidden_directory_repository.hidden()}

    def register_push_subscription(
        self,
        browser_push_subscription: BrowserPushSubscription,
    ) -> None:
        # One browser installation (DEVICE_ID) has one current PushManager
        # subscription. A VAPID rotation forces a new endpoint; remove the old
        # endpoint for this same installation so routing does not keep sending
        # to a subscription Apple has permanently rejected.
        for existing in self.push_subscription_repository.subscriptions():
            if (
                existing.device_id == browser_push_subscription.device_id
                and existing.endpoint != browser_push_subscription.endpoint
            ):
                self.push_subscription_repository.remove(existing.endpoint)
        self.push_subscription_repository.upsert(
            PushSubscription(
                endpoint=browser_push_subscription.endpoint,
                public_key=browser_push_subscription.public_key,
                authentication_secret=browser_push_subscription.authentication_secret,
                device_id=browser_push_subscription.device_id,
                device_label=browser_push_subscription.device_label,
                created_at=self.clock(),
            )
        )

    def report_presence(self, browser_presence: BrowserPresence) -> None:
        session_id = browser_presence.session_id
        if browser_presence.away:
            self.presence.mark_away(browser_presence.device_id, session_id)
            return
        self.presence.mark_device(browser_presence.device_id)
        if session_id:
            self.presence.mark_viewing(session_id)
