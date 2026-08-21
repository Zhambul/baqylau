# notify/channels/ — HOW an alert is delivered, and un-delivered.
#
# The split this package exists for: notifier.py decides WHEN an alert should
# happen (the tab diff, the grace window, the arm/cancel/escalate state
# machine); a channel knows what that means for one destination. Before
# retraction existed the two were one file, and it read fine — a send was a
# leaf call. A retraction is not a leaf: it is a SECOND round-trip that has to
# reach the exact thing the first one produced, so "what was delivered, and
# what does it take back" became a fact needing an owner. That fact is the
# HANDLE each channel returns.
#
#     alert.py     what an alert says, and the outcome vocabulary below
#     telegram.py  the off-device channel: the Bot API and its message id
#     webpush.py   the on-device channel: VAPID, encryption, and the tag
#
# Every send returns a handle or None. None means nothing retractable was
# delivered — either the channel was off/unconfigured, or nobody was
# subscribed. A handle is one of `NotificationHandle`'s members to the caller:
# the notifier's only business with it is storing it and later passing it back
# to `retract()`, which is the one place that opens it.
#
# Both directions are BEST-EFFORT and audited inside the channel rather than at
# the call site, because the audit row's shape is per-channel (a Telegram
# message id vs a push endpoint + device) and the watcher shouldn't have to
# know either. Nothing here raises into the 1 s watcher loop.
from typing import TypeAlias

from audit import record as A
from notify.channels import telegram, webpush
from notify.channels.alert import FAILED, GONE, NOTHING, OK, PENDING, alert_text, push_tag
from notify.channels.telegram import TelegramHandle
from notify.channels.webpush import WebPushHandle
from repository.contract.preferences import (
    PushSigningKeyRepository,
    PushSubscriptionRepository,
)

__all__ = [
    "FAILED", "GONE", "NOTHING", "OK", "PENDING", "NotificationHandle",
    "alert_text", "push_tag", "retract", "telegram", "webpush",
]

# Every channel's retraction handle, discriminated on its own `ch` field —
# `retract()` below narrows on it with `isinstance` (a registry keyed by `ch`
# cannot also carry each channel's own handle TYPE through to its
# `retract_alert`, which is the whole reason to declare the handle at all).
NotificationHandle: TypeAlias = TelegramHandle | WebPushHandle


def retract(
    handle: NotificationHandle | None,
    reason: str,
    badge: int = 0,
    *,
    push_signing_key_repository: PushSigningKeyRepository | None = None,
    push_subscription_repository: PushSubscriptionRepository | None = None,
) -> str:
    """Take back one delivered alert. Returns an outcome from the vocabulary in
    alert.py; PENDING is the only one the caller must retry.

    Deliberately does NOT write the `notify-retract` row: the notifier owns that
    action so the lifecycle has ONE writer and one row shape (it also files the
    expiries, which never reach a channel at all). What each channel audits is
    its own delivery detail — the resolve push's per-device result — which the
    notifier could not describe."""
    if handle is None:
        return NOTHING
    try:
        if isinstance(handle, TelegramHandle):
            return telegram.retract_alert(
                handle, reason, badge,
                push_signing_key_repository=push_signing_key_repository,
                push_subscription_repository=push_subscription_repository,
            )
        return webpush.retract_alert(
            handle, reason, badge,
            push_signing_key_repository=push_signing_key_repository,
            push_subscription_repository=push_subscription_repository,
        )
    except Exception:
        A.error("", "notify retract", {"session_id": handle.session_id})
        return FAILED
