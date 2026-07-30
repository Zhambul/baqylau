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

from core import env as EV
from core import tabs

HOST = "127.0.0.1"                 # never a routable interface (see header)
PORT = EV.env_int("CLAUDE_DASH_PORT", 8377)
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
# The listen(2) backlog. The socketserver default is FIVE, and a page refresh
# through the cloudflared tunnel is a parallel burst of ~16 origin connections
# (the 14 app.NN-*.js parts + style.css + the first API calls) — every
# connection past the queue is reset by the kernel, which cloudflared surfaces
# as "connection reset by peer" → a 502 for the document, or a half-loaded page
# throwing one ReferenceError per missing part (docs/dashboard.md
# *Cache-busting*; found from ~/Library/Logs/dash-tunnel.log, 2026-07-27).
BACKLOG = 128
# Versioned static assets (?v=<BOOT_ID>, see http/base.py static()) are
# immutable AT THAT URL: the stamp changes on every restart, and static bytes
# only change via a restart (the "does NOT hot-reload" contract, CLAUDE.md), so
# a browser may keep them for the max year. Everything else stays no-store.
CACHE_STATIC = "public, max-age=31536000, immutable"
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
    # the RASTER fallback favicon, served from the ROOT path (/favicon.ico, its
    # own route) because that path is what a client AUTO-DISCOVERS when it can
    # make no use of the declared SVG icon — iOS Safari, which supports SVG
    # favicons in no version (macOS Safari only since 26). Deliberately NOT
    # given a <link rel="icon"> of its own: an declared raster icon would
    # out-rank the data-URI SVG in browsers that handle both, and the SVG is the
    # one that carries the dynamic red asking-you badge (app.01-attention.js
    # FAVICON_ASK). Auto-discovery is exactly fallback-only semantics.
    # docs/dashboard.md *Favicon fallback*.
    "favicon.ico": "image/vnd.microsoft.icon",
    "apple-touch-icon.png": "image/png",
    "icon-180.png": "image/png",
    "icon-192.png": "image/png",
    "icon-512.png": "image/png",
    "icon-maskable-512.png": "image/png",
}

# The two tab transitions worth a toast (core/tabs.py vocabulary): red — Claude
# is asking you; green — done, your turn.
NOTIFY_STATES = {tabs.AWAITING_COMMAND: "asking", tabs.AWAITING_RESPONSE: "done"}

# Off-device alerts, layered on the same red/green transitions the in-page toast
# fires on (docs/dashboard.md, *Presence routing* / *Telegram alerts*). The
# alert is ARMED on the transition and sent on the SAME tick unless PRESENCE
# says you're already there. Browser-independent: it fires whether or not a page
# is open, since reaching you when away is the point.
# CLAUDE_DASH_NOTIFY_DELAY_S → grace seconds before the alert fires, DEFAULT 0.
# It used to be 60: wait a minute, and if the tab is still red assume you didn't
# react. Presence answers that question directly (are you at this device, is
# this session in front of you), so the clock's only remaining job is as an
# optional debounce for anyone who wants their alerts to hold fire. A bad /
# negative value falls back to the default.
NOTIFY_DELAY_S = EV.env_float("CLAUDE_DASH_NOTIFY_DELAY_S", 0)
# CLAUDE_DASH_NOTIFY_SETTLE_S → the extra wait a `done` alert serves before it
# fires, DEFAULT 20. The one place the two kinds need different clocks, because
# their tab states mean different things: red ASKING is a blocked session that
# will sit there until you act, so a delay only makes you later; green DONE is
# the resting state of a finished TURN, which the very next turn leaves.
#
# Measured 2026-07-29 over a day of real alerts: of 46 delivered `done` pushes
# that were later retracted `tab-moved`, the MEDIAN lifetime was 14.3 s — the
# turn had ended, the push went out the same second, and by the time a macOS
# banner had settled on screen the session was busy again and the banner was
# correctly deleted. A notification that exists for 14 s is one you never see,
# and 30 of those 46 lived under 20 s. So the fix is not to stop retracting (the
# retraction is right — the alert had genuinely stopped being true) but to stop
# SENDING an alert about a green that hasn't held still yet.
#
# 20 s is the knee of that curve, not a round number: 10 s would have suppressed
# 8/46, 15 s → 25, 20 s → 30, and past it the curve flattens hard (25 s and 30 s
# → 31, 60 s → 32). Everything beyond 20 s buys a couple of points for seconds
# of added latency on the alerts that ARE real. Set 0 for the old fire-instantly
# behaviour. Bad / negative → the default.
NOTIFY_SETTLE_S = EV.env_float("CLAUDE_DASH_NOTIFY_SETTLE_S", 20)
# Master switch: "0" disables arming + sending entirely (the in-page toast is
# unaffected). Default on.
NOTIFY_TELEGRAM = (os.environ.get("CLAUDE_DASH_NOTIFY_TELEGRAM") or "1") != "0"
# The ON-DEVICE Web Push channel (docs/dashboard.md, *Web push*): the same
# presence-routed, mute-honoring alert as Telegram, delivered to a subscribed
# browser (an installed iOS home-screen app, a desktop page) as a real system
# notification. Layered on — INDEPENDENT of — Telegram: either channel arms the
# pending alert, and each fires only if its own switch is on. Effectively off
# anyway when the crypto backend is missing (webpush.enabled()).
NOTIFY_WEBPUSH = (os.environ.get("CLAUDE_DASH_NOTIFY_WEBPUSH") or "1") != "0"
# The alert goes to the ONE device your PRESENCE says you were last on (see
# presence.route), not every subscription — so a session going done/asking
# reaches the device you're at, never all of them at once. A browser gets the
# push; the TERMINAL gets Telegram, since nothing else reaches a machine whose
# browser is shut. Telegram then ESCALATES a push: it fires as a nudge only if,
# ESCALATE_S after that on-device push, you STILL haven't acted on the session
# (a reaction / a look drops the arm in the cancel loop first). There is no
# escalation after a stage-1 Telegram — it already reaches every device you own.
# Telegram is ALSO the fallback when there's nothing to push to (nobody
# subscribed).
# CLAUDE_DASH_ESCALATE_S → seconds after the on-device push before Telegram
# nudges (default 300 = 5 min). Bad / negative → the default.
ESCALATE_S = EV.env_float("CLAUDE_DASH_ESCALATE_S", 300)
# Force BOTH channels at the FIRST send (device push AND Telegram together, no
# escalation wait) — the opt-out of the device-first/escalate model, e.g. you
# always want the Telegram copy too. Default off.
NOTIFY_TELEGRAM_ALWAYS = (os.environ.get("CLAUDE_DASH_NOTIFY_TELEGRAM_ALWAYS") or "") == "1"
# The reused `notify` skill script (Telegram bot). Overridable for a different
# transport / for the hermetic test's recorder; ~ is expanded.
NOTIFY_CMD = os.path.expanduser(
    os.environ.get("CLAUDE_DASH_NOTIFY_CMD")
    or "~/.claude/skills/notify/scripts/notify.py")
# RETRACTION (docs/dashboard.md, *Alert retraction*). Once an alert has been
# DELIVERED, the watcher keeps watching the session; when the thing it told you
# about stops being true — the tab left red/green, the session ended, you're
# composing a reply — the alert is taken back: the Telegram message is deleted,
# and a resolve push closes the on-device banner. Note this is a NARROWER
# question than the one that cancels a PENDING alert: a mere glance suppresses
# an alert not yet sent ("you don't need to be told"), but must NOT delete one
# already delivered — looking at a red tab and walking away would then destroy
# your only reminder while the tab is still red. notifier.RETRACT_REASONS is
# where that distinction is declared.
# CLAUDE_DASH_RETRACT_S → how long a delivered alert stays retractable (default
# 24 h). Must stay under telegram.DELETE_WINDOW_S (48 h), the Bot API's own
# ceiling on deleting your own message; past it the alert is simply history and
# an expiry row is audited. Bad / negative → the default.
RETRACT_S = EV.env_float("CLAUDE_DASH_RETRACT_S", 24 * 3600)
# The on-device half of retraction: push a `type:"resolve"` message that makes
# the service worker close the banner. "0" disables it — the Telegram delete
# still happens, and the page's foreground sweep still clears stale banners when
# you next open the app. The kill switch exists because this push deliberately
# raises NO notification, which iOS's userVisibleOnly contract only tolerates on
# a budget (see channels._retract_webpush): if WebKit ever starts answering it
# with placeholder banners, this is the off switch.
RESOLVE_PUSH = (os.environ.get("CLAUDE_DASH_RESOLVE_PUSH") or "1") != "0"
# Hard bound on delivered-but-not-yet-retracted alerts held in memory. RETRACT_S
# is the real bound; this is the backstop for the pathological case (a wedged
# terminal channel, hundreds of sessions) so the watcher's per-tick work and the
# process's memory can't grow without limit. Oldest are dropped first.
SENT_CAP = 200
# The base URL the alert's deep link points at — the PUBLIC (proxied) origin,
# not the bind: a Telegram alert lands on your phone, where http://127.0.0.1 is
# useless. Defaults to the cloudflared/tailscale front (docs/remote.md);
# CLAUDE_DASH_PUBLIC_URL overrides (trailing slash tolerated).
NOTIFY_URL_BASE = (os.environ.get("CLAUDE_DASH_PUBLIC_URL")
                   or "https://baqylau.zhambyl.top").rstrip("/")

# How long the `compacting` latch (plugins/claude_code/compact_fmt.py, armed on
# PreCompact) is believed before the read side ages it out — the ctx bar's
# compaction animation (docs/dashboard.md, *Compaction on the ctx bar*).
# PostCompact normally clears it within ~2 minutes (104-139s across the seven
# runs in the audit trail), but a compaction that dies on an API error or is
# interrupted fires NO closing hook (CLAUDE.md's no-hook-on-cancel invariant;
# the audit holds one such orphan), and the hook process that armed the latch
# is long gone and can't retract it. So the EXPIRY lives here, on the read
# side, where it is re-evaluated every tick: an animation must fail OFF.
# Generous against the measured spread — this is a stuck-forever backstop, not
# a deadline. CLAUDE_DASH_COMPACT_MAX_S overrides; bad / negative → the default.
COMPACT_MAX_S = EV.env_float("CLAUDE_DASH_COMPACT_MAX_S", 15 * 60)

# Tab states during which a composer send lands in Claude Code's own message
# QUEUE (a turn is in progress — the TUI queues typed input and delivers it
# when the turn ends) rather than starting a turn immediately. The /message
# response reports it (`queued`) so the page can show the message as pending
# until it surfaces in the transcript. awaiting-command (red) is deliberately
# NOT here: a dialog is up and typed text goes to the DIALOG, not the queue.
QUEUE_TABS = (tabs.THINKING, tabs.WORKING, tabs.EXECUTING)


# Tab states in which the session is MID-TURN — where an Escape means "stop the
# turn" (post_interrupt), and where the rewind MENU is therefore unavailable
# (post_rewind refuses; a typed /rewind would just queue as a message).
# awaiting-command (red) is DELIBERATELY NOT here: red means a MODAL DIALOG is
# open (AskUserQuestion / ExitPlanMode / a permission prompt), and an Esc there
# does not stop a turn — it DECLINES/dismisses the dialog. Such a gesture once
# landed on an open ask and killed the very answer the user was giving via the
# web ask card ("User declined to answer questions", 2026-07-20). The dashboard
# has dedicated cards for those states (ask/plan/confirm), so every Esc-sending
# gesture REFUSES on a red tab instead — see _dialog_open_guard, mirroring
# post_command's own awaiting-command 409.
BUSY_TABS = (tabs.THINKING, tabs.WORKING, tabs.EXECUTING, tabs.AWAITING_BG)


SID_OK = re.compile(r"^[A-Za-z0-9._-]+$")     # a mirror-log key, post-sanitize

# This process's identity, sent as the global SSE `hello` event. A page that
# reconnects and sees a DIFFERENT boot id knows the server restarted under it
# and its loaded JS may be stale (the client toasts "refresh").
BOOT_ID = str(int(time.time() * 1000))


# --- control-plane validation vocabulary (the /command, rename, new-session
# endpoints) --------------------------------------------------------------------
EFFORTS = ("low", "medium", "high", "xhigh", "max", "ultra")   # claude --effort
#           levels + codex's `ultra` (gpt-5.6 reasoning tier — the codex enum is
#           low/medium/high/xhigh/max/ultra, verified against the codex binary)
MODEL_OK = re.compile(r"^[A-Za-z0-9._-]+$")   # an alias or full model id — one
                                               # clean argv word, nothing else
# The scoreboard's quick-command row (post_command, docs/dashboard.md *Web
# quick commands*): model args are MODEL_OK's one-clean-word alphabet plus
# the CLI's literal `[1m]` context suffix (`/model sonnet[1m]`); effort args
# are the same EFFORTS levels the launch form validates.
MODEL_ARG_OK = re.compile(r"^[A-Za-z0-9._-]+(\[1m\])?$")
RENAME_MAX = 120     # rename display cap — picker/tab truncate anyway; a
                     # protocol-abuse guard on the appended record, not a format limit
NAME_CTRL = re.compile(r"[\x00-\x1f\x7f]+")   # control bytes never enter a name:
                                               # it goes VERBATIM to set-tab-title
                                               # and the picker — the OSC/CSI
                                               # injection class neutralize() exists for
