"""One session's application state — what YOU have on it — to its model.

The list page's equivalent is in `overview.py`, which needs the session rows and
so has to sit above this one.
"""

from __future__ import annotations

from api.common.mapper import values
from api.dashboard.models.application.session_application_response import (
    AnswerSelectionResponse,
    ApplicationErrorResponse,
    ComposerDraftResponse,
    ComposerQueueResponse,
    ComposerStateResponse,
    DialogDraftResponse,
    DialogStateResponse,
    QueuedMessageResponse,
    SessionApplicationResponse,
    SessionPreferencesResponse,
)
from dashboard.services.workspace import SessionApplicationSnapshot
from domain.workspace import ComposerState, DialogState


def composer_state(composer: ComposerState) -> ComposerStateResponse:
    return ComposerStateResponse(
        draft=(
            None if composer.draft is None
            else ComposerDraftResponse(
                text=composer.draft.text,
                origin=composer.draft.origin,
                sequence=composer.draft.sequence,
            )
        ),
        queue=(
            None if composer.queue is None
            else ComposerQueueResponse(
                items=tuple(
                    QueuedMessageResponse(text=item.text) for item in composer.queue.items
                ),
                origin=composer.queue.origin,
            )
        ),
    )


def dialog_state(dialog: DialogState) -> DialogStateResponse:
    return DialogStateResponse(
        draft=(
            None if dialog.draft is None
            else DialogDraftResponse(
                attention_id=dialog.draft.attention_id,
                answers=tuple(
                    AnswerSelectionResponse(selected=answer.selected, other=answer.other)
                    for answer in dialog.draft.answers
                ),
                origin=dialog.draft.origin,
            )
        ),
    )


def session_application(
    snapshot: SessionApplicationSnapshot,
) -> SessionApplicationResponse:
    return SessionApplicationResponse(
        preferences=SessionPreferencesResponse(
            view_mode=snapshot.preferences.view_mode,
            notifications_muted=snapshot.preferences.notifications_muted,
            tasks_hidden=snapshot.preferences.tasks_hidden,
        ),
        composer=composer_state(snapshot.composer),
        dialog=dialog_state(snapshot.dialog),
        terminal=values.terminal_state(snapshot.terminal),
        errors=tuple(
            ApplicationErrorResponse(
                error_id=error.error_id,
                timestamp=error.timestamp,
                component=error.component,
                action=error.action,
                traceback=error.traceback,
                context=error.context,
            )
            for error in snapshot.errors
        ),
    )
