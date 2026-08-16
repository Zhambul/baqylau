"""The devices subscribed for on-device push, newest use first."""


from dashboard.prefs.store import mutate_map, stored_object


# --- web-push subscriptions (the on-device iOS/desktop notification channel) ----
# Every browser that opted into Web Push (docs/dashboard.md, *Web push*) stores
# its push subscription here under one kv key, as {endpoint: subscription-json}
# — keyed by the endpoint URL so a re-subscribe from the same browser upserts in
# place instead of piling up duplicates. Global like the other dashboard prefs:
# a subscription is a per-DEVICE fact, not per-session, and the send honors the
# per-session ○ mute at fire time (same as the Telegram alert). A dead
# subscription (the push service returns 404/410) is pruned by remove_push_sub.
PUSH_SUBS_KEY = "push-subs"


def push_subscriptions():
    """The list of stored push subscriptions."""
    subscriptions = list(stored_object(PUSH_SUBS_KEY).values())
    if not all(isinstance(subscription, dict) for subscription in subscriptions):
        raise TypeError("push subscriptions must contain objects")
    return subscriptions


def add_push_subscription(sub, device, label=None):
    """Upsert one subscription (its wire JSON: {endpoint, keys:{p256dh, auth}}),
    keyed by endpoint so a repeat subscribe from the same browser replaces its
    prior entry. `device` (the browser's stable id) + `label` (a friendly name)
    are stored ALONGSIDE the wire fields so the notifier can route the on-device
    push to the most-recently-used device (webpush.send ignores the extra keys).
    Returns the updated map; best-effort like set()."""
    endpoint = sub["endpoint"]
    record = dict(sub)
    record["device"] = str(device)
    if label:
        record["label"] = str(label)
    return mutate_map(
        PUSH_SUBS_KEY,
        lambda document: document.__setitem__(str(endpoint), record),
    )


def remove_push_subscription(endpoint):
    """Drop the subscription for `endpoint` (an unsubscribe, or a prune after the
    push service reports it gone). Returns the updated map; best-effort."""
    return mutate_map(PUSH_SUBS_KEY, lambda d: d.pop(str(endpoint), None))
