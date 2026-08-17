# api/dashboard/application.py — browser application state: the Web Push
# feature probe, global and per-session preferences, drafts, and presence.
# (Dictation has no probe: the mic is always offered; the token mint is where
# a missing key surfaces.)
from __future__ import annotations

from fastapi import APIRouter, Depends

from api.common.models.replies.saved_response import SavedResponse
from api.dashboard.models.application.composer_draft_request import ComposerDraftRequest
from api.dashboard.models.application.composer_queue_request import ComposerQueueRequest
from api.dashboard.models.application.dialog_draft_request import DialogDraftRequest
from api.dashboard.models.application.global_notifications_request import (
    GlobalNotificationsRequest,
)
from api.dashboard.models.application.hidden_directories_response import (
    HiddenDirectoriesResponse,
)
from api.dashboard.models.application.hide_directory_request import HideDirectoryRequest
from api.dashboard.models.application.new_session_draft_request import (
    NewSessionDraftRequest,
)
from api.dashboard.models.application.new_session_preferences_request import (
    NewSessionPreferencesRequest,
)
from api.dashboard.models.application.notifications_muted_request import (
    NotificationsMutedRequest,
)
from api.dashboard.models.application.presence_request import PresenceRequest
from api.dashboard.models.application.push_configuration_response import (
    PushConfigurationResponse,
)
from api.dashboard.models.application.push_subscription_request import (
    PushSubscriptionRequest,
)
from api.dashboard.models.application.tasks_hidden_request import TasksHiddenRequest
from api.dashboard.models.application.view_mode_request import ViewModeRequest
from api.common.models.fields import SessionIdPath
from app.providers import GlobalApplication, PushSigningKeys, SessionApplication
from api.guard import control_plane
from api.responses import GUARDED
from notify.channels import webpush
from dashboard.services.overview import BrowserPresence, BrowserPushSubscription
from domain.workspace import AnswerSelection, QueuedMessage
from domain.ids import AttentionId, SessionId

router = APIRouter()
guarded = APIRouter(dependencies=[Depends(control_plane())], responses=GUARDED)


@router.get("/api/application/push-configuration")
def push_configuration(signing_keys: PushSigningKeys) -> PushConfigurationResponse:
    """The Web Push feature probe: the page offers the notification opt-in +
    subscribes only when push is possible AND has an application-server key.
    The public key is not a secret."""
    key = webpush.public_key(signing_keys)
    return PushConfigurationResponse(enabled=bool(webpush.enabled() and key), key=key)


@guarded.post("/api/application/notifications")
def set_global_notifications(
    body: GlobalNotificationsRequest, overview: GlobalApplication
) -> SavedResponse:
    overview.set_notifications_enabled(body.enabled)
    return SavedResponse()


@guarded.post("/api/application/new-session-preferences")
def save_new_session_preferences(
    body: NewSessionPreferencesRequest, overview: GlobalApplication
) -> SavedResponse:
    overview.save_new_session_preferences(
        working_directory=body.working_directory or None,
        harness=body.harness or None,
        model=body.model or None,
        effort=body.effort or None,
    )
    return SavedResponse()


@guarded.post("/api/application/new-session-drafts")
def save_new_session_draft(
    body: NewSessionDraftRequest, overview: GlobalApplication
) -> SavedResponse:
    saved = overview.save_new_session_draft(
        body.working_directory, body.text, body.sequence
    )
    return SavedResponse(saved=saved)


@guarded.post("/api/application/hidden-directories")
def hide_directory(
    body: HideDirectoryRequest, overview: GlobalApplication
) -> HiddenDirectoriesResponse:
    hidden = overview.hide_directory(body.working_directory)
    return HiddenDirectoriesResponse(hidden=hidden)


@guarded.post("/api/application/push-subscriptions")
def register_push_subscription(
    body: PushSubscriptionRequest, overview: GlobalApplication
) -> SavedResponse:
    overview.register_push_subscription(
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
def report_presence(body: PresenceRequest, overview: GlobalApplication) -> SavedResponse:
    overview.report_presence(
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
