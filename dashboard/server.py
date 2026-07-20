# dashboard/server.py — the web dashboard's HTTP server.
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
import gzip
import json
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

import frontends
import plugins
from core import copy as CP
from core import locks
from core import paths as P
from core import sessionapi as API
from core import spawn as SP
from core import state as ST
from core import tabs
from core.noaudit import load_audit
from core.tail import stream_lifecycle
from dashboard import askdialog, confirmdialog, dictate, opshtml, \
    plandialog, prefs, rewindmenu

A = load_audit()   # always-on audit trail (CLAUDE_AUDIT=0 disables); inert stub if it can't import

HOST = "127.0.0.1"                 # never a routable interface (see header)
PORT = int(os.environ.get("CLAUDE_DASH_PORT") or 8377)
LOCK_KEY = "dashboard"             # the claims-table key in paths.DASH_DB

TICK_S = 0.6                       # per-session SSE poll cadence
GLOBAL_TICK_S = 1.0                # sessions-list SSE + notification watcher cadence
SLOW_EVERY = 5                     # slow re-resolves (chain, win map), in ticks
HEARTBEAT_S = 15.0                 # SSE keep-alive comment cadence
SESSIONS_LIMIT = 50                # discovery depth for the list + the win map
GZIP_MIN = 1024                    # compress a _send body only at/above this size
POST_MAX = 64 * 1024               # request-body cap for the control-plane POSTs
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
    "app.js": "text/javascript; charset=utf-8",
    "style.css": "text/css; charset=utf-8",
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

_SID_OK = re.compile(r"^[A-Za-z0-9._-]+$")     # a mirror-log key, post-sanitize

# This process's identity, sent as the global SSE `hello` event. A page that
# reconnects and sees a DIFFERENT boot id knows the server restarted under it
# and its loaded JS may be stale (the client toasts "refresh").
BOOT_ID = str(int(time.time() * 1000))


# --- notification watcher -----------------------------------------------------------

def _session_ended(sid):
    """True when the session has a recorded SessionEnd (audit `ended_at` set) —
    it was closed/quit, so a pending Telegram alert is moot. A MISSING row is
    deliberately NOT ended: a transient read miss must never suppress a live
    session's alert (the fire path re-checks anyway)."""
    if not sid:
        return False
    return bool((API.session_row(sid) or {}).get("ended_at"))


class Notifier:
    """The tab-DB diff watcher + the /events fan-out. Clients register a
    Queue; the watcher thread pushes ('notify', payload) on every asking/done
    transition (the in-page toast + OS notification). Also keeps the win ->
    session map the payloads are named from (refreshed on the slow cadence —
    sessions come and go rarely).

    It ALSO drives the deferred off-device Telegram alert: each asking/done
    transition arms `self.pending[win]`; a later scan SENDS it iff the tab is
    still in that state after NOTIFY_DELAY_S (you didn't react) and the session
    isn't muted — otherwise the entry is dropped when the tab moves off that
    state OR the session ends (you closed it / moved on)."""

    def __init__(self):
        self.clients = set()
        self.lock = threading.Lock()
        self.prev = {}
        self.winmap = {}
        self.pending = {}              # win -> dict(payload, armed_at, state)

    def register(self):
        q = queue.Queue(maxsize=100)
        with self.lock:
            self.clients.add(q)
        return q

    def unregister(self, q):
        with self.lock:
            self.clients.discard(q)

    def push(self, event, payload):
        with self.lock:
            clients = list(self.clients)
        for q in clients:
            try:
                q.put_nowait((event, payload))
            except queue.Full:
                pass                       # a stalled client just misses toasts

    def refresh_winmap(self):
        m = {}
        for row in API.sessions(SESSIONS_LIMIT):
            win = row.get("kitty_window_id")
            # newest-first: the first (newest) session keeps the window
            if win and win not in m:
                m[win] = row
        self.winmap = m

    def _payload(self, kind, state, row):
        # a worktree session's toast names the PROJECT (the owning main
        # checkout), not the worktree dir — same root||cwd resolution the
        # list page groups by (git_info is cached, cheap here)
        cwd = canon_cwd(row.get("cwd") or "")
        home = (git_info(cwd) or {}).get("root") or cwd
        return {
            "kind": kind, "state": state, "sid": row.get("sid"),
            "cwd": cwd,
            "project": os.path.basename(home) or row.get("sid"),
            # resolved at push time, not winmap-refresh time: the title is
            # transcript-derived and the transcript just grew ((path, size)
            # cache in session_title keeps this cheap)
            "title": session_title(row.get("transcript_path") or ""),
        }

    def scan(self):
        cur = API.tab_states()
        prev, self.prev = self.prev, cur
        now = time.monotonic()
        for win, state in cur.items():
            kind = NOTIFY_STATES.get(state)
            if not kind or prev.get(win) == state or not prev:
                continue                   # first scan is baseline, not news
            row = self.winmap.get(win)
            if not row:
                continue
            payload = self._payload(kind, state, row)
            self.push("notify", payload)   # immediate in-page toast + OS notif
            if NOTIFY_TELEGRAM:             # arm the deferred off-device alert
                self.pending[win] = dict(payload, armed_at=now, state=state)
        # cancel the ones you reacted to, all before the delay: the tab left its
        # armed state (answered → busy, or the win vanished = tab gone), OR the
        # session ENDED — you closed it / quit, so you were satisfied and moved
        # on and the alert (whose deep link would open a dead session) is moot.
        # ended_at is the robust signal the win-vanish check can miss: a stale
        # tab row can linger, and a reused window id can even re-match the armed
        # state under a DIFFERENT session.
        for win in list(self.pending):
            entry = self.pending[win]
            if cur.get(win) != entry["state"] or _session_ended(entry.get("sid")):
                del self.pending[win]
        # fire the ones that persisted past the grace window (once each)
        for win in list(self.pending):
            entry = self.pending[win]
            if now - entry["armed_at"] < NOTIFY_DELAY_S:
                continue
            del self.pending[win]
            if not prefs.notify_muted(entry.get("sid")):
                self._telegram(entry)

    def _telegram(self, entry):
        """Send the deferred alert via the reused `notify` skill (Telegram),
        detached so a slow round-trip never stalls the 1 s watcher. Best-effort
        + audited; never raises into the loop."""
        asking = entry.get("kind") == "asking"
        proj = entry.get("project") or entry.get("sid") or "session"
        head = ("🔴 %s needs you" if asking else "🟢 %s is done") % proj
        title = entry.get("title") or (
            "Claude is asking a question" if asking else "finished — your turn")
        url = "%s/#/s/%s" % (NOTIFY_URL_BASE, entry.get("sid") or "")
        msg = "%s — %s\n%s" % (head, title, url)
        try:
            subprocess.Popen(
                [sys.executable or "python3", NOTIFY_CMD, msg],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=True)
            A.state_file("", "", "telegram-notify",
                         {"sid": entry.get("sid"), "kind": entry.get("kind")})
        except Exception:
            A.error("", "dashboard telegram notify",
                    {"sid": entry.get("sid")})

    def run(self):
        n = 0
        while True:
            try:
                if n % SLOW_EVERY == 0:
                    self.refresh_winmap()
                self.scan()
            except Exception:
                A.error("", "dashboard notifier")
                time.sleep(5)              # a broken poll must not spin-audit
            n += 1
            time.sleep(GLOBAL_TICK_S)


NOTIFIER = Notifier()


# --- payload builders ----------------------------------------------------------------

# Every path-keyed memo below is a process-lifetime cache in a days-long
# singleton — bounded with API.BoundedLRU so the KEY set (transcript/state-DB
# paths, cwds — one per session ever seen) can't grow without limit. The cap is
# far above the live working set (SESSIONS_LIMIT sessions + their agents), so an
# active session never thrashes; only paths that scrolled out of discovery age
# out, and their re-derivable values just re-read once if seen again.
MEMO_CAP = 8192

_TITLES = API.BoundedLRU(MEMO_CAP)   # transcript_path -> (size, title): a title
#                   only changes when the file grows, so (path, size) is the
#                   natural cache key — the list poll must not re-scan 50
#                   transcript heads per tick


def session_title(tpath):
    if not tpath:
        return ""
    try:
        size = os.path.getsize(tpath)
    except OSError:
        return ""
    hit = _TITLES.get(tpath)
    if hit and hit[0] == size:
        return hit[1]
    title = plugins.session_title(tpath) or ""
    _TITLES[tpath] = (size, title)
    return title


_GIT = API.BoundedLRU(MEMO_CAP)   # cwd -> the _git_resolve result (None = not a
#                   checkout). The ancestor walk + gitdir indirection is stable
#                   for a cwd, so it caches until LRU-evicted; HEAD itself is
#                   re-read on every call (one tiny file) so a branch switch
#                   shows on the next poll.

_DIRTY = API.BoundedLRU(MEMO_CAP)  # cwd -> (monotonic expiry, True|False|None).
#                   The dirty probe is the ONE sanctioned `git` subprocess
#                   here — worktree/index
#                   dirtiness is not derivable from .git metadata (detecting it
#                   IS `git status`'s stat-cache job), so it can't be a file
#                   read like the rest of git_info. The TTL cache bounds it to
#                   one probe per checkout per DIRTY_TTL_S instead of per row
#                   per tick; racing SSE threads at worst duplicate one probe.
DIRTY_TTL_S = 10.0     # dirty staleness bound (matches the slow SSE cadence ~3s
#                        polls: a flip shows within TTL + one tick)
DIRTY_TIMEOUT_S = 1.0  # a huge/network-mounted repo must not stall a poll tick;
#                        timeout -> None (unknown) cached like any other result


def _git_dirty(cwd):
    """Whether the checkout at cwd has uncommitted changes — the status-line
    dirty `*` (claude-hud: any `git status --porcelain` output counts, staged/
    unstaged/untracked alike). --no-optional-locks keeps this read-only
    observer from touching the index; None = unknown (no git, timeout, or a
    broken/fake checkout), which renders as no marker."""
    now = time.monotonic()
    hit = _DIRTY.get(cwd)
    if hit and hit[0] > now:
        return hit[1]
    try:
        res = subprocess.run(
            ["git", "-c", "core.quotePath=false", "--no-optional-locks",
             "status", "--porcelain"],
            cwd=cwd, capture_output=True, timeout=DIRTY_TIMEOUT_S)
        dirty = bool(res.stdout.strip()) if res.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        dirty = None
    _DIRTY[cwd] = (now + DIRTY_TTL_S, dirty)
    return dirty


def _git_resolve(cwd):
    """Walk up from cwd to its checkout: (gitdir, worktree_name, root) — gitdir
    the directory holding HEAD, worktree_name the linked-worktree name when
    `.git` is a FILE pointing into .../worktrees/<name> (a `git worktree add` /
    EnterWorktree checkout), and root the MAIN checkout owning that worktree
    (gitdir is <root>/.git/worktrees/<name>); both None for a main checkout.
    None when cwd is in no checkout. File reads only — never a `git`
    subprocess (this runs per row per poll)."""
    d = cwd
    while d and os.path.isdir(d):
        dotgit = os.path.join(d, ".git")
        if os.path.isdir(dotgit):
            return dotgit, None, None
        if os.path.isfile(dotgit):
            try:
                with open(dotgit, encoding="utf-8", errors="replace") as fh:
                    first = fh.readline().strip()
            except OSError:
                return None
            if not first.startswith("gitdir:"):
                return None
            gd = first[len("gitdir:"):].strip()
            if not os.path.isabs(gd):
                gd = os.path.normpath(os.path.join(d, gd))
            if (os.sep + "worktrees" + os.sep) in gd:
                wt = os.path.basename(gd)
                root = os.path.dirname(os.path.dirname(os.path.dirname(gd)))
            else:
                wt = root = None
            return gd, wt, root
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent
    return None


def canon_cwd(cwd):
    """Resolve a session cwd's symlinks, so the list groups one PROJECT under
    one entry. The 2026-07-19 baqylau rename left ~/code/personal/kitty as a
    symlink to .../baqylau; sessions started before the move (or through the
    old path) record the /kitty spelling — Claude Code reports the logical path
    and a live session re-stamps it on every event — so without canonicalising,
    the list splits one repo into a stale 'kitty' group and a 'baqylau' group.
    realpath collapses them. '' is returned as-is: realpath('') would be the
    dashboard process's OWN cwd, which is never a session's."""
    if not cwd:
        return cwd
    try:
        return os.path.realpath(cwd)
    except OSError:
        return cwd


def git_info(cwd):
    """The checkout state of a session's cwd, for the git chips: {"branch",
    "worktree", "root", "dirty"} — branch the HEAD ref's short name (a 7-char
    sha when detached), worktree the linked-worktree name or None for a main
    checkout, root the MAIN checkout directory owning a linked worktree (None
    for a main checkout — the list page groups sessions by root||cwd, so a
    worktree session files under its project, not its worktree dir), dirty
    the uncommitted-changes flag behind the branch chip's `*` (True/
    False/None-unknown — _git_dirty). None when cwd isn't inside a git
    checkout (or its worktree was removed)."""
    if not cwd:
        return None
    hit = _GIT.get(cwd, False)
    if hit is False:
        hit = _git_resolve(cwd)
        _GIT[cwd] = hit
    if not hit:
        return None
    gitdir, wt, root = hit
    try:
        with open(os.path.join(gitdir, "HEAD"), encoding="utf-8",
                  errors="replace") as fh:
            head = fh.read().strip()
    except OSError:
        return None
    if head.startswith("ref:"):
        ref = head[4:].strip()
        branch = ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref
    else:
        branch = head[:7] or "?"
    return {"branch": branch, "worktree": wt, "root": root,
            "dirty": _git_dirty(cwd)}


_CTX = API.BoundedLRU(MEMO_CAP)   # transcript_path -> (size, ctx): same
#                   (path, size) cache key
#                   as _TITLES — saturation only changes when the file grows, and
#                   the list/agents polls must not re-read every transcript tail
#                   per tick. The main= flag is per-path-constant (a path is
#                   always a main transcript or always an agent's), so it stays
#                   out of the key.


def session_ctx(tpath, main=False):
    """plugins.context() (the {used, window, pct, model} saturation of the
    file's last turn) behind the (path, size) cache; None when unknown."""
    if not tpath:
        return None
    try:
        size = os.path.getsize(tpath)
    except OSError:
        return None
    hit = _CTX.get(tpath)
    if hit and hit[0] == size:
        return hit[1]
    ctx = plugins.context(tpath, main=main)
    _CTX[tpath] = (size, ctx)
    return ctx


def _last_active(row, sdb):
    """The session's last-activity timestamp, for the list card's recency
    chip: the transcript's mtime (the file grows on every turn — the same
    activity signal interrupt-watch and escape-recheck trust), else the audit
    ended_at, else the state DB's mtime (the audit-less minimal parked rows
    carry no transcript path), else started_at. Why not started_at directly:
    an unlabeled "1h ago" reads as staleness, and a live session an hour into
    its work showed exactly that while actively streaming. Why not the audit
    hook_events MAX(ts): a per-row query against the big audit DB per tick vs
    one stat on a path the row already carries — and the audit can be off."""
    tpath = row.get("transcript_path") or ""
    if tpath:
        try:
            return os.path.getmtime(tpath)
        except OSError:
            pass
    if row.get("ended_at"):
        return row["ended_at"]
    try:
        return os.path.getmtime(sdb)
    except OSError:
        return row.get("started_at")


_STATS = API.BoundedLRU(MEMO_CAP)  # state-db path -> (sig, stats): the list poll
#                   must not open
#                   50 sqlite connections per tick — parked DBs never change
#                   and idle live ones change rarely. The sig is _db_sig (DB
#                   file AND -wal stat), not (path, size): a live writer
#                   appends to the WAL without touching the main file until
#                   checkpoint, so the main file's stat alone would serve
#                   stale numbers for exactly the sessions that move.

_ACCT = API.BoundedLRU(MEMO_CAP)  # state-db path -> (sig, (account kv, usage
#                   kv)): same
#                   _db_sig idea — the accounts strip re-scans the same 50
#                   session DBs per fetch, nearly all parked.


# The (path, sig) memo + fingerprint moved to core/sessionapi.py (db_sig/
# db_cached — the accounts read model needed them too); these aliases keep the
# call sites reading as before.
_db_sig = API.db_sig
_db_cached = API.db_cached


def sessions_payload():
    """The sessions list, enriched with what the list view shows per row:
    scoreboard stats (read-only, live or parked), the tab state, the
    display title (plugins.session_title over the transcript), and
    `last_active` (the recency chip / group order / archive boundary —
    _last_active). `live` is
    corrected to require an OPEN tab (see _live_windows): a session whose state
    DB lingers but whose tab is gone (closed without a SessionEnd — crash/kill,
    or a leaked DB) is demoted to not-live so it can't masquerade as running."""
    tabstates = API.tab_states()
    live_wins = _live_windows()
    out = []
    for row in API.sessions(SESSIONS_LIMIT):
        sdb = P.state_db(row["log"])
        if not os.path.isfile(sdb):
            sdb = P.parked_db(row["log"])
        # demote a state-DB-live session whose window is gone. Only when we can
        # actually enumerate windows (live_wins is not None) and the session
        # ever HAD a window (a headless/daemon session legitimately has none) —
        # and NOT within the just-started grace (a fresh launch's pane isn't
        # tagged yet; _within_live_grace).
        if (row.get("live") and live_wins is not None
                and row.get("kitty_window_id") and row["sid"] not in live_wins
                and not _within_live_grace(row)):
            row["live"] = False
        st = _db_cached(_STATS, sdb, API.stats_at)
        row["stats"] = st
        row["tab"] = tabstates.get(str(row.get("kitty_window_id") or "")) or ""
        row["title"] = session_title(row.get("transcript_path") or "")
        row["ctx"] = session_ctx(row.get("transcript_path") or "", main=True)
        row["cwd"] = canon_cwd(row.get("cwd") or "")   # collapse the /kitty symlink
        row["git"] = git_info(row["cwd"])
        row["last_active"] = _last_active(row, sdb)
        out.append(row)
    return out


def dir_live_sessions(key):
    """The live sessions whose list-page group key equals `key` — the SAME
    grouping app.js groupSessions does (git.root || cwd || ""), over the SAME
    window-corrected `live` sessions_payload computes (a state-DB-live session
    whose tab is gone is already demoted to not-live, so it doesn't count). This
    is the hide guard's truth: a directory with an active session can't be
    hidden (docs/dashboard.md *Hidden directories*). sessions_payload is not the
    cheapest call, but a hide is a rare user action, and reusing it keeps the
    'is this dir active' answer byte-identical to what the page shows."""
    return [r for r in sessions_payload()
            if r.get("live")
            and ((r.get("git") or {}).get("root") or r.get("cwd") or "") == key]


def _wire_row(r):
    """A sessions row as the PAGE consumes it: minus `transcript_path` and
    `log` — server-side paths the client never reads, ~20% of the snapshot's
    bytes at 50 rows. sessions_payload keeps them internally (the notifier's
    winmap and the title/ctx caches resolve through them); only the two wire
    exits (`/api/sessions`, the global SSE) strip."""
    return {k: v for k, v in r.items() if k not in ("transcript_path", "log")}


def _row_key(wire_row):
    """ONE wire row's change-detection key: the row minus stats['paused'].
    The scorebar accrues that float ~once per second for every session
    sitting at a prompt (its awaiting-pause accumulator), which would make
    the row differ on EVERY tick. Only the diff is paused-blind: a pushed
    row still carries the exact value, and the card's ⏱ (elapsed MINUS
    paused) is constant while paused accrues, so the frozen card a
    suppressed push leaves behind is already showing the right number."""
    st = wire_row.get("stats") or {}
    return json.dumps(
        dict(wire_row, stats={k: v for k, v in st.items() if k != "paused"}),
        default=str, sort_keys=True)


def accounts_payload():
    """The launchable accounts + their latest known usage, for the new-session
    picker AND the dashboard's top usage strip. Registry from
    plugins.accounts(); the per-slug freshest `usage`/`limit-hit` aggregation
    is core/sessionapi.account_usage (shared with the rate-limit migration's
    target picker — docs/relimit.md). Per-account by construction — each
    snapshot came from a session running under that account's own token
    (docs/dashboard.md). No API call, no token. Everything the page shows is
    server-computed (single-owner rule): `usage` is the EFFECTIVE snapshot
    (sessionapi.effective_usage — a rolled-over 5h/7d window is zeroed and
    its reset dropped, so a stale snapshot can't render 'resets now'
    forever), `five_hour_eff` the load-balancing 5h figure the new-session
    form preselects by, and `limit_hit` the still-active limit stamp
    (else None).

    The one exception to 'no API call': per-MODEL weekly windows (e.g.
    `seven_day_fable`) exist in NO tokenless channel, so plugins.model_windows
    fetches them from the OAuth /usage endpoint (piggybacking Claude Code's
    keychain login — docs/dashboard.md 'Per-model usage bars') and they are
    MERGED into `usage`, after which the generic renderer paints them like any
    other window. five_hour_eff/limit_hit stay on the tokenless snapshot; the
    merge only ADDS windows, so a missing/failed fetch simply omits them.

    One live-data override on the pill: a MODEL-scoped limit_hit stamp carries
    no reset epoch (the CLI message doesn't state one), so limit_hit_active
    falls back to 'blocked for a week from the hit'. When the fetched live
    window for that very model reads BELOW 100%, the cap has demonstrably
    cleared (Anthropic resets limits mid-week sometimes — reported
    2026-07-20), so the stale stamp is dropped here. Dashboard-presentation
    only — core (the relimit target picker) stays tokenless and keeps its
    conservative week-long fallback."""
    per = API.account_usage(SESSIONS_LIMIT, cache=_ACCT)
    model_win = plugins.model_windows(cache=_ACCT)
    out = []
    for a in plugins.accounts():
        ent = per.get(a["slug"]) or {}
        usage, hit = ent.get("usage"), ent.get("limit_hit")
        mw = model_win.get(a["slug"])
        if mw:                                   # per-model windows the tokenless
            usage = dict(usage or {}, **mw)      # snapshot can't carry
        active = API.limit_hit_active(hit)
        if active and (hit or {}).get("model"):
            pct = (mw or {}).get("seven_day_%s" % hit["model"])
            if isinstance(pct, (int, float)) and pct < 100:
                active = False                   # live window says the cap cleared
        out.append(dict(
            a, usage=API.effective_usage(usage),
            five_hour_eff=API.effective_five_hour(ent.get("usage")),
            limit_hit=hit if active else None))
    return out


def visible_agents(agents):
    """Drop HIDDEN-agent bookkeeping rows: a SubagentStop with no
    SubagentStart (Claude Code's hidden auxiliary agents — the subagent
    finaliser's 'never started (hidden agent)' path) leaves an agents-table
    row with EVERY field empty. Zero user-facing signal, so the dashboard
    filters them; any row with at least one real field (kind, desc, slot,
    transcript, a start time) stays. The API keeps reporting them — this is
    presentation policy, not truth policy."""
    return [a for a in agents
            if a.get("kind") or a.get("desc") or a.get("transcript")
            or a.get("slot") is not None or a.get("started_at")]


def agents_ctx(agents):
    """Stamp each agent row with its own transcript's context saturation
    (session_ctx over the streams-keystone src_path — an agent transcript is
    its sidechain turns, so main=False). Rows whose file yields nothing (husk
    rows, codex rollouts — no codex context provider yet) stay unstamped."""
    for a in agents:
        ctx = session_ctx(a.get("transcript") or "")
        if ctx:
            a["ctx"] = ctx
    return agents


def agents_model_effort(agents, effort):
    """Stamp each agent row with the short model id + effort level it runs — the
    web card's echo of the terminal mirror's `opus-4.8·high` op tag
    (substream.op_tag). The model rides FREE on the ctx probe agents_ctx already
    stamped (ctx["model"] is the raw id of the agent's last assistant turn, from
    transcript.context_probe), so no extra file read; effort mirrors the
    substream's `EFFORT_CFG or model_default_effort()` — the session's saved
    effort, else the running model's default (a frontmatter/env per-agent effort
    override, the substream's higher-precedence source, isn't readable here and
    is the one divergence). Rows with no ctx (husks, not-yet-started agents) stay
    unstamped, exactly as their ctx bar does."""
    from plugins.claude_code import model as M
    for a in agents:
        raw = (a.get("ctx") or {}).get("model") or ""
        if not raw:
            continue
        a["model"] = M.short_model(raw)
        eff = effort or M.model_default_effort(raw)
        if eff:
            a["effort"] = eff
    return agents


def _stamp_agent_cost(tl):
    """Stamp a subagent drill-down payload with `cost` — approximate USD for its
    OWN token rollup, priced from `usage` + the run's last model via the shared
    accountant (the web per-agent scoreboard's ≈cost, docs/dashboard.md *Subagent
    scoreboard swap*). None for an unknown/empty model (codex runs, husk reads) —
    the client just omits the ≈cost chip. This transcript pricing is the ONLY
    per-agent cost figure: OTEL `costs()` is aggregate by query_source
    (main/subagent/auxiliary), never attributable to a single agent_id."""
    from plugins.claude_code import accounting as ACC
    u = tl.get("usage") or {}
    if not u:
        return
    tl["cost"] = ACC.cost_usd(tl.get("model"), u.get("in", 0), u.get("out", 0),
                              u.get("cache", 0), u.get("create", 0),
                              u.get("create_1h", 0))


def _session_slug(sid):
    """The session's subscription-account slug from its statusline stash
    ('' for the default account / no stash) — resolves WHICH user-level
    settings the effort read consults."""
    sdb = API.state_db_for(sid)
    return ((API.kv_at(sdb, "account") or {}).get("slug") or "") if sdb else ""


def session_payload(sid):
    """One session's overview — session() plus the error count the ⚠ badge
    shows (full rows stay behind /errors) and the display title."""
    data = API.session(sid)
    data["agents"] = agents_ctx(visible_agents(data.get("agents") or []))
    data["error_count"] = API.error_count(sid)
    data["monitor_count"] = API.monitor_count(sid)   # the monitors tab badge
    data["job_count"] = API.job_count(sid)           # the jobs tab badge
    data["title"] = session_title(data.get("transcript_path") or "")
    data["ctx"] = session_ctx(data.get("transcript_path") or "", main=True)
    data["cwd"] = canon_cwd(data.get("cwd") or "")   # collapse the /kitty symlink
    data["git"] = git_info(data["cwd"])
    # the effort quick-button's label (docs/dashboard.md, *Web quick
    # commands*): the SAVED effort level — every /effort persists itself
    # there, so it is the last applied value; per-session effort is readable
    # from nowhere else. Resolved for the session's ACCOUNT (its statusline-
    # stashed slug picks the config dir — accounts each carry their own
    # settings.json)
    data["effort"] = plugins.effort_default(data.get("cwd") or "",
                                            _session_slug(sid))
    # the agent cards' per-agent model·effort — reuses the ctx just stamped, so
    # the session effort resolved above is its inherit-default
    agents_model_effort(data["agents"], data["effort"])
    data["running"] = API.running(sid)
    # Correct `live` to require an OPEN tab and gate the control plane on the
    # LIVE window (the pane currently tagged claude_session=<sid>), NOT the
    # audit row's start-time id — kitty reuses window ids, so a leaked/parked
    # "live" session would otherwise show a stop button that closes an
    # unrelated tab (see _live_windows). A session whose state DB lingers but
    # whose window is gone (closed without a SessionEnd) is demoted to not-live.
    live_wins = _live_windows()
    row = API.session_row(sid) or {}
    if (data.get("live") and live_wins is not None
            and row.get("kitty_window_id") and sid not in live_wins
            and not _within_live_grace(row)):
        data["live"] = False
    data["kitty_window_id"] = (live_wins or {}).get(sid, "") if data.get("live") else ""
    data["ask"] = _ask_pending(sid) if data.get("live") else None
    data["ask_draft"] = _ask_draft(sid, data["ask"]) if data.get("ask") else None
    data["plan"] = _plan_pending(sid) if data.get("live") else None
    # deliberately NOT live-gated: the `tasks` kv survives park (Claude Code
    # deletes the on-disk task files at session end — the stash is the only
    # record left), so a parked session still shows its final task list
    data["tasks"] = _session_tasks(sid)
    # deliberately NOT live-gated: the composer stays usable on a PARKED
    # session (the resume-&-send door), so its draft must restore there too
    data["composer_draft"] = _composer_draft(sid)
    data["composer_queue"] = _composer_queue(sid)
    # deliberately NOT live-gated: the Telegram-alert opt-out is a dashboard
    # pref (docs/dashboard.md, *Telegram alerts*), so the header toggle reflects
    # + flips it live AND parked
    data["notify_muted"] = prefs.notify_muted(sid)
    return data


def _dialog_pending(sid, key):
    """A pending modal-dialog stash (`ask-pending` / `plan-pending`), or None
    — the kv rows plugins/claude_code/ask_fmt.py maintains (write on
    PreToolUse, cleared on answer/turn-boundary). Read-only (kv_at — never
    creates the state DB). The endpoints verify the DIALOG on screen anyway,
    so a stale stash can never mis-answer."""
    sdb = API.state_db_for(sid)
    if not sdb:
        return None
    pending = API.kv_at(sdb, key)
    return pending if isinstance(pending, dict) else None


def _ask_pending(sid):
    return _dialog_pending(sid, "ask-pending")


def _ask_draft(sid, ask=None):
    """The unsubmitted ask answers (the `ask-draft` kv — written by the web
    ask card so a device switch / reopen restores in-progress selections),
    but ONLY when it still matches the OPEN ask: a draft left over from a
    replaced/answered question is ignored (ask_fmt.py clears it on the turn
    boundary anyway). Read-only (kv_at). None when there's no ask, no draft,
    or a tool_use_id mismatch."""
    ask = ask if ask is not None else _ask_pending(sid)
    if not ask:
        return None
    sdb = API.state_db_for(sid)
    if not sdb:
        return None
    draft = API.kv_at(sdb, "ask-draft")
    if not isinstance(draft, dict):
        return None
    if (draft.get("tool_use_id") or "") != (ask.get("tool_use_id") or ""):
        return None
    return draft


def _composer_draft(sid):
    """The UNSENT composer text (the `composer-draft` kv — written by the web
    composer so a device switch / reopen / return-to-session restores the
    half-typed message, docs/dashboard.md, *Web composer draft*). Read-only
    (kv_at — never creates the state DB; resolves the parked copy for a parked
    session, so a resume-&-send draft survives too). None when there's no draft
    or the stored text is empty — None keeps the composer blank."""
    sdb = API.state_db_for(sid)
    if not sdb:
        return None
    draft = API.kv_at(sdb, "composer-draft")
    if not isinstance(draft, dict) or not (draft.get("text") or "").strip():
        return None
    return draft


def _composer_queue(sid):
    """The still-PENDING queued messages (the `composer-queue` kv — the ⧗ chips
    the composer shows for messages typed mid-turn that the TUI queued and has
    not yet delivered). Browser memory alone lost these on a reload (the "gone
    even from the queue after refresh" report, 2026-07-19), so the page mirrors
    its chip list here; a delivered message is reconciled out client-side when
    its prompt lands in the stream. Read-only (kv_at). {"items": [{text}, …],
    "origin": …} or None when empty (docs/dashboard.md, *Web composer queue*)."""
    sdb = API.state_db_for(sid)
    if not sdb:
        return None
    q = API.kv_at(sdb, "composer-queue")
    if not isinstance(q, dict) or not (q.get("items") or []):
        return None
    return q


def _session_tasks(sid):
    """The session's task-list snapshot — the `tasks` kv task_fmt.py re-reads
    from Claude Code's on-disk task dir on every task-touching hook (docs/
    dashboard.md, *Web tasks*). A list of task records ({id, subject, status,
    …}, id-sorted), or None when the session never had tasks / the list is
    empty — None keeps the card hidden. Read-only (kv_at)."""
    sdb = API.state_db_for(sid)
    if not sdb:
        return None
    stash = API.kv_at(sdb, "tasks")
    tasks = stash.get("tasks") if isinstance(stash, dict) else None
    return tasks if isinstance(tasks, list) and tasks else None


def _plan_pending(sid):
    """The pending plan, ENRICHED for the page: `plan_html` (the markdown
    rendered server-side, the msg-bubble md_html — escape-first)."""
    pending = _dialog_pending(sid, "plan-pending")
    if pending and "plan_html" not in pending:
        pending = dict(pending)
        pending["plan_html"] = opshtml.md_html(pending.get("plan") or "")
    return pending


def _heal_stash(sid, log, sdb, key, step):
    """An endpoint's `open` bail means the dialog is GONE while the stash
    lingers (resolved in the terminal; the turn-boundary clear hasn't fired
    yet) — drop the stash so the page's card clears on the next SSE tick
    instead of sitting stale. Audited like ask_fmt's own removes."""
    if step != "open":
        return
    try:
        # kv_del_at, not kv_del: this runs on a request-handler THREAD, and
        # kv_del's cached connection is bound to whichever thread created it
        # (sqlite check_same_thread) — the delete would silently no-op
        if ST.kv_del_at(sdb, key):
            A.state_file(log, sdb, key,
                         {"action": "remove", "reason": "web open-bail"})
    except Exception:
        A.error(log, "dashboard stash heal (%s)" % key, {"sid": sid})


def _enrich_entry(ent):
    """Additively enrich ONE timeline entry for the page: message / prompt /
    teammsg entries gain an `html` field — md_html of their text/body, so the
    drill-down renders conversation text as readable markdown instead of a
    plain <pre>; tool entries gain `input_html` (a scannable render of a
    well-known tool's input — Bash command, Edit diff, Write body, Read
    one-liner, definition list) and, where it differs from a plain <pre>,
    `output_html`. Raw fields are left untouched (the API shape stays additive;
    app.js falls back to pre(text)/JSON when a field is absent — an older
    provider, or a tool with no rich render). The ONE post-processor both the
    REST timelines (_mdify) and the live SSE increments run."""
    t = ent.get("t")
    if t in ("message", "prompt"):
        ent["html"] = opshtml.md_html(ent.get("text", ""))
    elif t == "teammsg":
        ent["html"] = opshtml.md_html(ent.get("body", ""))
    elif t == "tool":
        ih = opshtml.tool_html(ent.get("tool", ""), ent.get("input"))
        if ih is not None:
            ent["input_html"] = ih
        oh = opshtml.tool_output_html(ent.get("output"), ent.get("failed"),
                                      ent.get("tool", ""))
        if oh is not None:
            ent["output_html"] = oh
    return ent


def _enrich_entries(entries):
    for ent in entries:
        _enrich_entry(ent)
    return entries


def _mdify(tl):
    """Enrich a whole timeline dict (plugins.activity result) in place — the
    REST /activity and /agent post-processor. See _enrich_entry."""
    _enrich_entries((tl or {}).get("entries", []))
    return tl


def _conv_items(recs):
    """Conversation records -> stream items. Additively carry `kind`
    (prompt|message|teammsg|question|answer) and, for prompts, the raw `text`:
    the page's queued-message chips match a DELIVERED prompt against what they
    sent — the transcript's prompt record is the one true delivery signal (tab
    transitions are useless: green flips busy again the instant a queued
    prompt starts processing). Every kind renders through opshtml.msg_html; only
    prompts need the raw text echoed back (queued-chip match + rewind picker)."""
    out = []
    for r in recs:
        it = {"g": None, "t": "msg", "kind": r["kind"],
              "html": opshtml.msg_html(r["kind"], r.get("text", ""),
                                       r.get("sender", ""))}
        if r["kind"] == "prompt":
            it["text"] = r.get("text", "")
        out.append(it)
    return out


TAIL_BLOCKS = 80       # initial backlog: how many stream BLOCKS to paint at once
HISTORY_BLOCKS = 40    # /history default page size (blocks), when ?blocks absent


def _merge_order(sid, key):
    """The full oldest->newest interleave of a session's ops and its main-thread
    conversation, WITHOUT rendering — a list of (slot_id, kind, obj) triples
    (kind 'op' -> obj is the op dict; 'msg' -> obj is a conversation record) so
    the block cut discards most ops before the costly op_html render runs. Also
    returns (last_op_id, transcript_pos).

    Interleave is by TIMESTAMP first: ops carry a wall-clock `_ts` (core.state)
    and conversation records carry the transcript line's `ts`
    (transcript.conversation) — when both are present a record lands after the
    last op that chronologically precedes it. Pre-migration history (no ts)
    falls back to the tool_use-id ANCHOR (ops carry `g`/`v`, records carry
    `anchor`; the record lands after that tool's last op). Records with neither
    keep their relative order at the head (pre-first-tool / anchor None) or tail
    (anchor never painted).

    The `slot_id` is what makes lazy-backlog cursors gap/overlap-free: it is the
    row id of the op an item belongs to (an op's own id; a conv record's is the
    id of the op it follows), 0 for the pre-first-tool HEAD group and last+1 for
    the never-painted TAIL group. Every window is a contiguous run of whole
    slots, and the op-id cursor names a slot boundary — see merged_backlog /
    history. Conversation is parsed in FULL here (cheap relative to op HTML —
    O(turns) text records vs O(thousands) ops, each op carrying a rendered,
    possibly large output block) and sliced by the merged window; the returned
    `mpos` is the whole-transcript end so the live SSE tail resumes correctly."""
    sdb = API.state_db_for(sid)
    last, ops = API.ops_at(sdb, 0) if sdb else (0, [])
    got = plugins.conversation(sid, 0)
    recs, mpos = got if got else ([], 0)
    # anchor -> last op index (the fallback placement); timestamped ops as
    # (ts, index) for the primary time-merge.
    lastpos = {}
    for i, op in enumerate(ops):
        for k in ("g", "v"):
            tid = op.get(k)
            if tid:
                lastpos[tid] = i
    ts_ops = [(op["_ts"], i) for i, op in enumerate(ops) if op.get("_ts") is not None]
    HEAD, TAIL = -1, len(ops)

    def place(r):
        ts = r.get("ts")
        if ts is not None and ts_ops:          # primary: chronological
            p = HEAD
            for ots, i in ts_ops:              # ts_ops is id-ordered == ts-ordered
                if ots <= ts:
                    p = i
            return p
        a = r.get("anchor")                    # fallback: the tool-use anchor
        if a in lastpos:
            return lastpos[a]
        return HEAD if a is None else TAIL

    buckets = {}
    for r in recs:
        buckets.setdefault(place(r), []).append(r)
    tail_slot = (ops[-1].get("_id", 0) + 1) if ops else 1
    entries = [(0, "msg", r) for r in buckets.get(HEAD, [])]
    for i, op in enumerate(ops):
        oid = op.get("_id")
        entries.append((oid, "op", op))
        for r in buckets.get(i, []):
            entries.append((oid, "msg", r))
    entries.extend((tail_slot, "msg", r) for r in buckets.get(TAIL, []))
    return entries, last, mpos


def _cut_blocks(entries, blocks):
    """Index into `entries` (oldest->newest) of the START of the newest-`blocks`
    stream blocks — 0 when they all fit. A block is a distinct non-null group
    `g` or a standalone item; `rule`/`blank` ops are spacing (dropped by
    op_items) and count as nothing, as do producer-source-stamped ops (`src` —
    dropped by op_items too: the web mirror is main-agent-only), so a window of
    N blocks means N VISIBLE blocks even when agent streams dominate the tail.
    Approximate by design (the window size is a
    soft limit) — cursor correctness rides slot ids, not this count."""
    seen, count = set(), 0
    for i in range(len(entries) - 1, -1, -1):
        _slot, kind, obj = entries[i]
        if kind == "op":
            if obj.get("t") in ("rule", "blank") or obj.get("src"):
                continue
            g = obj.get("g") or None
        else:
            g = None                           # a conv msg is a standalone block
        if g is None:
            count += 1
        elif g not in seen:
            seen.add(g)
            count += 1
        if count > blocks:
            return i + 1
    return 0


def _snap(entries, start):
    """Move `start` back to the beginning of its slot so a window contains only
    WHOLE slots (its first item is the slot's op, whose id is the cursor) — the
    guarantee that windows never split a slot across the load boundary."""
    while start > 0 and entries[start - 1][0] == entries[start][0]:
        start -= 1
    return start


def _render_window(entries, start, key):
    """Render entries[start:] to stream items ({g, t, html}); op entries through
    op_items, msg entries through _conv_items. Only the windowed slice is
    rendered — the whole point of the block cut."""
    out = []
    for _slot, kind, obj in entries[start:]:
        out.extend(opshtml.op_items([obj], key) if kind == "op"
                   else _conv_items([obj]))
    return out


def merged_backlog(sid, key, blocks=TAIL_BLOCKS):
    """The session view's INITIAL stream: the NEWEST `blocks` stream blocks of
    the op+conversation interleave, rendered to stream items ({g, t, html} — see
    _merge_order for the interleave rule). Returns
    (last_op_id, transcript_pos, oldest_op_id, [item, …]): `oldest` is the
    smallest op id painted — 0 when the whole history fits (nothing older to
    lazy-load), else the next cursor the client hands /history to load the
    previous blocks downward."""
    entries, last, mpos = _merge_order(sid, key)
    start = _snap(entries, _cut_blocks(entries, blocks))
    oldest = entries[start][0] if start > 0 else 0
    return last, mpos, oldest, _render_window(entries, start, key)


def history(sid, key, before, blocks):
    """The `blocks` stream blocks immediately OLDER than op id `before` — the
    lazy-backlog page. Reuses _merge_order's merge core (one implementation), so
    the initial backlog + successive history pages concatenate to the unlimited
    merge with no gap and no overlap. Returns (oldest_op_id, [item, …]): the
    next cursor (0 when the head is reached — history exhausted). `before` names
    a slot boundary (a returned `oldest`), so the older universe is every whole
    slot below it."""
    if before <= 0:
        return 0, []
    entries, _last, _mpos = _merge_order(sid, key)
    bound = len(entries)
    for i, (slot, _kind, _obj) in enumerate(entries):
        if slot >= before:                     # slots are id-ordered ascending
            bound = i
            break
    universe = entries[:bound]
    start = _snap(universe, _cut_blocks(universe, blocks))
    oldest = universe[start][0] if start > 0 else 0
    return oldest, _render_window(universe, start, key)


def ops_payload(sid, after):
    """(last_id, [item, …]) — rendered server-side so the page never touches
    raw op bytes (items: {g, t, html}, see opshtml.op_items). Reads via
    ops_at on the RESOLVED path (live or parked), which can never create the
    live DB."""
    sdb = API.state_db_for(sid)
    if not sdb:
        return after, []
    last, ops = API.ops_at(sdb, after)
    row = API.session_row(sid)
    key = P.sid_from_log(row["log"]) if row else sid
    return last, opshtml.op_items(ops, key)


def view_payload(sid, gid):
    """A click-to-view stash rendered to HTML, or None when there is no stash
    (pre-feature line / failed stash write — same no-op the terminal shows)."""
    sdb = API.state_db_for(sid)
    if not sdb:
        return None
    ops = API.kv_at(sdb, "view:" + gid)
    ops = [o for o in (ops or []) if isinstance(o, dict)]
    if not ops:
        return None
    return opshtml.view_html(ops, sid)


def _last_prompt(sid):
    """The session's LAST main-thread user prompt text (via
    plugins.conversation), or '' — what a mid-turn cancel-edit restores into
    the input, so the page can prefill its composer with it. Best-effort: a
    read failure just yields '' (the cancel still happened in the terminal)."""
    try:
        got = plugins.conversation(sid)
        if not got:
            return ""
        recs, _ = got
        for r in reversed(recs):
            if r.get("kind") == "prompt":
                return r.get("text") or ""
    except Exception:
        pass
    return ""


def _frontend():
    """The active Frontend for a CONTROL-PLANE write, or None when no terminal
    control channel resolves. The dashboard may be started OUTSIDE kitty (its
    lifecycle is deliberately independent — docs/dashboard.md), so resolve=True
    lets kitty hunt for its socket beyond the env, and a frontend that isn't
    usable() degrades to None → the endpoint returns a clean 'no terminal'
    error, never a 500."""
    try:
        fe = frontends.get(resolve=True)
        return fe if fe.usable() else None
    except Exception:
        return None


_LIVE_WINS = {"ts": -1e9, "val": None}   # memo: {sid: win_id} tagged in a live pane
_LIVE_TTL = 5.0   # every consumer of the map is READ-side (the live→parked
#                   demotion + the stop-button display gate) — the control
#                   plane never trusts it (each POST re-scans via
#                   fe.window_for_session at action time) — so staleness only
#                   delays noticing a crashed/killed tab by a few seconds.
#                   That buys dropping the ~21ms `kitten @ ls` SUBPROCESS from
#                   ~1.25/s (the old 0.8, chosen to bound it under the 1s
#                   tick) to 0.2/s while any client keeps the payloads warm.


_LIVE_GRACE_S = 10.0   # a just-started session is EXEMPT from the missing-window
#                        demotion for this long. Its audit `sessions` row (with
#                        kitty_window_id) is written a beat BEFORE its pane is
#                        tagged claude_session=<sid> (split.cmd_open runs
#                        A.session_start then tag_window), and _live_windows is
#                        memoized up to _LIVE_TTL on top — so a fresh launch would
#                        momentarily miss the tagged-window map and get demoted to
#                        not-live, flashing "parked" on a brand-new session (and
#                        the detail header, whose meta is fetched once, froze on
#                        it — app.js updateHeadFromList now self-heals, but the
#                        flash itself is the "starts parked" half of the report).
#                        Comfortably covers boot + the memo TTL; the only cost is
#                        a session that dies within its first 10s showing live
#                        briefly, which the next tick past the grace corrects.


def _within_live_grace(row):
    """True while `row`'s session is inside the just-started grace (see
    _LIVE_GRACE_S) — used to SUPPRESS the missing-window demotion. A parked
    minimal row carries no started_at (None → False), but those never reach the
    demotion anyway (their base `live` is already False)."""
    st = row.get("started_at") or 0
    return bool(st) and (time.time() - st) < _LIVE_GRACE_S


def _live_windows():
    """{sid: window_id} for every kitty pane CURRENTLY tagged
    claude_session=<sid> — the authoritative 'which sessions have an OPEN tab'.
    One `kitten @ ls`, memoized for _LIVE_TTL. None when no frontend resolves
    (can't tell → callers keep the state-DB liveness signal rather than wrongly
    marking sessions dead).

    Why this exists: the audit row's kitty_window_id is a START-TIME snapshot,
    and 'the state DB file exists' only means the session was never PARKED — a
    tab closed WITHOUT a SessionEnd (crash / kill -9, or a leaked test DB)
    leaves both intact, so the session shows live with a window id that kitty
    has since reused for an unrelated tab. Keying on the live user-var tag is
    the only collision-proof truth."""
    now = time.monotonic()
    if now - _LIVE_WINS["ts"] < _LIVE_TTL:
        return _LIVE_WINS["val"]
    fe = _frontend()
    val = None
    if fe is not None:
        try:
            val = {}
            for _osw, _tab, w in fe.iter_windows():
                sid = (w.get("user_vars") or {}).get("claude_session")
                if sid and w.get("id") is not None:
                    val.setdefault(sid, str(w["id"]))
        except Exception:
            val = None
    _LIVE_WINS["ts"], _LIVE_WINS["val"] = now, val
    return val


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


def launch_argv(words, cmd="claude"):
    """The argv a web new-session launches — the interactive-login-shell
    wrapper now owned by plugins.claude_code.account.launch_argv (the
    rate-limit migration composes the SAME launch; the rationale — GUI kitty
    has no user PATH/aliases, `cmd` must be a registry-vetted bareword, the
    prompt/flags ride "$@" — lives with the owner). Reached through the
    plugins registry root, the dashboard's one sanctioned plugin door."""
    return plugins.launch_argv(words, cmd)


# --- macOS focus steal watch (audit-only) -----------------------------------------------
# A web launch used to make macOS activate kitty over the browser: the plain
# tab launch is innocent, but the new session's SessionStart opened its
# mirror/scorebar panes with kitty's `--keep-focus`, whose focus-restore
# raises the OS window whenever the app is in the background — i.e. exactly
# when launching from a browser (live-measured steals at 2.2s/3.0s/5.8s, one
# per pane op). That is fixed at the SOURCE: frontends/kitty.py launch_pane
# passes --keep-focus only while kitty is the frontmost app. This watch is
# the PASSIVE regression evidence for that fix: it records when the terminal
# app takes the frontmost spot during a launch's startup window and NEVER
# touches focus itself. (An active bounce-back shipped on 2026-07-18 and was
# reverted the same day: it cannot distinguish kitty stealing focus from the
# user deliberately switching to kitty, so it yanked the user back to the
# browser when they genuinely wanted the terminal. Do not re-add it.)
# `lsappinfo` is a plain LaunchServices query — no Apple-events /
# accessibility permission prompts, unlike System Events AppleScript.
STEALWATCH_POLL_S = 0.5            # frontmost-app poll cadence after a launch
STEALWATCH_POLLS = 60              # ~30s — outlives the whole session startup
                                   # (claude boot + SessionStart pane opens,
                                   # stragglers measured past 12s)


def _front_app():
    """The frontmost macOS app's bundle id, or "" (non-mac / any failure)."""
    if sys.platform != "darwin":
        return ""
    try:
        asn = subprocess.run(["lsappinfo", "front"], capture_output=True,
                             text=True, timeout=2).stdout.strip()
        if not asn:
            return ""
        out = subprocess.run(["lsappinfo", "info", "-only", "bundleid", asn],
                             capture_output=True, text=True, timeout=2).stdout
        m = re.search(r'"CFBundleIdentifier"\s*=\s*"([^"]+)"', out)
        return m.group(1) if m else ""
    except Exception:
        return ""


def _steal_watch(before, terminal_app):
    """The post-launch focus watch (a daemon thread — the HTTP response never
    waits on it): record each TRANSITION of the frontmost app onto the
    terminal during the watch window, purely for the audit trail. Observes,
    never intervenes — the fix for the steal lives in the terminal frontend
    (launch_pane's conditional --keep-focus); a non-empty `steals` list on a
    current build means some launch path still activates the terminal and
    names the second it happened. One `web-launch-steal-watch` state_files
    row per watch (`steals` = seconds-into-watch of each takeover; [] =
    clean)."""
    t0 = time.time()
    steals, prev = [], before
    for _ in range(STEALWATCH_POLLS):
        time.sleep(STEALWATCH_POLL_S)
        now = _front_app()
        if not now:
            continue
        if now == terminal_app and prev != terminal_app:
            steals.append(round(time.time() - t0, 2))
        prev = now
    A.state_file("", "", "web-launch-steal-watch",
                 {"before": before, "terminal": terminal_app,
                  "steals": steals})


# --- post-launch SSE wake watch ------------------------------------------------------
# A web launch's session doesn't exist anywhere until claude finishes booting
# in the new tab and fires SessionStart (measured 1.4-2.1s across recent
# launches — the audit `web-launch` rows joined against the following
# SessionStart). Without a nudge the global SSE loop only notices the new
# sessions row on its next GLOBAL_TICK_S poll, adding up to a full second of
# dead air on top. This watch polls the sessions head at a fast cadence and,
# the moment the launched session appears, pushes a `wake` into NOTIFIER —
# the sse_global loops block on that queue, so every connected page both
# receives the `wake` (the launching page jumps straight to the sid it
# carries) and rebuilds/pushes the sessions snapshot NOW instead of at the
# tick. Matching: by kitty_window_id when the launch reported the new
# window's id (exact — covers fresh/resume/continue alike, since the audit's
# session upsert stamps the resumed row's new window too), else a session in
# the launch cwd whose started_at postdates the launch.
LAUNCHWAKE_POLL_S = 0.15           # sessions-head poll cadence after a launch
LAUNCHWAKE_MAX_S = 15.0            # claude boot measured ~2s; 15s covers a cold
#                                    machine without leaving a zombie poller


def _launch_wake(win, cwd, t0):
    """The post-launch appearance watch (a daemon thread — the HTTP response
    never waits on it). Ends with ONE `web-launch-wake` state_files row either
    way: found (`sid`, `waited_s` = launch→appearance latency, the dashboard's
    own share of it reconstructible next to the `web-launch` row) or timeout
    (`sid` empty). The `wake` push happens only on found — a timeout has
    nothing to hurry the loops for."""
    deadline = t0 + LAUNCHWAKE_MAX_S
    sid = ""
    while not sid and time.time() < deadline:
        try:
            for row in API.sessions(10):
                if ((win and str(row.get("kitty_window_id") or "") == win)
                        or (not win and row.get("cwd") == cwd
                            and (row.get("started_at") or 0) >= t0)):
                    sid = row["sid"]
                    break
        except Exception:
            A.error("", "dashboard launch wake")
            break
        if not sid:
            time.sleep(LAUNCHWAKE_POLL_S)
    if sid:
        NOTIFIER.push("wake", {"sid": sid, "win": win, "cwd": cwd})
    A.state_file("", "", "web-launch-wake",
                 {"sid": sid, "win": win, "cwd": cwd, "ok": bool(sid),
                  "waited_s": round(time.time() - t0, 3)})


# --- the HTTP handler ------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "claude-dash"

    def log_message(self, *a):              # stdlib logs to stderr — DEVNULL'd
        pass                                # anyway under spawn_detached

    # -- plumbing --
    def _accepts_gzip(self):
        # honour an explicit `gzip;q=0` refusal; otherwise any gzip token wins.
        for tok in self.headers.get("Accept-Encoding", "").lower().split(","):
            tok = tok.strip()
            if tok == "gzip" or tok.startswith("gzip;"):
                return "q=0" not in tok or "q=0." in tok
        return False

    def _send(self, code, body, ctype="application/json"):
        # Everything routed through _send is text (JSON/HTML/CSS/JS/plain), so
        # it all compresses; SSE never comes here (it holds the response open
        # and writes incremental frames, which buffering would break). Vary is
        # set whenever the body could vary by encoding, even when this response
        # stays plain, so a shared cache keys the two variants apart.
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Vary", "Accept-Encoding")
        if len(data) >= GZIP_MIN and self._accepts_gzip():
            data = gzip.compress(data)
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(data)
        except OSError:
            pass                            # client went away mid-write

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, default=str))

    def _sse_start(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _sse(self, event, obj):
        """One SSE frame; False when the client is gone (ends the loop)."""
        try:
            self.wfile.write(("event: %s\ndata: %s\n\n"
                              % (event, json.dumps(obj, default=str))).encode())
            self.wfile.flush()
            return True
        except OSError:
            return False

    def _sse_beat(self):
        try:
            self.wfile.write(b": beat\n\n")
            self.wfile.flush()
            return True
        except OSError:
            return False

    # -- routing --
    def do_GET(self):
        url = urlparse(self.path)
        parts = [unquote(p) for p in url.path.strip("/").split("/") if p]
        try:
            self.route(url, parts)
        except (BrokenPipeError, ConnectionResetError):
            pass                            # client disconnect is not an error
        except Exception:
            A.error("", "dashboard request", {"path": self.path[:200]})
            try:
                self._json({"error": "internal"}, 500)
            except Exception:
                pass

    def route(self, url, parts):
        if not parts:
            return self.static("index.html")
        if parts[0] == "static" and len(parts) == 2:
            return self.static(parts[1])
        if parts[0] == "events":
            if len(parts) == 1:
                return self.sse_global()
            if len(parts) == 3 and parts[1] == "session" and _sid(parts[2]):
                return self.sse_session(parts[2], _qint(url, "after"),
                                        _qint(url, "mpos"))
            if len(parts) == 4 and parts[1] == "agent" \
                    and _sid(parts[2]) and _sid(parts[3]):
                return self.sse_agent(parts[2], parts[3], _qint(url, "pos"))
            return self._json({"error": "not found"}, 404)
        if parts[0] != "api":
            return self._json({"error": "not found"}, 404)
        api = parts[1:]
        if api == ["sessions"]:
            return self._json([_wire_row(r) for r in sessions_payload()])
        if api == ["accounts"]:
            return self._json(accounts_payload())
        if api == ["dictate"]:
            # feature probe: the page renders mic buttons iff a Deepgram key
            # is configured (docs/dashboard.md *Web dictation*) — no key
            # means the feature is invisible, never a dead button
            return self._json({"available": dictate.available()})
        if api == ["commands"]:
            # the "/" menus (composer + new-session prompt): built-ins + the
            # given directory's discovered .claude commands/skills. cwd-keyed,
            # not sid-keyed — the new-session form completes for a directory
            # that has no session yet; a non-directory degrades to built-ins
            # + user-level entries, never an error.
            cwd = (parse_qs(url.query).get("cwd") or [""])[0]
            if not os.path.isdir(cwd):
                cwd = ""
            return self._json(plugins.slash_commands(cwd))
        if api == ["ns-prefs"]:
            # the new-session form's last-used {cwd, model, effort} — moved off
            # per-browser localStorage into the durable global prefs store so a
            # launch on one device pre-selects on the next (docs/dashboard.md,
            # *New-session prefs*). {} when nothing launched yet.
            return self._json(prefs.get("new-session", {}))
        if api == ["dirs", "hidden"]:
            # the {group_key: hidden_at_epoch} map the ✕ built (docs/dashboard.md
            # *Hidden directories*); the page seeds S.hidden from this on load —
            # the SSE snapshot carries the session ROWS, not this pref, and only
            # the browser that clicks ✕ mutates it, so no SSE push is needed.
            return self._json(prefs.hidden_dirs())
        if len(api) >= 2 and api[0] == "session" and _sid(api[1]):
            sid, rest = api[1], api[2:]
            if not rest:
                return self._json(session_payload(sid))
            if rest == ["ops"]:
                last, items = ops_payload(sid, _qint(url, "after"))
                return self._json({"last": last, "items": items})
            if rest == ["history"]:
                row = API.session_row(sid)
                key = P.sid_from_log(row["log"]) if row else sid
                oldest, items = history(sid, key, _qint(url, "before"),
                                        _qint(url, "blocks") or HISTORY_BLOCKS)
                return self._json({"oldest": oldest, "items": items})
            if rest == ["backlog"]:
                # The GET twin of the SSE fresh-connect backlog: same
                # merged_backlog output, but through _send — which GZIPS it
                # (8-9x on this HTML; SSE frames are never compressed), so a
                # remote/tunnel page gets its first paint in one compressed
                # round-trip. The page hands the returned cursors to the SSE,
                # which then only streams increments (the reconnect contract).
                row = API.session_row(sid)
                key = P.sid_from_log(row["log"]) if row else sid
                last, mpos, oldest, items = merged_backlog(sid, key)
                return self._json({"last": last, "mpos": mpos,
                                   "oldest": oldest, "items": items})
            if rest == ["activity"]:
                return self._json(_mdify(plugins.activity(sid)) or {"entries": []})
            if len(rest) == 2 and rest[0] == "agent":
                tl = _mdify(plugins.activity(sid, rest[1]))
                if tl is not None:
                    _stamp_agent_cost(tl)
                return self._json(tl if tl is not None else {"entries": []})
            if rest == ["errors"]:
                return self._json(API.errors(sid))
            if rest == ["monitors"]:
                return self._json({"monitors": plugins.monitors(sid) or []})
            if rest == ["jobs"]:
                return self._json({"jobs": API.jobs(sid)})
            if len(rest) == 2 and rest[0] == "view":
                html = view_payload(sid, rest[1])
                if html is None:
                    return self._json({"error": "no stash"}, 404)
                return self._send(200, html, "text/html; charset=utf-8")
            if len(rest) == 3 and rest[0] == "copy" \
                    and rest[2] in ("cmd", "out", "all"):
                sdb = API.state_db_for(sid)
                text = CP.collect(sdb, rest[1], rest[2]) if sdb else ""
                return self._send(200, text, "text/plain; charset=utf-8")
        return self._json({"error": "not found"}, 404)

    # -- POST routing (the control plane) --
    # The dashboard is READ-ONLY except these control-plane writes, which TYPE INTO a
    # terminal — a drive-by RCE if a random website could fire them. Any page
    # can send a *simple* cross-origin POST at 127.0.0.1 (no preflight), so the
    # defense is to make these NON-simple: require a JSON content type AND a
    # custom header (each forces a CORS preflight that a cross-origin page can't
    # pass, since we answer OPTIONS with a bare 501 — no Access-Control-Allow-*),
    # and additionally reject any Origin that isn't our own. See docs/dashboard.md.
    def do_POST(self):
        url = urlparse(self.path)
        parts = [unquote(p) for p in url.path.strip("/").split("/") if p]
        try:
            self.route_post(url, parts)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            A.error("", "dashboard POST", {"path": self.path[:200]})
            try:
                self._json({"error": "internal"}, 500)
            except Exception:
                pass

    def route_post(self, url, parts):
        api = parts[1:] if parts[:1] == ["api"] else None
        if api is None:
            return self._json({"error": "not found"}, 404)
        if len(api) == 3 and api[0] == "session" and _sid(api[1]) \
                and api[2] == "message":
            return self.post_message(api[1])
        if len(api) == 3 and api[0] == "session" and _sid(api[1]) \
                and api[2] == "command":
            return self.post_command(api[1])
        if len(api) == 3 and api[0] == "session" and _sid(api[1]) \
                and api[2] == "stop":
            return self.post_stop(api[1])
        if len(api) == 3 and api[0] == "session" and _sid(api[1]) \
                and api[2] == "interrupt":
            return self.post_interrupt(api[1])
        if len(api) == 3 and api[0] == "session" and _sid(api[1]) \
                and api[2] == "rename":
            return self.post_rename(api[1])
        if len(api) == 3 and api[0] == "session" and _sid(api[1]) \
                and api[2] == "migrate":
            return self.post_migrate(api[1])
        if len(api) == 3 and api[0] == "session" and _sid(api[1]) \
                and api[2] == "rewind":
            return self.post_rewind(api[1])
        if len(api) == 3 and api[0] == "session" and _sid(api[1]) \
                and api[2] == "rewind-to":
            return self.post_rewind_to(api[1])
        if len(api) == 3 and api[0] == "session" and _sid(api[1]) \
                and api[2] == "answer":
            return self.post_answer(api[1])
        if len(api) == 3 and api[0] == "session" and _sid(api[1]) \
                and api[2] == "ask-draft":
            return self.post_ask_draft(api[1])
        if len(api) == 3 and api[0] == "session" and _sid(api[1]) \
                and api[2] == "composer-draft":
            return self.post_composer_draft(api[1])
        if len(api) == 3 and api[0] == "session" and _sid(api[1]) \
                and api[2] == "composer-queue":
            return self.post_composer_queue(api[1])
        if len(api) == 3 and api[0] == "session" and _sid(api[1]) \
                and api[2] == "plan-options":
            return self.post_plan_options(api[1])
        if len(api) == 3 and api[0] == "session" and _sid(api[1]) \
                and api[2] == "plan-decision":
            return self.post_plan_decision(api[1])
        if len(api) == 3 and api[0] == "session" and _sid(api[1]) \
                and api[2] == "notify":
            return self.post_notify_mute(api[1])
        if api == ["sessions", "new"]:
            return self.post_new_session()
        if api == ["ns-prefs"]:
            return self.post_ns_prefs()
        if api == ["dirs", "hide"]:
            return self.post_hide_dir()
        if api == ["dictate", "token"]:
            return self.post_dictate_token()
        return self._json({"error": "not found"}, 404)

    def _reject(self, code, err):
        """A guard rejection: close the connection (an unread body would desync
        a kept-alive connection) and send the JSON error. Returns None (implicit)
        so the caller can `return self._reject(...)` straight out of _post_guard."""
        self.close_connection = True
        self._json({"error": err}, code)

    def _post_guard(self):
        """Validate a control-plane POST against the browser-vector defense
        (see do_POST) and return its parsed JSON body — or send a 4xx and return
        None (the caller just returns). Order: read-only kill switch, content
        type, custom header, Origin, size cap, then the JSON parse."""
        if READONLY:
            return self._reject(403, "control plane disabled (read-only)")
        ctype = self.headers.get("Content-Type", "").split(";")[0].strip()
        if ctype != "application/json":
            return self._reject(415, "content-type must be application/json")
        if self.headers.get(POST_HEADER) != "1":
            return self._reject(403, "missing %s header" % POST_HEADER)
        origin = self.headers.get("Origin")
        if origin and origin not in ALLOWED_ORIGINS:
            return self._reject(403, "cross-origin")
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n < 0 or n > POST_MAX:
            return self._reject(413, "body too large")
        try:
            raw = self.rfile.read(n) if n else b""
            body = json.loads(raw or b"{}")
        except (ValueError, OSError):
            return self._reject(400, "invalid JSON")
        if not isinstance(body, dict):
            return self._reject(400, "invalid JSON")
        return body

    def post_message(self, sid):
        """Type a message into a session's kitty window (the composer). 4xx when
        the session has no window (headless/daemon) or the text is empty; 503
        when no terminal resolves; else Frontend.send_text. Every attempt is a
        `web-send` state_files row, failures also an A.error.

        `clear_draft` (bool): the page sets it when resending an edited
        message after a mid-turn cancel-edit — the TUI input still holds the
        restored draft, so the send first kills the line (Ctrl+U to start +
        Ctrl+K to end, so the cursor position doesn't matter) and then
        delivers the text as a BRACKETED PASTE (paste_text): a raw send into
        the just-cleared input drops leading bytes (measured — the mangle),
        an atomic paste doesn't. This is what lets you edit AND resend from
        the web without touching the kitty tab (docs/dashboard.md)."""
        body = self._post_guard()
        if body is None:
            return
        text = body.get("text")
        if not isinstance(text, str) or not text.strip():
            return self._json({"error": "empty text"}, 400)
        clear_draft = bool(body.get("clear_draft"))
        row = API.session_row(sid) or {}
        log = row.get("log") or P.mirror_log(sid)
        sdb = API.state_db_for(sid) or P.state_db(log)
        fe = _frontend()
        if fe is None:
            A.error(log, "dashboard message (no terminal)", {"sid": sid})
            A.state_file(log, sdb, "web-send",
                         {"win": "", "chars": len(text), "ok": False})
            return self._json({"error": "no terminal available"}, 503)
        # AUTHORITATIVE window: the pane currently tagged claude_session=<sid>,
        # NOT the audit row's stale start-time id (typing into a reused id would
        # land in an unrelated tab — see _live_windows; a fresh scan, never the
        # TTL memo). '' ⇒ nothing to message.
        win = fe.window_for_session(sid) or ""
        if not win:
            A.state_file(log, sdb, "web-send",
                         {"win": "", "chars": len(text), "ok": False})
            return self._json({"error": "session has no live window"}, 409)
        # a message pasted while a MODAL dialog (AskUserQuestion / ExitPlanMode)
        # is up goes INTO the dialog, not the TUI message queue — it perturbs
        # the dialog and the text is lost (the "my queued message vanished mid
        # ask" report, 2026-07-19). Refuse with a clear pointer to the card; the
        # composer keeps its text (the page re-persists the draft on error).
        if _ask_pending(sid) or _plan_pending(sid):
            A.state_file(log, sdb, "web-send",
                         {"win": win, "chars": len(text), "ok": False,
                          "blocked": "modal"})
            return self._json({"error": "this session has an open question — "
                               "answer it in the card above (or dismiss it) "
                               "before sending", "modal": True}, 409)
        # the tab state AT SEND TIME decides whether this send starts a turn
        # or lands in the TUI's message queue (QUEUE_TABS); it rides the audit
        # row too — "my message vanished" is answerable as "it queued mid-turn"
        tab = API.tab_states().get(win) or ""
        if clear_draft:
            # kill the restored draft (both directions), settle, then paste
            fe.send_key(win, "ctrl+u")
            fe.send_key(win, "ctrl+k")
            time.sleep(DRAFT_CLEAR_GAP_S)
        # ALWAYS a bracketed paste, not a raw send: a raw send is delivered as
        # fast individual keystrokes and the TUI drops some depending on its
        # input state (reported live: "test" arrived as "t"; measured 8/8
        # clean for a bracketed paste, flaky for raw). The trailing CR is a
        # separate keystroke OUTSIDE the paste, so it still submits — and a
        # multi-line composer message pastes atomically instead of its internal
        # newlines submitting it early.
        ok = bool(fe.paste_text(win, text))
        A.state_file(log, sdb, "web-send",
                     {"win": win, "chars": len(text), "ok": ok, "tab": tab,
                      "clear_draft": clear_draft})
        if not ok:
            A.error(log, "dashboard message (send failed)",
                    {"sid": sid, "win": win})
            return self._json({"error": "send failed"}, 502)
        return self._json({"ok": True, "queued": tab in QUEUE_TABS, "tab": tab})

    def post_command(self, sid):
        """The scoreboard's quick-command row — type one of the TUI's OWN
        slash commands into the session's window: `{"cmd": "compact"}` →
        `/compact`, `{"cmd": "model", "arg": <alias|id>}` → `/model <arg>`,
        `{"cmd": "effort", "arg": <level>}` → `/effort <arg>` (both may open
        the TUI's switch-confirm menu, auto-answered Yes below — the reply's
        `confirm` field). A FIXED
        vocabulary, 400 on anything else — the arg is validated
        (_MODEL_ARG_OK / EFFORTS) precisely because it is typed into a
        terminal, and compact takes no arg (the closed vocabulary IS the
        point; free-form text is the composer's job). Delivery matches
        post_message (bracketed paste + CR via the live claude_session
        window), so mid-turn the command lands in the TUI's message queue and
        runs at the turn boundary (`queued` in the reply) — but a RED tab
        (awaiting-command: a modal dialog is up) is a 409: pasted text would
        land IN the dialog, its digits deciding it. Every attempt is a
        `web-command` state_files row, failures also an A.error."""
        body = self._post_guard()
        if body is None:
            return
        cmd, arg = body.get("cmd"), body.get("arg")
        if cmd == "compact" and not arg:
            text = "/compact"
        elif cmd == "model" and isinstance(arg, str) \
                and _MODEL_ARG_OK.match(arg):
            text = "/model " + arg
        elif cmd == "effort" and arg in EFFORTS:
            text = "/effort " + arg
        else:
            return self._reject_input("web-command", "bad cmd", "unknown command",
                                {"sid": sid, "cmd": cmd, "arg": arg})
        row = API.session_row(sid) or {}
        log = row.get("log") or P.mirror_log(sid)
        sdb = API.state_db_for(sid) or P.state_db(log)
        fe = _frontend()
        if fe is None:
            A.error(log, "dashboard command (no terminal)", {"sid": sid})
            A.state_file(log, sdb, "web-command",
                         {"win": "", "cmd": cmd, "arg": arg or "",
                          "ok": False})
            return self._json({"error": "no terminal available"}, 503)
        # AUTHORITATIVE window: the live claude_session=<sid> pane tag, same
        # as post_message (a reused stale id would type into an unrelated tab)
        win = fe.window_for_session(sid) or ""
        if not win:
            A.state_file(log, sdb, "web-command",
                         {"win": "", "cmd": cmd, "arg": arg or "",
                          "ok": False})
            return self._json({"error": "session has no live window"}, 409)
        tab = API.tab_states().get(win) or ""
        if tab == tabs.AWAITING_COMMAND:
            A.state_file(log, sdb, "web-command",
                         {"win": win, "cmd": cmd, "arg": arg or "",
                          "ok": False, "tab": tab})
            return self._json({"error": "a dialog is open — answer it first"},
                              409)
        ok = bool(fe.paste_text(win, text))
        A.state_file(log, sdb, "web-command",
                     {"win": win, "cmd": cmd, "arg": arg or "", "ok": ok,
                      "tab": tab})
        if not ok:
            A.error(log, "dashboard command (send failed)",
                    {"sid": sid, "win": win, "cmd": cmd})
            return self._json({"error": "send failed"}, 502)
        res = {"ok": True, "queued": tab in QUEUE_TABS, "tab": tab}
        if cmd in ("model", "effort") and tab not in QUEUE_TABS:
            # newer TUI builds interpose a Yes/No switch-confirm menu (the
            # prompt-cache warning) instead of applying outright — unanswered
            # it makes the click look dead, so press its own Yes (the button
            # IS the consent), screen-verified: dashboard/confirmdialog.py.
            # Mid-turn (queued) the command only runs at the turn boundary,
            # so there is no menu to wait for here — an unanswered late menu
            # surfaces as the red-tab notification.
            try:
                c = confirmdialog.confirm(fe, win)
                res["confirm"] = "confirmed" if c["dialog"] else "none"
            except Exception as e:      # ConfirmError or a frontend hiccup —
                # the menu (if any) is left open for the terminal user
                A.error(log, "dashboard command (confirm failed)",
                        {"sid": sid, "win": win, "cmd": cmd, "err": str(e)})
                res["confirm"] = "failed"
            A.state_file(log, sdb, "web-command-confirm",
                         {"win": win, "cmd": cmd,
                          "confirm": res["confirm"]})
        return self._json(res)

    def post_stop(self, sid):
        """Close a session's kitty tab (Frontend.close_tab — main window +
        mirror + scorebar at once). This is a GRACEFUL stop, not a kill: kitty
        HUPs the tab's processes, Claude Code exits and fires SessionEnd, and
        the normal end-of-session lifecycle (mirror park, audit close) runs on
        its own — verified empirically 2026-07-18 (docs/dashboard.md). 409
        when the session has no window (headless — nothing to close); 503
        when no terminal resolves. Every attempt is a `web-stop` state_files
        row, failures also an A.error."""
        body = self._post_guard()
        if body is None:
            return
        row = API.session_row(sid) or {}
        log = row.get("log") or P.mirror_log(sid)
        sdb = API.state_db_for(sid) or P.state_db(log)
        fe = _frontend()
        if fe is None:
            A.error(log, "dashboard stop (no terminal)", {"sid": sid})
            A.state_file(log, sdb, "web-stop", {"win": "", "ok": False})
            return self._json({"error": "no terminal available"}, 503)
        # AUTHORITATIVE window: the pane currently tagged claude_session=<sid>,
        # NOT the audit row's stale start-time id. Closing by a reused stale id
        # would close an UNRELATED live tab — the exact bug this fixes (a leaked
        # smoke-test session's reused window id closed the user's own tab).
        win = fe.window_for_session(sid) or ""
        if not win:
            A.state_file(log, sdb, "web-stop", {"win": "", "ok": False})
            return self._json({"error": "session has no live window"}, 409)
        ok = bool(fe.close_tab(win))
        A.state_file(log, sdb, "web-stop", {"win": win, "ok": ok})
        if not ok:
            A.error(log, "dashboard stop (close failed)",
                    {"sid": sid, "win": win})
            return self._json({"error": "close failed"}, 502)
        return self._json({"ok": True})

    def post_interrupt(self, sid):
        """Press Escape in a session's kitty window (Frontend.send_key) — the
        TUI's own interrupt: stops the current turn in place, the session
        stays up. Distinct from post_stop, which closes the whole tab. Key
        EVENTS, not send_text bytes — a raw \\x1b never reaches a TUI in the
        kitty keyboard protocol as Escape. 409 when the session has no window
        (headless — nothing to interrupt); 503 when no terminal resolves.
        Every attempt is a `web-interrupt` state_files row, failures also an
        A.error."""
        return self._escape_press(sid, "interrupt", "web-interrupt")

    def post_rename(self, sid):
        """Rename a session: append the `agent-name` naming record to its
        transcript (plugins.set_session_title — the /rename channel, docs/
        session-naming-findings.md) and, when a live window exists, also
        Frontend.set_tab_title so the kitty tab moves NOW (sticky — the tab
        stops following auto ai-titles; docs/dashboard.md *Web rename*).
        DELIBERATELY unlike post_message, no terminal / no window is NOT an
        error here — a parked session (or a dashboard outside kitty) still
        gets the JSONL rename and only the tab retitle degrades. Always
        appends, even mid-turn (a single atomic O_APPEND line — the tab state
        rides the audit row so a race is diagnosable). Every post-validation
        attempt is a `web-rename` state_files row, failures also an A.error."""
        body = self._post_guard()
        if body is None:
            return
        name = body.get("name")
        if not isinstance(name, str):
            return self._json({"error": "empty name"}, 400)
        name = _NAME_CTRL.sub(" ", name).strip()[:RENAME_MAX].strip()
        if not name:
            return self._json({"error": "empty name"}, 400)
        row = API.session_row(sid) or {}
        log = row.get("log") or P.mirror_log(sid)
        sdb = API.state_db_for(sid) or P.state_db(log)
        tpath = row.get("transcript_path") or ""
        if not tpath or not os.path.isfile(tpath):
            A.state_file(log, sdb, "web-rename",
                         {"win": "", "chars": len(name), "ok": False,
                          "reason": "no transcript"})
            return self._json({"error": "no transcript"}, 409)
        fe = _frontend()
        win = (fe.window_for_session(sid) or "") if fe else ""
        tab = (API.tab_states().get(win) or "") if win else ""
        try:
            appended = plugins.set_session_title(tpath, name)
        except OSError:
            A.error(log, "dashboard rename (append failed)", {"sid": sid})
            A.state_file(log, sdb, "web-rename",
                         {"win": win, "chars": len(name), "ok": False,
                          "tab": tab})
            return self._json({"error": "append failed"}, 502)
        if appended is None:        # no plugin owns the file (a codex rollout)
            A.state_file(log, sdb, "web-rename",
                         {"win": win, "chars": len(name), "ok": False,
                          "reason": "unsupported"})
            return self._json({"error": "unsupported session"}, 409)
        tab_retitled = bool(fe.set_tab_title(win, name)) if (fe and win) else False
        A.state_file(log, sdb, "web-rename",
                     {"win": win, "chars": len(name), "ok": True, "tab": tab,
                      "tab_retitled": tab_retitled})
        return self._json({"ok": True, "title": name,
                           "tab_retitled": tab_retitled})

    def post_migrate(self, sid):
        """Manually migrate a session to another subscription account — the
        header's ⇆ migrate button (docs/relimit.md *Manual migrate*). Spawns
        the SAME detached migrator the automatic rate-limit path uses
        (bin/claude-relimit.py: close the tab → wait for the SessionEnd park
        → `<alias> claude --resume <sid>` in a new tab; the adopt machinery
        carries the mirror history and the status-line capture flips the
        account chip), with two manual-intent differences baked into `mode=
        manual`: no auto-continue nudge (nothing was cut off — the resumed
        session opens at the prompt) and no 90% usage ceiling on the target
        (plugins.migration_target(manual=True) — an explicit click outranks
        the refuge rule). It runs the SAME fable→opus→sonnet downgrade ladder
        the automatic path does (docs/relimit.md *Model-downgrade ladder*):
        same model on another account when one has quota, else a downgrade rung
        passed through to `--model` (the current model is read off the
        transcript via plugins.context). Immediate, no confirm (user request —
        like ■ stop). Works live AND parked: a parked session skips the close
        leg and just relaunches. 404 for a sid this machine has never seen (no
        audit row, no live/parked state DB — the migrator can't tell "parked"
        from "never existed", so an unknown sid would sail through its park
        check and launch a doomed --resume tab; caught live 2026-07-19); 409
        when no account (any rung) qualifies;
        503 when no terminal resolves. Every attempt is a `web-migrate`
        state_files row, failures also an A.error."""
        body = self._post_guard()
        if body is None:
            return
        row = API.session_row(sid) or {}
        log = row.get("log") or P.mirror_log(sid)
        if not (row or os.path.isfile(P.state_db(log))
                or os.path.isfile(P.parked_db(log))):
            A.state_file(log, "", "web-migrate",
                         {"ok": False, "reason": "unknown sid"})
            return self._json({"error": "unknown session"}, 404)
        sdb = API.state_db_for(sid) or P.state_db(log)
        fe = _frontend()
        if fe is None:
            A.error(log, "dashboard migrate (no terminal)", {"sid": sid})
            A.state_file(log, sdb, "web-migrate",
                         {"ok": False, "reason": "no terminal"})
            return self._json({"error": "no terminal available"}, 503)
        cur = (API.kv_at(sdb, "account") or {}).get("slug") or ""
        # The model the session is running (off its transcript) feeds the
        # downgrade ladder (docs/relimit.md *Model-downgrade ladder*): a manual
        # ⇆ now downgrades too when no account has the current model free.
        cur_model = (plugins.context(row.get("transcript_path") or "")
                     or {}).get("model") or ""
        target = plugins.migration_target(cur, cur_model, manual=True)
        if target is None:
            A.state_file(log, sdb, "web-migrate",
                         {"ok": False, "reason": "no target", "from": cur})
            return self._json({"error": "no other account available"}, 409)
        # target["model"] is the downgrade rung (or "" for a same-model migrate);
        # pick_target already resolved same-vs-downgrade, so forward it verbatim.
        proc = SP.spawn_detached(
            os.path.join(P.BIN, "claude-relimit.py"),
            [log, sid, target["slug"], target["alias"],
             row.get("cwd") or "", "manual", target["model"]],
            log, purpose="relimit:%s (web)" % target["slug"])
        ok = proc is not None
        A.state_file(log, sdb, "web-migrate",
                     {"ok": ok, "from": cur, "to": target["slug"],
                      "model": target["model"], "eff": target["eff"],
                      "cwd": row.get("cwd") or ""})
        if not ok:                       # spawn failure already audited by SP
            return self._json({"error": "migrator spawn failed"}, 502)
        return self._json({"ok": True, "to": target["slug"]})

    def post_rewind(self, sid):
        """The double-Esc GESTURE, whose meaning in Claude Code depends on
        session state — mirrored here by the tab state at gesture time:

        MID-TURN (busy tab): double-Esc CANCELS the current work and
        restores the last message into the input for editing (it leaves the
        conversation). Mirrored with TWO Escape key events
        `DOUBLE_ESC_GAP_S` apart — measured 3/3 reliable mid-turn on a live
        session (2026-07-18), unlike the idle rewind-menu detection — plus
        the magenta escape-recheck (the same experiment showed the tab
        stays stuck thinking after the cancel). Editing then happens in the
        kitty tab.

        IDLE: double-Esc opens the rewind/checkpoint menu — mirrored by
        TYPING `/rewind` (documented identical; synthesized double-press
        key events opened the menu only ~2/3 at the best gap while the
        typed command opened it every time). No Escape pressed ⇒ no
        recheck.

        The response's `mode` (`cancel-edit` | `rewind`) tells the page
        which meaning fired; the same field rides the `web-rewind` audit
        row (`{win, ok, tab, mode}`)."""
        body = self._post_guard()
        if body is None:
            return
        row = API.session_row(sid) or {}
        log = row.get("log") or P.mirror_log(sid)
        sdb = API.state_db_for(sid) or P.state_db(log)
        fe = _frontend()
        if fe is None:
            A.error(log, "dashboard rewind (no terminal)", {"sid": sid})
            A.state_file(log, sdb, "web-rewind", {"win": "", "ok": False})
            return self._json({"error": "no terminal available"}, 503)
        win = fe.window_for_session(sid) or ""
        if not win:
            A.state_file(log, sdb, "web-rewind", {"win": "", "ok": False})
            return self._json({"error": "session has no live window"}, 409)
        tab = API.tab_states().get(win) or ""
        if self._dialog_open_guard(tab, log, sdb, win, "web-rewind"):
            return
        restored = ""
        if tab in BUSY_TABS:
            mode = "cancel-edit"
            # the message the cancel restores into the input for editing IS
            # the session's last user prompt — read it BEFORE the Escapes so
            # the page can prefill its composer + drop the cancelled bubble
            restored = _last_prompt(sid)
            tpath = row.get("transcript_path") or ""
            try:
                tsize = os.path.getsize(tpath) if tpath else -1
            except OSError:
                tsize = -1
            ok = bool(fe.send_key(win, "escape"))
            time.sleep(DOUBLE_ESC_GAP_S)
            ok = bool(fe.send_key(win, "escape")) and ok
            if ok and tab in (tabs.THINKING, tabs.WORKING):
                self._spawn_escape_recheck(fe, win, log, tpath, tsize)
        else:
            mode = "rewind"
            ok = bool(fe.send_text(win, "/rewind"))
        A.state_file(log, sdb, "web-rewind",
                     {"win": win, "ok": ok, "tab": tab, "mode": mode})
        if not ok:
            A.error(log, "dashboard rewind (send failed)",
                    {"sid": sid, "win": win, "mode": mode})
            return self._json({"error": "send failed"}, 502)
        return self._json({"ok": True, "tab": tab, "mode": mode,
                           "restored": restored})

    def post_rewind_to(self, sid):
        """FULL web rewind — restore the session to the checkpoint of a
        SPECIFIC prompt without touching the kitty tab (docs/dashboard.md,
        *Web rewind*): drives Claude Code's own rewind menu in the session's
        window via dashboard/rewindmenu.drive (typed `/rewind`, screen-
        verified navigation, digit resolved from the parsed option labels).

        Body: `text` — the target prompt's full text (menu entries are its
        first line, truncation-aware); `mode` — "conversation" | "both" |
        "code" (rewindmenu.MODE_LABELS); `ups` — the target's `up`-press
        distance from the menu's "(current)" cursor start (newer prompts
        + 1), a jump hint the text-verify scan corrects.

        409 when the tab is BUSY (mid-turn the double-Esc gesture means
        cancel, not rewind — and a typed `/rewind` would just queue as a
        message) or when the step didn't verify (MenuError — menus already
        closed; `step` says which). The response's `restored` echoes `text`
        for conversation restores — Claude Code puts the rewound prompt back
        into the TUI input, so the page prefills its composer and resends
        with clear_draft, the cancel-edit contract. Every attempt is a
        `web-rewind-to` state_files row carrying mode/ups/steps/digit (or
        the failing step), failures also an A.error."""
        body = self._post_guard()
        if body is None:
            return
        text = body.get("text")
        mode = body.get("mode") or "conversation"
        if not isinstance(text, str) or not text.strip():
            return self._json({"error": "empty text"}, 400)
        if mode not in rewindmenu.MODE_LABELS:
            return self._json({"error": "bad mode"}, 400)
        try:
            ups = max(0, int(body.get("ups") or 0))
        except (TypeError, ValueError):
            ups = 0
        row = API.session_row(sid) or {}
        log = row.get("log") or P.mirror_log(sid)
        sdb = API.state_db_for(sid) or P.state_db(log)
        fe = _frontend()
        if fe is None:
            A.error(log, "dashboard rewind-to (no terminal)", {"sid": sid})
            A.state_file(log, sdb, "web-rewind-to",
                         {"win": "", "ok": False, "mode": mode})
            return self._json({"error": "no terminal available"}, 503)
        win = fe.window_for_session(sid) or ""
        if not win:
            A.state_file(log, sdb, "web-rewind-to",
                         {"win": "", "ok": False, "mode": mode})
            return self._json({"error": "session has no live window"}, 409)
        tab = API.tab_states().get(win) or ""
        if self._dialog_open_guard(tab, log, sdb, win, "web-rewind-to"):
            return
        if tab in BUSY_TABS:
            A.state_file(log, sdb, "web-rewind-to",
                         {"win": win, "ok": False, "tab": tab, "mode": mode,
                          "step": "busy"})
            return self._json(
                {"error": "session is busy — stop or cancel it first",
                 "tab": tab}, 409)
        try:
            res = rewindmenu.drive(fe, win, text, mode, ups=ups)
        except rewindmenu.MenuError as e:
            A.error(log, "dashboard rewind-to (%s)" % e.step,
                    {"sid": sid, "win": win, "mode": mode, "detail": str(e)})
            A.state_file(log, sdb, "web-rewind-to",
                         {"win": win, "ok": False, "tab": tab, "mode": mode,
                          "ups": ups, "step": e.step})
            return self._json({"error": str(e), "step": e.step}, 409)
        A.state_file(log, sdb, "web-rewind-to",
                     {"win": win, "ok": True, "tab": tab, "mode": mode,
                      "ups": ups, "steps": res["steps"],
                      "digit": res["digit"], "degraded": res["degraded"]})
        restored = text if mode in ("conversation", "both") else ""
        return self._json({"ok": True, "mode": mode, "restored": restored,
                           "degraded": res["degraded"]})

    def post_ask_draft(self, sid):
        """Persist the UNSUBMITTED ask selections (the ask card's in-progress
        answers) to the `ask-draft` kv so another device — or the same one
        after a reload — restores them when it reopens the session (docs/
        dashboard.md, *Web ask*). This types NOTHING into the terminal: it is
        a pure state write, distinct from post_answer (which drives the real
        dialog). The session SSE re-broadcasts the draft as an `ask-draft`
        event so an already-open card on another device updates live; the
        writer suppresses its own echo via `origin`.

        Body: `tool_use_id` (must match the open `ask-pending` stash — a
        draft for a gone/replaced question is refused, 409), `answers` (a
        list aligned with the questions: {selected, other} per question),
        `origin` (an opaque per-page id, echoed back over SSE). ask_fmt.py
        clears the draft on the same boundary as `ask-pending`, so it never
        outlives its question. Best-effort: a write failure is a 500 but the
        card keeps its local state and retries on the next change."""
        body = self._post_guard()
        if body is None:
            return
        pending = _ask_pending(sid)
        if not pending:
            return self._json({"error": "no pending question"}, 409)
        if (body.get("tool_use_id") or "") != (pending.get("tool_use_id") or ""):
            return self._json({"error": "ask expired"}, 409)
        answers = body.get("answers")
        questions = pending.get("questions") or []
        if not isinstance(answers, list) or len(answers) != len(questions):
            return self._json({"error": "answers must match the %d question%s"
                               % (len(questions),
                                  "" if len(questions) == 1 else "s")}, 400)
        clean = [{"selected": [str(s) for s in (a.get("selected") or [])
                               if isinstance(a, dict)],
                  "other": str((a.get("other") or "") if isinstance(a, dict)
                               else "")}
                 for a in answers]
        draft = {"tool_use_id": pending.get("tool_use_id") or "",
                 "answers": clean,
                 "origin": str(body.get("origin") or "")}
        row = API.session_row(sid) or {}
        log = row.get("log") or P.mirror_log(sid)
        sdb = API.state_db_for(sid) or P.state_db(log)
        if not ST.kv_set_at(sdb, "ask-draft", draft):
            A.error(log, "dashboard ask-draft (write failed)", {"sid": sid})
            return self._json({"error": "draft not saved"}, 500)
        A.state_file(log, sdb, "ask-draft",
                     {"action": "write", "tool_use_id": draft["tool_use_id"],
                      "origin": draft["origin"]})
        return self._json({"ok": True})

    def post_composer_draft(self, sid):
        """Persist the UNSENT composer text (the message box's in-progress
        draft) to the `composer-draft` kv so another device — or the same one
        after a reload / a return to this session from another — restores it
        (docs/dashboard.md, *Web composer draft*). Like post_ask_draft this
        types NOTHING into the terminal: a pure state write, distinct from
        post_message (which sends). The session SSE re-broadcasts the draft as
        a `composer-draft` event so an already-open composer on another device
        updates live; the writer suppresses its own echo via `origin`.

        Body: `text` (the current draft — empty/blank DELETES the stash so the
        box clears everywhere), `origin` (an opaque per-page id, echoed back
        over SSE). Best-effort: a write failure is a 500 but the box keeps its
        local text and retries on the next change. Unlike the ask draft there
        is no tool_use_id / turn-boundary lifecycle — a message draft has no
        natural expiry, so it lives until sent or overwritten (that IS the
        'come back and it's still there' the user asked for)."""
        body = self._post_guard()
        if body is None:
            return
        text = body.get("text")
        if not isinstance(text, str):
            return self._json({"error": "text must be a string"}, 400)
        origin = str(body.get("origin") or "")
        seq = body.get("seq")
        seq = seq if isinstance(seq, (int, float)) else 0
        row = API.session_row(sid) or {}
        log = row.get("log") or P.mirror_log(sid)
        sdb = API.state_db_for(sid) or P.state_db(log)
        # STALE-WRITE GUARD: a debounced save and the clear-on-send race over a
        # slow tunnel and can arrive out of order — an old save landing after
        # the clear would resurrect a just-sent draft (the "draft didn't clear"
        # report, 2026-07-19). Each write carries a wall-clock `seq`; a write
        # older than what's stored is dropped so the newest state stands. The
        # CLEAR keeps an empty-text TOMBSTONE (not a delete) so its seq survives
        # to reject a later straggler; _composer_draft reads a tombstone as None.
        prev = API.kv_at(sdb, "composer-draft")
        prev_seq = (prev.get("seq") if isinstance(prev, dict) else 0) or 0
        if seq and prev_seq and seq < prev_seq:
            A.state_file(log, sdb, "composer-draft",
                         {"action": "stale", "seq": seq, "have": prev_seq,
                          "origin": origin})
            return self._json({"ok": True, "stale": True})
        # a whitespace-only box is a CLEAR: store a canonical empty-text
        # tombstone (keeps the seq to reject a later straggler; reads as None)
        draft = {"text": text if text.strip() else "", "origin": origin,
                 "seq": seq}
        if not ST.kv_set_at(sdb, "composer-draft", draft):
            A.error(log, "dashboard composer-draft (write failed)", {"sid": sid})
            return self._json({"error": "draft not saved"}, 500)
        A.state_file(log, sdb, "composer-draft",
                     {"action": "write" if text.strip() else "clear",
                      "chars": len(text), "seq": seq, "origin": origin})
        return self._json({"ok": True})

    def post_composer_queue(self, sid):
        """Persist the pending queued-message chips (the ⧗ list the composer
        shows for mid-turn messages the TUI queued but hasn't delivered) to the
        `composer-queue` kv, so a reload / another device restores them instead
        of losing the chip (the 'gone even from the queue after refresh'
        report, 2026-07-19; docs/dashboard.md, *Web composer queue*). Types
        NOTHING into the terminal — a pure state write, like the draft
        endpoints. The page sends the WHOLE current chip list on every change
        (queued, delivered-drain, ✕-hide); the SSE re-broadcasts it as a
        `composer-queue` event, the writer suppressing its own echo via
        `origin`.

        Body: `items` (a list of {text}; empty DELETES the stash), `origin`."""
        body = self._post_guard()
        if body is None:
            return
        items = body.get("items")
        if not isinstance(items, list):
            return self._json({"error": "items must be a list"}, 400)
        clean = [{"text": str(it.get("text") or "")}
                 for it in items if isinstance(it, dict)
                 and (it.get("text") or "").strip()]
        origin = str(body.get("origin") or "")
        row = API.session_row(sid) or {}
        log = row.get("log") or P.mirror_log(sid)
        sdb = API.state_db_for(sid) or P.state_db(log)
        if clean:
            if not ST.kv_set_at(sdb, "composer-queue",
                                {"items": clean, "origin": origin}):
                A.error(log, "dashboard composer-queue (write failed)",
                        {"sid": sid})
                return self._json({"error": "queue not saved"}, 500)
            A.state_file(log, sdb, "composer-queue",
                         {"action": "write", "n": len(clean), "origin": origin})
        else:
            ST.kv_del_at(sdb, "composer-queue")
            A.state_file(log, sdb, "composer-queue",
                         {"action": "remove", "origin": origin})
        return self._json({"ok": True})

    def post_answer(self, sid):
        """Answer the session's OPEN AskUserQuestion dialog from the web (the
        ask card — docs/dashboard.md, *Web ask*): drives the TUI's own dialog
        with screen-verified key events (dashboard/askdialog.drive).

        Body: `tool_use_id` — must match the `ask-pending` stash (a stale
        card is refused before any key is pressed); either `chat: true` (the
        dialog's own "Chat about this" — declines + invites discussion; the
        page then focuses its composer) or `answers` — a list aligned with
        the stash's questions: {"selected": [labels…], "other": "text"} per
        question (multiSelect may combine both; single-select uses one or
        the other).

        409 on a missing/expired stash, a stash/window mismatch, or any
        dialog step that didn't verify (AskError — the dialog is left OPEN,
        never Escape-closed: Escape would DECLINE the questions; `step` says
        what failed and a retry from the card re-normalizes). Every attempt
        is a `web-answer` state_files row, failures also an A.error. The
        card itself clears via the SSE `ask` event when the answer's
        PostToolUse drops the stash — the true end-to-end signal."""
        body = self._post_guard()
        if body is None:
            return
        chat = bool(body.get("chat"))
        answers = body.get("answers")
        row = API.session_row(sid) or {}
        log = row.get("log") or P.mirror_log(sid)
        sdb = API.state_db_for(sid) or P.state_db(log)
        pending = _ask_pending(sid)
        if not pending:
            return self._json({"error": "no pending question"}, 409)
        if (body.get("tool_use_id") or "") != (pending.get("tool_use_id") or ""):
            return self._json({"error": "ask expired — a newer question "
                               "replaced it (refresh)"}, 409)
        questions = pending.get("questions") or []
        if not chat and (not isinstance(answers, list)
                         or len(answers) != len(questions)):
            return self._json({"error": "answers must match the %d question%s"
                               % (len(questions),
                                  "" if len(questions) == 1 else "s")}, 400)
        fe = _frontend()
        if fe is None:
            A.error(log, "dashboard answer (no terminal)", {"sid": sid})
            A.state_file(log, sdb, "web-answer",
                         {"win": "", "ok": False, "chat": chat})
            return self._json({"error": "no terminal available"}, 503)
        win = fe.window_for_session(sid) or ""
        if not win:
            A.state_file(log, sdb, "web-answer",
                         {"win": "", "ok": False, "chat": chat})
            return self._json({"error": "session has no live window"}, 409)
        try:
            askdialog.drive(fe, win, questions, answers or [], chat=chat)
        except askdialog.AskError as e:
            A.error(log, "dashboard answer (%s)" % e.step,
                    {"sid": sid, "win": win, "chat": chat,
                     "detail": str(e)})
            A.state_file(log, sdb, "web-answer",
                         {"win": win, "ok": False, "chat": chat,
                          "step": e.step,
                          "tool_use_id": pending.get("tool_use_id") or ""})
            _heal_stash(sid, log, sdb, "ask-pending", e.step)
            return self._json({"error": str(e), "step": e.step}, 409)
        A.state_file(log, sdb, "web-answer",
                     {"win": win, "ok": True, "chat": chat,
                      "tool_use_id": pending.get("tool_use_id") or ""})
        # a PREVIEW-layout question has no typed-answer row (askdialog
        # _require_type_row), so the card routes a TYPED answer through 'Chat
        # about this' AND carries the typed text here as `message`: once the
        # dialog is dismissed (drive waited for that), deliver it as the
        # follow-up so the user's custom answer reaches the session as a
        # normal message (docs/dashboard.md, *Web ask*). Only with chat.
        msg = body.get("message")
        resp = {"ok": True, "chat": chat}
        if chat and isinstance(msg, str) and msg.strip():
            sent = bool(fe.paste_text(win, msg))
            A.state_file(log, sdb, "web-send",
                         {"win": win, "chars": len(msg), "ok": sent,
                          "via": "ask-chat"})
            if not sent:
                A.error(log, "dashboard answer-chat message (send failed)",
                        {"sid": sid, "win": win})
            resp["message_sent"] = sent
        return self._json(resp)

    def _plan_guard(self, sid):
        """The shared head of the two plan endpoints: guard the POST, match
        the stash, resolve the live window. Returns (body, pending, fe, win,
        log, sdb) — or (None, …) after sending the error response."""
        none = (None,) * 6
        body = self._post_guard()
        if body is None:
            return none
        row = API.session_row(sid) or {}
        log = row.get("log") or P.mirror_log(sid)
        sdb = API.state_db_for(sid) or P.state_db(log)
        pending = _plan_pending(sid)
        if not pending:
            self._json({"error": "no pending plan"}, 409)
            return none
        if (body.get("tool_use_id") or "") != (pending.get("tool_use_id")
                                               or ""):
            self._json({"error": "plan expired — a newer plan replaced it "
                        "(refresh)"}, 409)
            return none
        fe = _frontend()
        if fe is None:
            A.error(log, "dashboard plan (no terminal)", {"sid": sid})
            self._json({"error": "no terminal available"}, 503)
            return none
        win = fe.window_for_session(sid) or ""
        if not win:
            self._json({"error": "session has no live window"}, 409)
            return none
        return body, pending, fe, win, log, sdb

    def post_plan_options(self, sid):
        """The plan card's decision buttons — the dialog's option labels VARY
        with the session's permission mode ("Yes, and bypass permissions" vs
        "Yes, and auto-accept edits"), so the page fetches them from the live
        screen (plandialog.options — read-only: no key is pressed). An `open`
        bail self-heals the stash (the dialog resolved in the terminal)."""
        body, pending, fe, win, log, sdb = self._plan_guard(sid)
        if body is None:
            return
        try:
            opts = plandialog.options(fe, win)
        except plandialog.PlanError as e:
            _heal_stash(sid, log, sdb, "plan-pending", e.step)
            return self._json({"error": str(e), "step": e.step}, 409)
        return self._json({"ok": True, "options": opts})

    def post_plan_decision(self, sid):
        """Decide the OPEN plan dialog from the web (docs/dashboard.md, *Web
        plan mode*): drives the TUI's own dialog via dashboard/plandialog.

        Body (one of, after `tool_use_id` matching the `plan-pending` stash):
        `digit` + `label` — press that decision row, verified against the
        live screen (label drift = 409, nothing pressed); `feedback` — the
        "Tell Claude what to change" row: focus, type, Enter (rejects with
        feedback; newlines collapse — single-line editor); `dismiss: true` —
        Escape, the TUI's own reject-and-keep-planning.

        409 on stash mismatch or any unverified step (PlanError — the dialog
        is left OPEN: an Escape bail would REJECT a plan the user may still
        approve; `open` bails self-heal the stash). Every attempt is a
        `web-plan` state_files row, failures also an A.error. The card
        clears via the SSE `plan` event when the stash drops (approval's
        PostToolUse, or the turn boundary after a reject)."""
        body, pending, fe, win, log, sdb = self._plan_guard(sid)
        if body is None:
            return
        tid = pending.get("tool_use_id") or ""
        if body.get("dismiss"):
            kind, run = "dismiss", (plandialog.dismiss, (fe, win))
        elif isinstance(body.get("feedback"), str) \
                and body["feedback"].strip():
            kind = "feedback"
            run = (plandialog.feedback, (fe, win, body["feedback"]))
        elif body.get("digit") and isinstance(body.get("label"), str):
            kind = "decide"
            run = (plandialog.decide,
                   (fe, win, str(body["digit"]), body["label"]))
        else:
            return self._json({"error": "need digit+label, feedback, or "
                               "dismiss"}, 400)
        try:
            run[0](*run[1])
        except plandialog.PlanError as e:
            A.error(log, "dashboard plan (%s)" % e.step,
                    {"sid": sid, "win": win, "kind": kind,
                     "detail": str(e)})
            A.state_file(log, sdb, "web-plan",
                         {"win": win, "ok": False, "kind": kind,
                          "step": e.step, "tool_use_id": tid})
            _heal_stash(sid, log, sdb, "plan-pending", e.step)
            return self._json({"error": str(e), "step": e.step}, 409)
        A.state_file(log, sdb, "web-plan",
                     {"win": win, "ok": True, "kind": kind,
                      "label": body.get("label") or "", "tool_use_id": tid})
        return self._json({"ok": True, "kind": kind})

    def _dialog_open_guard(self, tab, log, sdb, win, action):
        """Refuse an Esc-sending gesture (interrupt / cancel-edit / rewind) when
        a MODAL DIALOG is open — the red `awaiting-command` tab means Claude is
        asking YOU (AskUserQuestion / ExitPlanMode / a permission prompt). An
        Esc there does not cancel a turn; it DECLINES/dismisses the dialog,
        which once killed the answer the user was giving through the web ask
        card ("User declined to answer questions", 2026-07-20). The dashboard's
        dedicated cards (ask/plan/confirm) are the response path, so bail with a
        409 and audit it — the same contract post_command uses on a red tab.
        Returns True when it handled (sent) the refusal; False to proceed."""
        if tab != tabs.AWAITING_COMMAND:
            return False
        A.state_file(log, sdb, action,
                     {"win": win, "ok": False, "tab": tab, "step": "dialog"})
        self._json({"error": "a dialog is open — answer it first"}, 409)
        return True

    def _escape_press(self, sid, verb, action):
        """Body of post_interrupt: guard, resolve the LIVE window, press
        Escape, audit as `action`, and spawn the escape-recheck when the
        press landed on magenta. A red (awaiting-command) tab is a 409: a
        dialog is open and the Esc would DECLINE it, not interrupt a turn."""
        body = self._post_guard()
        if body is None:
            return
        row = API.session_row(sid) or {}
        log = row.get("log") or P.mirror_log(sid)
        sdb = API.state_db_for(sid) or P.state_db(log)
        fe = _frontend()
        if fe is None:
            A.error(log, "dashboard %s (no terminal)" % verb, {"sid": sid})
            A.state_file(log, sdb, action, {"win": "", "ok": False})
            return self._json({"error": "no terminal available"}, 503)
        # AUTHORITATIVE window: the pane currently tagged claude_session=<sid>,
        # NOT the audit row's stale start-time id (an Escape into a reused id
        # would interrupt an unrelated session — see _live_windows; a fresh
        # scan, never the TTL memo).
        win = fe.window_for_session(sid) or ""
        if not win:
            A.state_file(log, sdb, action, {"win": "", "ok": False})
            return self._json({"error": "session has no live window"}, 409)
        tab = API.tab_states().get(win) or ""
        if self._dialog_open_guard(tab, log, sdb, win, action):
            return
        # Press-time transcript size — the escape-recheck's growth baseline
        # (stat'd BEFORE the key lands so even the interrupt line counts as
        # growth). '' / unstat-able transcript degrades to the recheck's own
        # start-time baseline.
        tpath = row.get("transcript_path") or ""
        try:
            tsize = os.path.getsize(tpath) if tpath else -1
        except OSError:
            tsize = -1
        ok = bool(fe.send_key(win, "escape"))
        A.state_file(log, sdb, action, {"win": win, "ok": ok, "tab": tab})
        if ok and tab in (tabs.THINKING, tabs.WORKING):
            # An Esc killed mid-think leaves NO signal anywhere (the known
            # interrupt-watch gap) — but a WEB interrupt is itself an event,
            # so spawn the escape-recheck: flip the dead magenta green unless
            # any real signal (state movement / transcript growth) shows up
            # within its grace. Detached + audited (A.spawn); its verdict
            # lands as tab_transitions rows under DISPATCH escape-recheck.
            self._spawn_escape_recheck(fe, win, log, tpath, tsize)
        if not ok:
            A.error(log, "dashboard %s (send failed)" % verb,
                    {"sid": sid, "win": win})
            return self._json({"error": "send failed"}, 502)
        return self._json({"ok": True, "tab": tab})

    def _spawn_escape_recheck(self, fe, win, log, tpath, tsize):
        """Detached `claude-tab-status.py escape-recheck <log> <transcript>
        <press-size>` for the session's window. Env carries the window id +
        the terminal-reach vars (fe.export_env — the detached process is
        re-parented, so the ppid socket walk can't find kitty). Spawn failure
        is audited by spawn_detached; assembly failure lands its own A.error
        (the recovery not firing must never be invisible)."""
        try:
            fe.export_env()
            env = dict(os.environ)
            env["KITTY_WINDOW_ID"] = str(win)
            args = ["escape-recheck", log, tpath]
            if tsize >= 0:
                args.append(str(tsize))
            SP.spawn_detached(os.path.join(P.BIN, "claude-tab-status.py"),
                              args, log, env=env,
                              purpose="watcher:escape-recheck")
        except Exception:
            A.error(log, "dashboard interrupt (escape-recheck spawn)",
                    {"win": win})

    def _reject_input(self, action, why, message, detail, code=400):
        """A control-plane INPUT-validation reject (the client sent a bad
        field). Audited as an `ok:False` state_files row under the handler's own
        `action` vocabulary, carrying the reason (`why`) and the EXACT received
        bytes (repr — a remote client's "but I picked it from the dropdown" is
        undebuggable otherwise, invisible characters included). Deliberately NOT
        an `errors` row: these are expected 4xx from client input (an
        abandoned/partial cwd, a typo'd model, a bad quick-command), not
        swallowed exceptions — their traceback would be a bare `NoneType: None`
        — and must not light the errwatch warning chip, which surfaces every
        session_id='' `errors` row as a `⚠ global:` in EVERY session's
        scorebar. This is the shape `post_dictate_token` already used inline for
        its bad-rate reject; the reject sites that mis-used A.error now share it.
        Distinct from `_reject` (the low-level guard rejection that closes the
        connection because it hasn't read the body — the input body is already
        consumed by `_post_guard` here, so no desync to guard against). Returns
        the response so callers stay `return self._reject_input(...)`."""
        A.state_file("", "", action,
                     dict({"ok": False, "why": why},
                          **{k: repr(v) for k, v in detail.items()}))
        return self._json({"error": message}, code)

    def post_new_session(self):
        """Launch a new session in a new tab (Frontend.launch_tab). 400 when the
        cwd isn't an existing directory or model/effort/resume/continue don't
        validate (model: one clean argv word; effort: the CLI's EFFORTS levels;
        resume: a clean session id, exclusive with continue); 503 when no
        terminal resolves; else the launch, with `--resume <sid>`/`--continue`
        and `--model`/`--effort` riding as positional "$@" words ahead of the
        prompt. The response carries the new tab's window id when the terminal
        reports one, and a _launch_wake watcher thread hurries the session's
        SSE appearance (see its block). Audited as a `web-launch` state_files
        row (no session db exists yet, so its log/path are empty; `win` = the
        launched window)."""
        body = self._post_guard()
        if body is None:
            return
        cwd = body.get("cwd")
        if not isinstance(cwd, str) or not cwd or not os.path.isdir(cwd):
            return self._reject_input("web-launch", "bad cwd",
                                "cwd is not an existing directory",
                                {"cwd": cwd})
        model, effort = body.get("model"), body.get("effort")
        if model is not None and (
                not isinstance(model, str) or not _MODEL_OK.match(model)):
            return self._reject_input("web-launch", "bad model", "invalid model",
                                {"model": model})
        if effort is not None and effort not in EFFORTS:
            return self._reject_input("web-launch", "bad effort", "invalid effort",
                                {"effort": effort})
        # resume / continue — the CLI's own conversation-pickup flags. resume
        # carries a session id (one clean argv word, same alphabet as our sid
        # routing); continue is a bare flag. Mutually exclusive, like the CLI.
        # A resumed conversation FORKS to a new sid; the existing adopt
        # machinery and the page's jump watch both handle that on their own.
        resume, cont = body.get("resume"), body.get("continue")
        if resume is not None and (
                not isinstance(resume, str) or not _SID_OK.match(resume)):
            return self._reject_input("web-launch", "bad resume", "invalid resume id",
                                {"resume": resume})
        if cont not in (None, False, True):
            return self._reject_input("web-launch", "bad continue",
                                "invalid continue", {"continue": cont})
        if resume and cont:
            return self._reject_input("web-launch", "resume+continue",
                                "resume and continue are exclusive",
                                {"resume": resume})
        # account: the switcher slug to launch under (default `claude` when
        # absent). Resolved to a registry-vetted command word — never the raw
        # value flows into the launch shell string.
        acct = body.get("account")
        cmd = plugins.account_alias(acct) if acct else "claude"
        if cmd is None:
            return self._reject_input("web-launch", "bad account", "unknown account",
                                {"account": acct})
        prompt = body.get("prompt")
        words = ((["--resume", resume] if resume else [])
                 + (["--continue"] if cont else [])
                 + (["--model", model] if model else [])
                 + (["--effort", effort] if effort else [])
                 + ([prompt] if isinstance(prompt, str) and prompt.strip()
                    else []))
        argv = launch_argv(words, cmd)
        opts = {"cwd": cwd, "model": model or "", "effort": effort or "",
                "resume": resume or "", "cont": bool(cont),
                "account": acct or ""}
        fe = _frontend()
        if fe is None:
            A.error("", "dashboard new-session (no terminal)", {"cwd": cwd})
            A.state_file("", "", "web-launch", dict(opts, ok=False))
            return self._json({"error": "no terminal available"}, 503)
        # Guard: never resume-launch a session that ALREADY has a live tab. A
        # second `claude --resume <sid>` would run a duplicate process against
        # the SAME transcript (two tabs, interleaved writes). The page issues a
        # resume-launch only when it believes the session is PARKED, but a
        # stale page (e.g. after the dashboard restarts and its SSE drops)
        # can misjudge a live session — this is the server-side backstop.
        # window_for_session is a fresh live kitten scan (authoritative over
        # any cached/page state); fresh and --continue launches are unaffected.
        # The page gets the live window back so it can focus/message it instead.
        if resume:
            live_win = fe.window_for_session(resume) or ""
            if live_win:
                A.state_file("", "", "web-launch",
                             dict(opts, ok=False, win=live_win))
                return self._json(
                    {"error": "session already live", "sid": resume,
                     "win": live_win}, 409)
        # the passive steal watch (see the block above the Handler class):
        # the frontmost app must be captured BEFORE the launch — a steal can
        # land before the kitten call returns. Skipped when the terminal was
        # ALREADY frontmost at click time (nothing to steal) or the frontend
        # has no OS app identity (the inert stub, off-mac).
        term = fe.app_id()
        before = _front_app() if term else ""
        # launch_tab: the new window's id on success when the terminal reports
        # one (kitty prints it), bare True when it doesn't, falsy on failure.
        got = fe.launch_tab(cwd, argv)
        win = got if isinstance(got, str) else ""
        A.state_file("", "", "web-launch", dict(opts, ok=bool(got), win=win))
        if not got:
            A.error("", "dashboard new-session (launch failed)", {"cwd": cwd})
            return self._json({"error": "launch failed"}, 502)
        # the SSE wake watch (see the block above the Handler class): hurry
        # the launched session's appearance to every connected page — and hand
        # the launching page its sid — the moment SessionStart lands.
        threading.Thread(target=_launch_wake, args=(win, cwd, time.time()),
                         daemon=True, name="web-launch-wake").start()
        if before and before != term:
            threading.Thread(target=_steal_watch, args=(before, term),
                             daemon=True, name="web-launch-steal-watch").start()
        # `win` lets the page match the launched session exactly (its jump
        # watch compares kitty_window_id); "" when the terminal didn't report
        # an id — the page falls back to its cwd heuristic.
        return self._json({"ok": True, "win": win})

    def post_ns_prefs(self):
        """Remember the new-session form's last-used {cwd, model, effort} in the
        durable GLOBAL prefs store (dashboard/prefs.py) so the next launch — on
        this device or any other pointing at this dashboard — pre-selects them
        (docs/dashboard.md, *New-session prefs*). The page calls this on a
        successful launch, exactly where it used to write localStorage; the
        BEHAVIOUR is unchanged, only the storage moved to the backend.

        Body: `cwd` (string), `model`/`effort` (validated against the same
        allowlists post_new_session uses — a bad value is dropped, never
        stored, so a corrupt pref can't later feed the launch path). Missing
        fields are simply omitted from the stored record. Best-effort: a write
        failure is a 500 but the launch itself already succeeded."""
        body = self._post_guard()
        if body is None:
            return
        rec = {}
        cwd = body.get("cwd")
        if isinstance(cwd, str) and cwd:
            rec["cwd"] = cwd
        model = body.get("model")
        if isinstance(model, str) and _MODEL_OK.match(model):
            rec["model"] = model
        effort = body.get("effort")
        if effort in EFFORTS:
            rec["effort"] = effort
        if not prefs.set("new-session", rec):
            A.error("", "dashboard ns-prefs (write failed)", {"rec": rec})
            return self._json({"error": "prefs not saved"}, 500)
        # global (no session) — audited with an empty log/path like web-launch
        A.state_file("", "", "ns-prefs", dict(rec, action="write"))
        return self._json({"ok": True})

    def post_notify_mute(self, sid):
        """Opt a session in/out of the deferred Telegram alert (docs/dashboard.md
        *Telegram alerts*) — the header 🔔/🔕 toggle. Body: `muted` (bool).
        Writes the durable global prefs store (dashboard/prefs.py), NOT any
        session/terminal state, so it works live AND parked. Behind _post_guard
        like every control-plane POST; audited as a `notify-mute` state_files row
        (global — empty log/path like hide-dir). Returns the flipped state."""
        body = self._post_guard()
        if body is None:
            return
        muted = body.get("muted")
        if not isinstance(muted, bool):
            return self._reject_input("notify-mute", "bad muted",
                                      "muted must be a boolean", {"muted": muted})
        prefs.set_notify_muted(sid, muted)
        A.state_file("", "", "notify-mute", {"sid": sid, "muted": muted})
        return self._json({"ok": True, "muted": muted})

    def post_hide_dir(self):
        """Hide a directory group from the list page (docs/dashboard.md *Hidden
        directories*). Non-destructive: the sessions keep running, their tabs and
        toasts still fire — the group just vanishes from the crowded list until a
        session STARTED after this moment shows up in it (the client compares each
        row's started_at against the stored hide time, so 'start a new session
        there' un-hides it, terminal- or dashboard-launched). Stores {key:
        time.time()} in the durable global prefs store (dashboard/prefs.py),
        keyed by the list's group key (git.root||cwd — the page posts g.cwd,
        already that key). Behind _post_guard like every control-plane POST,
        though it writes only the dashboard's OWN prefs, never a session/terminal.

        A directory with at least one ACTIVE (live) session can't be hidden — a
        409, the authoritative guard behind the disabled ✕ (dir_live_sessions;
        the client also disables the button, but a stale page could still POST).
        Audited as a `hide-dir` state_files row (global — empty log/path like
        ns-prefs). Returns the updated map so the page reconciles S.hidden with
        the server truth."""
        body = self._post_guard()
        if body is None:
            return
        key = body.get("cwd")
        # The EMPTY string is a valid key — it is the list's "no project"
        # aggregate group (sessions with no cwd / git root), which the user can
        # hide like any other. Only a MISSING/non-string cwd (None etc.) is a bad
        # request. repr() in the audit: a reject must keep the EXACT received
        # bytes (same rule as new-session's bad cwd). len cap: a group key is a
        # path — no legitimate one runs long, and the store is not a bucket.
        if not isinstance(key, str) or len(key) > 4096:
            return self._reject_input("hide-dir", "bad key", "cwd must be a string",
                                {"cwd": key})
        # A directory with an active session can't be hidden (409). Not an input
        # error — the key is well-formed — so it's a distinct `why`, but the same
        # audited-reject shape (no errors row / errwatch chip; an expected 4xx).
        live = dir_live_sessions(key)
        if live:
            return self._reject_input(
                "hide-dir", "live session",
                "can't hide a directory with an active session",
                {"cwd": key, "live": len(live)}, code=409)
        ts = time.time()
        m = prefs.hide_dir(key, ts)
        A.state_file("", "", "hide-dir", {"key": key, "hidden_at": ts})
        return self._json({"ok": True, "hidden": m})

    def post_dictate_token(self):
        """Mint a short-lived Deepgram grant for the browser's DIRECT wss
        connection (docs/dashboard.md *Web dictation* — the stdlib server
        can't speak WebSocket and must never see audio, so its whole role is
        this trade: on-disk API key → ~30s single-purpose JWT). The response
        carries the token plus the fully-assembled listen URL (model +
        keyterms server-side; the client contributes only its AudioContext
        sample rate). Behind _post_guard like every control-plane POST — on
        READONLY days dictation is off exactly like the composer it feeds.
        Every attempt is a `web-dictate` state_files row (no sid — the
        new-session form dictates too), failures also an A.error. The API
        key never appears in a response or an audit row."""
        body = self._post_guard()
        if body is None:
            return
        rate = body.get("sample_rate")
        if not isinstance(rate, int) or isinstance(rate, bool) \
                or not (dictate.SAMPLE_RATE_MIN <= rate
                        <= dictate.SAMPLE_RATE_MAX):
            A.state_file("", "", "web-dictate",
                         {"ok": False, "why": "bad-rate",
                          "rate": repr(rate)[:40]})
            return self._json({"error": "bad sample_rate"}, 400)
        if not dictate.available():
            # a race fallback only — the page hides the mic button when the
            # /api/dictate probe says unavailable
            A.state_file("", "", "web-dictate", {"ok": False, "why": "no-key"})
            return self._json({"error": "no deepgram key configured"}, 501)
        # optional cwd — keys the PROJECT vocabulary layer (the composer sends
        # its session's cwd, the new-session form its typed dir). A non-string
        # or non-directory degrades to global-only, never an error — the same
        # contract as /api/commands, and for the same reason (arbitrary
        # sessions' dirs come and go).
        cwd = body.get("cwd")
        if not isinstance(cwd, str) or not os.path.isdir(cwd):
            cwd = ""
        try:
            tok = dictate.grant()
        except Exception as e:
            A.error("", "dashboard dictate (grant failed)",
                    {"err": ("%s: %s" % (type(e).__name__, e))[:200]})
            A.state_file("", "", "web-dictate", {"ok": False, "why": "grant"})
            return self._json({"error": "token grant failed"}, 502)
        terms = dictate.keyterms(cwd)
        url = dictate.ws_url(rate, terms)
        A.state_file("", "", "web-dictate",
                     {"ok": True, "rate": rate, "cwd": cwd,
                      "keyterms": len(terms)})
        return self._json({"token": tok["access_token"],
                           "expires_in": tok.get("expires_in"),
                           "ws_url": url})

    def static(self, name):
        ctype = STATIC.get(name)
        if not ctype:
            return self._json({"error": "not found"}, 404)
        try:
            with open(os.path.join(STATIC_DIR, name), "rb") as fh:
                return self._send(200, fh.read(), ctype)
        except OSError:
            return self._json({"error": "unreadable"}, 500)

    # -- SSE loops --
    def sse_global(self):
        """The all-sessions stream: a `hello` (the server's BOOT_ID — the
        browser's EventSource auto-reconnects when the server restarts, and a
        changed boot id on reconnect is how an OPEN page learns its loaded JS
        may be stale; twice a redeploy shipped while a page sat open and its
        old handlers ran against the new server, audit-visibly), then a
        `sessions` snapshot on connect and whenever MEMBERSHIP or order
        changes, a `sessions-delta` {rows: [changed wire rows]} when only row
        contents moved (SSE frames are never gzipped, and the full 131-row
        snapshot re-sent every active tick measured 2.2MB/min per remote
        viewer — deltas are a few KB/min; the sid set + order pin the list
        layout, so a delta can always merge in place by sid), plus every
        `notify` toast the watcher pushes. Row diffs are paused-blind
        (_row_key) and rows are wire-stripped (_wire_row)."""
        self._sse_start()
        q = NOTIFIER.register()
        try:
            if not self._sse("hello", {"boot": BOOT_ID}):
                return
            beat = time.monotonic()
            wire = [_wire_row(r) for r in sessions_payload()]
            if not self._sse("sessions", wire):
                return
            keys = {r["sid"]: _row_key(r) for r in wire}
            while True:
                drained = False
                try:
                    while True:
                        ev, payload = q.get(timeout=GLOBAL_TICK_S)
                        drained = True
                        if not self._sse(ev, payload):
                            return
                except queue.Empty:
                    pass
                wire = [_wire_row(r) for r in sessions_payload()]
                cur = {r["sid"]: _row_key(r) for r in wire}
                if list(cur) != list(keys):
                    # a session appeared/vanished or the order moved — the
                    # delta contract can't express that; full resync
                    if not self._sse("sessions", wire):
                        return
                    keys = cur
                elif cur != keys:
                    changed = [r for r in wire if cur[r["sid"]] != keys[r["sid"]]]
                    if not self._sse("sessions-delta", {"rows": changed}):
                        return
                    keys = cur
                now = time.monotonic()
                if drained or now - beat > HEARTBEAT_S:
                    beat = now
                    if not self._sse_beat():
                        return
        finally:
            NOTIFIER.unregister(q)

    def sse_session(self, sid, after, mpos=0):
        """One session's live stream: `ops` (rendered HTML), `msgs` (the
        main-thread conversation from byte cursor `mpos`), `stats`, `agents`,
        `tab`, `costs`, `running` (the live slot ribbon), `errors` (the ⚠
        swallowed-error count) — each sent only on change. A FRESH connection
        (after=0, mpos=0) gets the anchor-merged backlog as its first ops
        event; a reconnect resumes both cursors and appends in arrival
        order (interleave is a backfill affordance, not a live guarantee)."""
        self._sse_start()
        last = after
        prev = {"stats": None, "agents": None, "tab": None, "costs": None,
                "running": None, "errors": None, "ask": None, "plan": None,
                "ctx": None, "git": None, "title": None, "effort": None,
                "tasks": None, "ask_draft": None, "composer_draft": None,
                "composer_queue": None, "monitors": None, "jobs": None}
        row = API.session_row(sid) or {}
        win = str(row.get("kitty_window_id") or "")
        key = P.sid_from_log(row.get("log") or P.mirror_log(sid))
        if not after and not mpos:
            last, mpos, oldest, items = merged_backlog(sid, key)
            if items and not self._sse("ops", {"last": last, "mpos": mpos,
                                               "oldest": oldest, "items": items}):
                return
        n, beat = 0, time.monotonic()
        while True:
            sdb = API.state_db_for(sid)
            if sdb:
                last2, ops = API.ops_at(sdb, last)
                if ops:
                    last = last2
                    if not self._sse("ops", {"last": last,
                                             "items": opshtml.op_items(ops, key)}):
                        return
            got = plugins.conversation(sid, mpos)
            if got:
                recs, mpos = got
                if recs and not self._sse("msgs", {"mpos": mpos,
                                                   "items": _conv_items(recs)}):
                    return
                st = API.stats_at(sdb)
                if st != prev["stats"]:
                    prev["stats"] = st
                    if not self._sse("stats", st):
                        return
            if n % SLOW_EVERY == 0:
                # a resume moves the session to a NEW kitty window (the
                # SessionStart upsert refreshes the sessions row) — re-resolve,
                # or a stream opened before the move polls the dead window's
                # lingering tab state forever (green while kitty is magenta)
                row = API.session_row(sid) or {}
                win = str(row.get("kitty_window_id") or "") or win
                # resolved up front so the agent cards' inherit-default effort
                # matches the effort quick-button pushed below (one resolve)
                eff = plugins.effort_default(row.get("cwd") or "",
                                             _session_slug(sid))
                agents = agents_model_effort(
                    agents_ctx(visible_agents(API.agents(sid))), eff)
                if agents != prev["agents"]:
                    prev["agents"] = agents
                    if not self._sse("agents", agents):
                        return
                # the main thread's context saturation — the stats row's ctx
                # chip, live (the transcript grew → the (path, size) cache
                # re-probes; pushed only on change like everything else here)
                ctx = session_ctx(row.get("transcript_path") or "", main=True)
                if ctx != prev["ctx"]:
                    prev["ctx"] = ctx
                    if not self._sse("ctx", {"ctx": ctx}):
                        return
                # the header's title, live — a web rename or a fresh auto
                # ai-title shows on the slow cadence (the (path, size)-cached
                # session_title makes the probe a getsize when nothing grew)
                t = session_title(row.get("transcript_path") or "")
                if t != prev["title"]:
                    prev["title"] = t
                    if not self._sse("title", {"title": t}):
                        return
                # the header's git chip, live — a checkout/branch switch (or a
                # removed worktree) shows on the slow cadence
                git = git_info(row.get("cwd") or "")
                if git != prev["git"]:
                    prev["git"] = git
                    if not self._sse("git", {"git": git}):
                        return
                # the effort quick-button, live — a terminal-side /effort
                # saves to settings and shows here on the slow cadence
                # (eff resolved above, before the agent-card stamp)
                if eff != prev["effort"]:
                    prev["effort"] = eff
                    if not self._sse("effort", {"effort": eff}):
                        return
                costs = API.costs(sid)
                if costs != prev["costs"]:
                    prev["costs"] = costs
                    if not self._sse("costs", costs):
                        return
                run = API.running(sid)
                if run != prev["running"]:
                    prev["running"] = run
                    if not self._sse("running", run):
                        return
                # the ⚠ error badge, live: a cheap COUNT (no tracebacks) on the
                # slow cadence, pushed only on change (full rows stay behind
                # /errors). The web sibling of the scorebar's errwatch chip.
                ec = API.error_count(sid)
                if ec != prev["errors"]:
                    prev["errors"] = ec
                    if not self._sse("errors", {"count": ec}):
                        return
                # the monitors tab badge, live: the cheap distinct-monitor COUNT
                # (streams keystone, no transcript parse), pushed on change — a
                # new Monitor launch bumps it. Full monitor detail (command,
                # events) stays behind /monitors, fetched when the tab opens.
                mc = API.monitor_count(sid)
                if mc != prev["monitors"]:
                    prev["monitors"] = mc
                    if not self._sse("monitors", {"count": mc}):
                        return
                # the jobs tab badge, live: the cheap distinct background-job
                # COUNT (streams keystone), pushed on change — a new bg launch
                # bumps it. Full job detail (command, output) stays behind /jobs
                # + /copy, fetched when the tab / drill-down opens.
                jc = API.job_count(sid)
                if jc != prev["jobs"]:
                    prev["jobs"] = jc
                    if not self._sse("jobs", {"count": jc}):
                        return
                # the pinned tasks card, live — a task create / status flip
                # re-stashes the `tasks` kv (task_fmt.py) and shows on the
                # slow cadence (tasks change per-hook, not per-keystroke;
                # nobody is blocked waiting on this card, unlike ask/plan)
                tasks = _session_tasks(sid)
                if tasks != prev["tasks"]:
                    prev["tasks"] = tasks
                    if not self._sse("tasks", {"tasks": tasks}):
                        return
                # the unsent composer draft — so a composer open on ANOTHER
                # device tracks this one's edits (the writer suppresses its own
                # echo by `origin`; the page skips the repaint while its own
                # box has focus). Slow cadence: a draft is convenience state, no
                # one is blocked on it (unlike the ask/plan dialogs below).
                cdraft = _composer_draft(sid)
                if cdraft != prev["composer_draft"]:
                    prev["composer_draft"] = cdraft
                    if not self._sse("composer-draft", {"draft": cdraft}):
                        return
                # the pending queued-message chips — so a reload / another
                # device restores what the TUI still holds unqueued (slow
                # cadence, convenience state like the draft above)
                cqueue = _composer_queue(sid)
                if cqueue != prev["composer_queue"]:
                    prev["composer_queue"] = cqueue
                    if not self._sse("composer-queue", {"queue": cqueue}):
                        return
            tab = (API.tab_states().get(win) or "") if win else ""
            if tab != prev["tab"]:
                prev["tab"] = tab
                if not self._sse("tab", {"tab": tab}):
                    return
            # the pending modal-dialog cards (fast cadence — the dialog just
            # appeared and the user is waiting); None clears each card
            ask = _ask_pending(sid)
            if ask != prev["ask"]:
                prev["ask"] = ask
                if not self._sse("ask", {"ask": ask}):
                    return
            # the unsubmitted-selections draft — so a card open on ANOTHER
            # device tracks this one's edits (the writer suppresses its own
            # echo by `origin`). Only meaningful while an ask is open;
            # _ask_draft returns None once it's gone, clearing the peer.
            draft = _ask_draft(sid, ask) if ask else None
            if draft != prev["ask_draft"]:
                prev["ask_draft"] = draft
                if not self._sse("ask-draft", {"draft": draft}):
                    return
            plan = _plan_pending(sid)
            if plan != prev["plan"]:
                prev["plan"] = plan
                if not self._sse("plan", {"plan": plan}):
                    return
            now = time.monotonic()
            if now - beat > HEARTBEAT_S:
                beat = now
                if not self._sse_beat():
                    return
            n += 1
            time.sleep(TICK_S)

    def sse_agent(self, sid, aid, pos):
        """One agent's LIVE drill-down timeline (docs/dashboard.md): appends
        `entries` (new increment entries, server-enriched exactly like the REST
        /agent endpoint — the shared _enrich_entries) and `resolve`
        (cross-increment tool resolutions — [(tool_use_id, output, failed), …]
        the client applies by data-tool-id) events as the agent's transcript
        grows from byte cursor `pos`, plus heartbeats; stops cleanly on client
        disconnect. `pos` is the cursor the /agent REST response handed the
        client, so the first increment resumes exactly where the initial fetch
        stopped — no gap, no overlap. A pair with no incremental provider
        (codex declines) yields None forever, so the loop is a heartbeat-only
        keep-alive until the client navigates away."""
        self._sse_start()
        beat = time.monotonic()
        while True:
            got = plugins.activity_since(sid, aid, pos)
            if got is not None:
                entries, resolutions, pos = got
                if entries:
                    _enrich_entries(entries)
                    if not self._sse("entries", {"pos": pos,
                                                 "entries": entries}):
                        return
                if resolutions:
                    if not self._sse("resolve", {"pos": pos,
                                                 "resolutions": resolutions}):
                        return
            now = time.monotonic()
            if now - beat > HEARTBEAT_S:
                beat = now
                if not self._sse_beat():
                    return
            time.sleep(TICK_S)


def _sid(s):
    return bool(_SID_OK.match(s or ""))


def _qint(url, name):
    try:
        return int((parse_qs(url.query).get(name) or ["0"])[0])
    except ValueError:
        return 0


# --- lifecycle ---------------------------------------------------------------------

def serve():
    """Run the server in THIS process (the `serve` CLI verb — `start` spawns
    it detached). Singleton: the paths.DASH_DB pid-lock first, the port bind
    as the second guard. The whole run is one audited stream (kind
    'dashboard') so uptime and the exit path are queryable."""
    res = locks.lock_acquire(P.DASH_DB, LOCK_KEY)
    if res.startswith("claim-denied"):
        A.error("", "dashboard serve (lock denied)", {"res": res})
        return 1
    with stream_lifecycle("", "dashboard", src_path="http://%s:%d" % (HOST, PORT),
                          ctx={"port": PORT},
                          on_exit=lambda: locks.lock_release(P.DASH_DB, LOCK_KEY)) as run:
        try:
            httpd = ThreadingHTTPServer((HOST, PORT), Handler)
        except OSError:
            run.end("port-busy")
            A.error("", "dashboard serve (port busy)", {"port": PORT})
            return 1
        httpd.daemon_threads = True
        threading.Thread(target=NOTIFIER.run, daemon=True).start()

        def _term(signum, frame):
            raise SystemExit(0)

        signal.signal(signal.SIGTERM, _term)
        run.end("stopped")                  # the expected exit (SIGTERM/^C);
        try:                                # a crash overwrites it via __exit__
            httpd.serve_forever(poll_interval=0.5)
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            try:
                httpd.server_close()
            except Exception:
                pass
    return 0
