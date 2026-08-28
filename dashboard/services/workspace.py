"""Composer state for one session: local drafts and the harness queue mirror.

The session does not see a draft or an incomplete dialog answer. The harness
does see queued messages and owns their delivery. Baqylau keeps a durable
mirror of that queue so a reload, a second tab, or another device shows the
same state.

The STORING is the repository's; what is left here is the filtering, which
needs canonical facts: a queued item is added after `message.queued`, a draft
and one matching queue item are dropped after the real prompt arrives, and a
dialog draft is dropped once its attention stops being pending.
"""

from __future__ import annotations

import time
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from audit.models import ApplicationError
from domain.events import (
    CanonicalEvent,
    EventPayload,
    MessageCreated,
    MessageQueued,
)
from domain.ids import AttentionId, SessionId
from domain.preferences import DEFAULT_VIEW_MODE, ViewMode
from domain.values import MessagePhase, MessageRole, content_text
from domain.workspace import (
    AnswerSelection,
    ComposerDraft,
    ComposerState,
    DialogDraft,
    DialogState,
    QueuedMessage,
)
from domain.entries import QuestionAskedBody
from domain.sessiondata import SessionTask
from domain.values import AttentionPrompt
from harness.contract import CanonicalEventReaction
from repository.contract.session_data import SessionDataRepository
from harness.models import TerminalSessionState
from harness.services.terminal_gate import SessionTerminalGate
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
        session_data_repository: SessionDataRepository,
        terminal_session_reader: TerminalSessionReader,
        audit_read_repository: AuditReadRepository,
        session_workspace_repository: SessionWorkspaceRepository,
        view_mode_repository: ViewModeRepository,
        notification_setting_repository: NotificationSettingRepository,
        task_dismissal_repository: TaskDismissalRepository,
        session_terminal_gate: SessionTerminalGate | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.session_data_repository = session_data_repository
        self.terminal_session_reader = terminal_session_reader
        self.audit_read_repository = audit_read_repository
        self.session_workspace_repository = session_workspace_repository
        self.view_mode_repository = view_mode_repository
        self.notification_setting_repository = notification_setting_repository
        self.task_dismissal_repository = task_dismissal_repository
        self.session_terminal_gate = session_terminal_gate or SessionTerminalGate()
        self.clock = clock or time.time
        self._terminal_text: dict[SessionId, str] = {}
        self._terminal_text_lock = threading.Lock()

    # --- what you chose -------------------------------------------------------

    def set_view_mode(self, session_id: SessionId, view_mode: str) -> None:
        if view_mode == DEFAULT_VIEW_MODE:
            # The default is stored as an ABSENCE, so the table stays the small
            # set of sessions someone actually switched.
            self.view_mode_repository.clear_view_mode(session_id)
            return
        if view_mode not in ("verbose", "focus"):
            raise ValueError(f"unknown view mode: {view_mode}")
        mode: ViewMode = view_mode  # type: ignore[assignment]
        self.view_mode_repository.set_view_mode(session_id, mode)

    def set_notifications_muted(self, session_id: SessionId, muted: bool) -> None:
        self.notification_setting_repository.set_muted(session_id, muted)

    def set_tasks_hidden(self, session_id: SessionId, hidden: bool) -> None:
        tasks = self._tasks(session_id)
        if hidden and (not tasks or any(task.state != "completed" for task in tasks)):
            raise ValueError("every task must be completed before hiding the task card")
        if not hidden:
            self.task_dismissal_repository.restore(session_id)
            return
        self.task_dismissal_repository.dismiss(
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
        return self.session_workspace_repository.save_composer_draft(session_id, ComposerDraft(text, origin, sequence))

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
        self.session_workspace_repository.save_dialog_draft(session_id, DialogDraft(attention_id, answers, origin))

    # --- the whole page's state in one answer ---------------------------------

    def snapshot(self, session_id: SessionId) -> SessionApplicationSnapshot:
        self._sync_terminal_draft(session_id)
        composer, dialog = self._state(session_id)
        tasks = self._tasks(session_id)
        dismissed = self.task_dismissal_repository.dismissed_task_ids(session_id)
        return SessionApplicationSnapshot(
            preferences=SessionPreferences(
                view_mode=self.view_mode_repository.view_mode(session_id) or DEFAULT_VIEW_MODE,
                notifications_muted=session_id in self.notification_setting_repository.muted_session_ids(),
                # The dismissal covers exactly the list it was made against, so
                # a new task — or a completed one re-opened — brings the card
                # back on its own.
                tasks_hidden=(bool(tasks) and dismissed == {task.task_id for task in tasks}),
            ),
            composer=composer,
            dialog=dialog,
            terminal=self.terminal_session_reader.state(session_id),
            errors=self.audit_read_repository.errors_for_session(session_id),
        )

    def _sync_terminal_draft(self, session_id: SessionId) -> None:
        """Move a changed terminal draft to the shared composer state."""
        with self.session_terminal_gate.enter(session_id):
            terminal = self.terminal_session_reader.state(session_id)
            state = terminal.input_state
            if state is None or state.typed_text is None:
                return
            text = state.typed_text
            with self._terminal_text_lock:
                known = session_id in self._terminal_text
                previous = self._terminal_text.get(session_id)
                if known and previous == text:
                    return
                self._terminal_text[session_id] = text
            workspace = self.session_workspace_repository.find(session_id)
            draft = None if workspace is None else workspace.draft
            if text:
                self.session_workspace_repository.save_composer_draft(
                    session_id,
                    ComposerDraft(text, "terminal", self.clock() * 1000),
                )
            elif draft is not None and draft.origin == "terminal":
                self.session_workspace_repository.save_composer_draft(
                    session_id,
                    ComposerDraft("", "terminal", self.clock() * 1000),
                )

    def _state(self, session_id: SessionId) -> tuple[ComposerState, DialogState]:
        workspace = self.session_workspace_repository.find(session_id)
        if workspace is None:
            return ComposerState(None, None), DialogState(None)

        queue = workspace.queue

        pending_attention_ids = set(self._pending_questions(session_id))
        dialog_draft = workspace.dialog
        if dialog_draft is not None and dialog_draft.attention_id not in pending_attention_ids:
            dialog_draft = None
        return ComposerState(workspace.draft, queue), DialogState(dialog_draft)

    def _tasks(self, session_id: SessionId) -> tuple[SessionTask, ...]:
        data = self.session_data_repository.read(session_id)
        return () if data is None else data.session.tasks

    def _pending_questions(
        self, session_id: SessionId
    ) -> Mapping[AttentionId, tuple[AttentionPrompt, ...]]:
        """The questions still waiting on a person, by attention.

        A plan is pending attention too, but it carries no questions to answer —
        a dialog draft against one would have nothing to hold.
        """
        pending_questions = {
            entry.body.attention_id: entry.body.questions
            for entry in self.session_data_repository.pending_attention(session_id)
            if isinstance(entry.body, QuestionAskedBody)
        }
        return pending_questions

def _prompt_matches(queued_text: str, delivered_text: str) -> bool:
    normalized = queued_text.strip()
    return bool(normalized) and delivered_text.strip().endswith(normalized)


class QueuedPromptCanonicalEventReaction(CanonicalEventReaction):
    """Keep a read-model mirror of messages in the harness queue."""

    def __init__(
        self,
        session_workspace_repository: SessionWorkspaceRepository,
    ) -> None:
        self.workspaces = session_workspace_repository

    def react(self, canonical_event: CanonicalEvent[EventPayload]) -> None:
        payload = canonical_event.payload
        if isinstance(payload, MessageQueued):
            self.workspaces.enqueue_composer_message(
                canonical_event.session_id,
                QueuedMessage(payload.request_id, content_text(payload.content)),
                "harness",
            )
            return
        workspace = self.workspaces.find(canonical_event.session_id)
        queue = workspace.queue if workspace is not None else None
        if queue is None or not queue.items:
            return
        if (
            not isinstance(payload, MessageCreated)
            or payload.role != MessageRole.USER
            or payload.phase != MessagePhase.PROMPT
        ):
            return
        delivered = content_text(payload.content)
        delivered_queue_item = next(
            (
                message
                for message in queue.items
                if _prompt_matches(message.text, delivered)
            ),
            None,
        )
        if delivered_queue_item is not None:
            self.workspaces.remove_queued_message(
                canonical_event.session_id,
                delivered_queue_item.request_id,
            )
