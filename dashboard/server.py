# dashboard/server.py — the web dashboard package's PUBLIC FACADE.
#
# The dashboard was decomposed into config / read / notify / control / http
# subpackages (docs/architecture.md). This module now only re-exports the
# surface that bin/ (serve) and the test suite reach through `dashboard.server`.
# The design notes below describe the SERVER that now lives in dashboard/http/.
#
# A thin localhost server over the read-side session API (core/sessionapi.py)
# and the plugins.activity() drill-down — the dashboard is a CONSUMER like the
# pane renderers, with a browser instead of a pty. Design decisions inherited
# from docs/sessionapi.md's dashboard notes (each rejects a specific trap):
#
#   * READ-ONLY, bound to 127.0.0.1 — never a routable interface; the page
#     shows raw command output and transcripts.
#   * ThreadingHTTPServer + per-request fresh mode=ro reads — NOT the OTLP
#     receiver's single-threaded loop (sqlite thread-affinity is incompatible
#     with concurrent SSE streams). Every read here goes through the API's
#     *_at()/fresh-conn paths; the server holds no cross-thread connection.
#     In particular ops are read via ops_at() on the RESOLVED DB path, never
#     ops_after() — the live-path readers go through connect(), which CREATES
#     the DB and would fake the session-alive signal for a parked session.
#   * Singleton via core/locks.py pid-lock on paths.DASH_DB plus the port bind
#     as the second guard; explicit serve lifecycle (start/stop/serve CLI) —
#     NOT the receiver's 900s idle-exit + respawn-on-SessionStart, which would
#     leave the dashboard down exactly when browsing parked sessions.
#   * Audit shape: the bin/ entry spawns `serve` via core/spawn.spawn_detached
#     (the A.spawn row) and serve() runs inside core.tail.stream_lifecycle
#     (kind='dashboard'), so the server's lifetime is a streams row with a
#     real end_reason (stopped / lock-denied / port-busy / crash).
#   * HTML-escaping (dashboard/opshtml.py) is the neutralize() analog.
#
# The notification watcher (toasts): one daemon thread diffs the global tab
# DB's whole table (sessionapi.tab_states) once a second and maps windows to
# their NEWEST audited session (sessions rows carry kitty_window_id). A
# transition to awaiting-command (red — Claude is asking you) or
# awaiting-response (green — done, your turn) is pushed to every connected
# /events client, which shows the toast / OS notification. Window-keyed by
# nature: a headless/daemon session has no window and therefore no toasts,
# same as it has no tab colour. The SAME transitions also arm a DEFERRED
# off-device Telegram alert (the reused `notify` skill) that fires only if the
# tab is still in that state after a grace window — you didn't react — and the
# session isn't muted (docs/dashboard.md, *Telegram alerts*).
# Behaviour lives in the subpackages; these bare imports exist only so the
# historical `dashboard.server.X` module handles (tests, bin) keep resolving.
import time  # noqa: F401  -- DS.time (tests patch DS.time.monotonic)

import frontends  # noqa: F401  -- DS.frontends
import plugins  # noqa: F401  -- DS.plugins
from core import paths as P  # noqa: F401  -- DS.P
from core import sessionapi as API  # noqa: F401  -- DS.API
from core import spawn as SP  # noqa: F401  -- DS.SP
from core.noaudit import load_audit
from dashboard import askdialog, confirmdialog, plandialog, prefs, \
    rewindmenu, webpush  # noqa: F401  -- DS.<dialog>/prefs/webpush test handles

A = load_audit()   # always-on audit trail (CLAUDE_AUDIT=0 disables); inert stub if it can't import


# Config vocabulary lives in dashboard/config.py; server.py re-exports it so the
# historical `server.X` reads (tests, bin) keep resolving, and reads the module
# as `config` for the knobs the notifier consults live.
from dashboard import config  # noqa: F401  -- re-exported as DS.config for live-knob patch targets
from dashboard.config import (  # noqa: F401  -- facade re-export of the config surface
    ALLOWED_ORIGINS, BOOT_ID, BUSY_TABS, CLIENTLOG_FIELD_MAX, CLIENTLOG_MAX,
    CLIENTLOG_STR_MAX, DOUBLE_ESC_GAP_S, DRAFT_CLEAR_GAP_S, ESCALATE_S,
    GLOBAL_TICK_S, GZIP_MIN, HEARTBEAT_S, HOST, IMAGE_MIMES, LOCK_KEY,
    NOTIFY_CMD, NOTIFY_DELAY_S, NOTIFY_STATES, NOTIFY_TELEGRAM,
    NOTIFY_TELEGRAM_ALWAYS, NOTIFY_URL_BASE, NOTIFY_WEBPUSH, POST_HEADER,
    POST_MAX, PORT, QUEUE_TABS, READONLY, RESUMABLE_MAX, RESUMABLE_SCAN,
    SCREEN_CLIP, SESSIONS_LIMIT, SLOW_EVERY, STATIC, STATIC_DIR,
    STATS_TOP_PROJECTS, STATS_TTL_S, TICK_S, UPLOAD_MAX, _SID_OK,
    _clip_screen, extra_origins,
)


# --- notification watcher ----------------------------------------------------
# The presence signals + the tab-diff Notifier live in dashboard/notify/;
# server.py re-exports the presence helpers its POST handlers call and the
# NOTIFIER singleton the SSE + launch-wake paths push to.
from dashboard.notify import notifier, presence  # noqa: F401  -- module handles for tests
from dashboard.notify.notifier import NOTIFIER, Notifier  # noqa: F401
from dashboard.notify.presence import (  # noqa: F401  -- facade re-export
    VIEW_TTL_S, _DEVICE_SEEN, _VIEWING, _composing, _device_seen, _mark_device,
    _mark_viewing, _mru_push_targets, _session_ended, _web_viewing,
)


# The read-side presentation model lives in dashboard/read/ (lists / session /
# mirror, over meta + cache). server.py (the HTTP layer) re-exports the payload
# builders it serves plus the few the control-plane POSTs and the tests reach.
from dashboard.read import lists, mirror, session  # noqa: F401  -- module handles for tests
from dashboard.read.lists import (  # noqa: F401  -- facade re-export
    accounts_payload, dir_live_sessions, resumable_payload, sessions_payload,
    stats_payload, _row_key, _wire_row,
)
from dashboard.read.meta import (  # noqa: F401  -- facade re-export
    canon_cwd, git_info, session_ctx, session_goal, session_title, _group_dir,
    _session_slug,
)
from dashboard.read.session import (  # noqa: F401  -- facade re-export
    agents_ctx, agents_model_effort, session_payload, visible_agents,
    _ask_draft, _ask_pending, _ask_wire, _chip_delivered, _composer_draft,
    _composer_queue, _delivered_prompts, _dialog_pending, _last_prompt,
    _plan_pending, _session_tasks, _stamp_agent_cost, _suggestion, _SUGGEST_TABS,
)
from dashboard.read.mirror import (  # noqa: F401  -- facade re-export
    history, merged_backlog, note_payload, ops_payload, view_payload,
    HISTORY_BLOCKS, _conv_items, _enrich_entries, _heal_stash, _mdify,
)


# The terminal-facing control machinery lives in dashboard/control/launch.py;
# the control-plane validation constants moved to config.py. Callers reach the
# frontend/live-window resolvers MODULE-QUALIFIED (launch._frontend /
# launch._live_windows) so a test patches the one owning module.
from dashboard.control import launch  # noqa: F401
from dashboard.control.launch import (  # noqa: F401  -- facade re-export
    launch_argv, _clear_clipboard_image, _clip_has_image, _front_app,
    _launch_wake, _steal_watch, _within_live_grace,
)
from dashboard.config import (  # noqa: F401  -- control-plane validation vocabulary
    EFFORTS, RENAME_MAX, _MODEL_ARG_OK, _MODEL_OK, _NAME_CTRL,
)


# --- the HTTP layer ----------------------------------------------------------
# The ~2400-line Handler was split into base/get/post/sse mixins composed in
# dashboard/http/handler.py; server.py re-exports the entry points bin/ (serve)
# and the tests reach through `dashboard.server`.
from dashboard.http.handler import Handler, serve  # noqa: F401  -- facade re-export
