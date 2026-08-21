"""One session's application state — what YOU have on it — to its model.

The list page's equivalent is in `overview.py`, which needs the session rows and
so has to sit above this one.
"""

from __future__ import annotations

from api.common.mapper import values
from api.application.models.preferences.global_application_response import (
    DashboardLimitsResponse,
    GlobalApplicationResponse,
    GlobalNotificationStateResponse,
    GlobalPreferencesResponse,
    NewSessionDraftResponse,
    NewSessionPreferencesResponse,
    NotificationNoticeResponse,
)
from api.application.models.preferences.session_application_response import (
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
from dashboard.services.preferences import ApplicationPreferences
from dashboard.services.workspace import SessionApplicationSnapshot
from domain.ids import SessionId
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


def global_application(snapshot: ApplicationPreferences) -> GlobalApplicationResponse:
    """The page's own state on the wire. Beside the per-session mapper above
    rather than in a file of its own: without the session rows it needs nothing
    the read model owns."""
    latest = snapshot.notifications.latest
    return GlobalApplicationResponse(
        usage_rows=tuple(values.usage_row(row) for row in snapshot.usage_rows),
        notifications=GlobalNotificationStateResponse(
            enabled=snapshot.notifications.enabled,
            latest=(
                None if latest is None
                else NotificationNoticeResponse(
                    revision=latest.revision,
                    session_id=SessionId(latest.session_id),
                    kind=latest.kind,
                    project=latest.project,
                    title=latest.title,
                )
            ),
        ),
        preferences=GlobalPreferencesResponse(
            new_session=NewSessionPreferencesResponse(
                working_directory=snapshot.new_session.working_directory,
                harness=snapshot.new_session.harness,
                model=snapshot.new_session.model,
                effort=snapshot.new_session.effort,
            ),
            new_session_drafts=tuple(
                NewSessionDraftResponse(
                    working_directory=draft.working_directory,
                    text=draft.text,
                    sequence=draft.sequence,
                )
                for draft in snapshot.new_session_drafts
            ),
            hidden_directories=dict(snapshot.hidden_directories),
            limits=DashboardLimitsResponse(
                upload_bytes=snapshot.limits.upload_bytes,
                rename_characters=snapshot.limits.rename_characters,
                presence_seconds=snapshot.limits.presence_seconds,
            ),
        ),
    )
