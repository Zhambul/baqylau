"""Task cards you dismissed, and the list they belonged to."""

import time

from dashboard.prefs.store import mutate_map, stored_object


# --- the pinned tasks card's ✕ (a finished list, dismissed) ---------------------
# Which sessions had their tasks card DISMISSED (docs/dashboard.md, *Web tasks*),
# stored under one kv key as {session_id: {ids, ts}}. PURELY VISUAL, like
# view-mode and unlike every other ✕ on the page: no task is completed, deleted
# or touched by this — harness TUI's own task records are never written by the
# dashboard at all (they are the TUI's, and the `tasks` kv is only a snapshot of
# them). The card just stops being painted.
#
# Global like the other dashboard prefs precisely BECAUSE the dismissal has to
# follow you: hiding a finished list on the phone must not leave it pinned on the
# desktop, and localStorage would give every device its own answer. It survives
# park, like the list it hides.
#
# The stored value is the ID SET that was hidden, not a bare True, because the
# card must COME BACK when the list moves on. The ✕ is only offered once EVERY
# task is completed (the server enforces that, not just the button's disabled
# state), so "still hidden" means "still that same finished list": a new task —
# or a completed one re-opened — makes the current list differ and the card
# re-appears on its own. That is the whole un-hide story; a bare flag would need
# a button to undo it, and a card you dismissed would otherwise swallow the next
# thing harness TUI planned.
#
# `ts` is the hide time, kept only to PRUNE: the map would otherwise gain a row
# per session whose list was ever dismissed, forever (renamed-title accepts that
# because a rename is a handful of sessions; a finished task list is most of
# them). Same recency prune as the new-session drafts.
TASKS_HIDE_KEY = "tasks-hidden"
TASKS_HIDE_MAX = 200


def _tasks_hide_entry(d):
    """Validate and return one stored {ids, ts} dismissal."""
    if not isinstance(d, dict):
        raise TypeError("task dismissal must contain an object")
    ids = d.get("ids")
    ts = d.get("ts")
    if not isinstance(ids, list) or not all(isinstance(task_id, str) for task_id in ids):
        raise TypeError("task dismissal ids must be strings")
    if not isinstance(ts, (int, float)) or isinstance(ts, bool):
        raise TypeError("task dismissal timestamp must be a number")
    return {"ids": ids, "ts": ts}


def tasks_hidden_ids(session_id):
    """The task ids `session_id`'s dismissed card covered ([] when never dismissed /
    unreadable). The CALLER decides whether that dismissal still applies to the
    current list — see dashboard/read/session.py tasks_hidden, the one predicate
    (this store is a dumb kv, like hidden-dirs)."""
    document = stored_object(TASKS_HIDE_KEY)
    record = document.get(str(session_id))
    return _tasks_hide_entry(record)["ids"] if record is not None else []


def set_tasks_hidden(session_id, ids, ts=None):
    """Dismiss `session_id`'s tasks card for exactly the task `ids` (a list), or RESTORE
    it when `ids` is None (the entry is deleted, so the map stays the small set of
    dismissed sessions). Prunes to TASKS_HIDE_MAX by recency. Returns the updated
    map; atomic read-modify-write (mutate_map), best-effort like set()."""
    stamp = time.time() if ts is None else float(ts)

    def _apply(d):
        if ids is None:
            d.pop(str(session_id), None)
            return
        d[str(session_id)] = {"ids": [str(i) for i in ids], "ts": stamp}
        if len(d) > TASKS_HIDE_MAX:
            # oldest-first by ts, keeping this write (it has the newest clock)
            for k in sorted(d, key=lambda k: _tasks_hide_entry(d[k])["ts"]
                            )[:len(d) - TASKS_HIDE_MAX]:
                d.pop(k, None)
    return mutate_map(TASKS_HIDE_KEY, _apply)
