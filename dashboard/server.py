# dashboard/server.py — the web dashboard package's PUBLIC FACADE.
#
# The dashboard was decomposed into config / read / notify / control / http
# subpackages (docs/architecture.md). This module now only re-exports the
# surface that bin/ (serve) and the test suite reach through `dashboard.server`.
# The design notes below describe the SERVER that now lives in dashboard/http/.
#
# A name belongs here only while something ACTUALLY reaches it through
# `dashboard.server` — a third of the original list was reached by nobody, which
# reads as a supported API for internals that had simply moved. New code inside
# the package imports its owner directly (the dependency direction config ←
# read/control/notify ← http); this file exists for the historical `DS.X` handles
# alone, and a re-export nothing consults should be deleted, not kept "for
# symmetry". The one thing that must NOT be re-exported flat is a KNOB — see the
# config note below.
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
#   * HTML-escaping (dashboard/opshtml/ansi.py) is the neutralize() analog.
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
from dashboard import prefs, telegram, webpush  # noqa: F401  -- DS.prefs/transport test handles
# The five Claude SCREEN DRIVERS (askdialog / plandialog / rewindmenu /
# confirmdialog / suggestion) are NOT re-exported here any more: they moved into
# plugins/claude_code/ with the gestures that drive them (P2), and this facade
# is the DASHBOARD's surface. A test that drives one reaches its owner directly
# (`from plugins.claude_code import askdialog`), which is also where its knobs
# now live.

A = load_audit()   # always-on audit trail (CLAUDE_AUDIT=0 disables); inert stub if it can't import


# Config vocabulary lives in dashboard/config.py, reached MODULE-QUALIFIED
# (`DS.config.X`) — there are deliberately NO flat `DS.<KNOB>` aliases here. A
# flat alias would be worse than dead surface: it is a patch TRAP. Every reader
# of a live knob reads `config.X` (the styleguide's rule, so a test can move it),
# so `monkeypatch.setattr(DS, "NOTIFY_DELAY_S", 0)` would bind a name nobody
# consults and pass while changing nothing.
from dashboard import config  # noqa: F401  -- DS.config: the knob surface + patch target


# --- notification watcher ----------------------------------------------------
# The presence signals, the /events BROKER and the tab-diff Notifier live in
# dashboard/notify/; server.py re-exports the presence helpers its POST handlers
# call, the bus the SSE + launch-wake paths push to, and the watcher singleton.
# `channels` is the notifier's delivery/retraction surface — a module handle so a
# test patches sends and retractions at their one owner.
from dashboard.notify import broker, channels, notifier, presence  # noqa: F401  -- module handles for tests
from dashboard.notify.broker import BROKER, Broker  # noqa: F401
from dashboard.notify.notifier import NOTIFIER, Notifier  # noqa: F401
from dashboard.notify.presence import (  # noqa: F401  -- facade re-export
    TERMINAL, VIEW_TTL_S, composing, device_active, device_seen, mark_device,
    mark_terminal, mark_viewing, route, session_ended, web_viewing,
)
# NOTE: VIEW_TTL_S is the one number here a test must patch on the OWNER
# (`DS.presence.VIEW_TTL_S`) — this alias is a read handle.
#
# The two in-memory presence MAPS are deliberately NOT re-exported: they are
# module STATE, not surface, and a test that reaches them says so by naming
# their owner (`DS.presence._VIEWING`). A flat alias would also be a trap of the
# same shape as a flat config knob — it binds the object, so a rebind on the
# owner would leave the alias pointing at the old dict.


# The read-side presentation model lives in dashboard/read/ (lists / session /
# mirror, over meta + cache). server.py (the HTTP layer) re-exports the payload
# builders it serves plus the few the control-plane POSTs and the tests reach.
from dashboard.read import lists, mirror, session  # noqa: F401  -- module handles for tests
from dashboard.read.lists import (  # noqa: F401  -- facade re-export
    accounts_payload, dir_live_sessions, sessions_payload, stats_payload,
    row_key,
)
from dashboard.read.meta import (  # noqa: F401  -- facade re-export
    canon_cwd, session_title, group_dir, session_slug,
)
from dashboard.read.session import (  # noqa: F401  -- facade re-export
    session_payload, ask_pending, chip_delivered, composer_draft,
    composer_queue, last_prompt, plan_pending, session_compacting,
)
from dashboard.read.mirror import (  # noqa: F401  -- facade re-export
    agent_scope, history, merge_live, merged_backlog, view_payload, conv_items,
)


# The terminal-facing control machinery lives in dashboard/control/launch.py;
# the control-plane validation constants moved to config.py. Callers reach the
# frontend/live-window resolvers MODULE-QUALIFIED (launch.frontend /
# launch.live_windows) so a test patches the one owning module.
from dashboard.control import launch  # noqa: F401
from dashboard.control.launch import (  # noqa: F401  -- facade re-export
    launch_argv, clear_clipboard_image, launch_wake, within_live_grace,
)


# --- the HTTP layer ----------------------------------------------------------
# The ~2400-line Handler was split into base/get/post/sse mixins composed in
# dashboard/http/handler.py; server.py re-exports the entry points bin/ (serve)
# and the tests reach through `dashboard.server`.
from dashboard.http.handler import Handler, Server, serve  # noqa: F401  -- facade re-export
from dashboard.http import sse  # noqa: F401  -- DS.sse: the stream's channel tables
# The two POST modules that own their OWN tuning knobs (the interrupt/verify
# timings, the draft-clear settle) rather than parking them in config.py — a
# test patches the owner, so it needs a handle on it.
from dashboard.http.post import interrupt as post_interrupt  # noqa: F401
from dashboard.http.post import typing as post_typing  # noqa: F401
