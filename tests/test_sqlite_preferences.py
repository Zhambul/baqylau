# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide test sqlite preferences."""

from __future__ import annotations

from tests import (
    sqlite_domain_dependencies as domain_dependencies,
    sqlite_repository_dependencies as repository_dependencies,
    sqlite_test_dependencies as test_dependencies,
    sqlite_test_shells,
    sqlite_value_dependencies as standard_dependencies,
)
from tests.harness_names import CODEX_HARNESS

SESSION = domain_dependencies.domain_ids.SessionId("session-one")
DISMISSED_TASK = domain_dependencies.domain_ids.TaskId("t")
HARNESS = CODEX_HARNESS
NEWER_PREFERENCE_TIME = 2.0
PROJECT_DIRECTORY = "/project"
FIRST_REQUEST_ID = "request-one"
FIRST_MESSAGE_TEXT = "one"
SEND_ORIGIN = "send"
DRAFT_RETENTION_LIMIT = 2
DISMISSAL_SESSION_LIMIT = 2


def test_queued_request_is_idempotent_and_can_be(main: repository_dependencies.SqliteDatabase) -> None:
    """Verify a queued request is idempotent and can be removed by identity."""
    workspace = test_dependencies.SqliteSessionWorkspaceRepository(main)
    message = standard_dependencies.composer.QueuedMessage(
        domain_dependencies.domain_ids.RequestId(FIRST_REQUEST_ID), FIRST_MESSAGE_TEXT,
    )
    workspace.enqueue_composer_message(SESSION, message, SEND_ORIGIN)
    workspace.enqueue_composer_message(SESSION, message, SEND_ORIGIN)
    stored = workspace.find(SESSION)
    assert stored is not None
    assert stored.queue is not None
    assert stored.queue.messages == (message,)
    workspace.remove_queued_message(SESSION, domain_dependencies.domain_ids.RequestId(FIRST_REQUEST_ID))
    stored = workspace.find(SESSION)
    assert stored is not None
    assert stored.queue is None


def test_a_view_mode_is_stored_and_cleared(main: repository_dependencies.SqliteDatabase) -> None:
    """Verify a view mode is stored and cleared."""
    view_modes = repository_dependencies.sqlite_preferences.SqliteViewModeRepository(main)
    assert view_modes.view_mode(SESSION) is None
    view_modes.set_view_mode(SESSION, domain_dependencies.preference_models.ViewMode.FOCUS)
    assert view_modes.view_mode(SESSION) == "focus"
    view_modes.clear_view_mode(SESSION)
    assert view_modes.view_mode(SESSION) is None


def test_alerting_defaults_on_and_mutes_come_back(main: repository_dependencies.SqliteDatabase) -> None:
    """Verify alerting defaults on and mutes come back in one call."""
    notifications = repository_dependencies.sqlite_preferences.SqliteNotificationSettingRepository(main)
    assert notifications.alerting_enabled()
    notifications.set_alerting_enabled(enabled=False)
    assert not notifications.alerting_enabled()
    notifications.set_muted(SESSION, muted=True)
    assert notifications.muted_session_ids() == frozenset((SESSION,))
    notifications.set_muted(SESSION, muted=False)
    assert notifications.muted_session_ids() == frozenset()


def test_hiding_dir_twice_keeps_newer_stamp(main: repository_dependencies.SqliteDatabase) -> None:
    """Verify hiding a directory twice keeps the newer stamp."""
    directories = repository_dependencies.sqlite_preferences.SqliteHiddenDirectoryRepository(main)
    directories.hide(PROJECT_DIRECTORY, 1.0)
    directories.hide(PROJECT_DIRECTORY, NEWER_PREFERENCE_TIME)
    assert [(entry.working_directory, entry.hidden_at) for entry in directories.hidden()] == [
        (PROJECT_DIRECTORY, NEWER_PREFERENCE_TIME),
    ]


def test_stale_new_session_draft_is_rejected(main: repository_dependencies.SqliteDatabase) -> None:
    """Verify a stale new session draft is rejected and the map is pruned."""
    new_sessions = repository_dependencies.sqlite_preferences.SqliteNewSessionRepository(main)
    assert not new_sessions.save_draft(
        domain_dependencies.preference_models.NewSessionDraft("/a", "newer", NEWER_PREFERENCE_TIME), 10,
    ).stale
    assert new_sessions.save_draft(domain_dependencies.preference_models.NewSessionDraft("/a", "older", 1.0), 10).stale
    assert [draft.text for draft in new_sessions.drafts()] == ["newer"]
    for index in range(5):
        draft = domain_dependencies.preference_models.NewSessionDraft(f"/dir{index}", "x", float(index + 10))
        new_sessions.save_draft(draft, DRAFT_RETENTION_LIMIT)
    assert len(new_sessions.drafts()) == DRAFT_RETENTION_LIMIT


def test_new_session_preferences_round_trip(main: repository_dependencies.SqliteDatabase) -> None:
    """Verify new session preferences round trip."""
    new_sessions = repository_dependencies.sqlite_preferences.SqliteNewSessionRepository(main)
    assert new_sessions.preferences() is None
    new_sessions.save_preferences(
        domain_dependencies.preference_models.NewSessionPreferences(PROJECT_DIRECTORY, HARNESS, "opus", "high"),
    )
    assert new_sessions.preferences() == domain_dependencies.preference_models.NewSessionPreferences(
        PROJECT_DIRECTORY, HARNESS, "opus", "high",
    )


def test_task_dismissals_store_id_set_and_prune(main: repository_dependencies.SqliteDatabase) -> None:
    """Verify task dismissals store the identifier set and prune by session."""
    dismissals = repository_dependencies.sqlite_preferences.SqliteTaskDismissalRepository(main)
    dismissed_task_ids = [
        domain_dependencies.domain_ids.TaskId("t1"),
        domain_dependencies.domain_ids.TaskId("t2"),
    ]
    dismissals.dismiss(SESSION, dismissed_task_ids, 1.0, 10)
    assert dismissals.dismissed_task_ids(SESSION) == frozenset(dismissed_task_ids)
    dismissals.restore(SESSION)
    assert dismissals.dismissed_task_ids(SESSION) == frozenset()
    for index in range(4):
        session_id = domain_dependencies.domain_ids.SessionId(f"s{index}")
        dismissals.dismiss(session_id, [DISMISSED_TASK], float(index), DISMISSAL_SESSION_LIMIT)
    remaining = sqlite_test_shells.dismissed_session_ids(dismissals)
    assert len(remaining) == DISMISSAL_SESSION_LIMIT
