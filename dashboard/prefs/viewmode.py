"""How dense you want one session's mirror: verbose, default, or focus."""

from dashboard.prefs.store import mutate_map, stored_object


# --- the mirror's VIEW MODE, per session (the view bar's 3-way control) ----------
# Which of harness TUI's three native history densities the session's web mirror is
# rendered at (docs/dashboard.md, *View modes*), stored under one kv key as a
# {session_id: mode} map.
#
# DELIBERATELY per-session and NOT a global default: switching one session to
# focus must not silently re-render every other one (a mode is a per-session
# reading choice, unlike the alerts switch, which is one machine-wide policy).
#
# A session nobody switched opens at DEFAULT — the same mode harness TUI's own
# `viewMode` defaults to, so the dashboard reads like the TUI it mirrors. It
# shipped defaulting to `verbose` (the mode that hides nothing) while the collapse
# was new and unproven; nothing is actually lost at `default` — every collapsed
# run is one click from expanded, and mutations, messages and the ⚠ warning line
# never fold at all — so the cautious default outlived its reason.
#
# This ONLY ever changes what the browser paints. harness TUI has its own
# `viewMode` setting for the TUI and this store is not it: nothing here is
# written into any settings.json, and the terminal mirror keeps painting
# everything at every mode.
#
# Global like the other dashboard prefs (it survives park, so a parked session
# re-opens at the mode you last read it in), and a mode set back to the default
# DELETES the key so the map stays the small set of overridden sessions.
VIEW_MODE_KEY = "view-mode"
# The mode vocabulary, in CONTROL order — the segmented control reads
# verbose → default → focus, densest to sparsest, which is why the default is not
# simply the first entry (the two are deliberately decoupled).
VIEW_MODES = ("verbose", "default", "focus")
VIEW_DEFAULT = "default"


def view_mode(session_id):
    """The stored view mode for `session_id`, or VIEW_DEFAULT when unset."""
    mode = stored_object(VIEW_MODE_KEY).get(str(session_id), VIEW_DEFAULT)
    if mode not in VIEW_MODES:
        raise ValueError(f"unknown stored view mode: {mode!r}")
    return mode


def set_view_mode(session_id, mode):
    """Persist `mode` as the view mode for `session_id`; returns the updated map.
    Atomic read-modify-write (mutate_map) so two sessions' concurrent switches
    can't lose each other's entry; best-effort like set(). The default mode is
    stored as an ABSENCE (see the note above)."""
    def _apply(d):
        if mode == VIEW_DEFAULT:
            d.pop(str(session_id), None)
        else:
            d[str(session_id)] = mode
    return mutate_map(VIEW_MODE_KEY, _apply)
