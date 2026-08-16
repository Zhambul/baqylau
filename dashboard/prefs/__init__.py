"""What YOU chose: cross-session, cross-device, and true only for you.

    store.py          the kv table itself — open it, read a key, write a key
    directories.py    the project directories you hid from the list
    newsession.py     the new-session form's last picks and unsent prompts
    notifications.py  alerting on or off, globally and per session
    viewmode.py       one session's mirror density
    push.py           the devices subscribed for on-device push
    tasks.py          the task cards you dismissed

One module owns one key, and gives it a typed shape; nothing outside them names
a key. The whole API is re-exported here, so a caller says what it wants
(`prefs.notify_muted(...)`) without knowing which key answers it.

Nothing here is a fact about a session — those live in `engine/`, are folded
from evidence, and are the same for everyone. These are yours, and a fresh
machine simply has none of them.
"""

from dashboard.prefs.directories import HIDDEN_KEY, hidden_dirs, hide_dir
from dashboard.prefs.newsession import (
    NEW_SESSION_DRAFT_KEY,
    NEW_SESSION_DRAFT_LIMIT,
    new_session_drafts,
    set_new_session_draft,
)
from dashboard.prefs.notifications import (
    NOTIFY_ENABLED_KEY,
    NOTIFY_MUTE_KEY,
    notify_enabled,
    notify_muted,
    set_notify_enabled,
    set_notify_muted,
)
from dashboard.prefs.push import (
    PUSH_SUBS_KEY,
    add_push_subscription,
    push_subscriptions,
    remove_push_subscription,
)
from dashboard.prefs.store import get, mutate_map, set, stored_object
from dashboard.prefs.tasks import (
    TASKS_HIDE_KEY,
    TASKS_HIDE_MAX,
    set_tasks_hidden,
    tasks_hidden_ids,
)
from dashboard.prefs.viewmode import (
    VIEW_DEFAULT,
    VIEW_MODE_KEY,
    VIEW_MODES,
    set_view_mode,
    view_mode,
)

__all__ = [
    "HIDDEN_KEY", "NEW_SESSION_DRAFT_KEY", "NEW_SESSION_DRAFT_LIMIT",
    "NOTIFY_ENABLED_KEY", "NOTIFY_MUTE_KEY", "PUSH_SUBS_KEY",
    "TASKS_HIDE_KEY", "TASKS_HIDE_MAX",
    "VIEW_DEFAULT", "VIEW_MODES", "VIEW_MODE_KEY",
    "add_push_subscription", "get", "hidden_dirs", "hide_dir", "mutate_map",
    "new_session_drafts", "notify_enabled", "notify_muted",
    "push_subscriptions", "remove_push_subscription", "set",
    "set_new_session_draft", "set_notify_enabled", "set_notify_muted",
    "set_tasks_hidden", "set_view_mode", "stored_object", "tasks_hidden_ids",
    "view_mode",
]
