# plugins/otel/receiver.py — the global (per-machine) OTLP metrics receiver.
# Entry point: claude-otlp-receiver.py (a thin shim — the entry FILENAME is the
# audit's script/stream vocabulary).
#
# WHY this exists. The scoreboard's token/cost totals used to be reconstructed by
# folding the session TRANSCRIPT, which structurally cannot see Claude Code's
# hidden "auxiliary" agents (summarizers / title generators): they fire only a
# SubagentStop hook with no usage in the payload and write no transcript, yet
# their billed cache-read spend reaches `/cost`. Claude Code DOES emit that spend
# via OpenTelemetry — `claude_code.token.usage` / `claude_code.cost.usage`, per
# API request, tagged with `session.id` and `query_source` (main/subagent/
# auxiliary). This receiver ingests those metrics and writes the SAME per-session
# counters the transcript fold used to (tk_in/tk_out/tk_read/tk_create/cost/
# tokens), so the scorebar display is unchanged but now includes hidden agents.
#
# SINGLETON. The OTLP endpoint is a process-global env var, so ONE receiver serves
# every session on the machine. Dual guard: a global pid-lock (core.paths.OTLP_DB)
# AND the port bind (EADDRINUSE = a peer already owns it). A duplicate writes a
# closed `streams` row (kind='otlp', end_reason='duplicate …') and exits 0 — the
# same clean-audit shape codex's watcher uses, so no "stream never ended" anomaly.
#
# DELTA TEMPORALITY. Claude Code exports delta datapoints (verified: per-session
# values are non-monotonic), so the receiver SUMS them via counter_add. The
# settings env pins OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=delta so this
# never depends on the exporter default.
import gzip
import json
import os
import socketserver
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from core import paths as P
from core import locks as LK
from core import state as S
from core import tail as T
from core.noaudit import load_audit
# The listen port is single-sited in plugins/otel/config.py — launch.py's
# already-listening pre-check resolves the SAME function, so probe and bind
# can never drift.
from plugins.otel.config import port as _port

A = load_audit()   # audit trail (real module, or an inert stub if it can't import)

# token.usage `type` attribute -> the scoreboard's per-category counter key.
_TYPE_KEY = {
    "input": "tk_in",
    "output": "tk_out",
    "cacheRead": "tk_read",
    "cacheCreation": "tk_create",
}
# The billed `tokens` counter (backs nothing on display now, but kept consistent
# with the transcript-fold invariant tk_in+tk_out+tk_create == tokens for audit).
_BILLED_TYPES = ("input", "output", "cacheCreation")

# A synthetic log key so the receiver's own audit rows (streams/spawns/errors) are
# attributable without colliding with any real session id.
SELF_LOG = P.mirror_log("otlp-receiver")


def _idle_s():
    # Exit after this long with no metrics — a new SessionStart respawns us. Kept
    # short in tests via CLAUDE_OTEL_GRACE_S.
    v = os.environ.get("CLAUDE_OTEL_GRACE_S") or "900"
    try:
        return float(v)
    except (TypeError, ValueError):
        return 900.0


# --- OTLP/JSON decode --------------------------------------------------------

def _attr(dp):
    out = {}
    for kv in dp.get("attributes", []):
        v = kv.get("value", {})
        out[kv.get("key")] = (v.get("stringValue")
                              if "stringValue" in v
                              else v.get("intValue", v.get("doubleValue")))
    return out


def datapoints(body):
    """Yield (metric, attrs, value) for every claude_code token/cost datapoint in
    one OTLP/JSON ExportMetricsServiceRequest body."""
    for rm in body.get("resourceMetrics", []):
        for sm in rm.get("scopeMetrics", []):
            for m in sm.get("metrics", []):
                name = m.get("name", "")
                if "token.usage" not in name and "cost.usage" not in name:
                    continue
                for dp in m.get("sum", {}).get("dataPoints", []):
                    val = dp.get("asDouble")
                    if val is None:
                        val = dp.get("asInt")
                    if val is None:
                        continue
                    yield name, _attr(dp), float(val)


def aggregate(body):
    """Fold one POST body into per-session {deltas, rows}, keyed by session.id.
    `deltas` are the summed counter increments to apply; `rows` are the RAW
    datapoints (verbatim, for the audit `otel` table — every OTEL input captured)."""
    per = {}
    for name, a, val in datapoints(body):
        sid = a.get("session.id")
        if not sid:
            continue
        e = per.setdefault(str(sid), {"deltas": {}, "rows": []})
        d = e["deltas"]
        qs = a.get("query_source") or "?"
        metric = "cost" if "cost.usage" in name else "token"
        typ = a.get("type") or ""
        e["rows"].append({"metric": metric, "query_source": qs,
                          "model": a.get("model") or "", "type": typ, "value": val})
        if metric == "cost":
            d["cost"] = d.get("cost", 0.0) + val
            k = "otel_cost:" + qs
            d[k] = d.get(k, 0.0) + val
        else:                                   # token.usage
            key = _TYPE_KEY.get(typ)
            if key:
                d[key] = d.get(key, 0.0) + val
            if typ in _BILLED_TYPES:
                d["tokens"] = d.get("tokens", 0.0) + val
    return per


# --- state-DB write ----------------------------------------------------------

def write_session(sid, entry):
    """Apply one session's summed deltas to its state DB and capture the raw OTLP
    datapoints in the audit. Skips a session whose DB doesn't exist (unknown/parked)
    — audit-drop, never create. Returns True on a real write (idle/lines tracking)."""
    deltas, rows = entry["deltas"], entry["rows"]
    log = P.mirror_log(sid)
    db = S.db_path(log)
    if not os.path.exists(db):
        return False
    conn = S.connect(log)
    if conn is None:
        return False
    try:
        with S.immediate(conn):
            for k, v in deltas.items():
                if v:
                    S.counter_add(conn, k, v)
            S.counter_add(conn, "otel_seen", 1)   # fallback sentinel (stop_fmt)
            S.counter_add(conn, "v", 1)            # scorebar repaint signal
            tot_cost = S.counter_get(conn, "cost")
            tot_tok = S.counter_get(conn, "tokens")
    except Exception:
        A.error(log, "otel write_session", {"sid": sid, "deltas": deltas})
        return False
    # RAW datapoints — every OTEL input captured verbatim, so the counters are
    # reconstructible (SUM(otel.value) GROUP BY type == the counter).
    A.otel(sid, rows)
    # Aggregated trail parallel to bump-agent: the delta + resulting totals, so a
    # wrong scoreboard number is traceable from the DB + the named OTEL source.
    A.state_file(log, db, "bump-otel", json.dumps(
        {"deltas": deltas, "now": {"tokens": tot_tok, "cost": tot_cost}},
        ensure_ascii=False))
    return True


# --- HTTP server -------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _read_body(self):
        if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
            raw = b""
            while True:
                line = self.rfile.readline().strip()
                if not line:
                    break
                sz = int(line.split(b";")[0], 16)
                if sz == 0:
                    self.rfile.readline()
                    break
                raw += self.rfile.read(sz)
                self.rfile.readline()
        else:
            n = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(n)
        if self.headers.get("Content-Encoding") == "gzip":
            try:
                raw = gzip.decompress(raw)
            except Exception:
                # Audit-then-degrade, matching do_POST's malformed-JSON
                # convention (swallow, still answer 200 — an OTLP exporter
                # retries on error responses, and bytes that failed to gunzip
                # once will fail forever, so an error status only earns a
                # resend loop). Degrade to an EMPTY body rather than falling
                # through: json.loads on the still-compressed bytes would fail
                # too, masking the real cause as a generic do_POST JSON error.
                A.error(SELF_LOG, "otel gzip decompress",
                        {"content_encoding": self.headers.get("Content-Encoding"),
                         "bytes": len(raw)})
                raw = b""
        return raw

    def do_POST(self):
        wrote = 0
        try:
            body = json.loads(self._read_body() or b"{}")
            for sid, entry in aggregate(body).items():
                if write_session(sid, entry):
                    wrote += 1
        except Exception:
            A.error(SELF_LOG, "otel do_POST")
        if wrote:
            self.server.mark(wrote)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")


class _Server(HTTPServer):
    # Single-threaded ON PURPOSE: every request is handled in the serve-loop thread,
    # the SAME thread that created the audit + state-DB connections (sqlite objects
    # are thread-affine — a ThreadingHTTPServer would make handler threads write on
    # the main thread's connection, tripping check_same_thread and silently spooling
    # every audit row). OTLP exports arrive every couple seconds, so one thread is
    # ample; a slow write only delays the next export's ingest, never the session.
    def server_bind(self):
        # Skip HTTPServer.server_bind's socket.getfqdn(host) reverse-DNS lookup. It
        # runs BETWEEN bind and listen, and on a host with slow/absent reverse DNS
        # (observed: macOS CI runners) it blocks for seconds-to-minutes — leaving
        # the socket bound but NOT yet listening (server_activate never runs), which
        # looks exactly like "the receiver never came up". We never use server_name.
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port

    def __init__(self, addr, run):
        super().__init__(addr, _Handler)
        self.run = run                  # the stream_lifecycle handle (for .lines)
        self.last = time.time()
        self.written = 0

    def mark(self, n):
        self.last = time.time()
        self.written += n
        self.run.lines = self.written


# --- lifecycle ---------------------------------------------------------------

def _own_lock():
    """True while the global singleton lock is still held by THIS pid."""
    return LK.lock_holder(P.OTLP_DB, "otlp-receiver") == os.getpid()


def serve():
    port = _port()
    # Guard 1: the global pid-lock.
    got = LK.lock_acquire(P.OTLP_DB, "otlp-receiver")
    if got.startswith("claim-denied"):
        A.event("streams", session_id="otlp-receiver", kind="otlp",
                pid=os.getpid(), started_at=time.time(), ended_at=time.time(),
                end_reason="duplicate (%s)" % got)
        return
    # Guard 2: the port bind (a peer that holds neither cleanly loses here).
    try:
        srv = _Server(("127.0.0.1", port), None)
    except OSError:
        LK.lock_release(P.OTLP_DB, "otlp-receiver")
        A.event("streams", session_id="otlp-receiver", kind="otlp",
                pid=os.getpid(), started_at=time.time(), ended_at=time.time(),
                end_reason="duplicate (port %d in use)" % port)
        return

    def release():
        try:
            srv.server_close()
        except Exception:
            pass
        LK.lock_release(P.OTLP_DB, "otlp-receiver")

    idle = _idle_s()
    with T.stream_lifecycle(SELF_LOG, "otlp", src_path="127.0.0.1:%d" % port,
                            ctx={"port": port}, on_exit=release) as run:
        srv.run = run
        srv.timeout = min(30.0, idle)
        while True:
            srv.handle_request()            # blocks up to srv.timeout
            if not _own_lock():
                run.end("lock-stolen")
                break
            if time.time() - srv.last > idle:
                run.end("idle-timeout")
                break


def entry():
    try:
        serve()
    except Exception:
        try:
            A.error(SELF_LOG, "otel serve")
        except Exception:
            pass
    sys.exit(0)
