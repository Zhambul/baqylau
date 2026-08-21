# api/application/preferences.py — browser application state: the Web Push
# feature probe, global and per-session preferences, drafts, and presence.
# (Dictation has no probe: the mic is always offered; the token mint is where
# a missing key surfaces.)
from __future__ import annotations

from fastapi import APIRouter, Depends

from api.common.models.replies.saved_response import SavedResponse
from api.application.models.preferences.composer_draft_request import ComposerDraftRequest
from api.application.models.preferences.composer_queue_request import ComposerQueueRequest
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
from api.guard import control_plane
from api.responses import GUARDED
from notify.channels import webpush
from dashboard.services.preferences import BrowserPresence, BrowserPushSubscription
from domain.workspace import AnswerSelection, QueuedMessage
from domain.ids import AttentionId, SessionId

router = APIRouter()
guarded = APIRouter(dependencies=[Depends(control_plane())], responses=GUARDED)


@router.get("/api/application/push-configuration")
def push_configuration(signing_keys: PushSigningKeys) -> PushConfigurationResponse:
    """The Web Push feature probe: the page offers the notification opt-in +
    subscribes only when push is possible AND has an application-server key.
    The public key is not a secret."""
    key = webpush.public_key(signing_keys)  # type: ignore[no-untyped-call]
    return PushConfigurationResponse(enabled=bool(webpush.enabled() and key), key=key)  # type: ignore[no-untyped-call]


# Browser-owned state, served from the PREFERENCES store and deliberately
# outside /sessionData: none of it is a fact a harness reported. The read surface
# is five routes over the read model and two over this store — this one and the
# per-session one below.
@router.get("/api/application")
def application_state(preferences: ApplicationPreferences) -> GlobalApplicationResponse:
    return mapper.global_application(preferences.snapshot())


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
    body: GlobalNotificationsRequest, preferences: ApplicationPreferences
) -> SavedResponse:
    preferences.set_notifications_enabled(body.enabled)
    return SavedResponse()


@guarded.post("/api/application/new-session-preferences")
def save_new_session_preferences(
    body: NewSessionPreferencesRequest, preferences: ApplicationPreferences
) -> SavedResponse:
    preferences.save_new_session_preferences(
        working_directory=body.working_directory or None,
        harness=body.harness or None,
        model=body.model or None,
        effort=body.effort or None,
    )
    return SavedResponse()


@guarded.post("/api/application/new-session-drafts")
def save_new_session_draft(
    body: NewSessionDraftRequest, preferences: ApplicationPreferences
) -> SavedResponse:
    saved = preferences.save_new_session_draft(
        body.working_directory, body.text, body.sequence
    )
    return SavedResponse(saved=saved)


@guarded.post("/api/application/hidden-directories")
def hide_directory(
    body: HideDirectoryRequest, preferences: ApplicationPreferences
) -> HiddenDirectoriesResponse:
    hidden = preferences.hide_directory(body.working_directory)
    return HiddenDirectoriesResponse(hidden=hidden)


@guarded.post("/api/application/push-subscriptions")
def register_push_subscription(
    body: PushSubscriptionRequest, preferences: ApplicationPreferences
) -> SavedResponse:
    preferences.register_push_subscription(
        BrowserPushSubscription(
            body.subscription.endpoint,
            body.subscription.keys.p256dh,
            body.subscription.keys.auth,
            body.device_id,
            body.device_label or None,
        )
    )
    return SavedResponse()


@guarded.post("/api/application/presence")
def report_presence(body: PresenceRequest, preferences: ApplicationPreferences) -> SavedResponse:
    preferences.report_presence(
        BrowserPresence(
            body.device_id,
            SessionId(body.session_id) if body.session_id else None,
            body.away,
        )
    )
    return SavedResponse()


@guarded.post("/api/sessions/{session_id}/application/composer-draft")
def save_composer_draft(
    session_id: SessionIdPath, body: ComposerDraftRequest, workspace: SessionApplication
) -> SavedResponse:
    saved = workspace.save_composer_draft(
        SessionId(session_id), body.text, body.origin, body.sequence
    )
    return SavedResponse(saved=saved)


@guarded.post("/api/sessions/{session_id}/application/composer-queue")
def save_composer_queue(
    session_id: SessionIdPath, body: ComposerQueueRequest, workspace: SessionApplication
) -> SavedResponse:
    messages = tuple(
        QueuedMessage(item.text) for item in body.items if item.text.strip()
    )
    workspace.save_composer_queue(
        SessionId(session_id), messages, body.origin
    )
    return SavedResponse()


@guarded.post("/api/sessions/{session_id}/application/dialog-draft")
def save_dialog_draft(
    session_id: SessionIdPath, body: DialogDraftRequest, workspace: SessionApplication
) -> SavedResponse:
    selections = tuple(
        AnswerSelection(answer.selected, answer.other) for answer in body.answers
    )
    workspace.save_dialog_draft(
        SessionId(session_id), AttentionId(body.attention_id), selections, body.origin
    )
    return SavedResponse()


@guarded.post("/api/sessions/{session_id}/application/view-mode")
def set_view_mode(
    session_id: SessionIdPath, body: ViewModeRequest, workspace: SessionApplication
) -> SavedResponse:
    workspace.set_view_mode(SessionId(session_id), body.view_mode)
    return SavedResponse()


@guarded.post("/api/sessions/{session_id}/application/notifications-muted")
def set_notifications_muted(
    session_id: SessionIdPath, body: NotificationsMutedRequest, workspace: SessionApplication
) -> SavedResponse:
    workspace.set_notifications_muted(SessionId(session_id), body.muted)
    return SavedResponse()


@guarded.post("/api/sessions/{session_id}/application/tasks-hidden")
def set_tasks_hidden(
    session_id: SessionIdPath, body: TasksHiddenRequest, workspace: SessionApplication
) -> SavedResponse:
    workspace.set_tasks_hidden(SessionId(session_id), body.hidden)
    return SavedResponse()
