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


def composer_state(composer_state: ComposerState) -> ComposerStateResponse:
    return ComposerStateResponse(
        draft=(
            None if composer_state.draft is None
            else ComposerDraftResponse(
                text=composer_state.draft.text,
                origin=composer_state.draft.origin,
                sequence=composer_state.draft.sequence,
            )
        ),
        queue=(
            None if composer_state.queue is None
            else ComposerQueueResponse(
                items=tuple(
                    QueuedMessageResponse(
                        request_id=str(item.request_id),
                        text=item.text,
                    )
                    for item in composer_state.queue.items
                ),
                origin=composer_state.queue.origin,
            )
        ),
    )


def dialog_state(dialog_state: DialogState) -> DialogStateResponse:
    return DialogStateResponse(
        draft=(
            None if dialog_state.draft is None
            else DialogDraftResponse(
                attention_id=dialog_state.draft.attention_id,
                answers=tuple(
                    AnswerSelectionResponse(selected=answer.selected, other=answer.other)
                    for answer in dialog_state.draft.answers
                ),
                origin=dialog_state.draft.origin,
            )
        ),
    )


def session_application(
    session_application_snapshot: SessionApplicationSnapshot,
) -> SessionApplicationResponse:
    return SessionApplicationResponse(
        preferences=SessionPreferencesResponse(
            view_mode=session_application_snapshot.preferences.view_mode,
            notifications_muted=session_application_snapshot.preferences.notifications_muted,
            tasks_hidden=session_application_snapshot.preferences.tasks_hidden,
        ),
        composer=composer_state(session_application_snapshot.composer),
        dialog=dialog_state(session_application_snapshot.dialog),
        terminal=values.terminal_state(session_application_snapshot.terminal),
        errors=tuple(
            ApplicationErrorResponse(
                error_id=error.error_id,
                timestamp=error.timestamp,
                component=error.component,
                action=error.action,
                traceback=error.traceback,
                context=error.context,
            )
            for error in session_application_snapshot.errors
        ),
    )


def global_application(application_preferences: ApplicationPreferences) -> GlobalApplicationResponse:
    """The page's own state at the HTTP boundary. Beside the per-session mapper above
    rather than in a file of its own: without the session rows it needs nothing
    the read model owns."""
    latest = application_preferences.notifications.latest
    return GlobalApplicationResponse(
        usage_rows=tuple(values.usage_row(row) for row in application_preferences.usage_rows),
        notifications=GlobalNotificationStateResponse(
            enabled=application_preferences.notifications.enabled,
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
                working_directory=application_preferences.new_session.working_directory,
                harness=application_preferences.new_session.harness,
                model=application_preferences.new_session.model,
                effort=application_preferences.new_session.effort,
            ),
            new_session_drafts=tuple(
                NewSessionDraftResponse(
                    working_directory=draft.working_directory,
                    text=draft.text,
                    sequence=draft.sequence,
                )
                for draft in application_preferences.new_session_drafts
            ),
            hidden_directories=dict(application_preferences.hidden_directories),
            limits=DashboardLimitsResponse(
                upload_bytes=application_preferences.limits.upload_bytes,
                rename_characters=application_preferences.limits.rename_characters,
                presence_seconds=application_preferences.limits.presence_seconds,
            ),
        ),
    )
