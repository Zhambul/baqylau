"""The list page's whole frame to its model.

Above `application.py` and `sessions.py` rather than beside them, because it
needs both: the reader's own preferences AND every session row.
"""

from __future__ import annotations

from api.common.mapper import values
from api.dashboard.mapper.sessions import session_list_item
from api.dashboard.models.application.global_application_response import (
    DashboardLimitsResponse,
    GlobalApplicationResponse,
    GlobalNotificationStateResponse,
    GlobalPreferencesResponse,
    NewSessionDraftResponse,
    NewSessionPreferencesResponse,
    NotificationNoticeResponse,
)
from dashboard.services.overview import GlobalApplicationSnapshot
from domain.ids import SessionId


def global_application(snapshot: GlobalApplicationSnapshot) -> GlobalApplicationResponse:
    latest = snapshot.notifications.latest
    preferences = snapshot.preferences
    return GlobalApplicationResponse(
        sessions=tuple(session_list_item(row) for row in snapshot.sessions),
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
                working_directory=preferences.new_session.working_directory,
                harness=preferences.new_session.harness,
                model=preferences.new_session.model,
                effort=preferences.new_session.effort,
            ),
            new_session_drafts=tuple(
                NewSessionDraftResponse(
                    working_directory=draft.working_directory,
                    text=draft.text,
                    sequence=draft.sequence,
                )
                for draft in preferences.new_session_drafts
            ),
            hidden_directories=dict(preferences.hidden_directories),
            limits=DashboardLimitsResponse(
                upload_bytes=preferences.limits.upload_bytes,
                rename_characters=preferences.limits.rename_characters,
                presence_seconds=preferences.limits.presence_seconds,
            ),
        ),
    )
