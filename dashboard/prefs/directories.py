"""Directories you hid from the list page, and when you hid them."""


from dashboard.prefs.store import mutate_map, stored_object


# --- hidden directories (the list page's ✕) ------------------------------------
# The set of project directories the user hid from the crowded list page
# (docs/dashboard.md, *Hidden directories*), stored under one kv key as
# {group_key: hidden_at_epoch}. Non-destructive: nothing is closed, the group
# just disappears from view. It re-appears the moment a session STARTED after
# hidden_at shows up in it — but that comparison is CLIENT-side (app.js
# dirHidden, over each wire row's started_at); this store only holds the stamp.
HIDDEN_KEY = "hidden-dirs"


def hidden_dirs():
    """The {group_key: hidden_at_epoch} map."""
    return stored_object(HIDDEN_KEY)


def hide_dir(key, ts):
    """Stamp `key` hidden at epoch `ts` and persist; returns the updated map.
    A re-hide (a re-appeared group hidden again) just overwrites with the newer
    time, which is what re-hides it. Atomic read-modify-write (mutate_map) so a
    second concurrent hide can't lose this stamp; best-effort like set()."""
    return mutate_map(HIDDEN_KEY, lambda d: d.__setitem__(str(key), float(ts)))
