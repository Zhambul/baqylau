# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide session application operations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, cast

from dashboard.services import terminal_drafts, workspace_attention, workspace_models
from domain import composer, dialogs, preferences

if TYPE_CHECKING:
    from collections.abc import Callable

    from app import session_application_resources as resources
    from domain.entries import SessionEntry
    from domain.ids import AttentionId, SessionId
    from domain.session_state import SessionTask

TASK_DISMISSAL_LIMIT = 200


def _tasks_can_hide(tasks: tuple[SessionTask, ...]) -> bool:
    return bool(tasks) and all(task.state == "completed" for task in tasks)


class SessionApplicationContext(Protocol):
    """Provide session application dependencies."""

    core: resources.SessionApplicationCore
    rules: resources.SessionApplicationRules
    clock: Callable[[], float]
    terminal_drafts: terminal_drafts.TerminalDraftSync

    def _preferences(
        self,
        session_id: SessionId,
        tasks: tuple[SessionTask, ...],
    ) -> workspace_models.SessionPreferences:
        """Build session preferences."""

    def _state(
        self,
        session_id: SessionId,
        pending_attention: tuple[SessionEntry, ...],
    ) -> tuple[composer.ComposerState, dialogs.DialogState]:
        """Build draft state."""


class SessionPreferenceOperations:
    """Provide session preference write operations."""

    def dismiss_goal(self: SessionApplicationContext, session_id: SessionId, objective: str) -> None:
        """Hide the matching completed goal."""
        self.rules.goal_dismissal_repository.dismiss(session_id, objective)

    def set_view_mode(self: SessionApplicationContext, session_id: SessionId, view_mode: str) -> None:
        """Set the session view mode.

        Raises:
            ValueError: If the requested view mode is not supported.

        """
        repository = self.core.view_mode_repository
        if view_mode == preferences.DEFAULT_VIEW_MODE:
            repository.clear_view_mode(session_id)
            return
        if view_mode not in {"verbose", "focus"}:
            msg = f"unknown view mode: {view_mode}"
            raise ValueError(msg)
        view_mode_value = cast("preferences.ViewMode", view_mode)
        repository.set_view_mode(session_id, view_mode_value)

    def set_notifications_muted(self: SessionApplicationContext, session_id: SessionId, *, muted: bool) -> None:
        """Set session notification muting."""
        self.rules.notification_setting_repository.set_muted(session_id, muted=muted)

    def set_tasks_hidden(self: SessionApplicationContext, session_id: SessionId, *, hidden: bool) -> None:
        """Set task-card visibility.

        Raises:
            ValueError: If hiding is requested for an empty or incomplete task list.

        """
        tasks = _tasks(self, session_id)
        repository = self.rules.task_dismissal_repository
        if hidden and not _tasks_can_hide(tasks):
            msg = "every task must be completed before hiding the task card"
            raise ValueError(msg)
        if not hidden:
            repository.restore(session_id)
            return
        repository.dismiss(session_id, [task.task_id for task in tasks], self.clock(), TASK_DISMISSAL_LIMIT)


class SessionDraftOperations:
    """Provide session draft write operations."""

    def save_composer_draft(
        self: SessionApplicationContext,
        session_id: SessionId,
        text: str,
        origin: str,
        sequence: float,
    ) -> bool:
        """Save a browser composer draft.

        Returns:
            True if the repository accepts the draft sequence.

        """
        return self.core.workspace_repository.save_composer_draft(
            session_id,
            composer.ComposerDraft(text, origin, sequence),
        )

    def save_dialog_draft(
        self: SessionApplicationContext,
        session_id: SessionId,
        attention_id: AttentionId,
        answers: tuple[dialogs.AnswerSelection, ...],
        origin: str,
    ) -> None:
        """Save a browser dialog draft.

        Raises:
            ValueError: If the attention is no longer pending or the answer count does not match.

        """
        questions = workspace_attention.pending_questions(
            self.core.session_data_repository.pending_attention(session_id),
        ).get(attention_id)
        if questions is None:
            msg = "attention is no longer pending"
            raise ValueError(msg)
        if len(answers) != len(questions):
            msg = "answers must match the pending questions"
            raise ValueError(msg)
        self.core.workspace_repository.save_dialog_draft(
            session_id,
            dialogs.DialogDraft(attention_id, answers, origin),
        )


def _tasks(session_application_context: SessionApplicationContext, session_id: SessionId) -> tuple[SessionTask, ...]:
    session_record = session_application_context.core.session_data_repository.read(session_id)
    return () if session_record is None else session_record.session.tasks


class SessionSnapshotOperations(SessionApplicationContext):
    """Provide session page-state read operations."""

    def snapshot(self: SessionApplicationContext, session_id: SessionId) -> workspace_models.SessionApplicationSnapshot:
        """Return the session application snapshot.

        Returns:
            The session application snapshot.

        """
        pending_attention = self.core.session_data_repository.pending_attention(session_id)
        terminal = self.terminal_drafts.state(session_id, attention_pending=bool(pending_attention))
        composer_state, dialog_state = self._state(session_id, pending_attention)
        tasks = _tasks(self, session_id)
        return workspace_models.SessionApplicationSnapshot(
            preferences=self._preferences(session_id, tasks),
            composer=composer_state,
            dialog=dialog_state,
            terminal=terminal,
            errors=self.core.audit_read_repository.errors_for_session(session_id),
        )

    def _preferences(
        self: SessionApplicationContext,
        session_id: SessionId,
        tasks: tuple[SessionTask, ...],
    ) -> workspace_models.SessionPreferences:
        dismissed = self.rules.task_dismissal_repository.dismissed_task_ids(session_id)
        return workspace_models.SessionPreferences(
            view_mode=self.core.view_mode_repository.view_mode(session_id) or preferences.DEFAULT_VIEW_MODE,
            notifications_muted=session_id in self.rules.notification_setting_repository.muted_session_ids(),
            tasks_hidden=bool(tasks) and dismissed == {task.task_id for task in tasks},
            goal_hidden=self.rules.goal_dismissal_repository.hidden(session_id),
        )

    def _state(
        self: SessionApplicationContext,
        session_id: SessionId,
        pending_attention: tuple[SessionEntry, ...],
    ) -> tuple[composer.ComposerState, dialogs.DialogState]:
        workspace = self.core.workspace_repository.find(session_id)
        if workspace is None:
            return composer.ComposerState(None, None), dialogs.DialogState(None)
        pending_attention_ids = set(workspace_attention.pending_questions(pending_attention))
        dialog_draft = workspace.dialog
        if dialog_draft is not None and dialog_draft.attention_id not in pending_attention_ids:
            dialog_draft = None
        return composer.ComposerState(workspace.draft, workspace.queue), dialogs.DialogState(dialog_draft)
