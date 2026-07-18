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
# same as it has no tab colour.
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
from dashboard import askdialog, opshtml, plandialog, rewindmenu

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

# Tab states during which a composer send lands in Claude Code's own message
# QUEUE (a turn is in progress — the TUI queues typed input and delivers it
# when the turn ends) rather than starting a turn immediately. The /message
# response reports it (`queued`) so the page can show the message as pending
# until it surfaces in the transcript. awaiting-command (red) is deliberately
# NOT here: a dialog is up and typed text goes to the DIALOG, not the queue.
QUEUE_TABS = (tabs.THINKING, tabs.WORKING, tabs.EXECUTING)

# Tab states in which the session is MID-TURN — where Claude Code's double-Esc
# means "cancel the work and restore the last message for editing", not the
# rewind menu (post_rewind mirrors that split). awaiting-command (red) counts:
# a dialog is mid-turn, and Esc-Esc there dismisses + cancels.
BUSY_TABS = (tabs.THINKING, tabs.WORKING, tabs.EXECUTING,
             tabs.AWAITING_BG, tabs.AWAITING_COMMAND)

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

class Notifier:
    """The tab-DB diff watcher + the /events fan-out. Clients register a
    Queue; the watcher thread pushes ('notify', payload) on every asking/done
    transition. Also keeps the win -> session map the payloads are named
    from (refreshed on the slow cadence — sessions come and go rarely)."""

    def __init__(self):
        self.clients = set()
        self.lock = threading.Lock()
        self.prev = {}
        self.winmap = {}

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

    def scan(self):
        cur = API.tab_states()
        prev, self.prev = self.prev, cur
        for win, state in cur.items():
            kind = NOTIFY_STATES.get(state)
            if not kind or prev.get(win) == state or not prev:
                continue                   # first scan is baseline, not news
            row = self.winmap.get(win)
            if not row:
                continue
            self.push("notify", {
                "kind": kind, "state": state, "sid": row.get("sid"),
                "cwd": row.get("cwd") or "",
                "project": os.path.basename(row.get("cwd") or "") or row.get("sid"),
                # resolved at push time, not winmap-refresh time: the title is
                # transcript-derived and the transcript just grew ((path, size)
                # cache in session_title keeps this cheap)
                "title": session_title(row.get("transcript_path") or ""),
            })

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

_TITLES = {}      # transcript_path -> (size, title): a title only changes when
#                   the file grows, so (path, size) is the natural cache key —
#                   the list poll must not re-scan 50 transcript heads per tick


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


_GIT = {}         # cwd -> the _git_resolve result (None = not a checkout). The
#                   ancestor walk + gitdir indirection is stable for a cwd, so it
#                   caches forever; HEAD itself is re-read on every call (one tiny
#                   file) so a branch switch shows on the next poll.

_DIRTY = {}       # cwd -> (monotonic expiry, True|False|None). The dirty probe
#                   is the ONE sanctioned `git` subprocess here — worktree/index
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
    """Walk up from cwd to its checkout: (gitdir, worktree_name) — gitdir the
    directory holding HEAD, worktree_name the linked-worktree name when `.git`
    is a FILE pointing into .../worktrees/<name> (a `git worktree add` /
    EnterWorktree checkout), else None. None when cwd is in no checkout.
    File reads only — never a `git` subprocess (this runs per row per poll)."""
    d = cwd
    while d and os.path.isdir(d):
        dotgit = os.path.join(d, ".git")
        if os.path.isdir(dotgit):
            return dotgit, None
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
            wt = os.path.basename(gd) if (os.sep + "worktrees" + os.sep) in gd \
                else None
            return gd, wt
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent
    return None


def git_info(cwd):
    """The checkout state of a session's cwd, for the git chips: {"branch",
    "worktree", "dirty"} — branch the HEAD ref's short name (a 7-char sha when
    detached), worktree the linked-worktree name or None for a main checkout,
    dirty the uncommitted-changes flag behind the branch chip's `*` (True/
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
    gitdir, wt = hit
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
    return {"branch": branch, "worktree": wt, "dirty": _git_dirty(cwd)}


_CTX = {}         # transcript_path -> (size, ctx): same (path, size) cache key
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


def sessions_payload():
    """The sessions list, enriched with what the list view shows per row:
    scoreboard stats (read-only, live or parked), the tab state, and the
    display title (plugins.session_title over the transcript). `live` is
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
        # ever HAD a window (a headless/daemon session legitimately has none).
        if (row.get("live") and live_wins is not None
                and row.get("kitty_window_id") and row["sid"] not in live_wins):
            row["live"] = False
        st = API.stats_at(sdb)
        row["stats"] = st
        row["tab"] = tabstates.get(str(row.get("kitty_window_id") or "")) or ""
        row["title"] = session_title(row.get("transcript_path") or "")
        row["ctx"] = session_ctx(row.get("transcript_path") or "", main=True)
        row["git"] = git_info(row.get("cwd") or "")
        out.append(row)
    return out


def accounts_payload():
    """The launchable accounts + their latest known usage, for the new-session
    picker AND the dashboard's top usage strip. Registry from
    plugins.accounts(); usage aggregated by scanning the recent sessions and
    keeping, per account slug, the freshest `usage` snapshot (newest `ts`).
    Per-account by construction — each snapshot came from a session running
    under that account's own token (docs/dashboard.md). No API call, no token."""
    reg = plugins.accounts()
    best = {}                                   # slug -> (ts, usage)
    for row in API.sessions(SESSIONS_LIMIT):
        sdb = P.state_db(row["log"])
        if not os.path.isfile(sdb):
            sdb = P.parked_db(row["log"])
        acc = API.kv_at(sdb, "account") or {}
        usage = API.kv_at(sdb, "usage")
        if not usage:
            continue
        slug = acc.get("slug") or ""
        ts = usage.get("ts") or 0
        if slug not in best or ts > best[slug][0]:
            best[slug] = (ts, usage)
    out = []
    for a in reg:
        u = best.get(a["slug"])
        out.append(dict(a, usage=u[1] if u else None))
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


def session_payload(sid):
    """One session's overview — session() plus the error count the ⚠ badge
    shows (full rows stay behind /errors) and the display title."""
    data = API.session(sid)
    data["agents"] = agents_ctx(visible_agents(data.get("agents") or []))
    data["error_count"] = API.error_count(sid)
    data["title"] = session_title(data.get("transcript_path") or "")
    data["ctx"] = session_ctx(data.get("transcript_path") or "", main=True)
    data["git"] = git_info(data.get("cwd") or "")
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
            and row.get("kitty_window_id") and sid not in live_wins):
        data["live"] = False
    data["kitty_window_id"] = (live_wins or {}).get(sid, "") if data.get("live") else ""
    data["ask"] = _ask_pending(sid) if data.get("live") else None
    data["plan"] = _plan_pending(sid) if data.get("live") else None
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
    (prompt|message|teammsg) and, for prompts, the raw `text`: the page's
    queued-message chips match a DELIVERED prompt against what they sent —
    the transcript's prompt record is the one true delivery signal (tab
    transitions are useless: green flips busy again the instant a queued
    prompt starts processing)."""
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
    op_items) and count as nothing. Approximate by design (the window size is a
    soft limit) — cursor correctness rides slot ids, not this count."""
    seen, count = set(), 0
    for i in range(len(entries) - 1, -1, -1):
        _slot, kind, obj = entries[i]
        if kind == "op":
            if obj.get("t") in ("rule", "blank"):
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
_LIVE_TTL = 0.8                          # bound kitten-ls calls under the 1s tick


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


def _live_window(sid):
    """The kitty window CURRENTLY tagged claude_session=<sid>, or '' — the ONLY
    trustworthy handle for the control-plane display gate. See _live_windows."""
    return (_live_windows() or {}).get(sid, "")


LAUNCH_SHELLS = ("zsh", "bash")    # login shells the "$@" wrapper is valid for
EFFORTS = ("low", "medium", "high", "xhigh", "max")   # claude --effort levels
_MODEL_OK = re.compile(r"^[A-Za-z0-9._-]+$")   # an alias or full model id — one
                                               # clean argv word, nothing else


def launch_argv(words, cmd="claude"):
    """The argv a web new-session launches: `cmd` (the account's launch word —
    `claude` for the default, or a switcher alias like `c1`/`c2`) through the
    user's INTERACTIVE LOGIN shell. kitty execs launch argv with kitty's OWN
    env — a GUI kitty has no user PATH (so a bare ["claude"] dies
    command-not-found and the tab closes instantly, while `kitten @ launch`
    still exits 0) and no shell aliases. `$SHELL -lic` reproduces exactly what
    typing `cmd` in a fresh tab does: profile PATH, rc aliases (c1/c2 ARE zsh
    aliases). `cmd` is placed in the FIXED command string, so it MUST be a
    registry-vetted bareword (plugins.account_alias) — never raw client text;
    the prompt/flags ride as positional args via "$@" (after the $0
    placeholder), never interpolated."""
    sh = os.environ.get("SHELL") or "/bin/zsh"
    if os.path.basename(sh) not in LAUNCH_SHELLS:
        sh = "/bin/zsh"
    return [sh, "-lic", '%s "$@"' % cmd, cmd, *words]


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
            return self._json(sessions_payload())
        if api == ["accounts"]:
            return self._json(accounts_payload())
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
            if rest == ["activity"]:
                return self._json(_mdify(plugins.activity(sid)) or {"entries": []})
            if len(rest) == 2 and rest[0] == "agent":
                tl = _mdify(plugins.activity(sid, rest[1]))
                return self._json(tl if tl is not None else {"entries": []})
            if rest == ["errors"]:
                return self._json(API.errors(sid))
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
                and api[2] == "stop":
            return self.post_stop(api[1])
        if len(api) == 3 and api[0] == "session" and _sid(api[1]) \
                and api[2] == "interrupt":
            return self.post_interrupt(api[1])
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
                and api[2] == "plan-options":
            return self.post_plan_options(api[1])
        if len(api) == 3 and api[0] == "session" and _sid(api[1]) \
                and api[2] == "plan-decision":
            return self.post_plan_decision(api[1])
        if api == ["sessions", "new"]:
            return self.post_new_session()
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
        # land in an unrelated tab — see _live_window). '' ⇒ nothing to message.
        win = fe.window_for_session(sid) or ""
        if not win:
            A.state_file(log, sdb, "web-send",
                         {"win": "", "chars": len(text), "ok": False})
            return self._json({"error": "session has no live window"}, 409)
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
        return self._json({"ok": True, "chat": chat})

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

    def _escape_press(self, sid, verb, action):
        """Body of post_interrupt: guard, resolve the LIVE window, press
        Escape, audit as `action`, and spawn the escape-recheck when the
        press landed on magenta."""
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
        # would interrupt an unrelated session — see _live_window).
        win = fe.window_for_session(sid) or ""
        if not win:
            A.state_file(log, sdb, action, {"win": "", "ok": False})
            return self._json({"error": "session has no live window"}, 409)
        tab = API.tab_states().get(win) or ""
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

    def post_new_session(self):
        """Launch a new session in a new tab (Frontend.launch_tab). 400 when the
        cwd isn't an existing directory or model/effort/resume/continue don't
        validate (model: one clean argv word; effort: the CLI's EFFORTS levels;
        resume: a clean session id, exclusive with continue); 503 when no
        terminal resolves; else the launch, with `--resume <sid>`/`--continue`
        and `--model`/`--effort` riding as positional "$@" words ahead of the
        prompt. Audited as a `web-launch` state_files row (no session db
        exists yet, so its log/path are empty)."""
        body = self._post_guard()
        if body is None:
            return
        cwd = body.get("cwd")
        if not isinstance(cwd, str) or not cwd or not os.path.isdir(cwd):
            # repr(): a validation reject must leave the EXACT received bytes
            # in the audit (invisible characters included) — a remote client's
            # "but I picked it from the dropdown" is undebuggable otherwise
            A.error("", "dashboard new-session (bad cwd)", {"cwd": repr(cwd)})
            return self._json({"error": "cwd is not an existing directory"}, 400)
        model, effort = body.get("model"), body.get("effort")
        if model is not None and (
                not isinstance(model, str) or not _MODEL_OK.match(model)):
            A.error("", "dashboard new-session (bad model)",
                    {"model": repr(model)})
            return self._json({"error": "invalid model"}, 400)
        if effort is not None and effort not in EFFORTS:
            A.error("", "dashboard new-session (bad effort)",
                    {"effort": repr(effort)})
            return self._json({"error": "invalid effort"}, 400)
        # resume / continue — the CLI's own conversation-pickup flags. resume
        # carries a session id (one clean argv word, same alphabet as our sid
        # routing); continue is a bare flag. Mutually exclusive, like the CLI.
        # A resumed conversation FORKS to a new sid; the existing adopt
        # machinery and the page's jump watch both handle that on their own.
        resume, cont = body.get("resume"), body.get("continue")
        if resume is not None and (
                not isinstance(resume, str) or not _SID_OK.match(resume)):
            A.error("", "dashboard new-session (bad resume)",
                    {"resume": repr(resume)})
            return self._json({"error": "invalid resume id"}, 400)
        if cont not in (None, False, True):
            A.error("", "dashboard new-session (bad continue)",
                    {"continue": repr(cont)})
            return self._json({"error": "invalid continue"}, 400)
        if resume and cont:
            A.error("", "dashboard new-session (resume+continue)",
                    {"resume": repr(resume)})
            return self._json({"error": "resume and continue are exclusive"}, 400)
        # account: the switcher slug to launch under (default `claude` when
        # absent). Resolved to a registry-vetted command word — never the raw
        # value flows into the launch shell string.
        acct = body.get("account")
        cmd = plugins.account_alias(acct) if acct else "claude"
        if cmd is None:
            A.error("", "dashboard new-session (bad account)",
                    {"account": repr(acct)})
            return self._json({"error": "unknown account"}, 400)
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
        # the passive steal watch (see the block above the Handler class):
        # the frontmost app must be captured BEFORE the launch — a steal can
        # land before the kitten call returns. Skipped when the terminal was
        # ALREADY frontmost at click time (nothing to steal) or the frontend
        # has no OS app identity (the inert stub, off-mac).
        term = fe.app_id()
        before = _front_app() if term else ""
        ok = bool(fe.launch_tab(cwd, argv))
        A.state_file("", "", "web-launch", dict(opts, ok=ok))
        if not ok:
            A.error("", "dashboard new-session (launch failed)", {"cwd": cwd})
            return self._json({"error": "launch failed"}, 502)
        if before and before != term:
            threading.Thread(target=_steal_watch, args=(before, term),
                             daemon=True, name="web-launch-steal-watch").start()
        return self._json({"ok": True})

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
        `sessions` snapshot whenever the list changes, plus every `notify`
        toast the watcher pushes."""
        self._sse_start()
        q = NOTIFIER.register()
        try:
            if not self._sse("hello", {"boot": BOOT_ID}):
                return
            prev, beat = None, time.monotonic()
            snap = sessions_payload()
            if not self._sse("sessions", snap):
                return
            prev = json.dumps(snap, default=str)
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
                snap = sessions_payload()
                enc = json.dumps(snap, default=str)
                if enc != prev:
                    if not self._sse("sessions", snap):
                        return
                    prev = enc
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
                "ctx": None, "git": None}
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
                agents = agents_ctx(visible_agents(API.agents(sid)))
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
                # the header's git chip, live — a checkout/branch switch (or a
                # removed worktree) shows on the slow cadence
                git = git_info(row.get("cwd") or "")
                if git != prev["git"]:
                    prev["git"] = git
                    if not self._sse("git", {"git": git}):
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
