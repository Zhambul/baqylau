"""Row DTOs to preference values.

With real columns and CHECK constraints there is almost nothing here: the nine
hand-written validators these replace existed only because the values arrived as
free-form JSON and every reader had to prove its own shape.
"""

from __future__ import annotations

from domain.ids import SessionId
from domain.preferences import (
    HiddenDirectory,
    NewSessionDraft,
    NewSessionPreferences,
    PushSigningKeypair,
    PushSubscription,
    ViewMode,
)
from repository.model.preferences import (
    HiddenDirectoryRow,
    NewSessionDraftRow,
    NewSessionPreferenceRow,
    PushSigningKeyRow,
    PushSubscriptionRow,
    SessionViewModeRow,
)
from repository.model.sql import SqlValues


def view_mode(session_view_mode_row: SessionViewModeRow) -> ViewMode:
    # The column carries a CHECK against the same three words, so the store
    # cannot hold a fourth.
    mode: ViewMode = session_view_mode_row.view_mode  # type: ignore[assignment]
    return mode


def hidden_directory(hidden_directory_row: HiddenDirectoryRow) -> HiddenDirectory:
    return HiddenDirectory(hidden_directory_row.working_directory, hidden_directory_row.hidden_at)


def new_session_preferences(
    new_session_preference_row: NewSessionPreferenceRow,
) -> NewSessionPreferences:
    return NewSessionPreferences(
        working_directory=new_session_preference_row.working_directory or None,
        harness=new_session_preference_row.harness or None,
        model=new_session_preference_row.model or None,
        effort=new_session_preference_row.effort or None,
    )


def new_session_draft(new_session_draft_row: NewSessionDraftRow) -> NewSessionDraft:
    return NewSessionDraft(
        new_session_draft_row.working_directory,
        new_session_draft_row.text,
        new_session_draft_row.sequence,
    )


def push_subscription(push_subscription_row: PushSubscriptionRow) -> PushSubscription:
    return PushSubscription(
        endpoint=push_subscription_row.endpoint,
        public_key=push_subscription_row.public_key,
        authentication_secret=push_subscription_row.authentication_secret,
        device_id=push_subscription_row.device_id,
        device_label=push_subscription_row.device_label,
        created_at=push_subscription_row.created_at,
    )


def push_subscription_values(push_subscription: PushSubscription) -> SqlValues:
    return (
        push_subscription.endpoint,
        push_subscription.public_key,
        push_subscription.authentication_secret,
        push_subscription.device_id,
        push_subscription.device_label,
        push_subscription.created_at,
    )


def push_signing_keypair(push_signing_key_row: PushSigningKeyRow) -> PushSigningKeypair:
    return PushSigningKeypair(push_signing_key_row.private_key_pem, push_signing_key_row.public_key)


def session_id(value: str) -> SessionId:
    return SessionId(value)
