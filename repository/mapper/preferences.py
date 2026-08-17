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


def view_mode(row: SessionViewModeRow) -> ViewMode:
    # The column carries a CHECK against the same three words, so the store
    # cannot hold a fourth.
    mode: ViewMode = row.view_mode  # type: ignore[assignment]
    return mode


def hidden_directory(row: HiddenDirectoryRow) -> HiddenDirectory:
    return HiddenDirectory(row.working_directory, row.hidden_at)


def new_session_preferences(row: NewSessionPreferenceRow) -> NewSessionPreferences:
    return NewSessionPreferences(
        working_directory=row.working_directory or None,
        harness=row.harness or None,
        model=row.model or None,
        effort=row.effort or None,
    )


def new_session_draft(row: NewSessionDraftRow) -> NewSessionDraft:
    return NewSessionDraft(row.working_directory, row.text, row.sequence)


def push_subscription(row: PushSubscriptionRow) -> PushSubscription:
    return PushSubscription(
        endpoint=row.endpoint,
        public_key=row.public_key,
        authentication_secret=row.authentication_secret,
        device_id=row.device_id,
        device_label=row.device_label,
        created_at=row.created_at,
    )


def push_subscription_values(subscription: PushSubscription) -> tuple[object, ...]:
    return (
        subscription.endpoint,
        subscription.public_key,
        subscription.authentication_secret,
        subscription.device_id,
        subscription.device_label,
        subscription.created_at,
    )


def push_signing_keypair(row: PushSigningKeyRow) -> PushSigningKeypair:
    return PushSigningKeypair(row.private_key_pem, row.public_key)


def session_id(value: str) -> SessionId:
    return SessionId(value)
