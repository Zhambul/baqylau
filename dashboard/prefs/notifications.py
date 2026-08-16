"""Whether you want alerting at all, and which sessions you muted.

Two keys, one question asked at two scales: the global toggle beside "+ session"
on the list page, and the per-session ◉/○ in the session header. An alert fires
only when both say yes.
"""


from dashboard.prefs.store import get, mutate_map, set, stored_object


# --- global alerts toggle (the list page's ◉/○, next to "+ session") -------------
# The ONE master switch over every dashboard notification — the cross-session
# toasts / OS notifications AND the deferred Telegram / web-push alerts
# (docs/dashboard.md, *Global alerts toggle*). Stored under one kv key as a bare
# bool. GLOBAL like the other dashboard prefs: it lives at DASH_PREFS_DB
# (~/.harness), independent of any repo checkout, so the one flag governs every
# session — live or parked, in the main checkout or any git worktree. DEFAULT ON:
# an absent key reads True, so a fresh install alerts until the user opts out.
# When OFF it OVERRIDES the per-session notify_muted map (everything suppressed);
# when ON the per-session mutes still apply.
NOTIFY_ENABLED_KEY = "notify-enabled"


def notify_enabled():
    """The global alerts toggle, defaulting to on when absent."""
    enabled = get(NOTIFY_ENABLED_KEY, True)
    if not isinstance(enabled, bool):
        raise TypeError("notification preference must be a boolean")
    return enabled


def set_notify_enabled(on):
    """Turn all dashboard alerts on/off globally; True on write (best-effort like
    set())."""
    return set(NOTIFY_ENABLED_KEY, bool(on))


# --- notification mute (the session header's ◉/○ opt-out) ------------------------
# The set of sessions the user opted OUT of the deferred Telegram alert
# (docs/dashboard.md, *Telegram alerts*), stored under one kv key as a
# {session_id: True} map. Global like hidden-dirs — the mute is a dashboard
# preference, not session state, so it survives park and applies live or parked.
# An un-mute DELETES the key so the map stays the small set of muted sids, never
# a row per session ever seen.
NOTIFY_MUTE_KEY = "notify-muted"


def notify_muted(session_id):
    """True when `session_id` is opted out of the deferred Telegram alert."""
    value = stored_object(NOTIFY_MUTE_KEY).get(str(session_id), False)
    if not isinstance(value, bool):
        raise TypeError("notification mute must be a boolean")
    return value


def set_notify_muted(session_id, muted):
    """Mute (or un-mute) the Telegram alert for `session_id`; returns the updated map.
    Atomic read-modify-write (mutate_map) so two concurrent mute toggles can't
    lose each other's change; best-effort like set()."""
    def _apply(d):
        if muted:
            d[str(session_id)] = True
        else:
            d.pop(str(session_id), None)
    return mutate_map(NOTIFY_MUTE_KEY, _apply)
