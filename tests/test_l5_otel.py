# L5 — the OpenTelemetry cost pipeline (plugins/otel/): the per-machine OTLP
# receiver that folds claude_code.token.usage / cost.usage into the per-session
# scoreboard counters, INCLUDING the hidden `auxiliary` agents transcript folding
# could never see. Drives the real receiver entry (claude-otlp-receiver.py) as a
# detached subprocess under the hermetic test env, exactly as the launcher would.
import json
import os
import socket
import sqlite3
import subprocess
import sys
import urllib.request

import oracle
import payloads as P
from conftest import wait_until

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECEIVER = os.path.join(REPO, "bin", "claude-otlp-receiver.py")


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# Grace = the receiver's no-metrics idle-exit. Kept generously LONG (every test
# kills its receiver in a finally, so it never needs to self-exit) — a short grace
# raced the test driver on a slow/loaded CI runner: the receiver idle-exited before
# the driver connected + POSTed, surfacing as a spurious "receiver listening"
# timeout. The singleton test needs no shorter grace either (it terminates r1).
def _spawn_receiver(env, port, grace="30"):
    e = dict(env)
    e["CLAUDE_OTEL_PORT"] = str(port)
    e["CLAUDE_OTEL_GRACE_S"] = grace
    return subprocess.Popen([sys.executable, RECEIVER], env=e,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def _wait_listening(port, recv=None, timeout=15):
    def up():
        if recv is not None and recv.poll() is not None:   # crashed before binding
            err = ""
            try:
                err = (recv.stderr.read() or b"").decode("utf-8", "replace")[-2000:]
            except Exception:
                pass
            raise AssertionError("receiver exited early rc=%s:\n%s"
                                 % (recv.returncode, err))
        c = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        c.settimeout(0.2)
        try:
            return c.connect_ex(("127.0.0.1", port)) == 0
        finally:
            c.close()
    wait_until(up, timeout=timeout, desc="receiver listening")


def _otel_rows(env, sid):
    """The raw audit `otel` datapoints for a session: (metric, query_source, type, value)."""
    return oracle.q(env, "SELECT metric, query_source, type, value FROM otel "
                         "WHERE session_id=? ORDER BY id", (sid,))


def _post(port, body):
    req = urllib.request.Request("http://127.0.0.1:%d/v1/metrics" % port,
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=3).read()


def _materialize_db(run_hook, s):
    """Create the session state DB the receiver writes into (a Bash post hook is
    the cheapest thing that connects + creates it)."""
    run_hook("claude-cmd-fmt.py", P.post_bash(s, "echo x", stdout="x\n"))


# --------------------------------------------------------- ingest + attribution

def test_receiver_folds_tokens_cost_including_auxiliary(run_hook, test_env, session):
    s = session.make()
    _materialize_db(run_hook, s)
    port = _free_port()
    recv = _spawn_receiver(test_env, port)
    try:
        _wait_listening(port, recv)
        _post(port, P.otlp_metrics(
            s.sid,
            tokens=[("main", "input", 100), ("main", "output", 50),
                    ("main", "cacheRead", 9000),
                    ("auxiliary", "cacheRead", 77000), ("auxiliary", "output", 20)],
            costs=[("main", 0.05), ("auxiliary", 0.11)]))
        # Wait for the AUDIT row (written after the counter) so terminate can't race
        # a half-written POST.
        wait_until(lambda: [r for r in oracle.state_files(test_env, s.sid)
                            if r[1] == "bump-otel"], desc="bump-otel audit row")
    finally:
        recv.terminate()
        recv.wait(timeout=5)
    c = s.counters()
    assert c["tk_in"] == 100 and c["tk_out"] == 70          # 50 main + 20 aux
    assert c["tk_read"] == 86000                            # 9000 + 77000 (incl aux!)
    assert round(c["cost"], 4) == 0.16                      # 0.05 + 0.11
    assert c["tokens"] == 170                               # billed: in+out+create
    assert c["otel_seen"] >= 1
    # the hidden-agent share is attributed and auditable
    assert round(c["otel_cost:auxiliary"], 4) == 0.11
    # every RAW datapoint is captured in the audit `otel` table (reconstructible)
    raw = _otel_rows(test_env, s.sid)
    assert len(raw) == 7, "expected 7 raw datapoints, got %d" % len(raw)
    aux_read = [v for (m, qs, t, v) in raw
                if m == "token" and qs == "auxiliary" and t == "cacheRead"]
    assert aux_read == [77000], aux_read


def test_receiver_sums_deltas_across_batches(run_hook, test_env, session):
    s = session.make()
    _materialize_db(run_hook, s)
    port = _free_port()
    recv = _spawn_receiver(test_env, port)
    try:
        _wait_listening(port, recv)
        _post(port, P.otlp_metrics(s.sid, costs=[("main", 0.05)]))
        wait_until(lambda: s.counters().get("cost"), desc="batch 1")
        _post(port, P.otlp_metrics(s.sid, costs=[("main", 0.07)]))
        wait_until(lambda: round(s.counters().get("cost", 0), 4) == 0.12,
                   desc="delta-summed batch 2")
    finally:
        recv.terminate()
        recv.wait(timeout=5)


# ------------------------------------------------------------------- lifecycle

def test_receiver_registers_stream_and_singleton(run_hook, test_env, session):
    session.make()
    port = _free_port()
    r1 = _spawn_receiver(test_env, port)
    try:
        _wait_listening(port, r1)
        # A second receiver on the same port loses the dual guard and exits fast
        # with a clean duplicate streams row (no lingering process).
        r2 = _spawn_receiver(test_env, port)
        r2.wait(timeout=5)
        assert r2.returncode == 0
    finally:
        r1.terminate()
        r1.wait(timeout=5)
    # streams() -> (kind, end_reason, ended_at, src_path)
    rows = oracle.streams(test_env, "otlp-receiver")
    assert rows and all(r[0] == "otlp" for r in rows), "no otlp streams row"
    assert any("duplicate" in (r[1] or "") for r in rows), \
        "the second receiver did not record a duplicate streams row"


# ------------------------------------------------- parked-session stragglers

def test_parked_straggler_dropped_and_conn_evicted(run_hook, test_env, session):
    """A datapoint arriving AFTER a session parked (SessionEnd) must not
    recreate the state DB (its absence is the session-alive signal watchers
    poll) and must release the receiver's cached connection (otherwise every
    ended session pins a conn + WAL/SHM fds until the receiver's idle exit).
    The dropped deltas stay auditable; a LIVE session keeps folding."""
    s, s2 = session.make(), session.make()
    _materialize_db(run_hook, s)
    _materialize_db(run_hook, s2)
    port = _free_port()
    recv = _spawn_receiver(test_env, port)
    try:
        _wait_listening(port, recv)
        _post(port, P.otlp_metrics(s.sid, costs=[("main", 0.05)]))  # caches s's conn
        wait_until(lambda: s.counters().get("cost"), desc="pre-park fold")
        run_hook("claude-split.py", P.session_end(s), argv=("close",))  # parks it
        assert os.path.exists(s.parked_db) and not os.path.exists(s.state_db)
        # a straggler for the parked session + a normal export for the live one
        _post(port, P.otlp_metrics(s.sid, costs=[("main", 0.99)]))
        _post(port, P.otlp_metrics(s2.sid, costs=[("main", 0.07)]))
        wait_until(lambda: s2.counters().get("cost"),
                   desc="live session still folds after the straggler")
        wait_until(lambda: [r for r in oracle.state_files(test_env, s.sid)
                            if r[1] == "drop-otel-parked"],
                   desc="drop-otel-parked audit row")
    finally:
        recv.terminate()
        recv.wait(timeout=5)
    # the straggler did NOT recreate the live DB (whose existence = session alive)
    assert not os.path.exists(s.state_db), "straggler datapoint recreated the state DB"
    # the cached conn was evicted (the sweep's audit row is the observable)
    assert [r for r in oracle.state_files(test_env, s.sid)
            if r[1] == "evict-parked"], "no evict-parked row — cached conn pinned"
    # the dropped deltas rode the drop row (audited, not silent)
    drop = [r for r in oracle.state_files(test_env, s.sid)
            if r[1] == "drop-otel-parked"]
    assert "0.99" in (drop[0][2] or ""), drop
    # …and never reached the parked snapshot (only the pre-park value is there)
    conn = sqlite3.connect("file:%s?mode=ro" % s.parked_db, uri=True, timeout=5)
    try:
        cost = conn.execute("SELECT val FROM counters WHERE key='cost'").fetchone()[0]
    finally:
        conn.close()
    assert round(cost, 4) == 0.05, cost
    # the live session's fold was unaffected
    assert round(s2.counters()["cost"], 4) == 0.07


# --------------------------------------------------- SessionEnd fallback gating

def test_sessionend_fallback_skipped_when_otel_present(run_hook, test_env, session):
    s = session.make()
    s.add_assistant("m1", usage={"input_tokens": 100, "output_tokens": 50,
                                 "cache_creation_input_tokens": 0,
                                 "cache_read_input_tokens": 0})
    _materialize_db(run_hook, s)
    port = _free_port()
    recv = _spawn_receiver(test_env, port)
    try:
        _wait_listening(port, recv)
        _post(port, P.otlp_metrics(s.sid, costs=[("main", 0.09)]))
        wait_until(lambda: s.counters().get("otel_seen"), desc="otel_seen set")
    finally:
        recv.terminate()
        recv.wait(timeout=5)
    before = s.counters().get("tokens", 0)
    run_hook("claude-stop-fmt.py", P.session_end(s))
    # otel_seen>0 -> the transcript fallback is skipped, so `tokens` (which the OTEL
    # cost datapoint above did NOT set) stays put; no double-count.
    assert s.counters().get("tokens", 0) == before
    dec = oracle.decisions(test_env, s.sid, "claude-stop-fmt.py")
    assert any("fold skipped" in d for d in dec), dec


# ------------------------------------------------------------- malformed input

def test_gzip_decompress_failure_audited_not_fatal(run_hook, test_env, session):
    """A body claiming Content-Encoding: gzip that won't gunzip is AUDITED (an
    errors row carrying the encoding header + byte count) and degraded to an
    empty batch — the receiver still answers 200 (an OTLP exporter retries on
    error responses, and the same bytes would fail forever) and keeps
    ingesting later exports."""
    s = session.make()
    _materialize_db(run_hook, s)
    port = _free_port()
    recv = _spawn_receiver(test_env, port)
    try:
        _wait_listening(port, recv)
        req = urllib.request.Request(
            "http://127.0.0.1:%d/v1/metrics" % port, data=b"\x1f\x8bnot-gzip",
            headers={"Content-Type": "application/json",
                     "Content-Encoding": "gzip"})
        urllib.request.urlopen(req, timeout=3).read()      # still HTTP 200
        wait_until(lambda: [r for r in oracle.errors(test_env)
                            if r[2] == "otel gzip decompress"],
                   desc="gzip-failure errors row")
        # the receiver survived and a later valid export still folds
        _post(port, P.otlp_metrics(s.sid, costs=[("main", 0.05)]))
        wait_until(lambda: s.counters().get("cost"),
                   desc="post-failure export still ingested")
    finally:
        recv.terminate()
        recv.wait(timeout=5)
    rows = [r for r in oracle.errors(test_env)
            if r[2] == "otel gzip decompress"]
    assert len(rows) == 1, rows
    ctx = rows[0][3] or ""
    assert '"content_encoding": "gzip"' in ctx and '"bytes":' in ctx, ctx


# ------------------------------------------------ connect-failure drop audited

def test_noconn_drop_is_audited(tmp_path, monkeypatch):
    """A connect failure PAST the parked check (locked/perms/corrupt DB) used
    to `return False` with no row at all — and since the dropped deltas never
    reached the `otel` table, the SUM(otel)==counters invariant still held, so
    no anomaly could ever see it. It must now leave a `drop-otel-noconn`
    state_files row carrying the deltas + raw datapoints, mirroring the
    parked-straggler drop (in-process: write_session with S.connect forced to
    None — a subprocess can't force that branch deterministically)."""
    from core import state as S
    from plugins.otel import receiver as R
    monkeypatch.setenv("CLAUDE_AUDIT", "1")
    monkeypatch.setenv("CLAUDE_AUDIT_DIR", str(tmp_path / "audit"))
    sid = "otel-noconn-unit"
    log = str(tmp_path / ("claude-mirror-" + sid + ".log"))
    monkeypatch.setattr(R.P, "mirror_log", lambda s: log)
    open(S.db_path(log), "w").close()               # DB exists -> not parked
    monkeypatch.setattr(R.S, "connect", lambda _log: None)
    entry = {"deltas": {"cost": 0.42, "tokens": 7},
             "rows": [("cost", "main", 0.42)]}
    assert R.write_session(sid, entry) is False
    db = str(tmp_path / "audit" / "audit.db")
    conn = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=5)
    try:
        got = conn.execute("SELECT action, content FROM state_files "
                           "WHERE session_id=?", (sid,)).fetchall()
        otel_rows = conn.execute("SELECT COUNT(*) FROM otel "
                                 "WHERE session_id=?", (sid,)).fetchone()[0]
    finally:
        conn.close()
    assert [a for a, _ in got] == ["drop-otel-noconn"], got
    payload = json.loads(got[0][1])
    assert payload["deltas"] == {"cost": 0.42, "tokens": 7}
    assert payload["rows"], payload
    # the dropped datapoints must NOT reach the otel table (SUM invariant)
    assert otel_rows == 0
