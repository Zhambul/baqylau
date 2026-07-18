# L0 — dashboard/: the ops→HTML presenter and the HTTP server. In-process:
# the server runs on an ephemeral 127.0.0.1 port in a thread (never through
# serve() — no singleton lock, no real port constant), session data is seeded
# through the REAL product APIs (core.ops.emit, core.state, core.audit) under
# the autouse hermetic CLAUDE_AUDIT_DIR + tmp-path mirror prefixes, exactly
# like test_l0_sessionapi.py.
import gzip
import json
import os
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest
from conftest import REPO

if REPO not in sys.path:
    sys.path.insert(0, REPO)

import plugins
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


# ------------------------------------------------------------------ md_html
# The conversation-text markdown subset. The load-bearing property is ESCAPING
# (the neutralize() analog): <script> must survive as escaped text in EVERY
# context, never as a tag. Markdown completeness is secondary.

import importlib.util

_HAVE_PYGMENTS = importlib.util.find_spec("pygments") is not None


def test_md_html_escapes_script_in_every_context():
    for txt in ("<script>alert(1)</script>",              # plain paragraph
                "**<script>x</script>**",                 # inside bold
                "[<script>](https://x.test/a)"):          # inside link text
        h = opshtml.md_html(txt)
        assert "<script>" not in h and "&lt;script&gt;" in h
    # in a highlighted fence the escaped entity is split across SGR spans, so
    # &lt;script&gt; isn't contiguous — the load-bearing fact is that neither
    # the opening nor closing tag survives, and escaping did happen.
    hf = opshtml.md_html("```python\n<script>alert(1)</script>\n```")
    assert "<script>" not in hf and "</script>" not in hf and "&lt;" in hf


def test_md_html_javascript_link_is_plain_text():
    h = opshtml.md_html("[click](javascript:alert(1))")
    assert "<a" not in h                                  # scheme rejected
    assert "[click](javascript:alert(1))" in h            # rendered literally
    ok = opshtml.md_html("see [docs](https://x.test/d)")
    assert '<a href="https://x.test/d" target="_blank" rel="noopener">docs</a>' in ok


def test_md_html_block_elements():
    assert "<h2>Title</h2>" in opshtml.md_html("## Title")
    ul = opshtml.md_html("- one\n- two")
    assert ul == "<ul><li>one</li><li>two</li></ul>"
    ol = opshtml.md_html("1. a\n2. b")
    assert ol == "<ol><li>a</li><li>b</li></ol>"
    assert "<blockquote>quoted</blockquote>" in opshtml.md_html("> quoted")
    assert "<hr>" in opshtml.md_html("above\n\n---\n\nbelow")
    h = opshtml.md_html("a **bold** and *ital* and `code` word")
    assert "<strong>bold</strong>" in h and "<em>ital</em>" in h
    assert "<code>code</code>" in h


@pytest.mark.skipif(not _HAVE_PYGMENTS, reason="pygments optional (see coderender)")
def test_md_html_fenced_python_is_highlighted():
    # a python fence is coloured through the single lexer owner (render.lexer)
    # -> SGR -> ansi_html spans; guarded because pygments is an optional dep.
    h = opshtml.md_html("```python\ndef f(x):\n    return x\n```")
    assert '<pre class="md-code">' in h and "<span style=\"color:rgb(" in h
    assert "def" in h and "&lt;" not in h                 # nothing to escape here


def test_md_html_malformed_never_raises():
    for bad in ("```python\nx=1\nno closing fence",       # unclosed fence
                "**unclosed *nested _ stuff",             # tangled emphasis
                "###### too deep\n> \n- \n\n\n",          # odd blocks
                "", None):
        h = opshtml.md_html(bad)
        assert isinstance(h, str) and "<script>" not in h


def test_msg_html_renders_markdown_body():
    h = opshtml.msg_html("message", "a **bold** claim")
    assert 'class="msg message"' in h and "<div class=\"md\">" in h
    assert "<strong>bold</strong>" in h


# ---------------------------------------------------------- rich tool rendering

def test_tool_html_bash_highlights_command():
    h = opshtml.tool_html("Bash", {"command": "grep -rn foo src/",
                                    "description": "search"})
    assert "<pre class=\"oc\">" in h and "<span" in h   # codefmt highlight spans
    assert "class=\"tdesc\">search" in h                # dim description


def test_tool_html_edit_diff_escapes_content():
    # old_string with markup stays ESCAPED inside removed/added rows.
    h = opshtml.tool_html("Edit", {"old_string": "<script>alert(1)</script>",
                                    "new_string": "safe()", "replace_all": True})
    assert "class=\"dl removed\"" in h and "class=\"dl added\"" in h
    assert "&lt;script&gt;" in h and "<script>" not in h
    assert "class=\"tflag\">replace_all" in h


def test_tool_html_write_caps_long_content():
    body = "\n".join("line %d" % i for i in range(opshtml.WRITE_CAP + 50))
    h = opshtml.tool_html("Write", {"file_path": "/w/big.txt", "content": body})
    assert "class=\"tfile\">/w/big.txt" in h
    assert "class=\"telide\">… (50 more lines)" in h


def test_tool_html_write_highlights_known_lexer():
    h = opshtml.tool_html("Write", {"file_path": "/w/f.py",
                                    "content": "def f(x):\n    return x\n"})
    assert "<pre class=\"oc\">" in h and "<span" in h    # python lexer ran


def test_tool_html_read_one_liner():
    h = opshtml.tool_html("Read", {"file_path": "/w/a.py", "offset": 10,
                                   "limit": 20})
    assert "class=\"tline\">" in h and "Read" in h and "10-29" in h


def test_tool_html_deflist_for_search_tools():
    h = opshtml.tool_html("Grep", {"pattern": "foo", "path": "src"})
    assert "<dl class=\"tdl\">" in h
    assert "<dt>pattern</dt><dd>foo</dd>" in h
    # a long Task prompt is first-lined, not dumped whole
    t = opshtml.tool_html("Task", {"prompt": "line one\nline two\nline three"})
    assert "<dd>line one</dd>" in t and "line two" not in t


def test_tool_html_unknown_tool_and_empty_fall_back():
    assert opshtml.tool_html("MysteryTool", {"x": 1}) is None
    assert opshtml.tool_html("Bash", {}) is None
    assert opshtml.tool_html("Bash", "notadict") is None


def test_tool_output_html_only_bash():
    assert opshtml.tool_output_html("plain", False, "Read") is None
    h = opshtml.tool_output_html("\x1b[31mred\x1b[0m ok", False, "Bash")
    assert h is not None and "<pre class=\"oc\">" in h and "color:rgb(" in h


# ------------------------------------------------------------------ the server

@pytest.fixture
def dash(monkeypatch, tmp_path):
    monkeypatch.setattr(P, "PREFIX", str(tmp_path) + "/claude-mirror-")
    monkeypatch.setattr(P, "HISTORY_DIR", str(tmp_path / "park"))
    # Hermetic default: never enumerate the REAL kitty windows from the read
    # path (that would demote test sessions to not-live when the suite runs
    # inside a live kitty session). None = "can't enumerate → keep the state-DB
    # liveness signal"; a demotion test overrides this with a controlled map.
    monkeypatch.setattr(DS, "_live_windows", lambda: None)
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


def _sse_event(url, want, timeout=10):
    """Read a per-session SSE stream until an `event: <want>` frame arrives and
    return its data payload (raw JSON string); '' on timeout/EOF."""
    r = _req(url, timeout=timeout)
    try:
        pending = None
        for raw in r:
            line = raw.decode("utf-8", "replace").rstrip("\n")
            if line.startswith("event: "):
                pending = line[len("event: "):]
            elif line.startswith("data: ") and pending == want:
                return line[len("data: "):]
        return ""
    finally:
        r.close()


def test_running_ribbon_payload_and_sse(dash):
    """session_payload carries the live-slot ribbon (sessionapi.running()), and
    the per-session SSE announces it as a `running` event."""
    from core import slots
    A.session_start({"session_id": "run1", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("run1")
    slots.claim("monitor", log)                    # owned by THIS process -> alive
    slots.pid_set(log, "agentR", os.getpid())
    run = _get_json(dash + "/api/session/run1")["running"]
    assert "monitor" in run and run["monitor"][0]["alive"] is True
    assert "sub.pid" in run and run["sub.pid"][0]["key"] == "agentR"
    data = _sse_event(dash + "/events/session/run1?after=0&mpos=0", "running")
    assert data and "monitor" in json.loads(data)


def test_error_badge_payload_and_sse(dash):
    """session_payload carries the live ⚠ error count (error_count, chain-aware
    COUNT — not len(errors())), and the per-session SSE announces it as an
    `errors` {count} event on the slow cadence."""
    A.session_start({"session_id": "errS", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("errS")
    A.error(log, "boom", {"n": 1})
    A.error(log, "bang", {"n": 2})
    ov = _get_json(dash + "/api/session/errS")
    assert ov["error_count"] == 2
    data = _sse_event(dash + "/events/session/errS?after=0&mpos=0", "errors")
    assert data and json.loads(data)["count"] == 2


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
    # _mdify enriches the tool entry additively: a Bash command gets a
    # highlighted input_html; the raw input stays untouched.
    assert "<pre class=\"oc\">" in tool["input_html"]
    assert tool["input"] == {"command": "ls"}
    # agents list carries the streams keystone fields the cards render
    ags = _get_json(dash + "/api/session/dash3")["agents"]
    assert ags and ags[0]["end_reason"] == "stop-sentinel"
    # the /agent response carries a byte cursor `pos` (additive) so a live
    # client can hand it to the agent SSE for a race-free resume
    assert d["pos"] > 0


def _agent_transcript(tmp_path, sid, aid):
    """Seed an agent transcript + its audit streams row (the keystone
    sessionapi.agent_transcript resolves), returning its path."""
    tp = tmp_path / ("agent-%s.jsonl" % aid)
    tp.write_text(
        json.dumps({"type": "assistant", "message": {
            "id": "m1", "content": [
                {"type": "text", "text": "starting"},
                {"type": "tool_use", "id": "t1", "name": "Bash",
                 "input": {"command": "ls"}}]}}) + "\n" +
        json.dumps({"type": "user", "message": {
            "content": [{"type": "tool_result", "tool_use_id": "t1",
                         "content": "listing"}]}}) + "\n")
    A.session_start({"session_id": sid, "cwd": "/w", "transcript_path": ""})
    A.stream_start(P.mirror_log(sid), "subagent", agent_id=aid, src_path=str(tp))
    return tp


def test_activity_since_fanout(dash, tmp_path):
    """plugins.activity_since resolves (sid, agent_id) to the claude provider's
    (entries, resolutions, new_pos); an unknown pair falls through to None."""
    _agent_transcript(tmp_path, "fanout1", "agF")
    got = plugins.activity_since("fanout1", "agF", 0)
    assert got is not None
    ents, res, pos = got
    assert [e["t"] for e in ents] == ["message", "tool"]
    assert ents[1]["output"] == "listing"          # paired in the same window
    assert res == [] and pos > 0
    assert plugins.activity_since("nope", "nada", 0) is None


def test_sse_agent_streams_entries(dash, tmp_path):
    """The /events/agent SSE announces the increment from the given cursor as
    an `entries` event, server-enriched exactly like the REST endpoint."""
    _agent_transcript(tmp_path, "sseA", "agS")
    data = _sse_event(dash + "/events/agent/sseA/agS?pos=0", "entries")
    assert data
    d = json.loads(data)
    assert d["pos"] > 0
    kinds = [e["t"] for e in d["entries"]]
    assert kinds == ["message", "tool"]
    tool = d["entries"][1]
    assert "<pre class=\"oc\">" in tool["input_html"]   # _enrich_entries ran


def test_activity_entries_carry_markdown_html(dash, tmp_path):
    # /activity post-processes the timeline: message/prompt entries gain an
    # `html` field (md_html of their text) so the page renders markdown; the
    # raw text field stays untouched (additive shape).
    tp = tmp_path / "mdconv.jsonl"
    tp.write_text(
        json.dumps({"type": "user", "message": {"content": "**do** it"}}) + "\n" +
        json.dumps({"type": "assistant", "message": {
            "id": "m1", "content": [
                {"type": "text", "text": "here is a **bold** answer"}]}}) + "\n")
    A.session_start({"session_id": "dashmd", "cwd": "/w", "transcript_path": str(tp)})
    d = _get_json(dash + "/api/session/dashmd/activity")
    msg = next(e for e in d["entries"] if e["t"] == "message")
    assert "<strong>bold</strong>" in msg["html"]
    assert msg["text"] == "here is a **bold** answer"      # raw untouched
    prompt = next(e for e in d["entries"] if e["t"] == "prompt")
    assert "<strong>do</strong>" in prompt["html"]


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


def _req(url, headers=None, timeout=10):
    return urllib.request.urlopen(
        urllib.request.Request(url, headers=headers or {}), timeout=timeout)


def test_gzip_large_response_round_trips(dash):
    # A response at/above GZIP_MIN compresses when the client offers gzip, and
    # the compressed body decompresses to the byte-identical plain response.
    A.session_start({"session_id": "gz1", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("gz1")
    for i in range(60):
        O.emit(log, O.label("block %d" % i, (170, 185, 210), g="g%d" % i),
               O.gut("output line for block %d " % i * 3, (170, 185, 210),
                     g="g%d" % i))
    url = dash + "/api/session/gz1/ops?after=0"

    plain = _req(url)                                  # no Accept-Encoding
    assert plain.headers.get("Content-Encoding") is None
    assert plain.headers.get("Vary") == "Accept-Encoding"
    ref = plain.read()
    assert len(ref) >= DS.GZIP_MIN

    gz = _req(url, {"Accept-Encoding": "gzip, deflate"})
    assert gz.headers.get("Content-Encoding") == "gzip"
    raw = gz.read()                                    # urllib does not auto-inflate
    assert int(gz.headers.get("Content-Length")) == len(raw)
    assert len(raw) < len(ref)                         # smaller on the wire
    assert gzip.decompress(raw) == ref


def test_gzip_small_response_stays_plain(dash):
    # Below the threshold, gzip is skipped even when offered (framing overhead
    # would outweigh the win); an empty ops tail is well under GZIP_MIN.
    A.session_start({"session_id": "gz2", "cwd": "/w", "transcript_path": ""})
    r = _req(dash + "/api/session/gz2/ops?after=999999",
             {"Accept-Encoding": "gzip"})
    body = r.read()
    assert len(body) < DS.GZIP_MIN
    assert r.headers.get("Content-Encoding") is None
    assert json.loads(body)["items"] == []


def test_sse_is_never_gzipped(dash):
    # SSE holds the response open and writes incremental frames; buffering it
    # through gzip would break the stream, so it must stay identity-encoded
    # even when the client offers gzip.
    r = _req(dash + "/events", {"Accept-Encoding": "gzip"})
    try:
        assert r.headers.get("Content-Type", "").startswith("text/event-stream")
        assert r.headers.get("Content-Encoding") is None
    finally:
        r.close()


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
    last, mpos, oldest, items = DS.merged_backlog("dash6", "dash6")
    kinds = ["prompt" if "msg prompt" in it["html"] else
             "message" if "msg message" in it["html"] else "op"
             for it in items]
    assert kinds == ["prompt", "op", "op", "message"]
    assert last >= 2 and mpos > 0
    assert oldest == 0            # whole history fits under the default tail
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
    last, mpos, oldest, items = DS.merged_backlog("dash7", "dash7")
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


# ------------------------------------------------------- lazy backlog + history

def _blocks(sid, n):
    """Seed a session with `n` standalone label-op blocks (distinct group), the
    simplest thing that counts as one stream block each. Returns the op ids in
    emit order."""
    A.session_start({"session_id": sid, "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log(sid)
    for i in range(n):
        O.emit(log, O.label("block %d" % i, (170, 185, 210), g="b%d" % i))
    _, ops = DS.API.ops_at(DS.API.state_db_for(sid), 0)
    return [op["_id"] for op in ops]


def test_merged_backlog_tail_limit_and_oldest(dash):
    ids = _blocks("lz1", 6)
    # the whole history fits under a generous limit -> no lazy-load cursor
    _, _, oldest_all, items_all = DS.merged_backlog("lz1", "lz1", blocks=100)
    assert oldest_all == 0 and len(items_all) == 6
    # a tail of 2 blocks paints only the newest two, and reports the smallest
    # painted op id as the `oldest` cursor (block 4's op id, 0-indexed).
    _, _, oldest, items = DS.merged_backlog("lz1", "lz1", blocks=2)
    texts = [it["html"] for it in items]
    assert len(items) == 2
    assert "block 4" in texts[0] and "block 5" in texts[1]
    assert oldest == ids[4]                        # smallest painted op id


def test_history_chains_to_exhaustion_no_gap_no_overlap(dash):
    _blocks("lz2", 7)
    full = DS.merged_backlog("lz2", "lz2", blocks=1000)[3]     # the unlimited merge
    last, mpos, oldest, items = DS.merged_backlog("lz2", "lz2", blocks=3)
    assert len(items) == 3 and oldest > 0
    acc = list(items)
    guard = 0
    while oldest > 0:
        guard += 1
        assert guard < 50                          # must terminate
        oldest, page = DS.history("lz2", "lz2", oldest, 3)
        acc = page + acc                            # pages are OLDER -> prepend
    # concatenation of every slice equals the unlimited merge: no gap, no overlap
    assert [it["html"] for it in acc] == [it["html"] for it in full]


def test_history_straddling_group_not_duplicated(dash):
    # interleaved emits make group g1's ops non-contiguous (id1, id3) around
    # group g2 (id2); a tail of 1 block puts g1's newest op in the initial
    # window and its older op in history — the group straddles the boundary but
    # each op item appears exactly once across the slices.
    A.session_start({"session_id": "lz3", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("lz3")
    O.emit(log, O.label("g1 head", (1, 2, 3), g="g1"))
    O.emit(log, O.label("g2 head", (1, 2, 3), g="g2"))
    O.emit(log, O.gut("g1 more", (1, 2, 3), g="g1"))
    _, _, oldest, initial = DS.merged_backlog("lz3", "lz3", blocks=1)
    assert oldest > 0
    _, older = DS.history("lz3", "lz3", oldest, 10)
    ini_g1 = [it for it in initial if it["g"] == "g1"]
    old_g1 = [it for it in older if it["g"] == "g1"]
    assert ini_g1 and old_g1                        # g1 straddles the boundary
    # union carries both g1 ops exactly once (no duplicated card body)
    allg1 = [it["html"] for it in ini_g1 + old_g1]
    assert len(allg1) == 2 and len(set(allg1)) == 2
    assert any("g1 more" in h for h in allg1) and any("g1 head" in h for h in allg1)


def test_http_history_endpoint(dash):
    ids = _blocks("lz4", 5)
    d = _get_json(dash + "/api/session/lz4/history?before=%d&blocks=2" % ids[3])
    # before block 3's op id: the previous 2 blocks (1 and 2), newest cursor at
    # block 1's op id (block 0 still older).
    texts = [it["html"] for it in d["items"]]
    assert len(texts) == 2
    assert "block 1" in texts[0] and "block 2" in texts[1]
    assert d["oldest"] == ids[1]
    # before=0 is the exhausted signal (no older content)
    assert _get_json(dash + "/api/session/lz4/history?before=0&blocks=2") \
        == {"oldest": 0, "items": []}


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


class _FakeFE:
    """A usable Frontend stub capturing control-plane writes (injected via
    monkeypatching frontends.get in the server module)."""

    def __init__(self, send_ok=True, launch_ok=True):
        self.sent = []
        self.launched = []
        self.closed = []
        self.send_ok = send_ok
        self.launch_ok = launch_ok
        self.wins = {}          # sid -> live window override (stale/missing tag)

    def usable(self):
        return True

    def window_for_session(self, sid, tree=None):
        # simulate the live claude_session=<sid> pane tag: by default the
        # recorded (healthy, non-stale) window id; a test sets self.wins[sid]
        # to model a stale/missing tag (None) that must be refused
        if sid in self.wins:
            return self.wins[sid]
        row = DS.API.session_row(sid) or {}
        return str(row.get("kitty_window_id") or "") or None

    def send_text(self, win, text):
        self.sent.append((win, text))
        return self.send_ok

    def close_tab(self, win):
        self.closed.append(win)
        return True

    def launch_tab(self, cwd, argv):
        self.launched.append((cwd, argv))
        return self.launch_ok


def _inject_fe(monkeypatch, fe):
    monkeypatch.setattr(DS.frontends, "get", lambda **kw: fe)


def _post(url, body=None, ctype="application/json", header="1", origin=None,
          raw=None):
    """A control-plane POST. Defaults pass the guard (JSON + X-Claude-Dash: 1,
    no Origin); pass ctype=None / header=None / origin=… to exercise a
    rejection."""
    data = raw if raw is not None else json.dumps(body or {}).encode()
    headers = {}
    if ctype is not None:
        headers["Content-Type"] = ctype
    if header is not None:
        headers["X-Claude-Dash"] = header
    if origin is not None:
        headers["Origin"] = origin
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, r.read().decode("utf-8", "replace")


def test_post_message_success(dash, monkeypatch):
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "42")      # session_start reads the env
    A.session_start({"session_id": "msg1", "cwd": "/w", "transcript_path": ""})
    code, body = _post(dash + "/api/session/msg1/message",
                       {"text": "hello claude"})
    # no tab state recorded → not mid-turn → queued False
    assert code == 200 and json.loads(body) == {"ok": True, "queued": False,
                                                "tab": ""}
    assert fe.sent == [("42", "hello claude")]


def test_post_message_reports_queued_mid_turn(dash, monkeypatch):
    # a send while the tab is busy lands in Claude Code's own message queue —
    # the response says so (`queued`), and the web-send audit row carries the
    # tab state at send time ("my message vanished" → "it queued mid-turn")
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "77")
    A.session_start({"session_id": "msgq", "cwd": "/w", "transcript_path": ""})
    states = {"77": "working"}
    monkeypatch.setattr(DS.API, "tab_states", lambda: dict(states))
    code, body = _post(dash + "/api/session/msgq/message", {"text": "later"})
    assert code == 200
    assert json.loads(body) == {"ok": True, "queued": True, "tab": "working"}
    states["77"] = "awaiting-response"               # turn over: immediate send
    code, body = _post(dash + "/api/session/msgq/message", {"text": "now"})
    assert json.loads(body) == {"ok": True, "queued": False,
                                "tab": "awaiting-response"}
    # awaiting-command (a dialog is up) must NEVER claim queued — typed text
    # goes to the dialog, not the queue
    states["77"] = "awaiting-command"
    code, body = _post(dash + "/api/session/msgq/message", {"text": "hm"})
    assert json.loads(body)["queued"] is False


def test_conv_items_carry_kind_and_prompt_text():
    items = DS._conv_items([
        {"kind": "prompt", "text": "do the thing"},
        {"kind": "message", "text": "on it"},
        {"kind": "teammsg", "text": "hi", "sender": "reviewer"},
    ])
    assert [it["kind"] for it in items] == ["prompt", "message", "teammsg"]
    assert items[0]["text"] == "do the thing"        # the queue-chip match key
    assert "text" not in items[1] and "text" not in items[2]
    assert all(it["t"] == "msg" and it["g"] is None for it in items)


def test_post_message_no_window_is_409(dash, monkeypatch):
    _inject_fe(monkeypatch, _FakeFE())
    monkeypatch.delenv("KITTY_WINDOW_ID", raising=False)   # headless session
    A.session_start({"session_id": "msg2", "cwd": "/w", "transcript_path": ""})
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/msg2/message", {"text": "hi"})
    assert e.value.code == 409


def test_post_message_empty_text_is_400(dash, monkeypatch):
    _inject_fe(monkeypatch, _FakeFE())
    monkeypatch.setenv("KITTY_WINDOW_ID", "9")
    A.session_start({"session_id": "msg3", "cwd": "/w", "transcript_path": ""})
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/msg3/message", {"text": "   "})
    assert e.value.code == 400


class _NoTermFE:
    """A frontend with no reachable control channel (dashboard started outside
    kitty) → _frontend() returns None → a clean 503, never a 500."""

    def usable(self):
        return False


def test_post_message_no_terminal_is_503(dash, monkeypatch):
    _inject_fe(monkeypatch, _NoTermFE())
    monkeypatch.setenv("KITTY_WINDOW_ID", "5")
    A.session_start({"session_id": "msg4", "cwd": "/w", "transcript_path": ""})
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/msg4/message", {"text": "hi"})
    assert e.value.code == 503


def test_post_guard_rejections(dash):
    url = dash + "/api/sessions/new"
    with pytest.raises(urllib.error.HTTPError) as e:      # missing custom header
        _post(url, {"cwd": "/w"}, header=None)
    assert e.value.code == 403
    with pytest.raises(urllib.error.HTTPError) as e:      # wrong origin
        _post(url, {"cwd": "/w"}, origin="https://evil.test")
    assert e.value.code == 403
    with pytest.raises(urllib.error.HTTPError) as e:      # not JSON content type
        _post(url, {"cwd": "/w"}, ctype="text/plain")
    assert e.value.code == 415
    with pytest.raises(urllib.error.HTTPError) as e:      # malformed JSON body
        _post(url, raw=b"{not json")
    assert e.value.code == 400


def test_post_new_session_bad_cwd_is_400(dash, monkeypatch):
    _inject_fe(monkeypatch, _FakeFE())
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/sessions/new", {"cwd": "/no/such/dir/here"})
    assert e.value.code == 400


def test_post_new_session_launches(dash, monkeypatch, tmp_path):
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    code, body = _post(dash + "/api/sessions/new",
                       {"cwd": str(tmp_path), "prompt": "do the thing"})
    assert code == 200 and json.loads(body) == {"ok": True}
    # claude runs through the user's interactive login shell (kitty's own env
    # has no user PATH / aliases); the prompt is a POSITIONAL arg, never
    # interpolated into the fixed command string.
    cwd, argv = fe.launched[0]
    assert cwd == str(tmp_path)
    sh, flags, script, dollar0 = argv[:4]
    assert os.path.basename(sh) in DS.LAUNCH_SHELLS
    assert flags == "-lic" and script == 'claude "$@"' and dollar0 == "claude"
    assert argv[4:] == ["do the thing"]
    # no prompt → no positional args after the $0 placeholder
    _post(dash + "/api/sessions/new", {"cwd": str(tmp_path)})
    assert fe.launched[-1][1][4:] == []
    # a hostile prompt stays one argv word — nothing for the shell to parse
    evil = '"; rm -rf ~; echo "'
    _post(dash + "/api/sessions/new", {"cwd": str(tmp_path), "prompt": evil})
    assert fe.launched[-1][1][4:] == [evil]


def test_extra_origins_parse():
    assert DS.extra_origins("https://dash.zhambyl.top, https://a.b ,,") == \
        {"https://dash.zhambyl.top", "https://a.b"}
    assert DS.extra_origins(None) == set()
    assert DS.extra_origins("") == set()


def test_proxied_origin_allowed(dash, monkeypatch, tmp_path):
    # a CLAUDE_DASH_ORIGINS origin passes the guard (proxied deployment —
    # docs/remote.md); anything else stays 403 (covered by the guard test)
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    ext = "https://dash.zhambyl.top"
    monkeypatch.setattr(DS, "ALLOWED_ORIGINS", DS.ALLOWED_ORIGINS | {ext})
    code, body = _post(dash + "/api/sessions/new",
                       {"cwd": str(tmp_path)}, origin=ext)
    assert code == 200 and json.loads(body) == {"ok": True}


def test_readonly_kills_control_plane(dash, monkeypatch, tmp_path):
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setattr(DS, "READONLY", True)
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/sessions/new", {"cwd": str(tmp_path)})
    assert e.value.code == 403
    assert fe.launched == []
    assert _get(dash + "/api/sessions")[0] == 200      # reads unaffected


def test_post_new_session_model_effort(dash, monkeypatch, tmp_path):
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    # flags ride as "$@" words AHEAD of the prompt
    _post(dash + "/api/sessions/new",
          {"cwd": str(tmp_path), "model": "opus", "effort": "high",
           "prompt": "go"})
    assert fe.launched[-1][1][4:] == ["--model", "opus",
                                     "--effort", "high", "go"]
    # either alone
    _post(dash + "/api/sessions/new", {"cwd": str(tmp_path), "effort": "low"})
    assert fe.launched[-1][1][4:] == ["--effort", "low"]
    _post(dash + "/api/sessions/new",
          {"cwd": str(tmp_path), "model": "claude-fable-5"})
    assert fe.launched[-1][1][4:] == ["--model", "claude-fable-5"]
    # invalid values are 400, never launched
    n = len(fe.launched)
    for bad in ({"effort": "turbo"}, {"model": "opus high"},
                {"model": "a b; c"}, {"model": 7}):
        with pytest.raises(urllib.error.HTTPError) as e:
            _post(dash + "/api/sessions/new", dict({"cwd": str(tmp_path)}, **bad))
        assert e.value.code == 400
    assert len(fe.launched) == n


def test_post_stop_closes_tab(dash, monkeypatch):
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "55")
    A.session_start({"session_id": "stop1", "cwd": "/w", "transcript_path": ""})
    code, body = _post(dash + "/api/session/stop1/stop", {})
    assert code == 200 and json.loads(body) == {"ok": True}
    assert fe.closed == ["55"]


def test_post_stop_refuses_stale_window(dash, monkeypatch):
    # the bug: a session's recorded window id goes stale (kitty reuses ids), so
    # the pane is no longer tagged with this sid. Stop must resolve the LIVE
    # tag (window_for_session), find none, and refuse — never close the tab
    # that inherited the stale id.
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "11")
    A.session_start({"session_id": "stale1", "cwd": "/w", "transcript_path": ""})
    fe.wins["stale1"] = None                  # the claude_session tag is gone
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/stale1/stop", {})
    assert e.value.code == 409
    assert fe.closed == []                     # nothing closed — the fix
    # message is refused the same way (typing into a reused id is just as bad)
    monkeypatch.setenv("KITTY_WINDOW_ID", "5")
    A.session_start({"session_id": "stale2", "cwd": "/w", "transcript_path": ""})
    fe.wins["stale2"] = None
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/stale2/message", {"text": "hi"})
    assert e.value.code == 409
    assert fe.sent == []


def test_closed_tab_not_marked_live(dash, monkeypatch):
    # a session whose state DB lingers but whose tab is gone must NOT show live
    monkeypatch.setenv("KITTY_WINDOW_ID", "11")
    A.session_start({"session_id": "ghost", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("ghost")
    O.emit(log, O.label("x", (1, 2, 3)))       # create the state DB (state-DB live)
    # window enumeration returns a map WITHOUT this sid → tab is closed
    monkeypatch.setattr(DS, "_live_windows", lambda: {"other": "99"})
    row = next(r for r in _get_json(dash + "/api/sessions") if r["sid"] == "ghost")
    assert row["live"] is False                # demoted — the requirement
    ov = _get_json(dash + "/api/session/ghost")
    assert ov["live"] is False and ov["kitty_window_id"] == ""
    # when the tab IS open (sid in the map) it stays live and controllable
    monkeypatch.setattr(DS, "_live_windows", lambda: {"ghost": "11"})
    row = next(r for r in _get_json(dash + "/api/sessions") if r["sid"] == "ghost")
    assert row["live"] is True
    ov = _get_json(dash + "/api/session/ghost")
    assert ov["live"] is True and ov["kitty_window_id"] == "11"


def test_post_stop_no_window_is_409(dash, monkeypatch):
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.delenv("KITTY_WINDOW_ID", raising=False)   # headless session
    A.session_start({"session_id": "stop2", "cwd": "/w", "transcript_path": ""})
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/stop2/stop", {})
    assert e.value.code == 409
    assert fe.closed == []


def test_account_registry_and_alias(tmp_path, monkeypatch):
    from plugins.claude_code import account as ACC
    tsv = tmp_path / "accounts.tsv"
    tsv.write_text("c1\toboard\tsvc-1\nc2\tclaude-01\tsvc-2\n")
    monkeypatch.setattr(ACC, "ACCOUNTS_TSV", str(tsv))
    reg = ACC.registry()
    # no synthetic "default" — the plain-claude login duplicates a real account
    assert [a["slug"] for a in reg] == ["c1", "c2"]
    assert {"slug": "c2", "label": "claude-01", "alias": "c2"} in reg
    assert ACC.alias_for("c1") == "c1"
    # empty/claude still resolve to plain claude (the server's absent-account
    # fallback), even though the picker no longer offers them
    assert ACC.alias_for("") == "claude" and ACC.alias_for("claude") == "claude"
    assert ACC.alias_for("nope") is None          # unknown → caller 400s
    monkeypatch.setenv("CLAUDE_SUBSCRIPTION_SLUG", "c2")
    monkeypatch.setenv("CLAUDE_SUBSCRIPTION_LABEL", "claude-01")
    assert ACC.current() == {"slug": "c2", "label": "claude-01"}
    monkeypatch.delenv("CLAUDE_SUBSCRIPTION_SLUG", raising=False)
    monkeypatch.delenv("CLAUDE_SUBSCRIPTION_LABEL", raising=False)
    assert ACC.current() == {"slug": "", "label": "default"}


def test_statusline_shim_captures_and_delegates(tmp_path, monkeypatch):
    # the shim stashes account + usage into an EXISTING state DB, normalizes a
    # ms reset to seconds, and never creates the DB when it's absent
    monkeypatch.setattr(P, "PREFIX", str(tmp_path) + "/claude-mirror-")
    from core import hostpane as HP
    from plugins.claude_code import statusline as SL
    monkeypatch.setenv("CLAUDE_SUBSCRIPTION_SLUG", "c2")
    monkeypatch.setenv("CLAUDE_SUBSCRIPTION_LABEL", "claude-01")
    payload = {"session_id": "slcap", "rate_limits": {
        "five_hour": {"used_percentage": 10.6, "resets_at": 1784304000},
        "seven_day": {"used_percentage": 23, "resets_at": 1784500000000}}}
    raw = json.dumps(payload).encode()
    log = P.mirror_log("slcap")
    SL.capture(raw)                              # no DB yet → must be a no-op
    assert not os.path.isfile(P.state_db(log))   # (kv_get would CREATE it — don't)
    HP.ensure_db(log)
    SL.capture(raw)
    assert S.kv_get(log, "account") == {"slug": "c2", "label": "claude-01"}
    u = S.kv_get(log, "usage")
    assert u["five_hour"] == 11 and u["seven_day"] == 23        # rounded pct
    assert u["seven_day_reset"] == 1784500000.0                 # ms → s
    # a payload with no rate_limits leaves the last good usage in place
    SL.capture(json.dumps({"session_id": "slcap"}).encode())
    assert S.kv_get(log, "usage")["five_hour"] == 11
    # delegate runs with the same stdin and its exit code is returned
    assert SL.run(["sh", "-c", "cat >/dev/null; exit 3"], raw) == 3
    assert SL.run([], raw) == 0                                 # bare shim → 0


def test_accounts_payload_aggregates_usage(dash, monkeypatch, tmp_path):
    # /api/accounts returns the registry + newest usage per account slug
    monkeypatch.setattr(DS.plugins, "accounts", lambda: [
        {"slug": "", "label": "default", "alias": "claude"},
        {"slug": "c2", "label": "claude-01", "alias": "c2"}])
    A.session_start({"session_id": "accs1", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("accs1")
    S.kv_set(log, "account", {"slug": "c2", "label": "claude-01"})
    S.kv_set(log, "usage", {"five_hour": 40, "seven_day": 55, "ts": 100})
    rows = _get_json(dash + "/api/accounts")
    by = {r["slug"]: r for r in rows}
    assert by["c2"]["usage"]["five_hour"] == 40
    assert by[""]["usage"] is None                 # default has no captured usage


def test_post_new_session_account_picker(dash, monkeypatch, tmp_path):
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    # a known slug launches via its alias command word (c2 "$@")
    _post(dash + "/api/sessions/new", {"cwd": str(tmp_path), "account": "c2"})
    argv = fe.launched[-1][1]
    assert argv[2] == 'c2 "$@"' and argv[3] == "c2"
    # default / absent → plain claude
    _post(dash + "/api/sessions/new", {"cwd": str(tmp_path)})
    assert fe.launched[-1][1][3] == "claude"
    # an unknown account is 400, never launched
    n = len(fe.launched)
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/sessions/new", {"cwd": str(tmp_path), "account": "evil; rm"})
    assert e.value.code == 400
    assert len(fe.launched) == n


def test_session_payload_carries_account_and_usage(dash, monkeypatch):
    monkeypatch.setenv("KITTY_WINDOW_ID", "88")
    A.session_start({"session_id": "acsess", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("acsess")
    S.kv_set(log, "account", {"slug": "c1", "label": "oboard"})
    S.kv_set(log, "usage", {"five_hour": 5, "seven_day": 9, "ts": 1})
    ov = _get_json(dash + "/api/session/acsess")
    assert ov["account"] == {"slug": "c1", "label": "oboard"}
    assert ov["usage"]["seven_day"] == 9


def test_post_new_session_resume_continue(dash, monkeypatch, tmp_path):
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    sid = "85065b28-d9ea-4861-b209-bbc871e57357"
    _post(dash + "/api/sessions/new",
          {"cwd": str(tmp_path), "resume": sid, "prompt": "go on"})
    assert fe.launched[-1][1][4:] == ["--resume", sid, "go on"]
    _post(dash + "/api/sessions/new",
          {"cwd": str(tmp_path), "continue": True, "model": "opus"})
    assert fe.launched[-1][1][4:] == ["--continue", "--model", "opus"]
    # continue: false is a no-flag no-op, not an error
    _post(dash + "/api/sessions/new", {"cwd": str(tmp_path), "continue": False})
    assert fe.launched[-1][1][4:] == []
    # invalid: bad resume id / non-bool continue / both at once → 400, no launch
    n = len(fe.launched)
    for bad in ({"resume": "x y; z"}, {"resume": 7}, {"continue": "yes"},
                {"resume": sid, "continue": True}):
        with pytest.raises(urllib.error.HTTPError) as e:
            _post(dash + "/api/sessions/new", dict({"cwd": str(tmp_path)}, **bad))
        assert e.value.code == 400
    assert len(fe.launched) == n


def test_launch_argv_falls_back_to_zsh(monkeypatch):
    monkeypatch.setenv("SHELL", "/opt/homebrew/bin/fish")   # no POSIX "$@"
    assert DS.launch_argv([])[0] == "/bin/zsh"
    monkeypatch.delenv("SHELL", raising=False)
    assert DS.launch_argv([])[0] == "/bin/zsh"


def test_slash_commands_discovery(tmp_path, monkeypatch):
    from plugins.claude_code import slashcmds
    proj = tmp_path / "proj"
    (proj / ".claude" / "commands" / "gh").mkdir(parents=True)
    (proj / ".claude" / "commands" / "deploy.md").write_text(
        "---\ndescription: ship it\n---\nbody\n")
    (proj / ".claude" / "commands" / "gh" / "fix.md").write_text(
        "Fix a GitHub issue\n")
    skill = proj / ".claude" / "skills" / "audit-debug"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: audit-debug\ndescription: triage the audit\n---\n")
    user = tmp_path / "userclaude"
    (user / "commands").mkdir(parents=True)
    (user / "commands" / "deploy.md").write_text(
        "---\ndescription: user-level deploy\n---\n")
    (user / "commands" / "standup.md").write_text("# Daily standup notes\n")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(user))
    # env pinning must NOT redirect an arbitrary-cwd lookup (env_pin=False):
    # the dashboard resolves OTHER sessions' cwds, whatever spawned it
    other = tmp_path / "other" / ".claude" / "commands"
    other.mkdir(parents=True)
    (other / "pinned.md").write_text("must not appear\n")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "other"))
    cmds = {c["name"]: c for c in slashcmds.slash_commands(str(proj))}
    assert cmds["compact"]["src"] == "built-in"
    assert cmds["deploy"] == {"name": "deploy", "desc": "ship it",
                              "src": "project"}    # project shadows user
    assert cmds["gh:fix"]["desc"] == "Fix a GitHub issue"   # namespaced, first line
    assert cmds["audit-debug"] == {"name": "audit-debug",
                                   "desc": "triage the audit",
                                   "src": "project skill"}
    assert cmds["standup"] == {"name": "standup",
                               "desc": "Daily standup notes", "src": "user"}
    assert "pinned" not in cmds
    names = [c["name"] for c in slashcmds.slash_commands(str(proj))]
    assert names == sorted(names)
    # no cwd → built-ins + user-level only (no getcwd fallback walk)
    cmds = {c["name"]: c for c in slashcmds.slash_commands("")}
    assert "standup" in cmds and "deploy" in cmds and "gh:fix" not in cmds
    assert cmds["deploy"]["desc"] == "user-level deploy"


def test_http_commands(dash, tmp_path, monkeypatch):
    # cwd-keyed (not sid-keyed): the new-session form completes for a
    # directory that has no session yet
    from urllib.parse import quote
    proj = tmp_path / "cproj"
    (proj / ".claude" / "commands").mkdir(parents=True)
    (proj / ".claude" / "commands" / "ship.md").write_text(
        "---\ndescription: ship\n---\n")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "no-such-claude"))
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    rows = _get_json(dash + "/api/commands?cwd=" + quote(str(proj)))
    byname = {c["name"]: c for c in rows}
    assert byname["ship"]["src"] == "project"
    assert byname["compact"]["src"] == "built-in"
    # a non-directory cwd degrades to built-ins (+ user-level), never an error
    for q in ("?cwd=/no/such/dir", ""):
        rows = _get_json(dash + "/api/commands" + q)
        assert any(c["name"] == "compact" for c in rows)
        assert not any(c["name"] == "ship" for c in rows)


def test_notifier_ignores_windowless_transitions(monkeypatch):
    n = DS.Notifier()
    n.winmap = {}                             # no session known for the window
    q = n.register()
    seq = [{"9": "working"}, {"9": "awaiting-command"}]
    monkeypatch.setattr(DS.API, "tab_states", lambda: seq.pop(0))
    n.scan(); n.scan()
    assert q.empty()
