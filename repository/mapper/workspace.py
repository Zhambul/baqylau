"""Row DTOs to a session's unsent work.

The two JSON columns this used to encode and decode — `queued_messages` and
`dialog_answers` — are tables now, so what is left is assembly.
"""

from __future__ import annotations

from domain.ids import AttentionId, SessionId
from domain.workspace import (
    AnswerSelection,
    ComposerDraft,
    ComposerQueue,
    DialogDraft,
    QueuedMessage,
    SessionWorkspace,
)
from repository.model.workspace import (
    ComposerQueueItemRow,
    DialogAnswerRow,
    DialogAnswerSelectionRow,
    SessionWorkspaceRow,
)
from repository.model.sql import SqlValues


def session_workspace(
    session_workspace_row: SessionWorkspaceRow,
    queue_items: tuple[ComposerQueueItemRow, ...],
    answers: tuple[DialogAnswerRow, ...],
    selections: tuple[DialogAnswerSelectionRow, ...],
) -> SessionWorkspace:
    return SessionWorkspace(
        session_id=SessionId(session_workspace_row.session_id),
        draft=_draft(session_workspace_row),
        queue=_queue(session_workspace_row, queue_items),
        dialog=_dialog(session_workspace_row, answers, selections),
    )


def _draft(session_workspace_row: SessionWorkspaceRow) -> ComposerDraft | None:
    if not session_workspace_row.composer_text.strip():
        return None
    return ComposerDraft(
        session_workspace_row.composer_text,
        session_workspace_row.composer_origin,
        session_workspace_row.composer_sequence,
    )


def _queue(
    session_workspace_row: SessionWorkspaceRow,
    queue_items: tuple[ComposerQueueItemRow, ...],
) -> ComposerQueue | None:
    messages = tuple(
        QueuedMessage(item.request_id, item.text)
        for item in sorted(queue_items, key=lambda item: item.position)
    )
    return ComposerQueue(messages, session_workspace_row.queue_origin) if messages else None


def _dialog(
    session_workspace_row: SessionWorkspaceRow,
    answers: tuple[DialogAnswerRow, ...],
    selections: tuple[DialogAnswerSelectionRow, ...],
) -> DialogDraft | None:
    if session_workspace_row.dialog_attention_id is None:
        return None
    by_prompt: dict[int, list[DialogAnswerSelectionRow]] = {}
    for selection in selections:
        by_prompt.setdefault(selection.prompt_index, []).append(selection)
    selected = tuple(
        AnswerSelection(
            tuple(
                value.selected_value
                for value in sorted(
                    by_prompt.get(answer.prompt_index, ()),
                    key=lambda value: value.selection_index,
                )
            ),
            answer.other_text,
        )
        for answer in sorted(answers, key=lambda answer: answer.prompt_index)
    )
    return DialogDraft(
        AttentionId(session_workspace_row.dialog_attention_id),
        selected,
        session_workspace_row.dialog_origin,
    )


def dialog_answer_values(
    session_id: SessionId,
    dialog_draft: DialogDraft,
) -> tuple[SqlValues, ...]:
    return tuple(
        (str(session_id), prompt_index, answer.other)
        for prompt_index, answer in enumerate(dialog_draft.answers)
    )


def dialog_selection_values(
    session_id: SessionId,
    dialog_draft: DialogDraft,
) -> tuple[SqlValues, ...]:
    return tuple(
        (str(session_id), prompt_index, selection_index, value)
        for prompt_index, answer in enumerate(dialog_draft.answers)
        for selection_index, value in enumerate(answer.selected)
    )
