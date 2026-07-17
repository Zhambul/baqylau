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
from core import tabs
from core.noaudit import load_audit
from core.tail import stream_lifecycle
from dashboard import opshtml

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
ALLOWED_ORIGINS = {"http://%s:%d" % (HOST, PORT), "http://localhost:%d" % PORT}

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
STATIC = {                         # whitelist — no path resolution on user input
    "index.html": "text/html; charset=utf-8",
    "app.js": "text/javascript; charset=utf-8",
    "style.css": "text/css; charset=utf-8",
}

# The two tab transitions worth a toast (core/tabs.py vocabulary): red — Claude
# is asking you; green — done, your turn.
NOTIFY_STATES = {tabs.AWAITING_COMMAND: "asking", tabs.AWAITING_RESPONSE: "done"}

_SID_OK = re.compile(r"^[A-Za-z0-9._-]+$")     # a mirror-log key, post-sanitize


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


def sessions_payload():
    """The sessions list, enriched with what the list view shows per row:
    scoreboard stats (read-only, live or parked), the tab state, and the
    display title (plugins.session_title over the transcript)."""
    tabstates = API.tab_states()
    out = []
    for row in API.sessions(SESSIONS_LIMIT):
        sdb = P.state_db(row["log"])
        if not os.path.isfile(sdb):
            sdb = P.parked_db(row["log"])
        st = API.stats_at(sdb)
        row["stats"] = st
        row["tab"] = tabstates.get(str(row.get("kitty_window_id") or "")) or ""
        row["title"] = session_title(row.get("transcript_path") or "")
        out.append(row)
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


def session_payload(sid):
    """One session's overview — session() plus the error count the ⚠ badge
    shows (full rows stay behind /errors) and the display title."""
    data = API.session(sid)
    data["agents"] = visible_agents(data.get("agents") or [])
    data["error_count"] = API.error_count(sid)
    data["title"] = session_title(data.get("transcript_path") or "")
    data["running"] = API.running(sid)
    # The control-plane composer gates on a reachable window: the mirror is
    # keyed to a kitty window, and a headless/daemon session has none (the
    # /message endpoint would 409). Additive field; app.js disables the box.
    row = API.session_row(sid) or {}
    data["kitty_window_id"] = str(row.get("kitty_window_id") or "")
    return data


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
    return [{"g": None, "t": "msg",
             "html": opshtml.msg_html(r["kind"], r.get("text", ""),
                                      r.get("sender", ""))}
            for r in recs]


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
    # The dashboard is READ-ONLY except these two writes, and they TYPE INTO a
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
        None (the caller just returns). Order: content type, custom header,
        Origin, size cap, then the JSON parse."""
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
        `web-send` state_files row, failures also an A.error."""
        body = self._post_guard()
        if body is None:
            return
        text = body.get("text")
        if not isinstance(text, str) or not text.strip():
            return self._json({"error": "empty text"}, 400)
        row = API.session_row(sid) or {}
        log = row.get("log") or P.mirror_log(sid)
        sdb = API.state_db_for(sid) or P.state_db(log)
        win = str(row.get("kitty_window_id") or "")
        if not win:
            A.state_file(log, sdb, "web-send",
                         {"win": "", "chars": len(text), "ok": False})
            return self._json({"error": "session has no window (headless)"}, 409)
        fe = _frontend()
        if fe is None:
            A.error(log, "dashboard message (no terminal)", {"sid": sid})
            A.state_file(log, sdb, "web-send",
                         {"win": win, "chars": len(text), "ok": False})
            return self._json({"error": "no terminal available"}, 503)
        ok = bool(fe.send_text(win, text))
        A.state_file(log, sdb, "web-send",
                     {"win": win, "chars": len(text), "ok": ok})
        if not ok:
            A.error(log, "dashboard message (send failed)",
                    {"sid": sid, "win": win})
            return self._json({"error": "send failed"}, 502)
        return self._json({"ok": True})

    def post_new_session(self):
        """Launch a new session in a new tab (Frontend.launch_tab). 400 when the
        cwd isn't an existing directory; 503 when no terminal resolves; else the
        launch. Audited as a `web-launch` state_files row (no session db exists
        yet, so its log/path are empty)."""
        body = self._post_guard()
        if body is None:
            return
        cwd = body.get("cwd")
        if not isinstance(cwd, str) or not cwd or not os.path.isdir(cwd):
            return self._json({"error": "cwd is not an existing directory"}, 400)
        prompt = body.get("prompt")
        argv = ["claude"] + ([prompt]
                             if isinstance(prompt, str) and prompt.strip() else [])
        fe = _frontend()
        if fe is None:
            A.error("", "dashboard new-session (no terminal)", {"cwd": cwd})
            A.state_file("", "", "web-launch", {"cwd": cwd, "ok": False})
            return self._json({"error": "no terminal available"}, 503)
        ok = bool(fe.launch_tab(cwd, argv))
        A.state_file("", "", "web-launch", {"cwd": cwd, "ok": ok})
        if not ok:
            A.error("", "dashboard new-session (launch failed)", {"cwd": cwd})
            return self._json({"error": "launch failed"}, 502)
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
        """The all-sessions stream: a `sessions` snapshot whenever the list
        changes, plus every `notify` toast the watcher pushes."""
        self._sse_start()
        q = NOTIFIER.register()
        try:
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
                "running": None, "errors": None}
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
                agents = visible_agents(API.agents(sid))
                if agents != prev["agents"]:
                    prev["agents"] = agents
                    if not self._sse("agents", agents):
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
