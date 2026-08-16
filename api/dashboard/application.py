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
from api.dependencies import ApplicationGraph
from api.guard import control_plane
from notify.channels import webpush
from dashboard.application import (
    AnswerSelection,
    BrowserPresence,
    BrowserPushSubscription,
    QueuedMessage,
)
from domain.ids import AttentionId, SessionId

router = APIRouter()
guarded = APIRouter(dependencies=[Depends(control_plane())])


@router.get("/api/application/push-configuration")
def push_configuration() -> PushConfigurationResponse:
    """The Web Push feature probe: the page offers the notification opt-in +
    subscribes only when push is possible AND has an application-server key.
    The public key is not a secret."""
    key = webpush.public_key()
    return PushConfigurationResponse(enabled=bool(webpush.enabled() and key), key=key)


@guarded.post("/api/application/notifications")
def set_global_notifications(
    body: GlobalNotificationsRequest, application: ApplicationGraph
) -> SavedResponse:
    application.global_application.set_notifications_enabled(body.enabled)
    return SavedResponse()


@guarded.post("/api/application/new-session-preferences")
def save_new_session_preferences(
    body: NewSessionPreferencesRequest, application: ApplicationGraph
) -> SavedResponse:
    application.global_application.save_new_session_preferences(
        working_directory=body.working_directory or None,
        harness=body.harness or None,
        model=body.model or None,
        effort=body.effort or None,
    )
    return SavedResponse()


@guarded.post("/api/application/new-session-drafts")
def save_new_session_draft(
    body: NewSessionDraftRequest, application: ApplicationGraph
) -> SavedResponse:
    saved = application.global_application.save_new_session_draft(
        body.working_directory, body.text, body.sequence
    )
    return SavedResponse(saved=saved)


@guarded.post("/api/application/hidden-directories")
def hide_directory(
    body: HideDirectoryRequest, application: ApplicationGraph
) -> HiddenDirectoriesResponse:
    hidden = application.global_application.hide_directory(body.working_directory)
    return HiddenDirectoriesResponse(hidden=hidden)


@guarded.post("/api/application/push-subscriptions")
def register_push_subscription(
    body: PushSubscriptionRequest, application: ApplicationGraph
) -> SavedResponse:
    application.global_application.register_push_subscription(
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
def report_presence(body: PresenceRequest, application: ApplicationGraph) -> SavedResponse:
    application.global_application.report_presence(
        BrowserPresence(
            body.device_id,
            SessionId(body.session_id) if body.session_id else None,
            body.away,
        )
    )
    return SavedResponse()


@guarded.post("/api/sessions/{session_id}/application/composer-draft")
def save_composer_draft(
    session_id: str, body: ComposerDraftRequest, application: ApplicationGraph
) -> SavedResponse:
    saved = application.session_application.save_composer_draft(
        SessionId(session_id), body.text, body.origin, body.sequence
    )
    return SavedResponse(saved=saved)


@guarded.post("/api/sessions/{session_id}/application/composer-queue")
def save_composer_queue(
    session_id: str, body: ComposerQueueRequest, application: ApplicationGraph
) -> SavedResponse:
    messages = tuple(
        QueuedMessage(item.text) for item in body.items if item.text.strip()
    )
    application.session_application.save_composer_queue(
        SessionId(session_id), messages, body.origin
    )
    return SavedResponse()


@guarded.post("/api/sessions/{session_id}/application/dialog-draft")
def save_dialog_draft(
    session_id: str, body: DialogDraftRequest, application: ApplicationGraph
) -> SavedResponse:
    selections = tuple(
        AnswerSelection(answer.selected, answer.other) for answer in body.answers
    )
    application.session_application.save_dialog_draft(
        SessionId(session_id), AttentionId(body.attention_id), selections, body.origin
    )
    return SavedResponse()


@guarded.post("/api/sessions/{session_id}/application/view-mode")
def set_view_mode(
    session_id: str, body: ViewModeRequest, application: ApplicationGraph
) -> SavedResponse:
    application.session_application.set_view_mode(SessionId(session_id), body.view_mode)
    return SavedResponse()


@guarded.post("/api/sessions/{session_id}/application/notifications-muted")
def set_notifications_muted(
    session_id: str, body: NotificationsMutedRequest, application: ApplicationGraph
) -> SavedResponse:
    application.session_application.set_notifications_muted(SessionId(session_id), body.muted)
    return SavedResponse()


@guarded.post("/api/sessions/{session_id}/application/tasks-hidden")
def set_tasks_hidden(
    session_id: str, body: TasksHiddenRequest, application: ApplicationGraph
) -> SavedResponse:
    application.session_application.set_tasks_hidden(SessionId(session_id), body.hidden)
    return SavedResponse()
