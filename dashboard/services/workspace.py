"""Your unsent work on one session: drafts, a queue, a half-made choice.

The session page keeps state the session itself never sees — the message you
are still typing, the ones you queued behind it, the option you highlighted in
a dialog, the view density you chose. It lives here so a reload, a second tab
or another device picks up exactly where the last one left off.

The STORING is the repository's; what is left here is the filtering, which
needs canonical facts: a draft is dropped once its text has actually been
DELIVERED as a prompt, and a dialog draft is dropped once its attention stops
being pending.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

from audit.models import ApplicationError
from domain.ids import AttentionId, SessionId
from domain.preferences import DEFAULT_VIEW_MODE, ViewMode
from domain.values import TextContent
from domain.workspace import (
    AnswerSelection,
    ComposerDraft,
    ComposerQueue,
    ComposerState,
    DialogDraft,
    DialogState,
    QueuedMessage,
)
from domain.entries import MessageBody, QuestionAskedBody
from domain.sessiondata import SessionTask
from domain.values import AttentionPrompt
from repository.contract.session_data import SessionDataRepository
from harness.models import TerminalSessionState
from repository.contract.audit import AuditReadRepository
from repository.contract.preferences import (
    NotificationSettingRepository,
    TaskDismissalRepository,
    ViewModeRepository,
)
from repository.contract.workspace import SessionWorkspaceRepository


class TerminalSessionReader(Protocol):
    """The one thing this service asks a terminal: where the session is, if it
    is anywhere. Declared here, like `app/services/resume.py` declares its own —
    a two-line protocol is cheaper stated twice than imported across a tier."""

    def state(self, session_id: SessionId) -> TerminalSessionState: ...

# A finished task list is dismissed for most sessions eventually, so the table
# is bounded by session; the prune runs inside the write that triggers it.
TASK_DISMISSAL_LIMIT = 200


@dataclass(frozen=True)
class SessionPreferences:
    view_mode: ViewMode
    notifications_muted: bool
    tasks_hidden: bool


@dataclass(frozen=True)
class SessionApplicationSnapshot:
    preferences: SessionPreferences
    composer: ComposerState
    dialog: DialogState
    terminal: TerminalSessionState
    errors: tuple[ApplicationError, ...]


class SessionApplicationService:
    def __init__(
        self,
        read_model: SessionDataRepository,
        terminal: TerminalSessionReader,
        audit: AuditReadRepository,
        workspaces: SessionWorkspaceRepository,
        view_modes: ViewModeRepository,
        notifications: NotificationSettingRepository,
        dismissals: TaskDismissalRepository,
        clock=None,
    ) -> None:
        self.read_model = read_model
        self.terminal = terminal
        self.audit = audit
        self.workspaces = workspaces
        self.view_modes = view_modes
        self.notifications = notifications
        self.dismissals = dismissals
        self.clock = clock or time.time

    # --- what you chose -------------------------------------------------------

    def set_view_mode(self, session_id: SessionId, view_mode: str) -> None:
        if view_mode == DEFAULT_VIEW_MODE:
            # The default is stored as an ABSENCE, so the table stays the small
            # set of sessions someone actually switched.
            self.view_modes.clear_view_mode(session_id)
            return
        if view_mode not in ("verbose", "focus"):
            raise ValueError(f"unknown view mode: {view_mode}")
        mode: ViewMode = view_mode  # type: ignore[assignment]
        self.view_modes.set_view_mode(session_id, mode)

    def set_notifications_muted(self, session_id: SessionId, muted: bool) -> None:
        self.notifications.set_muted(session_id, muted)

    def set_tasks_hidden(self, session_id: SessionId, hidden: bool) -> None:
        tasks = self._tasks(session_id)
        if hidden and (not tasks or any(task.state != "completed" for task in tasks)):
            raise ValueError("every task must be completed before hiding the task card")
        if not hidden:
            self.dismissals.restore(session_id)
            return
        self.dismissals.dismiss(
            session_id,
            [task.task_id for task in tasks],
            self.clock(),
            TASK_DISMISSAL_LIMIT,
        )

    # --- what you have not sent yet -------------------------------------------

    def save_composer_draft(
        self,
        session_id: SessionId,
        text: str,
        origin: str,
        sequence: float,
    ) -> bool:
        """Save the newest browser draft; return False for an older concurrent write."""
        return self.workspaces.save_composer_draft(
            session_id, ComposerDraft(text, origin, sequence)
        )

    def save_composer_queue(
        self,
        session_id: SessionId,
        messages: tuple[QueuedMessage, ...],
        origin: str,
    ) -> None:
        self.workspaces.save_composer_queue(session_id, ComposerQueue(messages, origin))

    def save_dialog_draft(
        self,
        session_id: SessionId,
        attention_id: AttentionId,
        answers: tuple[AnswerSelection, ...],
        origin: str,
    ) -> None:
        questions = self._pending_questions(session_id).get(attention_id)
        if questions is None:
            raise ValueError("attention is no longer pending")
        if len(answers) != len(questions):
            raise ValueError("answers must match the pending questions")
        self.workspaces.save_dialog_draft(
            session_id, DialogDraft(attention_id, answers, origin)
        )

    # --- the whole page's state in one answer ---------------------------------

    def snapshot(self, session_id: SessionId) -> SessionApplicationSnapshot:
        composer, dialog = self._state(session_id)
        tasks = self._tasks(session_id)
        dismissed = self.dismissals.dismissed_task_ids(session_id)
        return SessionApplicationSnapshot(
            preferences=SessionPreferences(
                view_mode=self.view_modes.view_mode(session_id) or DEFAULT_VIEW_MODE,
                notifications_muted=session_id in self.notifications.muted_session_ids(),
                # The dismissal covers exactly the list it was made against, so
                # a new task — or a completed one re-opened — brings the card
                # back on its own.
                tasks_hidden=(
                    bool(tasks) and dismissed == {task.task_id for task in tasks}
                ),
            ),
            composer=composer,
            dialog=dialog,
            terminal=self.terminal.state(session_id),
            errors=self.audit.errors_for_session(session_id),
        )

    def _state(self, session_id: SessionId) -> tuple[ComposerState, DialogState]:
        workspace = self.workspaces.find(session_id)
        if workspace is None:
            return ComposerState(None, None), DialogState(None)

        delivered = self._delivered_prompts(session_id)
        queue = None
        if workspace.queue is not None:
            messages = tuple(
                message
                for message in workspace.queue.items
                if message.text.strip() and not self._delivered(message.text, delivered)
            )
            queue = ComposerQueue(messages, workspace.queue.origin) if messages else None

        pending_attention_ids = set(self._pending_questions(session_id))
        dialog_draft = workspace.dialog
        if dialog_draft is not None and dialog_draft.attention_id not in pending_attention_ids:
            dialog_draft = None
        return ComposerState(workspace.draft, queue), DialogState(dialog_draft)

    def _tasks(self, session_id: SessionId) -> tuple[SessionTask, ...]:
        data = self.read_model.read(session_id)
        return () if data is None else data.session.tasks

    def _pending_questions(
        self, session_id: SessionId
    ) -> dict[AttentionId, tuple[AttentionPrompt, ...]]:
        """The questions still waiting on a person, by attention.

        A plan is pending attention too, but it carries no questions to answer —
        a dialog draft against one would have nothing to hold.
        """
        return {
            entry.body.attention_id: entry.body.questions
            for entry in self.read_model.pending_attention(session_id)
            if isinstance(entry.body, QuestionAskedBody)
        }

    def _delivered_prompts(self, session_id: SessionId) -> tuple[str, ...]:
        """Every prompt this session has actually received.

        Read from the feed, which is where a delivered prompt IS: a queued
        message whose text already arrived was sent, and showing it as still
        queued is how a person sends it twice.
        """
        prompts = []
        for entry in self.read_model.entries_of_types(session_id, ("message",)):
            body = entry.body
            if (
                isinstance(body, MessageBody)
                and body.role == "user"
                and body.phase == "prompt"
                and isinstance(body.content, TextContent)
            ):
                text = body.content.text.strip()
                if text:
                    prompts.append(text)
        return tuple(prompts)

    @staticmethod
    def _delivered(text: str, prompts: tuple[str, ...]) -> bool:
        normalized = text.strip()
        return bool(normalized) and any(prompt.endswith(normalized) for prompt in prompts)
