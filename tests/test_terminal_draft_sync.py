"""Terminal drafts use the same durable composer state as the web page."""

from __future__ import annotations

from typing import Any, cast

from domain.ids import SessionId, WindowId
from domain.workspace import ComposerDraft, SessionWorkspace
from dashboard.services.workspace import SessionApplicationService
from harness.models import TerminalInputState, TerminalSessionState


class TerminalStates:
    def __init__(self, text: str) -> None:
        self.text = text

    def state(self, _session_id: SessionId) -> TerminalSessionState:
        return TerminalSessionState(
            WindowId("window-one"),
            TerminalInputState(self.text, None),
        )


class Workspaces:
    def __init__(self) -> None:
        self.rows: dict[SessionId, SessionWorkspace] = {}

    def find(self, session_id: SessionId) -> SessionWorkspace | None:
        return self.rows.get(session_id)

    def save_composer_draft(
        self,
        session_id: SessionId,
        draft: ComposerDraft,
    ) -> bool:
        current = self.rows.get(session_id)
        if current is not None and current.draft is not None:
            if draft.sequence < current.draft.sequence:
                return False
        self.rows[session_id] = SessionWorkspace(
            session_id,
            None if not draft.text.strip() else draft,
        )
        return True


class ReadModel:
    def read(self, _session_id):
        return None

    def pending_attention(self, _session_id):
        return ()


class AuditReads:
    def errors_for_session(self, _session_id):
        return ()


class ViewModes:
    def view_mode(self, _session_id):
        return None


class NotificationSettings:
    def muted_session_ids(self):
        return set()


class TaskDismissals:
    def dismissed_task_ids(self, _session_id):
        return set()


def service(states: TerminalStates, workspaces: Workspaces, times: list[float]):
    return SessionApplicationService(
        cast(Any, ReadModel()),
        states,
        cast(Any, AuditReads()),
        cast(Any, workspaces),
        cast(Any, ViewModes()),
        cast(Any, NotificationSettings()),
        cast(Any, TaskDismissals()),
        clock=lambda: times.pop(0),
    )


def test_an_unchanged_terminal_draft_does_not_overwrite_a_web_edit() -> None:
    session_id = SessionId("session-one")
    states = TerminalStates("test")
    workspaces = Workspaces()
    application = service(states, workspaces, [1.0, 3.0])

    first = application.snapshot(session_id)
    assert first.composer.draft == ComposerDraft("test", "terminal", 1000)

    workspaces.save_composer_draft(
        session_id,
        ComposerDraft("web edit", "browser-one", 2000),
    )
    unchanged = application.snapshot(session_id)
    assert unchanged.composer.draft == ComposerDraft(
        "web edit", "browser-one", 2000
    )

    states.text = "terminal edit"
    changed = application.snapshot(session_id)
    assert changed.composer.draft == ComposerDraft(
        "terminal edit", "terminal", 3000
    )


def test_an_empty_terminal_clears_only_a_terminal_owned_draft() -> None:
    session_id = SessionId("session-one")
    states = TerminalStates("test")
    workspaces = Workspaces()
    application = service(states, workspaces, [1.0, 2.0])

    application.snapshot(session_id)
    states.text = ""
    assert application.snapshot(session_id).composer.draft is None

    workspaces.save_composer_draft(
        session_id,
        ComposerDraft("web edit", "browser-one", 3000),
    )
    assert application.snapshot(session_id).composer.draft == ComposerDraft(
        "web edit", "browser-one", 3000
    )
