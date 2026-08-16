"""Your unsent work on one session: drafts, a queue, a half-made choice.

The session page keeps state the session itself never sees — the message you
are still typing, the ones you queued behind it, the option you highlighted in
a dialog, the view density you chose. It lives here so a reload, a second tab
or another device picks up exactly where the last one left off.

A draft is dropped once its text has actually been DELIVERED as a prompt: that
comparison against canonical messages is why this reads the event store.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from dashboard import prefs
from diagnostics.read import ApplicationError, OperationalDiagnostics
from domain.events import MessageCreated
from domain.ids import AttentionId, SessionId
from domain.values import TextContent
from engine.projections import SessionQueries
from engine.store.canonical import CanonicalEventStore
from engine.store.database import connect
from harness.models import TerminalSessionState
from dashboard.services.sessions import TerminalSessionReader


@dataclass(frozen=True)
class SessionPreferences:
    view_mode: str
    notifications_muted: bool
    tasks_hidden: bool


@dataclass(frozen=True)
class ComposerDraft:
    text: str
    origin: str
    sequence: float


@dataclass(frozen=True)
class QueuedMessage:
    text: str


@dataclass(frozen=True)
class ComposerQueue:
    items: tuple[QueuedMessage, ...]
    origin: str


@dataclass(frozen=True)
class ComposerState:
    draft: ComposerDraft | None
    queue: ComposerQueue | None


@dataclass(frozen=True)
class AnswerSelection:
    selected: tuple[str, ...]
    other: str


@dataclass(frozen=True)
class DialogDraft:
    attention_id: AttentionId
    answers: tuple[AnswerSelection, ...]
    origin: str


@dataclass(frozen=True)
class DialogState:
    draft: DialogDraft | None


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
        canonical_store: CanonicalEventStore,
        queries: SessionQueries,
        terminal: TerminalSessionReader,
        diagnostics: OperationalDiagnostics,
    ) -> None:
        self.canonical_store = canonical_store
        self.queries = queries
        self.terminal = terminal
        self.diagnostics = diagnostics

    def set_view_mode(self, session_id: SessionId, view_mode: str) -> None:
        if view_mode not in prefs.VIEW_MODES:
            raise ValueError(f"unknown view mode: {view_mode}")
        prefs.set_view_mode(str(session_id), view_mode)

    def set_notifications_muted(self, session_id: SessionId, muted: bool) -> None:
        prefs.set_notify_muted(str(session_id), muted)

    def set_tasks_hidden(self, session_id: SessionId, hidden: bool) -> None:
        tasks = self.queries.tasks(session_id)
        if hidden and (not tasks or any(task.state != "completed" for task in tasks)):
            raise ValueError("every task must be completed before hiding the task card")
        prefs.set_tasks_hidden(
            str(session_id),
            [str(task.task_id) for task in tasks] if hidden else None,
        )

    def save_composer_draft(
        self,
        session_id: SessionId,
        text: str,
        origin: str,
        sequence: float,
    ) -> bool:
        """Save the newest browser draft; return False for an older concurrent write."""
        with connect(self.canonical_store.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._ensure_row(connection, session_id)
            current = connection.execute(
                "SELECT composer_sequence FROM session_application_state WHERE session_id=?",
                (str(session_id),),
            ).fetchone()
            if sequence < current["composer_sequence"]:
                return False
            connection.execute(
                "UPDATE session_application_state SET composer_text=?, composer_origin=?, "
                "composer_sequence=? WHERE session_id=?",
                (text if text.strip() else "", origin, sequence, str(session_id)),
            )
        return True

    def save_composer_queue(
        self,
        session_id: SessionId,
        messages: tuple[QueuedMessage, ...],
        origin: str,
    ) -> None:
        encoded = json.dumps(
            [{"text": message.text} for message in messages],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        with connect(self.canonical_store.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._ensure_row(connection, session_id)
            connection.execute(
                "UPDATE session_application_state SET queued_messages=?, queue_origin=? "
                "WHERE session_id=?",
                (encoded, origin, str(session_id)),
            )

    def save_dialog_draft(
        self,
        session_id: SessionId,
        attention_id: AttentionId,
        answers: tuple[AnswerSelection, ...],
        origin: str,
    ) -> None:
        pending = {
            item.request.attention_id: item.request
            for item in self.queries.attention(session_id).pending
        }
        request = pending.get(attention_id)
        if request is None:
            raise ValueError("attention is no longer pending")
        if len(answers) != len(request.prompts):
            raise ValueError("answers must match the pending questions")
        encoded = json.dumps(
            [
                {"selected": list(answer.selected), "other": answer.other}
                for answer in answers
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        with connect(self.canonical_store.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._ensure_row(connection, session_id)
            connection.execute(
                "UPDATE session_application_state SET dialog_attention_id=?, "
                "dialog_answers=?, dialog_origin=? WHERE session_id=?",
                (str(attention_id), encoded, origin, str(session_id)),
            )

    def snapshot(self, session_id: SessionId) -> SessionApplicationSnapshot:
        composer, dialog = self._state(session_id)
        tasks = self.queries.tasks(session_id)
        hidden_task_ids = set(prefs.tasks_hidden_ids(str(session_id)))
        return SessionApplicationSnapshot(
            preferences=SessionPreferences(
                view_mode=prefs.view_mode(str(session_id)),
                notifications_muted=prefs.notify_muted(str(session_id)),
                tasks_hidden=(
                    bool(tasks)
                    and hidden_task_ids == {str(task.task_id) for task in tasks}
                ),
            ),
            composer=composer,
            dialog=dialog,
            terminal=self.terminal.state(session_id),
            errors=self.diagnostics.errors(session_id),
        )

    def _state(self, session_id: SessionId) -> tuple[ComposerState, DialogState]:
        with connect(self.canonical_store.database_path) as connection:
            row = connection.execute(
                "SELECT * FROM session_application_state WHERE session_id=?",
                (str(session_id),),
            ).fetchone()
        if row is None:
            return ComposerState(None, None), DialogState(None)

        draft = (
            ComposerDraft(row["composer_text"], row["composer_origin"], row["composer_sequence"])
            if row["composer_text"].strip()
            else None
        )
        delivered = self._delivered_prompts(session_id)
        messages = tuple(
            QueuedMessage(str(item["text"]))
            for item in json.loads(row["queued_messages"])
            if str(item["text"]).strip() and not self._delivered(str(item["text"]), delivered)
        )
        queue = ComposerQueue(messages, row["queue_origin"]) if messages else None

        pending_attention_ids = {
            item.request.attention_id for item in self.queries.attention(session_id).pending
        }
        attention_id = (
            AttentionId(row["dialog_attention_id"])
            if row["dialog_attention_id"] is not None
            else None
        )
        dialog_draft = None
        if attention_id in pending_attention_ids:
            dialog_draft = DialogDraft(
                attention_id,
                tuple(
                    AnswerSelection(tuple(answer["selected"]), str(answer["other"]))
                    for answer in json.loads(row["dialog_answers"])
                ),
                row["dialog_origin"],
            )
        return ComposerState(draft, queue), DialogState(dialog_draft)

    def _delivered_prompts(self, session_id: SessionId) -> tuple[str, ...]:
        prompts = []
        for stored in self.canonical_store.through(session_id).events:
            payload = stored.event.payload
            if (
                isinstance(payload, MessageCreated)
                and payload.role == "user"
                and payload.phase == "prompt"
                and isinstance(payload.content, TextContent)
            ):
                text = payload.content.text.strip()
                if text:
                    prompts.append(text)
        return tuple(prompts)

    @staticmethod
    def _delivered(text: str, prompts: tuple[str, ...]) -> bool:
        normalized = text.strip()
        return bool(normalized) and any(prompt.endswith(normalized) for prompt in prompts)

    @staticmethod
    def _ensure_row(connection, session_id: SessionId) -> None:
        connection.execute(
            "INSERT OR IGNORE INTO session_application_state(session_id) VALUES(?)",
            (str(session_id),),
        )
