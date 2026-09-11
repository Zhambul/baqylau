# Copyright (c) 2026 Zhambyl Yermagambet
"""One session's application state — what YOU have on it — to its model.

The list page's equivalent is in `overview.py`, which needs the session rows and
so has to sit above this one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from api.application.models.preferences.composer_state_response import (
    ComposerDraftResponse,
    ComposerQueueResponse,
    ComposerStateResponse,
    QueuedMessageResponse,
)
from api.application.models.preferences.dialog_state_response import (
    AnswerSelectionResponse,
    DialogDraftResponse,
    DialogStateResponse,
)
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
    ApplicationErrorResponse,
    SessionApplicationResponse,
    SessionPreferencesResponse,
)
from api.common.mapper import states, usage
from domain.ids import SessionId

if TYPE_CHECKING:
    from dashboard.services.preference_models import ApplicationPreferences
    from dashboard.services.workspace import SessionApplicationSnapshot
    from domain.composer import ComposerState
    from domain.dialogs import DialogState


def composer_state(composer_state: ComposerState) -> ComposerStateResponse:
    """Return the composer state.

    Returns:
        Composer state.

    """
    return ComposerStateResponse(
        draft=(
            None
            if composer_state.draft is None
            else ComposerDraftResponse(
                text=composer_state.draft.text,
                origin=composer_state.draft.origin,
                sequence=composer_state.draft.sequence,
            )
        ),
        queue=(
            None
            if composer_state.queue is None
            else ComposerQueueResponse(
                items=tuple(
                    QueuedMessageResponse(
                        request_id=str(queued_message.request_id),
                        text=queued_message.text,
                    )
                    for queued_message in composer_state.queue.messages
                ),
                origin=composer_state.queue.origin,
            )
        ),
    )


def dialog_state(dialog_state: DialogState) -> DialogStateResponse:
    """Return the dialog state.

    Returns:
        Dialog state.

    """
    return DialogStateResponse(
        draft=(
            None
            if dialog_state.draft is None
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
    """Return the session application.

    Returns:
        Session application.

    """
    return SessionApplicationResponse(
        preferences=SessionPreferencesResponse(
            view_mode=session_application_snapshot.preferences.view_mode,
            notifications_muted=session_application_snapshot.preferences.notifications_muted,
            tasks_hidden=session_application_snapshot.preferences.tasks_hidden,
            goal_hidden=session_application_snapshot.preferences.goal_hidden,
        ),
        composer=composer_state(session_application_snapshot.composer),
        dialog=dialog_state(session_application_snapshot.dialog),
        terminal=states.terminal_state(session_application_snapshot.terminal),
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
    """Return the global application.

    The page's own state at the HTTP boundary. Beside the per-session mapper above
        rather than in a file of its own: without the session rows it needs nothing
        the read model owns.

    Returns:
        Global application.

    """
    latest = application_preferences.notifications.latest
    return GlobalApplicationResponse(
        usage_rows=tuple(usage.usage_row(row) for row in application_preferences.usage_rows),
        notifications=GlobalNotificationStateResponse(
            enabled=application_preferences.notifications.enabled,
            latest=(
                None
                if latest is None
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
            hidden_directories=application_preferences.hidden_directories,
            limits=DashboardLimitsResponse(
                upload_bytes=application_preferences.limits.upload_bytes,
                rename_characters=application_preferences.limits.rename_characters,
                presence_seconds=application_preferences.limits.presence_seconds,
            ),
        ),
    )
