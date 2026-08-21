"""The nine preference tables that replaced one key–value table.

The pruning policies that used to be Python read-modify-write loops over a JSON
map are `DELETE … WHERE … NOT IN (SELECT … ORDER BY … LIMIT ?)` inside the same
transaction as the write that triggers them. The "default is an absence" rule
survives: a mode set back to the default deletes its row, so each table stays
the small set of things someone actually chose.
"""

from __future__ import annotations

from typing import Sequence

from domain.ids import SessionId, TaskId
from domain.preferences import (
    DraftWrite,
    HiddenDirectory,
    NewSessionDraft,
    NewSessionPreferences,
    PushSigningKeypair,
    PushSubscription,
    ViewMode,
)
from repository.contract.preferences import (
    HiddenDirectoryRepository,
    NewSessionRepository,
    NotificationSettingRepository,
    PushSigningKeyRepository,
    PushSubscriptionRepository,
    TaskDismissalRepository,
    ViewModeRepository,
)
from repository.impl.sqlite import rows
from repository.impl.sqlite.connection import SqliteDatabase
from repository.mapper import preferences as mapper


class SqliteViewModeRepository(ViewModeRepository):
    def __init__(self, sqlite_database: SqliteDatabase) -> None:
        self.sqlite_database = sqlite_database

    def view_mode(self, session_id: SessionId) -> ViewMode | None:
        with self.sqlite_database.read() as connection:
            row = connection.execute(
                "SELECT * FROM session_view_modes WHERE session_id=?", (str(session_id),)
            ).fetchone()
        return mapper.view_mode(rows.session_view_mode(row)) if row is not None else None

    def set_view_mode(self, session_id: SessionId, view_mode: ViewMode) -> None:
        with self.sqlite_database.write() as connection:
            connection.execute(
                "INSERT INTO session_view_modes(session_id, view_mode) VALUES(?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET view_mode=excluded.view_mode",
                (str(session_id), view_mode),
            )

    def clear_view_mode(self, session_id: SessionId) -> None:
        with self.sqlite_database.write() as connection:
            connection.execute(
                "DELETE FROM session_view_modes WHERE session_id=?", (str(session_id),)
            )


class SqliteNotificationSettingRepository(NotificationSettingRepository):
    def __init__(self, sqlite_database: SqliteDatabase) -> None:
        self.sqlite_database = sqlite_database

    def alerting_enabled(self) -> bool:
        with self.sqlite_database.read() as connection:
            row = connection.execute(
                "SELECT alerting_enabled FROM notification_settings WHERE id=1"
            ).fetchone()
        # Absent reads True: a fresh install alerts until the user opts out.
        return True if row is None else bool(row["alerting_enabled"])

    def set_alerting_enabled(self, enabled: bool) -> None:
        with self.sqlite_database.write() as connection:
            connection.execute(
                "INSERT INTO notification_settings(id, alerting_enabled) VALUES(1, ?) "
                "ON CONFLICT(id) DO UPDATE SET alerting_enabled=excluded.alerting_enabled",
                (1 if enabled else 0,),
            )

    def muted_session_ids(self) -> frozenset[SessionId]:
        with self.sqlite_database.read() as connection:
            found = connection.execute(
                "SELECT session_id FROM session_notification_mutes"
            ).fetchall()
        return frozenset(SessionId(row["session_id"]) for row in found)

    def set_muted(self, session_id: SessionId, muted: bool) -> None:
        with self.sqlite_database.write() as connection:
            if muted:
                connection.execute(
                    "INSERT OR IGNORE INTO session_notification_mutes(session_id, muted_at) "
                    "VALUES(?, strftime('%s','now'))",
                    (str(session_id),),
                )
            else:
                connection.execute(
                    "DELETE FROM session_notification_mutes WHERE session_id=?",
                    (str(session_id),),
                )


class SqliteHiddenDirectoryRepository(HiddenDirectoryRepository):
    def __init__(self, sqlite_database: SqliteDatabase) -> None:
        self.sqlite_database = sqlite_database

    def hidden(self) -> tuple[HiddenDirectory, ...]:
        with self.sqlite_database.read() as connection:
            found = connection.execute(
                "SELECT * FROM hidden_directories ORDER BY working_directory"
            ).fetchall()
        return tuple(mapper.hidden_directory(rows.hidden_directory(row)) for row in found)

    def hide(self, working_directory: str, hidden_at: float) -> None:
        with self.sqlite_database.write() as connection:
            connection.execute(
                "INSERT INTO hidden_directories(working_directory, hidden_at) VALUES(?, ?) "
                "ON CONFLICT(working_directory) DO UPDATE SET hidden_at=excluded.hidden_at",
                (str(working_directory), float(hidden_at)),
            )


class SqliteNewSessionRepository(NewSessionRepository):
    def __init__(self, sqlite_database: SqliteDatabase) -> None:
        self.sqlite_database = sqlite_database

    def preferences(self) -> NewSessionPreferences | None:
        with self.sqlite_database.read() as connection:
            row = connection.execute(
                "SELECT * FROM new_session_preferences WHERE id=1"
            ).fetchone()
        if row is None:
            return None
        return mapper.new_session_preferences(rows.new_session_preference(row))

    def save_preferences(self, new_session_preferences: NewSessionPreferences) -> None:
        with self.sqlite_database.write() as connection:
            connection.execute(
                "INSERT INTO new_session_preferences(id, working_directory, harness, model, effort) "
                "VALUES(1, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                "working_directory=excluded.working_directory, harness=excluded.harness, "
                "model=excluded.model, effort=excluded.effort",
                (
                    new_session_preferences.working_directory,
                    new_session_preferences.harness,
                    new_session_preferences.model,
                    new_session_preferences.effort,
                ),
            )

    def drafts(self) -> tuple[NewSessionDraft, ...]:
        with self.sqlite_database.read() as connection:
            found = connection.execute(
                "SELECT * FROM new_session_drafts ORDER BY working_directory"
            ).fetchall()
        return tuple(mapper.new_session_draft(rows.new_session_draft(row)) for row in found)

    def save_draft(self, new_session_draft: NewSessionDraft, keep_newest: int) -> DraftWrite:
        with self.sqlite_database.write() as connection:
            current = connection.execute(
                "SELECT * FROM new_session_drafts WHERE working_directory=?",
                (new_session_draft.working_directory,),
            ).fetchone()
            if current is not None and new_session_draft.sequence < current["sequence"]:
                # A debounced save in flight when the launch cleared the box
                # must not resurrect it by landing later.
                return DraftWrite(mapper.new_session_draft(rows.new_session_draft(current)), True)
            connection.execute(
                "INSERT INTO new_session_drafts(working_directory, text, sequence) "
                "VALUES(?, ?, ?) ON CONFLICT(working_directory) DO UPDATE SET "
                "text=excluded.text, sequence=excluded.sequence",
                (
                    new_session_draft.working_directory,
                    new_session_draft.text,
                    new_session_draft.sequence,
                ),
            )
            connection.execute(
                "DELETE FROM new_session_drafts WHERE working_directory NOT IN ("
                "  SELECT working_directory FROM new_session_drafts "
                "  ORDER BY sequence DESC LIMIT ?)",
                (keep_newest,),
            )
        return DraftWrite(new_session_draft, False)


class SqliteTaskDismissalRepository(TaskDismissalRepository):
    def __init__(self, sqlite_database: SqliteDatabase) -> None:
        self.sqlite_database = sqlite_database

    def dismissed_task_ids(self, session_id: SessionId) -> frozenset[TaskId]:
        with self.sqlite_database.read() as connection:
            found = connection.execute(
                "SELECT task_id FROM task_dismissals WHERE session_id=?", (str(session_id),)
            ).fetchall()
        return frozenset(TaskId(row["task_id"]) for row in found)

    def dismiss(
        self,
        session_id: SessionId,
        task_ids: Sequence[TaskId],
        dismissed_at: float,
        keep_newest: int,
    ) -> None:
        with self.sqlite_database.write() as connection:
            connection.execute(
                "DELETE FROM task_dismissals WHERE session_id=?", (str(session_id),)
            )
            connection.executemany(
                "INSERT INTO task_dismissals(session_id, task_id, dismissed_at) VALUES(?, ?, ?)",
                tuple(
                    (str(session_id), str(task_id), dismissed_at) for task_id in task_ids
                ),
            )
            # Bound by SESSION, not by row: a finished task list is dismissed
            # for most sessions eventually, and the map would otherwise gain a
            # row per session forever.
            connection.execute(
                "DELETE FROM task_dismissals WHERE session_id NOT IN ("
                "  SELECT session_id FROM task_dismissals "
                "  GROUP BY session_id ORDER BY MAX(dismissed_at) DESC LIMIT ?)",
                (keep_newest,),
            )

    def restore(self, session_id: SessionId) -> None:
        with self.sqlite_database.write() as connection:
            connection.execute(
                "DELETE FROM task_dismissals WHERE session_id=?", (str(session_id),)
            )


class SqlitePushSubscriptionRepository(PushSubscriptionRepository):
    def __init__(self, sqlite_database: SqliteDatabase) -> None:
        self.sqlite_database = sqlite_database

    def subscriptions(self) -> tuple[PushSubscription, ...]:
        with self.sqlite_database.read() as connection:
            found = connection.execute(
                "SELECT * FROM push_subscriptions ORDER BY created_at DESC"
            ).fetchall()
        return tuple(mapper.push_subscription(rows.push_subscription(row)) for row in found)

    def upsert(self, push_subscription: PushSubscription) -> None:
        with self.sqlite_database.write() as connection:
            connection.execute(
                "INSERT INTO push_subscriptions(endpoint, public_key, authentication_secret, "
                "device_id, device_label, created_at) VALUES(?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(endpoint) DO UPDATE SET "
                "public_key=excluded.public_key, "
                "authentication_secret=excluded.authentication_secret, "
                "device_id=excluded.device_id, device_label=excluded.device_label, "
                "created_at=excluded.created_at",
                mapper.push_subscription_values(push_subscription),
            )

    def remove(self, endpoint: str) -> None:
        with self.sqlite_database.write() as connection:
            connection.execute(
                "DELETE FROM push_subscriptions WHERE endpoint=?", (str(endpoint),)
            )


class SqlitePushSigningKeyRepository(PushSigningKeyRepository):
    def __init__(self, sqlite_database: SqliteDatabase) -> None:
        self.sqlite_database = sqlite_database

    def keypair(self) -> PushSigningKeypair | None:
        with self.sqlite_database.read() as connection:
            row = connection.execute("SELECT * FROM push_signing_keys WHERE id=1").fetchone()
        return mapper.push_signing_keypair(rows.push_signing_key(row)) if row is not None else None

    def save_keypair(self, push_signing_keypair: PushSigningKeypair) -> None:
        with self.sqlite_database.write() as connection:
            connection.execute(
                "INSERT INTO push_signing_keys(id, private_key_pem, public_key) "
                "VALUES(1, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                "private_key_pem=excluded.private_key_pem, public_key=excluded.public_key",
                (push_signing_keypair.private_key_pem, push_signing_keypair.public_key),
            )
