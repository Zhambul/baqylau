# api/application/preferences.py — browser application state: the Web Push
# feature probe, global and per-session preferences, drafts, and presence.
# (Dictation has no probe: the mic is always offered; the token mint is where
# a missing key surfaces.)
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from api.common.models.replies.saved_response import SavedResponse
from api.application.models.preferences.composer_draft_request import ComposerDraftRequest
from api.application.models.preferences.dialog_draft_request import DialogDraftRequest
from api.application.models.preferences.global_notifications_request import (
    GlobalNotificationsRequest,
)
from api.application.models.preferences.hidden_directories_response import (
    HiddenDirectoriesResponse,
)
from api.application.models.preferences.hide_directory_request import HideDirectoryRequest
from api.application.models.preferences.new_session_draft_request import (
    NewSessionDraftRequest,
)
from api.application.models.preferences.new_session_preferences_request import (
    NewSessionPreferencesRequest,
)
from api.application.models.preferences.notifications_muted_request import (
    NotificationsMutedRequest,
)
from api.application.models.preferences.presence_request import PresenceRequest
from api.application.models.preferences.push_configuration_response import (
    PushConfigurationResponse,
)
from api.application.models.preferences.push_subscription_request import (
    PushSubscriptionRequest,
)
from api.application.models.preferences.tasks_hidden_request import TasksHiddenRequest
from api.application.models.preferences.view_mode_request import ViewModeRequest
from api.common.models.fields import SessionIdPath
from api.application.mapper import preferences as mapper
from api.application.models.preferences.global_application_response import (
    GlobalApplicationResponse,
)
from api.application.models.preferences.session_application_response import (
    SessionApplicationResponse,
)
from app.providers import ApplicationPreferences, PushSigningKeys, SessionApplication
from notify.channels import webpush
from dashboard.services.preferences import BrowserPresence, BrowserPushSubscription
from domain.workspace import AnswerSelection
from domain.ids import AttentionId, DeviceId, HarnessName, SessionId

router = APIRouter()
guarded = APIRouter()


@router.get("/api/application/push-configuration")
def push_configuration(signing_keys: PushSigningKeys) -> PushConfigurationResponse:
    """The Web Push feature probe: the page offers the notification opt-in +
    subscribes only when push is possible AND has an application-server key.
    The public key is not a secret."""
    key = webpush.public_key(signing_keys)
    return PushConfigurationResponse(enabled=bool(webpush.enabled() and key), key=key)


# Browser-owned state, served from the PREFERENCES store and deliberately
# outside /sessionData: none of it is a fact a harness reported. The read surface
# is five routes over the read model and two over this store — this one and the
# per-session one below.
@router.get("/api/application")
def application_state(application_preferences: ApplicationPreferences) -> GlobalApplicationResponse:
    return mapper.global_application(application_preferences.snapshot())


# Browser-owned per-session state, served from the PREFERENCES store and
# deliberately outside /sessionData: none of it is a fact a harness reported, so
# none of it belongs in the read model. The read surface is five routes over the
# read model and this one over the store — and without it a draft somebody typed
# would be written and never readable again.
@router.get("/api/sessions/{session_id}/application")
def session_application(
    session_id: SessionIdPath, workspace: SessionApplication
) -> SessionApplicationResponse:
    return mapper.session_application(workspace.snapshot(SessionId(session_id)))


@guarded.post("/api/application/notifications")
def set_global_notifications(
    global_notifications_request: GlobalNotificationsRequest,
    application_preferences: ApplicationPreferences,
) -> SavedResponse:
    application_preferences.set_notifications_enabled(global_notifications_request.enabled)
    return SavedResponse()


@guarded.post("/api/application/new-session-preferences")
def save_new_session_preferences(
    new_session_preferences_request: NewSessionPreferencesRequest,
    application_preferences: ApplicationPreferences,
) -> SavedResponse:
    application_preferences.save_new_session_preferences(
        working_directory=new_session_preferences_request.working_directory or None,
        harness=(
            HarnessName(new_session_preferences_request.harness)
            if new_session_preferences_request.harness
            else None
        ),
        model=new_session_preferences_request.model or None,
        effort=new_session_preferences_request.effort or None,
    )
    return SavedResponse()


@guarded.post("/api/application/new-session-drafts")
def save_new_session_draft(
    new_session_draft_request: NewSessionDraftRequest,
    application_preferences: ApplicationPreferences,
) -> SavedResponse:
    saved = application_preferences.save_new_session_draft(
        new_session_draft_request.working_directory,
        new_session_draft_request.text,
        new_session_draft_request.sequence,
    )
    return SavedResponse(saved=saved)


@guarded.post("/api/application/hidden-directories")
def hide_directory(
    hide_directory_request: HideDirectoryRequest,
    application_preferences: ApplicationPreferences,
) -> HiddenDirectoriesResponse:
    hidden = application_preferences.hide_directory(hide_directory_request.working_directory)
    return HiddenDirectoriesResponse(hidden=hidden)


@guarded.post("/api/application/push-subscriptions")
def register_push_subscription(
    push_subscription_request: PushSubscriptionRequest,
    application_preferences: ApplicationPreferences,
) -> SavedResponse:
    application_preferences.register_push_subscription(
        BrowserPushSubscription(
            push_subscription_request.subscription.endpoint,
            push_subscription_request.subscription.keys.p256dh,
            push_subscription_request.subscription.keys.auth,
            DeviceId(push_subscription_request.device_id),
            push_subscription_request.device_label or None,
        )
    )
    return SavedResponse()


@guarded.post("/api/application/presence")
def report_presence(
    presence_request: PresenceRequest, application_preferences: ApplicationPreferences
) -> SavedResponse:
    application_preferences.report_presence(
        BrowserPresence(
            DeviceId(presence_request.device_id),
            SessionId(presence_request.session_id) if presence_request.session_id else None,
            presence_request.away,
        )
    )
    return SavedResponse()


@guarded.post("/api/sessions/{session_id}/application/composer-draft")
def save_composer_draft(
    session_id: SessionIdPath, composer_draft_request: ComposerDraftRequest, workspace: SessionApplication
) -> SavedResponse:
    saved = workspace.save_composer_draft(
        SessionId(session_id),
        composer_draft_request.text,
        composer_draft_request.origin,
        composer_draft_request.sequence,
    )
    return SavedResponse(saved=saved)


@guarded.post("/api/sessions/{session_id}/application/dialog-draft")
def save_dialog_draft(
    session_id: SessionIdPath, dialog_draft_request: DialogDraftRequest, workspace: SessionApplication
) -> SavedResponse:
    selections = tuple(
        AnswerSelection(answer.selected, answer.other) for answer in dialog_draft_request.answers
    )
    workspace.save_dialog_draft(
        SessionId(session_id), AttentionId(dialog_draft_request.attention_id), selections, dialog_draft_request.origin
    )
    return SavedResponse()


@guarded.post("/api/sessions/{session_id}/application/view-mode")
def set_view_mode(
    session_id: SessionIdPath, view_mode_request: ViewModeRequest, workspace: SessionApplication
) -> SavedResponse:
    workspace.set_view_mode(SessionId(session_id), view_mode_request.view_mode)
    return SavedResponse()


@guarded.post("/api/sessions/{session_id}/application/notifications-muted")
def set_notifications_muted(
    session_id: SessionIdPath, notifications_muted_request: NotificationsMutedRequest, workspace: SessionApplication
) -> SavedResponse:
    workspace.set_notifications_muted(SessionId(session_id), notifications_muted_request.muted)
    return SavedResponse()


@guarded.post("/api/sessions/{session_id}/application/tasks-hidden")
def set_tasks_hidden(
    session_id: SessionIdPath, tasks_hidden_request: TasksHiddenRequest, workspace: SessionApplication
) -> SavedResponse:
    try:
        workspace.set_tasks_hidden(SessionId(session_id), tasks_hidden_request.hidden)
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    return SavedResponse()
