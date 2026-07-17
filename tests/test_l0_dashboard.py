# L0 — dashboard/: the ops→HTML presenter and the HTTP server. In-process:
# the server runs on an ephemeral 127.0.0.1 port in a thread (never through
# serve() — no singleton lock, no real port constant), session data is seeded
# through the REAL product APIs (core.ops.emit, core.state, core.audit) under
# the autouse hermetic CLAUDE_AUDIT_DIR + tmp-path mirror prefixes, exactly
# like test_l0_sessionapi.py.
import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest
from conftest import REPO

if REPO not in sys.path:
    sys.path.insert(0, REPO)

import core.audit as A
from core import ops as O
from core import paths as P
from core import state as S
from dashboard import opshtml
from dashboard import server as DS


# ------------------------------------------------------------------ opshtml

def test_ansi_html_escapes_markup_and_strips_executables():
    # The neutralize() analog: raw op bytes can carry markup AND terminal
    # control sequences — markup must escape, a DCS must vanish entirely.
    h = opshtml.ansi_html("x <script>alert(1)</script> \x1bP@kitty-cmd{}\x1b\\ y")
    assert "<script>" not in h and "&lt;script&gt;" in h
    assert "@kitty-cmd" not in h and "\x1b" not in h


def test_ansi_html_sgr_spans():
    h = opshtml.ansi_html("\x1b[38;2;10;20;30mhi\x1b[0m plain \x1b[2mdim\x1b[0m")
    assert '<span style="color:rgb(10,20,30)">hi</span>' in h
    assert "plain" in h and 'opacity:.55">dim</span>' in h


def test_ansi_html_osc8_links():
    cc = "\x1b]8;;claude-copy:///k1/g1/view\x1b\\✎ Read(f.py)\x1b]8;;\x1b\\"
    h = opshtml.ansi_html(cc)
    assert '<a class="cc" data-cc="k1/g1/view">' in h and "✎ Read(f.py)</a>" in h
    ext = "\x1b]8;;https://x.test/a\x1b\\link\x1b]8;;\x1b\\"
    h2 = opshtml.ansi_html(ext)
    assert '<a href="https://x.test/a" target="_blank" rel="noopener">link</a>' in h2


def test_label_copy_links_default_and_custom():
    d = opshtml.op_html({"t": "label", "s": "hdr", "c": [1, 2, 3], "g": "gid"}, "key")
    assert 'data-cc="key/gid/cmd">⧉cmd</a>' in d
    assert 'data-cc="key/gid/out">⧉out</a>' in d
    c = opshtml.op_html({"t": "label", "s": "hdr", "c": [1, 2, 3], "g": "gid",
                         "lk": [["all", "⧉copy"]]}, "key")
    assert 'data-cc="key/gid/all">⧉copy</a>' in c and "⧉cmd" not in c
    # without a key there is no link target — the affordance drops cleanly
    assert "data-cc" not in opshtml.op_html(
        {"t": "label", "s": "hdr", "c": [1, 2, 3], "g": "gid"})


def test_gut_lex_num_and_view_tag():
    h = opshtml.op_html({"t": "gut", "s": "x=1\ny=2", "c": [9, 9, 9],
                         "lex": "python", "num": 10, "v": "vid1"})
    assert 'data-v="vid1"' in h
    assert "   10" in h and "   11" in h        # line numbers survive stripping
    assert "border-left-color:rgb(9,9,9)" in h


def test_ops_html_skips_unknown_ops():
    assert opshtml.ops_html([{"t": "nope"}, {"t": "line", "s": "a"}, "junk"]) \
        == ['<pre class="opl">a</pre>']


def test_op_items_drop_spacing_and_carry_group():
    items = opshtml.op_items([{"t": "rule"}, {"t": "blank"},
                              {"t": "label", "s": "h", "c": [1, 2, 3], "g": "g9"},
                              {"t": "line", "s": "solo"}], "k")
    assert [(it["g"], it["t"]) for it in items] == \
        [("g9", "label"), (None, "line")]


# ------------------------------------------------------------------ the server

@pytest.fixture
def dash(monkeypatch, tmp_path):
    monkeypatch.setattr(P, "PREFIX", str(tmp_path) + "/claude-mirror-")
    monkeypatch.setattr(P, "HISTORY_DIR", str(tmp_path / "park"))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), DS.Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d" % httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def _get(url, timeout=10):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace")


def _get_json(url):
    code, body = _get(url)
    assert code == 200
    return json.loads(body)


def test_http_root_and_static_whitelist(dash):
    code, body = _get(dash + "/")
    assert code == 200 and body.lstrip().startswith("<!doctype html>")
    code, _ = _get(dash + "/static/app.js")
    assert code == 200
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(dash + "/static/secret.txt")          # not on the whitelist
    assert e.value.code == 404


def test_http_sessions_and_ops(dash):
    A.session_start({"session_id": "dash1", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("dash1")
    O.emit(log, O.label("▶ foreground", (170, 185, 210), g="g1"),
           O.code("echo hi", g="g1"), O.gut("hi", (170, 185, 210), g="g1"))
    rows = _get_json(dash + "/api/sessions")
    row = next(r for r in rows if r["sid"] == "dash1")
    assert row["live"] is True
    d = _get_json(dash + "/api/session/dash1/ops?after=0")
    assert d["last"] >= 3 and len(d["items"]) >= 3
    assert any("chip" in it["html"] for it in d["items"])
    # grouped items carry their copy-group id so the app can fold the block
    assert all(it["g"] == "g1" for it in d["items"])
    # the overview composes without error even for a minimal session
    ov = _get_json(dash + "/api/session/dash1")
    assert ov["sid"] == "dash1" and ov["live"] is True


def test_http_copy_and_view(dash):
    A.session_start({"session_id": "dash2", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("dash2")
    O.emit(log, O.label("hdr", (1, 2, 3), g="cg"), O.code("echo copyme", g="cg"),
           O.gut("outline", (1, 2, 3), g="cg"))
    S.kv_set(log, "view:vg", [{"t": "gut", "s": "stash body", "c": [1, 2, 3]}])
    code, text = _get(dash + "/api/session/dash2/copy/cg/cmd")
    assert code == 200 and "echo copyme" in text
    code, text = _get(dash + "/api/session/dash2/copy/cg/out")
    assert code == 200 and text.strip() == "outline"
    code, html = _get(dash + "/api/session/dash2/view/vg")
    assert code == 200 and "view-block" in html and "stash body" in html
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(dash + "/api/session/dash2/view/missing")
    assert e.value.code == 404


def test_http_agent_timeline(dash, tmp_path):
    tp = tmp_path / "agent-ag2.jsonl"
    tp.write_text(
        json.dumps({"type": "assistant", "message": {
            "id": "m1", "model": "claude-x",
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "content": [{"type": "text", "text": "hi there"},
                        {"type": "tool_use", "id": "t1", "name": "Bash",
                         "input": {"command": "ls"}}]}}) + "\n" +
        json.dumps({"type": "user", "message": {
            "content": [{"type": "tool_result", "tool_use_id": "t1",
                         "content": "listing"}]}}) + "\n")
    log = P.mirror_log("dash3")
    A.session_start({"session_id": "dash3", "cwd": "/w", "transcript_path": ""})
    rid = A.stream_start(log, "subagent", agent_id="ag2", src_path=str(tp))
    A.stream_end(rid, "stop-sentinel", lines_emitted=2)
    d = _get_json(dash + "/api/session/dash3/agent/ag2")
    kinds = [e["t"] for e in d["entries"]]
    assert kinds == ["message", "tool"] and d["model"] == "claude-x"
    tool = d["entries"][1]
    assert tool["tool"] == "Bash" and tool["output"] == "listing"
    # agents list carries the streams keystone fields the cards render
    ags = _get_json(dash + "/api/session/dash3")["agents"]
    assert ags and ags[0]["end_reason"] == "stop-sentinel"


def test_hidden_agent_husk_rows_are_filtered(dash):
    # A SubagentStop with no SubagentStart (hidden auxiliary agent) leaves an
    # agents-table row with every field empty — the finaliser's 'never
    # started (hidden agent)' path. The dashboard must not show it; a row
    # with any real signal (desc, kind, transcript, slot, start) stays.
    A.session_start({"session_id": "dash7", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("dash7")
    S.agent_set(log, "husk1", done=0)                  # the hidden-agent shape
    S.agent_set(log, "real1", desc="do a thing")
    ags = _get_json(dash + "/api/session/dash7")["agents"]
    assert [a["agent_id"] for a in ags] == ["real1"]


def test_http_rejects_bad_sids(dash):
    for bad in ("a%2Fb", "a%20b"):
        with pytest.raises(urllib.error.HTTPError) as e:
            _get(dash + "/api/session/%s/ops" % bad)
        assert e.value.code == 404


# ------------------------------------------- titles + the merged conversation

def _jl(*objs):
    return "".join(json.dumps(o) + "\n" for o in objs)


def _tw(tmp_path, name, *objs):
    p = tmp_path / name
    p.write_text(_jl(*objs))
    return str(p)


def test_session_title_prefers_summary_then_first_real_prompt(tmp_path):
    from plugins.claude_code import transcript as TR
    p = _tw(tmp_path, "t1.jsonl",
            {"type": "summary", "summary": "old summary"},
            {"type": "summary", "summary": "newest summary"},
            {"type": "user", "isMeta": True,
             "message": {"content": "<local-command-caveat>x</local-command-caveat>"}},
            {"type": "user", "message": {"content": "real question here\nmore"}})
    assert TR.session_title(p) == "newest summary"
    q = _tw(tmp_path, "t2.jsonl",
            {"type": "user", "message": {"content": "<command-name>/clear</command-name>"}},
            {"type": "user", "message": {"content": "fix the flaky test\nplease"}})
    assert TR.session_title(q) == "fix the flaky test"
    assert TR.session_title(str(tmp_path / "absent.jsonl")) == ""


def test_conversation_anchors_and_cursor(tmp_path):
    from plugins.claude_code import transcript as TR
    p = _tw(tmp_path, "c1.jsonl",
            {"type": "user", "message": {"content": "do the thing"}},
            {"type": "assistant", "message": {"id": "m1", "content": [
                {"type": "text", "text": "starting"},
                {"type": "tool_use", "id": "t1", "name": "Bash",
                 "input": {"command": "ls"}}]}},
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}},
            {"type": "assistant", "message": {"id": "m2", "content": [
                {"type": "text", "text": "done"}]}})
    recs, pos = TR.conversation(p, 0)
    assert [(r["kind"], r["anchor"]) for r in recs] == \
        [("prompt", None), ("message", None), ("message", "t1")]
    assert pos > 0
    # incremental: nothing new -> empty, cursor stable
    assert TR.conversation(p, pos) == ([], pos)
    with open(p, "a") as fh:
        fh.write(json.dumps({"type": "user",
                             "message": {"content": "next ask"}}) + "\n")
    recs2, pos2 = TR.conversation(p, pos)
    assert [r["kind"] for r in recs2] == ["prompt"] and pos2 > pos


def test_http_sessions_carry_titles(dash, tmp_path):
    tp = _tw(tmp_path, "titled.jsonl",
             {"type": "user", "message": {"content": "build the dashboard"}})
    A.session_start({"session_id": "dash5", "cwd": "/w", "transcript_path": tp})
    rows = _get_json(dash + "/api/sessions")
    row = next(r for r in rows if r["sid"] == "dash5")
    assert row["title"] == "build the dashboard"


def test_merged_backlog_interleaves_by_anchor(dash, tmp_path):
    # ops for tool t1 + a conversation (prompt -> tool t1 -> message):
    # the message must land AFTER t1's last op, the prompt before everything.
    tp = _tw(tmp_path, "conv.jsonl",
             {"type": "user", "message": {"content": "run it"}},
             {"type": "assistant", "message": {"id": "m1", "content": [
                 {"type": "tool_use", "id": "t1", "name": "Bash",
                  "input": {"command": "echo hi"}}]}},
             {"type": "user", "message": {"content": [
                 {"type": "tool_result", "tool_use_id": "t1", "content": "hi"}]}},
             {"type": "assistant", "message": {"id": "m2", "content": [
                 {"type": "text", "text": "all done"}]}})
    A.session_start({"session_id": "dash6", "cwd": "/w", "transcript_path": tp})
    log = P.mirror_log("dash6")
    O.emit(log, O.label("▶ foreground", (170, 185, 210), g="t1"),
           O.gut("hi", (170, 185, 210), g="t1"))
    last, mpos, items = DS.merged_backlog("dash6", "dash6")
    kinds = ["prompt" if "msg prompt" in it["html"] else
             "message" if "msg message" in it["html"] else "op"
             for it in items]
    assert kinds == ["prompt", "op", "op", "message"]
    assert last >= 2 and mpos > 0
    assert "run it" in items[0]["html"] and "all done" in items[-1]["html"]


def test_merged_backlog_interleaves_by_timestamp(dash, tmp_path):
    # Timestamps are PRIMARY over anchors: the "between" message is anchored to
    # x2 (by anchor it would follow op-two) but its transcript timestamp falls
    # BETWEEN the two ops' emit stamps, so it must land between them.
    import time
    from datetime import datetime, timezone
    tp = str(tmp_path / "ts.jsonl")
    A.session_start({"session_id": "dash7", "cwd": "/w", "transcript_path": tp})
    log = P.mirror_log("dash7")
    O.emit(log, O.label("op-one", (1, 2, 3), g="x1"))
    time.sleep(0.02)
    O.emit(log, O.label("op-two", (1, 2, 3), g="x2"))
    sdb = DS.API.state_db_for("dash7")
    _, ops = DS.API.ops_at(sdb, 0)
    t1, t2 = ops[0]["_ts"], ops[1]["_ts"]
    assert t1 and t2 and t1 < t2

    def iso(e):
        return datetime.fromtimestamp(e, tz=timezone.utc).isoformat()

    with open(tp, "w") as fh:
        fh.write(_jl(
            {"type": "user", "timestamp": iso(t1 - 1),
             "message": {"content": "first ask"}},
            {"type": "assistant", "timestamp": iso(t1),
             "message": {"id": "m1", "content": [
                 {"type": "tool_use", "id": "x2", "name": "Bash",
                  "input": {"command": "echo hi"}}]}},
            {"type": "assistant", "timestamp": iso((t1 + t2) / 2),
             "message": {"id": "m2", "content": [
                 {"type": "text", "text": "between msg"}]}},
            {"type": "assistant", "timestamp": iso(t2 + 1),
             "message": {"id": "m3", "content": [
                 {"type": "text", "text": "final msg"}]}}))
    last, mpos, items = DS.merged_backlog("dash7", "dash7")
    kinds = ["prompt" if "msg prompt" in it["html"] else
             "message" if "msg message" in it["html"] else "op"
             for it in items]
    assert kinds == ["prompt", "op", "message", "op", "message"]
    # "between msg" precedes op-two -> the timestamp beat the x2 anchor
    between = next(i for i, it in enumerate(items) if "between msg" in it["html"])
    optwo = next(i for i, it in enumerate(items) if "op-two" in it["html"])
    assert between < optwo
    assert "first ask" in items[0]["html"] and "final msg" in items[-1]["html"]
    assert last >= 2 and mpos > 0


# ------------------------------------------------------- notification watcher

def test_notifier_transitions(monkeypatch):
    n = DS.Notifier()
    n.winmap = {"7": {"sid": "s7", "cwd": "/w/proj"}}
    q = n.register()
    seq = [{"7": "working"}, {"7": "working"}, {"7": "awaiting-command"},
           {"7": "awaiting-command"}, {"7": "awaiting-response"}]
    monkeypatch.setattr(DS.API, "tab_states", lambda: seq.pop(0))
    n.scan()                                  # baseline — never news
    n.scan()                                  # unchanged — nothing
    n.scan()                                  # -> asking
    n.scan()                                  # unchanged again — nothing
    n.scan()                                  # -> done
    got = []
    while not q.empty():
        got.append(q.get_nowait())
    assert [(ev, p["kind"]) for ev, p in got] == \
        [("notify", "asking"), ("notify", "done")]
    assert got[0][1]["sid"] == "s7" and got[0][1]["project"] == "proj"
    n.unregister(q)


def test_notifier_ignores_windowless_transitions(monkeypatch):
    n = DS.Notifier()
    n.winmap = {}                             # no session known for the window
    q = n.register()
    seq = [{"9": "working"}, {"9": "awaiting-command"}]
    monkeypatch.setattr(DS.API, "tab_states", lambda: seq.pop(0))
    n.scan(); n.scan()
    assert q.empty()
