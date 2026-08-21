# dashboard/config.py — the dashboard presenter tier's configuration vocabulary.
#
# What remains here after the HTTP layer moved to api/: the knobs the
# dashboard OWNS — its public origin, its static assets, its notification
# timing and switches, and its projection limits. The HTTP contract
# (host, port, headers, body caps) lives in core/daemon/contract.py; server policy
# (origins, read-only, caching) lives in api/config.py. Import-pure: only env
# reads + literals, no I/O, no DB, no frontend.
import os

from core import env as EV

GLOBAL_REFRESH_SECONDS = 1.0
INSIGHTS_PROJECT_LIMIT = 8
RESUMABLE_SESSION_LIMIT = 25                 # new-session resume picker: rows shown per dir
RESUMABLE_SCAN = 2000              # …and how deep it discovers to search history

# The dashboard's externally reachable origin.  It is one fact with two
# consumers: browser POST admission (api/config.py ALLOWED_ORIGINS) and
# notification deep links.  Keeping those separate allowed a dashboard started
# outside launchd to generate correct public links while rejecting POSTs from
# that same public page. A Telegram alert lands on your phone, where
# http://127.0.0.1 is useless — so the deep links use this, never the bind.
PUBLIC_URL = (os.environ.get("BAQYLAU_DASHBOARD_PUBLIC_URL")
              or "https://baqylau.zhambyl.top").rstrip("/")

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
STATIC = {                         # whitelist — no path resolution on user input
    "index.html": "text/html; charset=utf-8",
    # the SPA is served as the ordered app.NN-*.js parts, admitted by shape in
    # api/application/static.py (_APP_PART) — no per-part whitelist entry.
    "style.css": "text/css; charset=utf-8",
    # the Web Push service worker — served from the ROOT path (/sw.js, its own
    # route) so its scope is the whole origin, not just /static/ (a SW controls
    # only paths under its own URL).
    "sw.js": "text/javascript; charset=utf-8",
    # the installed-app manifest + home-screen icons. The manifest is referenced from /static/ so it
    # rides the normal static route; iOS reads the apple-touch-icon link.
    "manifest.webmanifest": "application/manifest+json; charset=utf-8",
    # the RASTER fallback favicon, served from the ROOT path (/favicon.ico, its
    # own route) because that path is what a client AUTO-DISCOVERS when it can
    # make no use of the declared SVG icon — iOS Safari, which supports SVG
    # favicons in no version (macOS Safari only since 26). Deliberately NOT
    # given a <link rel="icon"> of its own: a declared raster icon would
    # out-rank the data-URI SVG in browsers that handle both, and the SVG is the
    # one that carries the dynamic red asking-you badge (app.01-attention.js
    # FAVICON_ASK). Auto-discovery is exactly fallback-only semantics.
    "favicon.ico": "image/vnd.microsoft.icon",
    "apple-touch-icon.png": "image/png",
    "icon-180.png": "image/png",
    "icon-192.png": "image/png",
    "icon-512.png": "image/png",
    "icon-maskable-512.png": "image/png",
}


# Off-device alerts, layered on the same red/green transitions the in-page toast
# fires on. The
# alert is ARMED on the transition and sent on the SAME tick unless PRESENCE
# says you're already there. Browser-independent: it fires whether or not a page
# is open, since reaching you when away is the point.
# BAQYLAU_DASHBOARD_NOTIFICATION_DELAY_SECONDS → grace seconds before the alert fires, DEFAULT 0.
# It used to be 60: wait a minute, and if the tab is still red assume you didn't
# react. Presence answers that question directly (are you at this device, is
# this session in front of you), so the clock's only remaining job is as an
# optional debounce for anyone who wants their alerts to hold fire. A bad /
# negative value falls back to the default.
NOTIFICATION_DELAY_SECONDS = EV.env_float("BAQYLAU_DASHBOARD_NOTIFICATION_DELAY_SECONDS", 0)
# BAQYLAU_DASHBOARD_NOTIFICATION_SETTLE_SECONDS → the extra wait a `done` alert serves before it
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
NOTIFICATION_SETTLE_SECONDS = EV.env_float("BAQYLAU_DASHBOARD_NOTIFICATION_SETTLE_SECONDS", 20)
# Master switch: "0" disables arming + sending entirely (the in-page toast is
# unaffected). Default on.
NOTIFY_TELEGRAM = (os.environ.get("BAQYLAU_DASHBOARD_NOTIFY_TELEGRAM") or "1") != "0"
# The ON-DEVICE Web Push channel: the same
# presence-routed, mute-honoring alert as Telegram, delivered to a subscribed
# browser (an installed iOS home-screen app, a desktop page) as a real system
# notification. Layered on — INDEPENDENT of — Telegram: either channel arms the
# pending alert, and each fires only if its own switch is on. Effectively off
# anyway when the crypto backend is missing (webpush.enabled()).
NOTIFY_WEBPUSH = (os.environ.get("BAQYLAU_DASHBOARD_NOTIFY_WEBPUSH") or "1") != "0"
# The alert goes to the ONE device your PRESENCE says you were last on (see
# presence.route), not every subscription — so a session going done/asking
# reaches the device you're at, never all of them at once. A browser gets the
# push; the TERMINAL gets Telegram, since nothing else reaches a machine whose
# browser is shut. Telegram then ESCALATES a push: it fires as a nudge only if,
# ESCALATION_DELAY_SECONDS after that on-device push, you STILL haven't acted on the session
# (a reaction / a look drops the arm in the cancel loop first). There is no
# escalation after a stage-1 Telegram — it already reaches every device you own.
# Telegram is ALSO the fallback when there's nothing to push to (nobody
# subscribed).
# BAQYLAU_DASHBOARD_ESCALATION_DELAY_SECONDS → seconds after the on-device push before Telegram
# nudges (default 300 = 5 min). Bad / negative → the default.
ESCALATION_DELAY_SECONDS = EV.env_float("BAQYLAU_DASHBOARD_ESCALATION_DELAY_SECONDS", 300)
# Force BOTH channels at the FIRST send (device push AND Telegram together, no
# escalation wait) — the opt-out of the device-first/escalate model, e.g. you
# always want the Telegram copy too. Default off.
NOTIFY_TELEGRAM_ALWAYS = (os.environ.get("BAQYLAU_DASHBOARD_NOTIFY_TELEGRAM_ALWAYS") or "") == "1"
# RETRACTION. Once an alert has been
# DELIVERED, the watcher keeps watching the session; when the thing it told you
# about stops being true — the tab left red/green, the session ended, you're
# composing a reply — the alert is taken back: the Telegram message is deleted,
# and a resolve push closes the on-device banner. Note this is a NARROWER
# question than the one that cancels a PENDING alert: a mere glance suppresses
# an alert not yet sent ("you don't need to be told"), but must NOT delete one
# already delivered — looking at a red tab and walking away would then destroy
# your only reminder while the tab is still red. notifier._resolve enforces
# that distinction by comparing the delivered state with the current state.
# BAQYLAU_DASHBOARD_RETRACTION_LIFETIME_SECONDS → how long a delivered alert stays retractable (default
# 24 h). Must stay under telegram.DELETE_WINDOW_SECONDS (48 h), the Bot API's own
# ceiling on deleting your own message; past it the alert is simply history and
# an expiry row is audited. Bad / negative → the default.
RETRACTION_LIFETIME_SECONDS = EV.env_float("BAQYLAU_DASHBOARD_RETRACTION_LIFETIME_SECONDS", 24 * 3600)
# The on-device half of retraction: push a `type:"resolve"` message that makes
# the service worker close the banner. "0" disables it — the Telegram delete
# still happens, and the page's foreground sweep still clears stale banners when
# you next open the app. The kill switch exists because this push deliberately
# raises NO notification, which iOS's userVisibleOnly contract only tolerates on
# a budget (see channels._retract_webpush): if WebKit ever starts answering it
# with placeholder banners, this is the off switch.
RESOLVE_PUSH = (os.environ.get("BAQYLAU_DASHBOARD_RESOLVE_PUSH") or "1") != "0"
# Hard bound on delivered-but-not-yet-retracted alerts held in memory. RETRACTION_LIFETIME_SECONDS
# is the real bound; this is the backstop for the pathological case (a wedged
# terminal channel, hundreds of sessions) so the watcher's per-tick work and the
# process's memory can't grow without limit. Oldest are dropped first.
SENT_CAP = 200

RENAME_CHARACTER_LIMIT = 120
