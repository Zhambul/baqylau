# dashboard/config.py — the web dashboard's configuration vocabulary.
#
# The one owner of the server's tunable constants and env-knob reads: ports and
# cadences, the CORS/origin allow-list, the static-file whitelist, the request
# caps, and the notification timing/switches. Split out of server.py so the rest
# of the dashboard tier reads a knob from ONE place (config.X) rather than a
# module-global re-encoded per file. Import-pure: only env reads + literals, no
# I/O, no DB, no frontend (docs/architecture.md import-time purity rule).
import os
import re
import time

from core import tabs

HOST = "127.0.0.1"                 # never a routable interface (see header)
PORT = int(os.environ.get("CLAUDE_DASH_PORT") or 8377)
LOCK_KEY = "dashboard"             # the claims-table key in paths.DASH_DB

TICK_S = 0.6                       # per-session SSE poll cadence
GLOBAL_TICK_S = 1.0                # sessions-list SSE + notification watcher cadence
SLOW_EVERY = 5                     # slow re-resolves (chain, win map), in ticks
HEARTBEAT_S = 15.0                 # SSE keep-alive comment cadence
SESSIONS_LIMIT = 50                # discovery depth for the list + the win map
STATS_TTL_S = 30                   # /api/stats memo: the Stats page aggregates the
#                                    WHOLE audit history, so a short WALL-CLOCK memo
#                                    (distinct from the per-state-DB _db_sig memos —
#                                    this keys on time) makes re-opening cheap without
#                                    serving hours-stale numbers.
STATS_TOP_PROJECTS = 8             # top-N projects in each Pulse window's bar list
RESUMABLE_MAX = 25                 # new-session resume picker: rows shown per dir
RESUMABLE_SCAN = 2000              # …and how deep it discovers to search history
GZIP_MIN = 1024                    # compress a _send body only at/above this size
POST_MAX = 64 * 1024               # request-body cap for the control-plane POSTs
# The composer-attachment upload endpoint (post_upload) carries base64-encoded
# bytes, so it gets its OWN, larger cap — ~14 MiB admits a base64-inflated 10 MB
# image (Claude's per-image ceiling) with headroom for the JSON envelope. Every
# other POST stays at the tiny POST_MAX default.
UPLOAD_MAX = 14 * 1024 * 1024
# The frontend-audit (clientlog) batch cap: most events per POST we'll persist as
# `web-client` rows (a page can't flood the audit with an oversized batch — the
# rest is silently dropped, the ring on the client already bounds normal volume).
CLIENTLOG_MAX = 64
# Per-event scalar fields we keep from a clientlog event (keys outside this set are
# dropped, so the page can't stuff arbitrary bulk into the audit). Strings capped.
CLIENTLOG_FIELD_MAX = 24
CLIENTLOG_STR_MAX = 200
# Image content types the composer treats as inline screenshots (thumbnailed,
# and always admitted). Non-image files are still allowed as attachments, just
# size-capped and shown as a filename chip. Kept in sync with Claude's vision
# formats (docs/dashboard.md, *Web attachments*).
IMAGE_MIMES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
POST_HEADER = "X-Claude-Dash"      # the custom header a simple cross-origin POST can't add
# The only Origins a legit same-origin browser POST carries (it usually sends
# none at all for same-origin fetches; when it does, it is one of these).
# CLAUDE_DASH_ORIGINS extends the set for a proxied deployment (cloudflared /
# tailscale serve — docs/remote.md): comma-separated FULL origins, scheme and
# all (e.g. "https://dash.zhambyl.top"). The knob adds origins, never replaces
# the local ones, and is NOT an exposure switch — the bind stays 127.0.0.1;
# only an outbound connector on this machine can front the port.
def extra_origins(raw):
    """CLAUDE_DASH_ORIGINS → the set of extra allowed origins (comma-separated,
    whitespace-tolerant, empty entries dropped)."""
    return {o.strip() for o in (raw or "").split(",") if o.strip()}


SCREEN_CLIP = 2000     # cap on a bail's captured screen in an audit errors row


def _clip_screen(scr, cap=SCREEN_CLIP):
    """Bound a captured `get_text` screen for the audit `errors` context while
    keeping BOTH diagnostic ends. A plain `scr[-cap:]` kept only the TAIL, but a
    `step:open` bail's discriminator — is the ☐/☒ header-chip bar present at the
    TOP? — lives at the HEAD (dialog-too-tall vs footer-drift vs blank capture,
    docs/dashboard.md *Web ask*): a WIDE window whose visible screen exceeds
    `cap` would have an on-screen chip bar truncated away and read as
    'off-screen'. Keep the head and the tail with a marker between."""
    if not scr or len(scr) <= cap:
        return scr
    half = cap // 2
    return scr[:half] + "\n…[%d chars elided]…\n" % (len(scr) - cap) + scr[-half:]


ALLOWED_ORIGINS = ({"http://%s:%d" % (HOST, PORT), "http://localhost:%d" % PORT}
                   | extra_origins(os.environ.get("CLAUDE_DASH_ORIGINS")))
# CLAUDE_DASH_READONLY=1 switches the control plane off entirely (every POST
# is 403) — remote eyes, no remote hands, whatever the proxy in front allows.
READONLY = (os.environ.get("CLAUDE_DASH_READONLY") or "") == "1"

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
STATIC = {                         # whitelist — no path resolution on user input
    "index.html": "text/html; charset=utf-8",
    # the SPA is served as the ordered app.NN-*.js parts, admitted by shape in
    # http/base.py (_APP_PART) — no per-part whitelist entry, and no monolithic
    # app.js anymore.
    "style.css": "text/css; charset=utf-8",
    # the Web Push service worker — served from the ROOT path (/sw.js, its own
    # route) so its scope is the whole origin, not just /static/ (a SW controls
    # only paths under its own URL). docs/dashboard.md *Web push*.
    "sw.js": "text/javascript; charset=utf-8",
    # the installed-app manifest + home-screen icons (docs/dashboard.md
    # *Installed-app polish*). The manifest is referenced from /static/ so it
    # rides the normal static route; iOS reads the apple-touch-icon link.
    "manifest.webmanifest": "application/manifest+json; charset=utf-8",
    "apple-touch-icon.png": "image/png",
    "icon-180.png": "image/png",
    "icon-192.png": "image/png",
    "icon-512.png": "image/png",
    "icon-maskable-512.png": "image/png",
}

# The two tab transitions worth a toast (core/tabs.py vocabulary): red — Claude
# is asking you; green — done, your turn.
NOTIFY_STATES = {tabs.AWAITING_COMMAND: "asking", tabs.AWAITING_RESPONSE: "done"}

# Deferred off-device (Telegram) alerts, layered on the same red/green
# transitions the in-page toast fires on (docs/dashboard.md, *Telegram alerts*).
# The alert is ARMED on the transition and only actually SENT if the tab is
# STILL in that state after the grace window — i.e. you didn't react (answer,
# resume the turn, or close the session) in time. Browser-independent: it fires
# whether or not a page is open, since reaching you when away is the point.
def _notify_delay():
    """CLAUDE_DASH_NOTIFY_DELAY_S → grace seconds before a Telegram alert fires
    (default 60). A bad / negative value falls back to the default."""
    try:
        v = float(os.environ.get("CLAUDE_DASH_NOTIFY_DELAY_S") or 60)
    except ValueError:
        return 60.0
    return v if v >= 0 else 60.0


NOTIFY_DELAY_S = _notify_delay()
# Master switch: "0" disables arming + sending entirely (the in-page toast is
# unaffected). Default on.
NOTIFY_TELEGRAM = (os.environ.get("CLAUDE_DASH_NOTIFY_TELEGRAM") or "1") != "0"
# The ON-DEVICE Web Push channel (docs/dashboard.md, *Web push*): the same
# deferred, grace-windowed, mute-honoring alert as Telegram, delivered to every
# subscribed browser (an installed iOS home-screen app, a desktop page) as a
# real system notification. Layered on — INDEPENDENT of — Telegram: either
# channel arms the pending alert, and each fires only if its own switch is on.
# Effectively off anyway when the crypto backend is missing (webpush.enabled()).
NOTIFY_WEBPUSH = (os.environ.get("CLAUDE_DASH_NOTIFY_WEBPUSH") or "1") != "0"
# The on-device push goes to the ONE device you most recently used (see
# _mru_push_targets), not every subscription — so a session going done/asking
# alerts only the device you're working on, never all of them at once. Telegram
# then ESCALATES: it fires as a nudge only if, ESCALATE_S after that on-device
# push, you STILL haven't acted on the session (a reaction / a look drops the
# arm in the cancel loop first). So the order is device-first, Telegram-if-
# ignored — not the old "either/or". Telegram is ALSO the immediate fallback
# when there's no device to push to at all (nobody subscribed).
def _escalate_delay():
    """CLAUDE_DASH_ESCALATE_S → seconds after the on-device push before Telegram
    nudges (default 300 = 5 min). Bad / negative → the default."""
    try:
        v = float(os.environ.get("CLAUDE_DASH_ESCALATE_S") or 300)
    except ValueError:
        return 300.0
    return v if v >= 0 else 300.0


ESCALATE_S = _escalate_delay()
# Force BOTH channels at the FIRST send (device push AND Telegram together, no
# escalation wait) — the opt-out of the device-first/escalate model, e.g. you
# always want the Telegram copy too. Default off.
NOTIFY_TELEGRAM_ALWAYS = (os.environ.get("CLAUDE_DASH_NOTIFY_TELEGRAM_ALWAYS") or "") == "1"
# The reused `notify` skill script (Telegram bot). Overridable for a different
# transport / for the hermetic test's recorder; ~ is expanded.
NOTIFY_CMD = os.path.expanduser(
    os.environ.get("CLAUDE_DASH_NOTIFY_CMD")
    or "~/.claude/skills/notify/scripts/notify.py")
# The base URL the alert's deep link points at — the PUBLIC (proxied) origin,
# not the bind: a Telegram alert lands on your phone, where http://127.0.0.1 is
# useless. Defaults to the cloudflared/tailscale front (docs/remote.md);
# CLAUDE_DASH_PUBLIC_URL overrides (trailing slash tolerated).
NOTIFY_URL_BASE = (os.environ.get("CLAUDE_DASH_PUBLIC_URL")
                   or "https://baqylau.zhambyl.top").rstrip("/")

# Tab states during which a composer send lands in Claude Code's own message
# QUEUE (a turn is in progress — the TUI queues typed input and delivers it
# when the turn ends) rather than starting a turn immediately. The /message
# response reports it (`queued`) so the page can show the message as pending
# until it surfaces in the transcript. awaiting-command (red) is deliberately
# NOT here: a dialog is up and typed text goes to the DIALOG, not the queue.
QUEUE_TABS = (tabs.THINKING, tabs.WORKING, tabs.EXECUTING)

# Tab states in which the session is MID-TURN — where Claude Code's double-Esc
# means "cancel the work and restore the last message for editing", not the
# rewind menu (post_rewind mirrors that split). awaiting-command (red) is
# DELIBERATELY NOT here: red means a MODAL DIALOG is open (AskUserQuestion /
# ExitPlanMode / a permission prompt), and an Esc there does not "cancel a
# turn" — it DECLINES/dismisses the dialog. A cancel-edit gesture's Esc-Esc
# once landed on an open ask and killed the very answer the user was giving via
# the web ask card ("User declined to answer questions", 2026-07-20). The
# dashboard has dedicated cards for those states (ask/plan/confirm), so every
# Esc-sending gesture REFUSES on a red tab instead — see _dialog_open_guard,
# mirroring post_command's own awaiting-command 409.
BUSY_TABS = (tabs.THINKING, tabs.WORKING, tabs.EXECUTING, tabs.AWAITING_BG)

DRAFT_CLEAR_GAP_S = 0.15           # settle between killing the restored draft
#                                    (ctrl+u/k) and the bracketed paste of the
#                                    edited resend (post_message clear_draft)
DOUBLE_ESC_GAP_S = 0.15            # beat between the cancel-edit gesture's two
#                                    Escapes — measured 3/3 reliable mid-turn
#                                    (the idle rewind-menu detection is flaky at
#                                    every gap, which is why THAT path types
#                                    /rewind instead — see post_rewind)

# Interrupt verification (post_interrupt / _escape_press). A single synthesized
# Escape via `kitten @ send-key` is only ~2/3 reliable (kitty reports no
# per-window delivery), so a blind press silently misses — a fresh web-launched
# turn ran to completion despite ok:true (2026-07-24, session a16a181f). A
# BUSY-tab interrupt is now VERIFIED against Claude Code's working spinner
# (WORKING_MARKERS) and re-pressed WHILE it is still up — but never on an idle
# box (a stray Esc there could open /rewind). INTERRUPT_RETRY_S sits well above
# DOUBLE_ESC_GAP_S so two spaced retries never read as a double-Esc (a lone
# late Esc at an idle prompt is a harmless no-op).
INTERRUPT_TRIES = 3                # verification passes (up to 2 re-presses)
INTERRUPT_RETRY_S = 0.4            # settle before each re-probe / re-press
WORKING_MARKERS = ("tok/s",)      # lowercased substrings Claude Code renders
#                                    while a turn is ACTIVELY producing output —
#                                    the live output-rate footer `out: NNN tok/s`
#                                    (measured 2026-07-24: it appears only while
#                                    generating, is absent when idle/stopped, and
#                                    unlike the animated spinner glyph / gerund
#                                    it is a FIXED literal that never collides
#                                    with an agent-activity line's `↓ Nk tokens`).
#                                    Their ABSENCE from the viewport = the turn is
#                                    not streaming (interrupted, idle, or the rare
#                                    pre-first-token thinking window — then we
#                                    can't confirm and just don't retry). Screen-
#                                    scraped like the ghost suggestion — no hook
#                                    fires for it. Version-fragile: if Claude Code
#                                    drops the rate footer, re-measure a live
#                                    working window (docs/CONTRIBUTING-style note
#                                    in CLAUDE.md, *Experimenting with live
#                                    sessions*) and update this literal.

_SID_OK = re.compile(r"^[A-Za-z0-9._-]+$")     # a mirror-log key, post-sanitize

# This process's identity, sent as the global SSE `hello` event. A page that
# reconnects and sees a DIFFERENT boot id knows the server restarted under it
# and its loaded JS may be stale (the client toasts "refresh").
BOOT_ID = str(int(time.time() * 1000))


# --- control-plane validation vocabulary (the /command, rename, new-session
# endpoints) --------------------------------------------------------------------
EFFORTS = ("low", "medium", "high", "xhigh", "max")   # claude --effort levels
_MODEL_OK = re.compile(r"^[A-Za-z0-9._-]+$")   # an alias or full model id — one
                                               # clean argv word, nothing else
# The scoreboard's quick-command row (post_command, docs/dashboard.md *Web
# quick commands*): model args are _MODEL_OK's one-clean-word alphabet plus
# the CLI's literal `[1m]` context suffix (`/model sonnet[1m]`); effort args
# are the same EFFORTS levels the launch form validates.
_MODEL_ARG_OK = re.compile(r"^[A-Za-z0-9._-]+(\[1m\])?$")
RENAME_MAX = 120     # rename display cap — picker/tab truncate anyway; a
                     # protocol-abuse guard on the appended record, not a format limit
_NAME_CTRL = re.compile(r"[\x00-\x1f\x7f]+")   # control bytes never enter a name:
                                               # it goes VERBATIM to set-tab-title
                                               # and the picker — the OSC/CSI
                                               # injection class neutralize() exists for
