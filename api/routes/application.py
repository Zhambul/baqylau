# api/routes/application.py — browser application state: the two feature
# probes, global and per-session preferences, drafts, presence, and the
# browser telemetry sinks.
from __future__ import annotations

from fastapi import APIRouter, Depends

from api.dependencies import ApplicationGraph
from api.guard import control_plane
from api.models import (
    BrowserEventsBody,
    ClientFailureBody,
    ComposerDraftBody,
    ComposerQueueBody,
    DialogDraftBody,
    DictationProbe,
    GlobalNotificationsBody,
    HiddenDirectories,
    HideDirectoryBody,
    NewSessionDraftBody,
    NewSessionPreferencesBody,
    NotificationsMutedBody,
    OptimisticActionBody,
    PresenceBody,
    PushConfiguration,
    PushSubscriptionBody,
    Recorded,
    Saved,
    TasksHiddenBody,
    ViewModeBody,
)
from app.telemetry import (
    BrowserEvent,
    BrowserEventBatch,
    ClientFailureReport,
    OptimisticActionReport,
)
from dashboard import dictate, webpush
from dashboard.application import (
    AnswerSelection,
    BrowserPresence,
    BrowserPushSubscription,
    QueuedMessage,
)
from domain.ids import AttentionId, SessionId

router = APIRouter()
guarded = APIRouter(dependencies=[Depends(control_plane())])


@router.get("/api/application/dictation")
def dictation_probe() -> DictationProbe:
    """Feature probe: the page renders mic buttons iff a Deepgram key is
    configured — no key means the feature is invisible, never a dead button."""
    return DictationProbe(available=dictate.available())


@router.get("/api/application/push-configuration")
def push_configuration() -> PushConfiguration:
    """The Web Push feature probe: the page offers the notification opt-in +
    subscribes only when push is possible AND has an application-server key.
    The public key is not a secret."""
    key = webpush.public_key()
    return PushConfiguration(enabled=bool(webpush.enabled() and key), key=key)


@guarded.post("/api/application/notifications")
def set_global_notifications(body: GlobalNotificationsBody, application: ApplicationGraph) -> Saved:
    application.global_application.set_notifications_enabled(body.enabled)
    return Saved()


@guarded.post("/api/application/new-session-preferences")
def save_new_session_preferences(
    body: NewSessionPreferencesBody, application: ApplicationGraph
) -> Saved:
    application.global_application.save_new_session_preferences(
        working_directory=body.working_directory or None,
        harness=body.harness or None,
        model=body.model or None,
        effort=body.effort or None,
    )
    return Saved()


@guarded.post("/api/application/new-session-drafts")
def save_new_session_draft(body: NewSessionDraftBody, application: ApplicationGraph) -> Saved:
    saved = application.global_application.save_new_session_draft(
        body.working_directory, body.text, body.sequence
    )
    return Saved(saved=saved)


@guarded.post("/api/application/hidden-directories")
def hide_directory(body: HideDirectoryBody, application: ApplicationGraph) -> HiddenDirectories:
    hidden = application.global_application.hide_directory(body.working_directory)
    return HiddenDirectories(hidden=hidden)


@guarded.post("/api/application/push-subscriptions")
def register_push_subscription(
    body: PushSubscriptionBody, application: ApplicationGraph
) -> Saved:
    application.global_application.register_push_subscription(
        BrowserPushSubscription(
            body.subscription.endpoint,
            body.subscription.keys.p256dh,
            body.subscription.keys.auth,
            body.device_id,
            body.device_label or None,
        )
    )
    return Saved()


@guarded.post("/api/application/presence")
def report_presence(body: PresenceBody, application: ApplicationGraph) -> Saved:
    application.global_application.report_presence(
        BrowserPresence(
            body.device_id,
            SessionId(body.session_id) if body.session_id else None,
            body.away,
        )
    )
    return Saved()


@guarded.post("/api/application/browser-events")
def record_browser_events(body: BrowserEventsBody, application: ApplicationGraph) -> Recorded:
    application.browser_telemetry.record_events(
        BrowserEventBatch(
            body.client_id,
            body.device_id,
            body.connection,
            tuple(
                BrowserEvent(
                    SessionId(event.session_id) if event.session_id else None,
                    event.name,
                    event.timestamp,
                    event.details,
                )
                for event in body.events
            ),
        )
    )
    return Recorded()


@guarded.post("/api/sessions/{session_id}/application/composer-draft")
def save_composer_draft(
    session_id: str, body: ComposerDraftBody, application: ApplicationGraph
) -> Saved:
    saved = application.session_application.save_composer_draft(
        SessionId(session_id), body.text, body.origin, body.sequence
    )
    return Saved(saved=saved)


@guarded.post("/api/sessions/{session_id}/application/composer-queue")
def save_composer_queue(
    session_id: str, body: ComposerQueueBody, application: ApplicationGraph
) -> Saved:
    messages = tuple(
        QueuedMessage(item.text) for item in body.items if item.text.strip()
    )
    application.session_application.save_composer_queue(
        SessionId(session_id), messages, body.origin
    )
    return Saved()


@guarded.post("/api/sessions/{session_id}/application/dialog-draft")
def save_dialog_draft(
    session_id: str, body: DialogDraftBody, application: ApplicationGraph
) -> Saved:
    selections = tuple(
        AnswerSelection(answer.selected, answer.other) for answer in body.answers
    )
    application.session_application.save_dialog_draft(
        SessionId(session_id), AttentionId(body.attention_id), selections, body.origin
    )
    return Saved()


@guarded.post("/api/sessions/{session_id}/application/view-mode")
def set_view_mode(session_id: str, body: ViewModeBody, application: ApplicationGraph) -> Saved:
    application.session_application.set_view_mode(SessionId(session_id), body.view_mode)
    return Saved()


@guarded.post("/api/sessions/{session_id}/application/notifications-muted")
def set_notifications_muted(
    session_id: str, body: NotificationsMutedBody, application: ApplicationGraph
) -> Saved:
    application.session_application.set_notifications_muted(SessionId(session_id), body.muted)
    return Saved()


@guarded.post("/api/sessions/{session_id}/application/tasks-hidden")
def set_tasks_hidden(
    session_id: str, body: TasksHiddenBody, application: ApplicationGraph
) -> Saved:
    application.session_application.set_tasks_hidden(SessionId(session_id), body.hidden)
    return Saved()


@guarded.post("/api/sessions/{session_id}/application/optimistic-actions")
def record_optimistic_action(
    session_id: str, body: OptimisticActionBody, application: ApplicationGraph
) -> Recorded:
    application.browser_telemetry.record_optimistic_action(
        OptimisticActionReport(
            SessionId(session_id),
            body.action,
            body.phase,
            body.character_count,
            body.elapsed_milliseconds,
            body.reason or None,
        )
    )
    return Recorded()


@guarded.post("/api/sessions/{session_id}/application/client-failures")
def record_client_failure(
    session_id: str, body: ClientFailureBody, application: ApplicationGraph
) -> Recorded:
    application.browser_telemetry.record_client_failure(
        ClientFailureReport(
            SessionId(session_id),
            body.gesture,
            body.failure_kind,
            body.error,
            body.status_code,
            body.character_count,
        )
    )
    return Recorded()
