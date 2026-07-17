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
    assert d["last"] >= 3 and len(d["html"]) >= 3
    assert any("chip" in h for h in d["html"])
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


def test_http_rejects_bad_sids(dash):
    for bad in ("a%2Fb", "a%20b"):
        with pytest.raises(urllib.error.HTTPError) as e:
            _get(dash + "/api/session/%s/ops" % bad)
        assert e.value.code == 404


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
