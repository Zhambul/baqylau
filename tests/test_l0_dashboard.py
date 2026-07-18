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
from conftest import REPO, wait_until

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


def test_sse_tab_re_resolves_window_after_resume(dash, monkeypatch):
    """A resume moves the session to a NEW kitty window (the SessionStart
    upsert refreshes the sessions row) — a session SSE stream opened BEFORE
    the move must re-resolve the window on the slow cadence instead of polling
    the dead window's lingering tab state forever (shipped: the page showed
    the dead window's green while kitty was magenta)."""
    monkeypatch.setenv("KITTY_WINDOW_ID", "71")
    A.session_start({"session_id": "resse", "cwd": "/w", "transcript_path": ""})
    monkeypatch.setattr(DS.API, "tab_states",
                        lambda: {"71": "awaiting-response", "72": "thinking"})
    seen = []
    r = _req(dash + "/events/session/resse?after=0&mpos=0", timeout=15)
    try:
        pending = None
        for raw in r:
            line = raw.decode("utf-8", "replace").rstrip("\n")
            if line.startswith("event: "):
                pending = line[len("event: "):]
            elif line.startswith("data: ") and pending == "tab":
                seen.append(json.loads(line[len("data: "):])["tab"])
                if seen[-1] == "thinking":
                    break
                # first tab arrived on the OLD window — now "resume": the
                # upsert moves the sessions row to window 72
                monkeypatch.setenv("KITTY_WINDOW_ID", "72")
                A.session_start({"session_id": "resse", "cwd": "/w",
                                 "transcript_path": ""})
    finally:
        r.close()
    assert seen[0] == "awaiting-response" and seen[-1] == "thinking", seen


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


def test_context_saturation_payloads_and_sse(dash, tmp_path):
    """The ctx-saturation chips' one data path (plugins.context over transcript
    tails, (path, size)-cached): sessions rows and the session overview carry
    the MAIN transcript's {used, window, pct, model} — sidechain records
    skipped — agent rows carry their OWN transcript's, and the per-session SSE
    announces the main figure as a `ctx` event."""
    tp = tmp_path / "ctx-main.jsonl"
    tp.write_text(
        json.dumps({"type": "assistant", "message": {
            "id": "m1", "model": "claude-haiku-4-5",
            "usage": {"input_tokens": 1000, "cache_read_input_tokens": 99000,
                      "output_tokens": 5}}}) + "\n" +
        json.dumps({"type": "assistant", "isSidechain": True, "message": {
            "id": "m2", "model": "claude-haiku-4-5",
            "usage": {"input_tokens": 7, "output_tokens": 1}}}) + "\n")
    A.session_start({"session_id": "ctxS", "cwd": "/w",
                     "transcript_path": str(tp)})
    atp = tmp_path / "agent-agC.jsonl"
    atp.write_text(json.dumps({"type": "assistant", "isSidechain": True,
                               "message": {"id": "a1", "model": "claude-haiku-4-5",
                                           "usage": {"input_tokens": 60000,
                                                     "output_tokens": 9}}}) + "\n")
    A.stream_start(P.mirror_log("ctxS"), "subagent", agent_id="agC",
                   src_path=str(atp))
    row = next(r for r in _get_json(dash + "/api/sessions") if r["sid"] == "ctxS")
    assert row["ctx"] == {"used": 100000, "window": 200000, "pct": 50,
                          "model": "claude-haiku-4-5"}
    ov = _get_json(dash + "/api/session/ctxS")
    assert ov["ctx"]["pct"] == 50                   # the sidechain row didn't win
    ag = next(a for a in ov["agents"] if a["agent_id"] == "agC")
    assert ag["ctx"]["used"] == 60000 and ag["ctx"]["pct"] == 30
    data = _sse_event(dash + "/events/session/ctxS?after=0&mpos=0", "ctx")
    assert data and json.loads(data)["ctx"]["pct"] == 50


def test_git_chip_payloads(dash, tmp_path):
    """sessions rows and the overview carry the cwd's checkout state {branch,
    worktree}, read from the .git files directly (never a git subprocess): a
    main checkout resolves HEAD's ref short name, a linked worktree (a .git
    FILE pointing into .../worktrees/<name>) carries the worktree name and a
    detached HEAD shows a 7-char sha, and a non-checkout cwd carries None."""
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".git" / "HEAD").write_text("ref: refs/heads/feat/x\n")
    wtgd = repo / ".git" / "worktrees" / "wt1"
    wtgd.mkdir(parents=True)
    (wtgd / "HEAD").write_text("abcdef0123456789abcdef0123456789abcdef01\n")
    wt = tmp_path / "wt1"
    wt.mkdir()
    (wt / ".git").write_text("gitdir: %s\n" % wtgd)
    A.session_start({"session_id": "gitA", "cwd": str(repo), "transcript_path": ""})
    A.session_start({"session_id": "gitB", "cwd": str(wt), "transcript_path": ""})
    A.session_start({"session_id": "gitC", "cwd": str(tmp_path / "nowhere"),
                     "transcript_path": ""})
    rows = {r["sid"]: r for r in _get_json(dash + "/api/sessions")}
    assert rows["gitA"]["git"] == {"branch": "feat/x", "worktree": None}
    assert rows["gitB"]["git"] == {"branch": "abcdef0", "worktree": "wt1"}
    assert rows["gitC"]["git"] is None
    ov = _get_json(dash + "/api/session/gitB")
    assert ov["git"] == {"branch": "abcdef0", "worktree": "wt1"}
    # HEAD is re-read each call: a branch switch shows without cache eviction
    (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    rows = {r["sid"]: r for r in _get_json(dash + "/api/sessions")}
    assert rows["gitA"]["git"]["branch"] == "main"


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


def test_sse_global_says_hello_with_boot_id(dash):
    # the first /events frame is the server's boot id — the stale-open-page
    # detector: a reconnecting EventSource that sees a different boot knows
    # the server (and likely the JS it would serve) changed underneath it
    data = json.loads(_sse_event(dash + "/events", "hello"))
    assert data.get("boot") == DS.BOOT_ID


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


def test_session_title_prefers_naming_records(tmp_path):
    # The naming records (docs/session-naming-findings.md) are what the kitty
    # tab shows — they beat summary/prompt, last-of-kind wins, and a custom
    # agent-name beats the auto ai-title regardless of order.
    from plugins.claude_code import transcript as TR
    p = _tw(tmp_path, "n1.jsonl",
            {"type": "summary", "summary": "a summary"},
            {"type": "user", "message": {"content": "first prompt"}},
            {"type": "ai-title", "aiTitle": "old auto title"},
            {"type": "ai-title", "aiTitle": "new auto title"})
    assert TR.session_title(p) == "new auto title"
    q = _tw(tmp_path, "n2.jsonl",
            {"type": "agent-name", "agentName": "my-renamed-session"},
            {"type": "ai-title", "aiTitle": "auto title after rename"})
    assert TR.session_title(q) == "my-renamed-session"


def test_session_title_finds_ai_title_past_head_window(tmp_path):
    # ai-title rows land near EOF — far beyond TITLE_SCAN in a long transcript.
    from plugins.claude_code import transcript as TR
    rows = [{"type": "user", "message": {"content": "the first prompt"}}]
    rows += [{"type": "assistant", "message": {"content": [{"type": "text", "text": "x" * 400}]}}
             for _ in range(TR.TITLE_SCAN + 20)]
    rows.append({"type": "ai-title", "aiTitle": "title near eof"})
    p = _tw(tmp_path, "n3.jsonl", *rows)
    assert os.path.getsize(p) > TR.TITLE_TAIL_B     # tail seek path, torn first line
    assert TR.session_title(p) == "title near eof"


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
    n.winmap = {"7": {"sid": "s7", "cwd": "/w/proj",
                      "transcript_path": "/w/t.jsonl"}}
    monkeypatch.setattr(DS, "session_title",
                        lambda p: "fix the flaky test" if p else "")
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
    assert got[0][1]["title"] == "fix the flaky test"
    n.unregister(q)


class _FakeFE:
    """A usable Frontend stub capturing control-plane writes (injected via
    monkeypatching frontends.get in the server module)."""

    def __init__(self, send_ok=True, launch_ok=True):
        self.sent = []
        self.pasted = []
        self.launched = []
        self.closed = []
        self.keyed = []
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

    def paste_text(self, win, text):
        self.pasted.append((win, text))
        return self.send_ok

    def send_key(self, win, *keys):
        self.keyed.append((win, keys))
        return self.send_ok

    def export_env(self):
        pass

    def close_tab(self, win):
        self.closed.append(win)
        return True

    def launch_tab(self, cwd, argv):
        self.launched.append((cwd, argv))
        return self.launch_ok

    def app_id(self):
        # "" = no OS-level app identity → the focus-bounce guard stays off;
        # the bounce tests override this with a real-looking bundle id
        return self.bundle_id

    bundle_id = ""


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
    # composer sends go through a bracketed paste (atomic — a raw send drops
    # bytes depending on TUI state), never send_text
    assert fe.pasted == [("42", "hello claude")]
    assert fe.sent == []


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


class _WatchAudit:
    """Wraps the server's audit handle: records web-launch-steal-watch rows
    in-memory (the watch thread's audit write cross-thread would land in the
    spool, invisible to a same-process DB read) and delegates everything else
    to the real module."""

    def __init__(self, real):
        self.real, self.rows = real, []

    def __getattr__(self, name):
        return getattr(self.real, name)

    def state_file(self, log, path, action, content=""):
        if action == "web-launch-steal-watch":
            self.rows.append(content)
        return self.real.state_file(log, path, action, content)


def _watch_rig(monkeypatch, fronts, bundle="app.term"):
    """Wire the steal watch for a test: a _FakeFE with an OS app id, a
    scripted _front_app sequence (call 1 = the pre-launch capture, the rest =
    the watch polls; the last value repeats), a fast poll cadence, a recorded
    audit. Returns (fe, rows) — rows collects the watch's audit content."""
    fe = _FakeFE()
    fe.bundle_id = bundle
    seq = list(fronts)
    monkeypatch.setattr(DS, "_front_app",
                        lambda: seq.pop(0) if len(seq) > 1 else seq[0])
    monkeypatch.setattr(DS, "STEALWATCH_POLL_S", 0.005)
    aud = _WatchAudit(DS.A)
    monkeypatch.setattr(DS, "A", aud)
    return fe, aud.rows


def test_new_session_steal_watch_records_takeovers(dash, monkeypatch,
                                                   tmp_path):
    # the watch records each TRANSITION onto the terminal (steal → back to the
    # browser → steal again = 2 entries, not one per poll while stolen), and
    # NEVER intervenes — there is deliberately no focus-changing code left in
    # the dashboard (the 2026-07-18 bounce-back yanked users who genuinely
    # switched to the terminal; the fix lives in launch_pane's conditional
    # --keep-focus instead)
    fe, rows = _watch_rig(
        monkeypatch, ["com.browser", "app.term", "app.term", "com.browser",
                      "app.term"])
    _inject_fe(monkeypatch, fe)
    code, _ = _post(dash + "/api/sessions/new", {"cwd": str(tmp_path)})
    assert code == 200 and fe.launched
    wait_until(lambda: rows, desc="steal watch wrote its audit row")
    assert len(rows[0]["steals"]) == 2
    assert rows[0]["before"] == "com.browser"
    assert rows[0]["terminal"] == "app.term"


def test_new_session_steal_watch_clean_run(dash, monkeypatch, tmp_path):
    # frontmost never lands on the terminal (unchanged, or the user switching
    # to some OTHER app) → an empty steals list
    fe, rows = _watch_rig(monkeypatch, ["com.browser", "com.other"])
    _inject_fe(monkeypatch, fe)
    code, _ = _post(dash + "/api/sessions/new", {"cwd": str(tmp_path)})
    assert code == 200
    wait_until(lambda: rows, desc="steal watch wrote its audit row")
    assert rows[0]["steals"] == []


def test_new_session_watch_off_without_app_id(dash, monkeypatch, tmp_path):
    # a frontend with no OS-level app identity (the inert stub, a future
    # terminal that can't name itself) → the watch never probes the OS
    fe = _FakeFE()                                     # bundle_id stays ""
    _inject_fe(monkeypatch, fe)
    probed = []
    monkeypatch.setattr(DS, "_front_app", lambda: probed.append(1) or "x")
    code, _ = _post(dash + "/api/sessions/new", {"cwd": str(tmp_path)})
    assert code == 200 and probed == []


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
    assert fe.sent == [] and fe.pasted == []


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


def test_post_interrupt_sends_escape(dash, monkeypatch):
    # interrupt = an Escape key EVENT into the session's window (send_key,
    # never send_text bytes) — the turn stops, the session stays up
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "66")
    A.session_start({"session_id": "intr1", "cwd": "/w", "transcript_path": ""})
    code, body = _post(dash + "/api/session/intr1/interrupt", {})
    assert code == 200 and json.loads(body) == {"ok": True, "tab": ""}
    assert fe.keyed == [("66", ("escape",))]
    assert fe.closed == []                    # never touches the tab


def test_post_interrupt_magenta_spawns_escape_recheck(dash, monkeypatch,
                                                      tmp_path):
    # an Esc into a THINKING tab may be the signal-less mid-thinking cancel —
    # the endpoint spawns the escape-recheck with the press-time transcript
    # size as the growth baseline; a non-busy tab spawns nothing
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    spawned = []
    monkeypatch.setattr(DS.SP, "spawn_detached",
                        lambda path, argv, log, env=None, purpose="", **kw:
                        spawned.append((path, argv, env, purpose)) or None)
    tp = tmp_path / "intr3.jsonl"
    tp.write_text('{"type":"user"}\n')
    monkeypatch.setenv("KITTY_WINDOW_ID", "77")
    A.session_start({"session_id": "intr3", "cwd": "/w",
                     "transcript_path": str(tp)})
    monkeypatch.setattr(DS.API, "tab_states", lambda: {"77": "thinking"})
    code, body = _post(dash + "/api/session/intr3/interrupt", {})
    assert code == 200 and json.loads(body) == {"ok": True, "tab": "thinking"}
    assert len(spawned) == 1
    path, argv, env, purpose = spawned[0]
    assert path.endswith("claude-tab-status.py")
    assert argv[:2] == ["escape-recheck", DS.P.mirror_log("intr3")]
    assert argv[2] == str(tp)
    assert argv[3] == str(tp.stat().st_size)      # press-time baseline
    assert env["KITTY_WINDOW_ID"] == "77"
    assert purpose == "watcher:escape-recheck"
    # green tab -> no recheck (nothing to recover)
    monkeypatch.setattr(DS.API, "tab_states",
                        lambda: {"77": "awaiting-response"})
    _post(dash + "/api/session/intr3/interrupt", {})
    assert len(spawned) == 1


def test_post_rewind_idle_types_the_command(dash, monkeypatch):
    # IDLE double-Esc = the rewind menu: TYPES /rewind (documented identical
    # to double-Esc, and deterministic where synthesized double-press key
    # events were ~2/3 flaky at any gap) — no Escape key events
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "88")
    A.session_start({"session_id": "rew1", "cwd": "/w", "transcript_path": ""})
    code, body = _post(dash + "/api/session/rew1/interrupt", {})
    assert json.loads(body) == {"ok": True, "tab": ""}
    assert fe.keyed == [("88", ("escape",))]          # single press = interrupt
    code, body = _post(dash + "/api/session/rew1/rewind", {})
    assert code == 200
    assert json.loads(body) == {"ok": True, "tab": "", "mode": "rewind",
                                "restored": ""}   # idle: nothing to restore
    assert fe.sent == [("88", "/rewind")]             # typed, not key events
    assert fe.keyed == [("88", ("escape",))]          # no extra Escapes
    assert fe.closed == []
    # same live-tag discipline as interrupt/stop
    fe.wins["rew1"] = None
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/rew1/rewind", {})
    assert e.value.code == 409
    assert fe.sent == [("88", "/rewind")]


def test_post_rewind_busy_is_cancel_edit(dash, monkeypatch):
    # MID-TURN double-Esc = cancel + restore the last message for editing:
    # TWO Escape key events (measured 3/3 reliable mid-turn), never the typed
    # command (which would queue as a message), plus the magenta recheck (the
    # cancel leaves the tab stuck thinking — same experiment)
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setattr(DS, "DOUBLE_ESC_GAP_S", 0)
    spawned = []
    monkeypatch.setattr(DS.SP, "spawn_detached",
                        lambda path, argv, log, env=None, purpose="", **kw:
                        spawned.append(argv) or None)
    monkeypatch.setenv("KITTY_WINDOW_ID", "89")
    A.session_start({"session_id": "rew2", "cwd": "/w", "transcript_path": ""})
    monkeypatch.setattr(DS.API, "tab_states", lambda: {"89": "working"})
    # the cancel restores the session's last user prompt — returned so the
    # page can prefill its composer
    monkeypatch.setattr(DS, "_last_prompt", lambda sid: "the cancelled message")
    code, body = _post(dash + "/api/session/rew2/rewind", {})
    assert code == 200
    assert json.loads(body) == {"ok": True, "tab": "working",
                                "mode": "cancel-edit",
                                "restored": "the cancelled message"}
    assert fe.keyed == [("89", ("escape",)), ("89", ("escape",))]
    assert fe.sent == []                              # nothing typed mid-turn
    assert len(spawned) == 1 and spawned[0][0] == "escape-recheck"
    # blue (executing) is also mid-turn = cancel-edit, but NOT magenta — the
    # bg writer-liveness recovery owns it, so no recheck
    monkeypatch.setattr(DS.API, "tab_states", lambda: {"89": "executing"})
    code, body = _post(dash + "/api/session/rew2/rewind", {})
    assert json.loads(body)["mode"] == "cancel-edit"
    assert len(spawned) == 1


class _MenuFE(_FakeFE):
    """_FakeFE plus a tiny simulation of Claude Code's rewind menu, so
    rewindmenu.drive's SCREEN-VERIFIED navigation runs against reactive
    screens instead of a canned transcript of get_text results: `/rewind`
    opens the checkpoint list, up/down move the cursor (pegging at the
    edges like the real TUI), Enter opens the numbered confirm menu, a
    digit selects (recorded in .picked) and Escape backs out one level.
    Screen shapes copied from live captures (2026-07-18): indented menu
    cursor rows, a column-0 scrollback prompt echo that must NOT parse as
    the cursor, the "(current)" trailing entry, numbered confirm rows."""

    def __init__(self, prompts, options=("Restore code and conversation",
                                         "Restore conversation",
                                         "Restore code", "Never mind")):
        super().__init__()
        self.prompts = list(prompts)         # oldest-first menu first-lines
        self.options = list(options)
        self.state = "idle"                  # idle | menu | confirm
        self.cursor = len(self.prompts)      # start on "(current)"
        self.picked = None                   # (prompt index, option label)

    def send_text(self, win, text):
        ok = super().send_text(win, text)
        if text == "/rewind" and self.state == "idle":
            self.state, self.cursor = "menu", len(self.prompts)
        return ok

    def send_key(self, win, *keys):
        ok = super().send_key(win, *keys)
        for k in keys:
            if self.state == "menu":
                if k == "up":
                    self.cursor = max(0, self.cursor - 1)
                elif k == "down":
                    self.cursor = min(len(self.prompts), self.cursor + 1)
                elif k == "enter" and self.cursor < len(self.prompts):
                    self.state = "confirm"
                elif k == "escape":
                    self.state = "idle"
            elif self.state == "confirm":
                if k == "escape":
                    self.state = "menu"
                elif k.isdigit() and 1 <= int(k) <= len(self.options):
                    self.picked = (self.cursor, self.options[int(k) - 1])
                    self.state = "idle"
        return ok

    def get_text(self, win, extent="screen"):
        if self.state == "menu":
            rows = ["❯ a scrollback prompt echo at column 0", "", "  Rewind",
                    "", "  Restore the code and/or conversation to the point…"]
            for i, p in enumerate(self.prompts + ["(current)"]):
                rows += [("  ❯ " if i == self.cursor else "    ") + p, ""]
            rows.append("  Enter to continue · Esc to cancel")
            return "\n".join(rows)
        if self.state == "confirm":
            # the real confirm screen states the code consequence — absent
            # code options always pair with "The code will be unchanged."
            has_code = any("code" in o.lower() for o in self.options)
            rows = ["", "  Rewind", "", "  Confirm you want to restore to the"
                    " point before you sent this message:", "",
                    "  The code will be restored +1 -1 in f.txt." if has_code
                    else "  The code will be unchanged.", ""]
            for i, o in enumerate(self.options):
                rows.append(("  ❯ " if i == 0 else "    ")
                            + "%d. %s" % (i + 1, o))
            return "\n".join(rows)
        return "❯ composer\n  -- INSERT --"


def _rewind_env(monkeypatch, sid, win, fe):
    _inject_fe(monkeypatch, fe)
    monkeypatch.setattr(DS.rewindmenu, "POLL_S", 0.01)
    monkeypatch.setattr(DS.rewindmenu, "KEY_GAP_S", 0)
    monkeypatch.setenv("KITTY_WINDOW_ID", win)
    A.session_start({"session_id": sid, "cwd": "/w", "transcript_path": ""})


def test_post_rewind_to_drives_the_menu(dash, monkeypatch):
    # full web rewind: /rewind typed, the checkpoint list navigated to the
    # TARGET prompt (verified by its menu text — the entry is the prompt's
    # first line), the confirm option picked by LABEL, and the restored text
    # echoed back for the page's composer prefill
    fe = _MenuFE(prompts=["make alpha", "make beta"])
    _rewind_env(monkeypatch, "rwt1", "31", fe)
    code, body = _post(dash + "/api/session/rwt1/rewind-to",
                       {"text": "make beta\nsecond line the menu never shows",
                        "mode": "both", "ups": 1})
    assert code == 200
    assert json.loads(body) == {
        "ok": True, "mode": "both", "degraded": False,
        "restored": "make beta\nsecond line the menu never shows"}
    assert fe.picked == (1, "Restore code and conversation")
    assert ("31", "/rewind") in fe.sent
    assert fe.state == "idle"                 # menu fully closed


def test_post_rewind_to_digit_follows_labels(dash, monkeypatch):
    # the confirm menu's NUMBERING SHIFTS with content (no code changes ⇒
    # "Restore conversation" is 1., not 2.) — the digit must come from the
    # parsed labels, never a hard-coded position
    fe = _MenuFE(prompts=["only prompt"],
                 options=("Restore conversation", "Summarize from here",
                          "Summarize up to here", "Never mind"))
    _rewind_env(monkeypatch, "rwt2", "32", fe)
    code, body = _post(dash + "/api/session/rwt2/rewind-to",
                       {"text": "only prompt", "mode": "conversation",
                        "ups": 1})
    assert code == 200 and json.loads(body)["ok"] is True
    assert fe.picked == (0, "Restore conversation")


def test_post_rewind_to_stale_hint_self_corrects(dash, monkeypatch):
    # a stale page hint (dead-branch bubbles the menu doesn't list) bursts to
    # the wrong entry — the text-verified scan walks up to the top, then back
    # down through the list, and still lands on the right checkpoint. Also
    # the code-only mode: no `restored` (the TUI composer got no draft).
    fe = _MenuFE(prompts=["p one", "p two", "p three"])
    _rewind_env(monkeypatch, "rwt3", "33", fe)
    code, body = _post(dash + "/api/session/rwt3/rewind-to",
                       {"text": "p three", "mode": "code", "ups": 3})
    assert code == 200
    assert json.loads(body) == {"ok": True, "mode": "code", "restored": "",
                                "degraded": False}
    assert fe.picked == (2, "Restore code")


def test_post_rewind_to_busy_is_409(dash, monkeypatch):
    # mid-turn the double-Esc gesture means CANCEL, and a typed /rewind would
    # queue as a message — the endpoint refuses outright
    fe = _MenuFE(prompts=["p"])
    _rewind_env(monkeypatch, "rwt4", "34", fe)
    monkeypatch.setattr(DS.API, "tab_states", lambda: {"34": "working"})
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/rwt4/rewind-to",
              {"text": "p", "mode": "both", "ups": 1})
    assert e.value.code == 409
    assert fe.sent == [] and fe.state == "idle"     # nothing typed


def test_post_rewind_to_not_found_bails_closed(dash, monkeypatch):
    # a target the menu doesn't list (e.g. rewound away in kitty since the
    # page loaded) scans the whole list, then Escapes the menu shut — the
    # session is never left sitting inside an open menu
    fe = _MenuFE(prompts=["p one", "p two"])
    _rewind_env(monkeypatch, "rwt5", "35", fe)
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/rwt5/rewind-to",
              {"text": "no such prompt", "mode": "both", "ups": 1})
    assert e.value.code == 409
    assert json.loads(e.value.read())["step"] == "find"
    assert fe.state == "idle" and fe.picked is None


def test_post_rewind_to_both_degrades_when_code_unchanged(dash, monkeypatch):
    # "restore code and conversation" at a checkpoint with NO code changes:
    # the code is already in the target state, Claude Code omits the code
    # options as no-ops — the driver degrades to "Restore conversation"
    # (verified against the screen's "The code will be unchanged." line)
    # instead of failing (reported live 2026-07-18)
    fe = _MenuFE(prompts=["p"],
                 options=("Restore conversation", "Summarize from here",
                          "Summarize up to here", "Never mind"))
    _rewind_env(monkeypatch, "rwt7", "37", fe)
    code, body = _post(dash + "/api/session/rwt7/rewind-to",
                       {"text": "p", "mode": "both", "ups": 1})
    assert code == 200
    assert json.loads(body) == {"ok": True, "mode": "both", "restored": "p",
                                "degraded": True}
    assert fe.picked == (0, "Restore conversation")


def test_post_rewind_to_missing_option_bails_closed(dash, monkeypatch):
    # asking for a code restore at a checkpoint with no code changes: the
    # option isn't on the confirm menu — back out (both menus closed), 409
    fe = _MenuFE(prompts=["p"],
                 options=("Restore conversation", "Summarize from here",
                          "Never mind"))
    _rewind_env(monkeypatch, "rwt6", "36", fe)
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/rwt6/rewind-to",
              {"text": "p", "mode": "code", "ups": 1})
    assert e.value.code == 409
    err = json.loads(e.value.read())
    assert err["step"] == "option"
    # the bail explains WHY the option is absent (the screen said the code
    # is unchanged) — "rewind failed" alone sent the user to the audit
    assert "no code changes to revert" in err["error"]
    assert fe.state == "idle" and fe.picked is None


# real screen captures (live session, 2026-07-18; longest prompt lines
# shortened for the linter — shapes and prefixes untouched) the parsers pin
_MENU_SCREEN = """\
❯ Use the Write tool to write the word ALPHA into rewind-test.txt.

⏺ Done.

  Rewind

  Restore the code and/or conversation to the point before…

    Use the Write tool to write the word ALPHA into rewind-test.txt.
    rewind-test.txt +1

  ❯ Now use Write to overwrite /private/tmp/rewind-test.txt with the single word BETA. Reply with one word.
    rewind-test.txt +1 -1

    This is a deliberately very long first line meant to overflow the menu entry width and show truncation …
    No code changes

    (current)

  Enter to continue · Esc to cancel"""

_CONFIRM_SCREEN = """\
  Rewind

  Confirm you want to restore to the point before you sent this message:

  │ Now use Write to overwrite /private/tmp/rewind-test.txt with the single word BETA. Reply with one word.
  │ (52s ago)

  The conversation will be forked.
  The code will be restored +1 -1 in rewind-test.txt.

  ❯ 1. Restore code and conversation
    2. Restore conversation
    3. Restore code
    4. Summarize from here
  ↓ 5. Summarize up to here

  ⚠ Rewinding does not affect files edited manually or via bash."""


def test_rewindmenu_parsers_pin_the_real_screens():
    RM = DS.rewindmenu
    assert RM.menu_open(_MENU_SCREEN)
    assert not RM.confirm_open(_MENU_SCREEN)
    # the column-0 scrollback prompt echo is NOT the cursor; the indented
    # "  ❯ " row is
    assert RM.cursor_entry(_MENU_SCREEN) == ("Now use Write to overwrite "
        "/private/tmp/rewind-test.txt with the single word BETA. "
        "Reply with one word.")
    assert RM.confirm_open(_CONFIRM_SCREEN)
    assert not RM.menu_open(_CONFIRM_SCREEN)
    assert RM.confirm_options(_CONFIRM_SCREEN) == {
        "restore code and conversation": "1",
        "restore conversation": "2",
        "restore code": "3",
        "summarize from here": "4",
        "summarize up to here": "5",     # the ↓ scroll indicator is tolerated
    }
    assert not RM.menu_open("❯ composer\n  -- INSERT --")
    assert RM.menu_region("no menu here at all") == ""


def test_rewindmenu_entry_match_is_truncation_aware():
    RM = DS.rewindmenu
    long = ("This is a deliberately very long first line meant to overflow "
            "the rewind menu entry width and show me how truncation is "
            "rendered at the edge of the pane, if at all, in the checkpoint "
            "list.\nSecond line here.")
    trunc = ("This is a deliberately very long first line meant to overflow "
             "the rewind menu entry width and show me how truncation is "
             "rendered …")
    assert RM.entry_matches(trunc, long)             # ellipsis = prefix match
    assert RM.entry_matches("short prompt", "short prompt\nsecond line")
    assert not RM.entry_matches("short prompt", "short prompt but longer")
    assert not RM.entry_matches("(current)", "anything")
    assert not RM.entry_matches("other …", long)


class _AskFE(_FakeFE):
    """_FakeFE plus a reactive simulation of the AskUserQuestion dialog, per
    the live captures (2026-07-18): a header-chip bar, one pane per question
    (numbered options; multiSelect checkboxes; a "Type something" row whose
    label mutates to the typed text; multiSelect's unnumbered Submit row),
    "Chat about this" below a rule, and the review pane. Key semantics as
    measured: single-select digits answer+advance (a sole single-select
    question submits outright), multiSelect digits toggle, typing goes into
    the focused Type row, Enter there selects (single) / toggles (multi),
    left/right move tabs (left no-ops at the first), review digit 1 submits."""

    def __init__(self, questions):
        super().__init__()
        self.questions = questions
        n = len(questions)
        self.tab = 0                    # question index; n = the review pane
        self.cursor = 0                 # row index on the current pane
        self.open = True
        self.single = {}                # qi -> answered label/text
        self.checks = [set() for _ in range(n)]
        self.typed = [""] * n
        self.submitted = None           # final {question: answer} on submit
        self.chatted = False

    # pane rows, mirroring the real numbering: options 1..k, Type row k+1,
    # then (multi only) the unnumbered Submit row; Chat is the next digit
    def _labels(self, qi):
        q = self.questions[qi]
        return [o["label"] for o in q.get("options") or []]

    def _type_label(self, qi):
        return self.typed[qi] or ("Type something"
                                  + ("" if self.questions[qi].get("multiSelect")
                                     else "."))

    def _rows(self, qi):
        q = self.questions[qi]
        labels = self._labels(qi)
        rows = [(str(i + 1), lb) for i, lb in enumerate(labels)]
        rows.append((str(len(labels) + 1), self._type_label(qi)))
        if q.get("multiSelect"):
            rows.append(("", "Submit"))
        return rows                     # Chat row rendered separately

    def _advance(self):
        self.tab += 1
        self.cursor = 0
        if self.tab >= len(self.questions):
            if len(self.questions) == 1 \
                    and not self.questions[0].get("multiSelect"):
                self._finish()          # sole single-select: no review pane

    def _finish(self):
        out = {}
        for qi, q in enumerate(self.questions):
            if q.get("multiSelect"):
                sel = [lb for lb in self._labels(qi)
                       if lb in self.checks[qi]]
                if self.typed[qi] and "__typed__" in self.checks[qi]:
                    sel.append(self.typed[qi])
                out[q["question"]] = ", ".join(sel)
            else:
                out[q["question"]] = self.single.get(qi, "")
        self.submitted = out
        self.open = False

    def send_text(self, win, text):
        ok = super().send_text(win, text)
        if not self.open or self.tab >= len(self.questions):
            return ok
        qi, q = self.tab, self.questions[self.tab]
        if self.cursor == len(self._labels(qi)):     # the Type row
            self.typed[qi] = text
            if not q.get("multiSelect"):             # the CR selects+advances
                self.single[qi] = text
                self._advance()
        return ok

    def send_key(self, win, *keys):
        ok = super().send_key(win, *keys)
        for k in keys:
            if not self.open:
                continue
            if self.tab >= len(self.questions):      # review pane
                if k == "1":
                    self._finish()
                elif k == "left":
                    self.tab = len(self.questions) - 1
                    self.cursor = 0
                continue
            qi, q = self.tab, self.questions[self.tab]
            labels = self._labels(qi)
            nrows = len(self._rows(qi)) + 1          # + the Chat row
            if k == "left":
                if self.tab > 0:
                    self.tab -= 1
                    self.cursor = 0
            elif k == "right":
                self.tab += 1
                self.cursor = 0
            elif k == "up":
                self.cursor = max(0, self.cursor - 1)
            elif k == "down":
                self.cursor = min(nrows - 1, self.cursor + 1)
            elif k == "enter" and self.cursor == len(labels):
                if q.get("multiSelect"):             # toggle the custom row
                    self.checks[qi] ^= {"__typed__"}
            elif k.isdigit():
                d = int(k)
                if d == len(labels) + (3 if q.get("multiSelect") else 2):
                    self.chatted = True              # "Chat about this"
                    self.open = False
                elif 1 <= d <= len(labels):
                    if q.get("multiSelect"):
                        self.checks[qi] ^= {labels[d - 1]}
                    else:
                        self.single[qi] = labels[d - 1]
                        self._advance()
        return ok

    def get_text(self, win, extent="screen"):
        if not self.open:
            return "❯ composer\n  -- INSERT --"
        chips = "  ".join(
            ("☒ " if (self.single.get(i) or self.checks[i]) else "☐ ")
            + (q.get("header") or "Q%d" % (i + 1))
            for i, q in enumerate(self.questions))
        bar = "←  %s  ✔ Submit  →" % chips
        if self.tab >= len(self.questions):
            return "\n".join([bar, "", "Review your answers", "",
                              "Ready to submit your answers?", "",
                              "❯ 1. Submit answers", "  2. Cancel"])
        qi, q = self.tab, self.questions[self.tab]
        lines = [bar, "", q.get("question") or "", ""]
        for i, (digit, label) in enumerate(self._rows(qi)):
            cur = "❯ " if i == self.cursor else "  "
            if not digit:
                lines.append(cur + "   Submit")
                continue
            check = ""
            if q.get("multiSelect"):
                on = (label in self.checks[qi]
                      or (i == len(self._labels(qi))
                          and "__typed__" in self.checks[qi]))
                check = "[✔] " if on else "[ ] "
            lines.append("%s%s. %s%s" % (cur, digit, check, label))
        chat_digit = len(self._rows(qi)) + 1
        lines += ["────────", "  %d. Chat about this" % chat_digit, "",
                  "Enter to select · ↑/↓ to navigate · Esc to cancel"]
        return "\n".join(lines)


def _ask_env(monkeypatch, sid, win, fe, questions, tid="toolu_a1"):
    _inject_fe(monkeypatch, fe)
    monkeypatch.setattr(DS.askdialog, "POLL_S", 0.01)
    monkeypatch.setattr(DS.askdialog, "KEY_GAP_S", 0)
    monkeypatch.setenv("KITTY_WINDOW_ID", win)
    A.session_start({"session_id": sid, "cwd": "/w", "transcript_path": ""})
    S.kv_set(DS.P.mirror_log(sid), "ask-pending",
              {"tool_use_id": tid, "questions": questions})


_ASK_1S = [{"question": "Which fruit?", "header": "Fruit", "multiSelect": False,
            "options": [{"label": "Apple", "description": "crisp"},
                        {"label": "Banana", "description": "soft"},
                        {"label": "Cherry", "description": "tart"}]}]
_ASK_2Q = [{"question": "Pick a planet", "header": "Planet",
            "multiSelect": False,
            "options": [{"label": "Mars"}, {"label": "Venus"}]},
           {"question": "Pick metals", "header": "Metals", "multiSelect": True,
            "options": [{"label": "Iron"}, {"label": "Copper"},
                        {"label": "Zinc"}]}]


def test_post_answer_single_label(dash, monkeypatch):
    # one single-select question: the digit answers AND submits (no review)
    fe = _AskFE(_ASK_1S)
    _ask_env(monkeypatch, "ask1", "41", fe, _ASK_1S)
    code, body = _post(dash + "/api/session/ask1/answer",
                       {"tool_use_id": "toolu_a1",
                        "answers": [{"selected": ["Banana"], "other": ""}]})
    assert code == 200 and json.loads(body) == {"ok": True, "chat": False}
    assert fe.submitted == {"Which fruit?": "Banana"}


def test_post_answer_two_questions_mixed(dash, monkeypatch):
    # the live-verified shape: single label + multiSelect labels + custom
    # text, driven through the review pane ("1. Submit answers")
    fe = _AskFE(_ASK_2Q)
    _ask_env(monkeypatch, "ask2", "42", fe, _ASK_2Q)
    code, body = _post(dash + "/api/session/ask2/answer",
                       {"tool_use_id": "toolu_a1",
                        "answers": [{"selected": ["Venus"], "other": ""},
                                    {"selected": ["Iron", "Zinc"],
                                     "other": "titanium"}]})
    assert code == 200
    assert fe.submitted == {"Pick a planet": "Venus",
                            "Pick metals": "Iron, Zinc, titanium"}


def test_post_answer_multi_diffs_against_screen(dash, monkeypatch):
    # digits TOGGLE — boxes the user pre-checked in the terminal must be
    # reconciled (unwanted ones toggled OFF), never blindly re-pressed
    fe = _AskFE(_ASK_2Q)
    fe.single[0] = "Mars"          # Q1 already answered in the TUI
    fe.tab = 1                     # dialog sitting on Q2
    fe.checks[1] = {"Copper"}      # an unwanted pre-toggle
    _ask_env(monkeypatch, "ask3", "43", fe, _ASK_2Q)
    code, _ = _post(dash + "/api/session/ask3/answer",
                    {"tool_use_id": "toolu_a1",
                     "answers": [{"selected": ["Mars"], "other": ""},
                                 {"selected": ["Zinc"], "other": ""}]})
    assert code == 200
    assert fe.submitted == {"Pick a planet": "Mars", "Pick metals": "Zinc"}


def test_post_answer_chat_about_this(dash, monkeypatch):
    fe = _AskFE(_ASK_1S)
    _ask_env(monkeypatch, "ask4", "44", fe, _ASK_1S)
    code, body = _post(dash + "/api/session/ask4/answer",
                       {"tool_use_id": "toolu_a1", "chat": True})
    assert code == 200 and json.loads(body) == {"ok": True, "chat": True}
    assert fe.chatted and fe.submitted is None


def test_post_answer_free_text_single(dash, monkeypatch):
    fe = _AskFE(_ASK_1S)
    _ask_env(monkeypatch, "ask5", "45", fe, _ASK_1S)
    code, _ = _post(dash + "/api/session/ask5/answer",
                    {"tool_use_id": "toolu_a1",
                     "answers": [{"selected": [], "other": "oolong tea"}]})
    assert code == 200
    assert fe.submitted == {"Which fruit?": "oolong tea"}


def test_post_answer_guards(dash, monkeypatch):
    # stale/missing stash and a wrong answers count are refused BEFORE any
    # key is pressed; a stash without a dialog on screen bails at "open"
    fe = _AskFE(_ASK_1S)
    _ask_env(monkeypatch, "ask6", "46", fe, _ASK_1S)
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/ask6/answer",
              {"tool_use_id": "toolu_WRONG", "answers": []})
    assert e.value.code == 409 and "expired" in json.loads(e.value.read())["error"]
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/ask6/answer",
              {"tool_use_id": "toolu_a1", "answers": []})
    assert e.value.code == 400
    assert fe.keyed == [] and fe.submitted is None
    fe.open = False                       # dialog dismissed in the terminal
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/ask6/answer",
              {"tool_use_id": "toolu_a1",
               "answers": [{"selected": ["Apple"], "other": ""}]})
    assert e.value.code == 409
    assert json.loads(e.value.read())["step"] == "open"
    # no pending stash at all
    S.kv_del(DS.P.mirror_log("ask6"), "ask-pending")
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/ask6/answer",
              {"tool_use_id": "toolu_a1", "answers": []})
    assert e.value.code == 409
    assert "no pending" in json.loads(e.value.read())["error"]


# real screen captures (live session 2026-07-18) the askdialog parsers pin
_ASK_MULTI_SCREEN = """\
❯ a scrollback prompt echo at column 0
────
←  ☒ Toppings  ✔ Submit  →

Which toppings?

❯ 1. [ ] Cheese
  Melted cheese topping
  2. [✔] Olives
  Sliced black or green olives
  3. [ ] Onions
  Diced or sliced onions
  4. [✔] Peppers
  Bell or chili peppers
  5. [ ] Type something
     Submit
────
  6. Chat about this

Enter to select · ↑/↓ to navigate · Esc to cancel"""

_ASK_REVIEW_SCREEN = """\
←  ☒ Pets  ☒ Drink  ✔ Submit  →

Review your answers

 ● Cats or dogs?
   → Cats
 ● Tea or coffee?
   → Coffee

Ready to submit your answers?

❯ 1. Submit answers
  2. Cancel"""


def test_askdialog_parsers_pin_the_real_screens():
    AD = DS.askdialog
    assert AD.dialog_open(_ASK_MULTI_SCREEN)
    assert not AD.review_open(_ASK_MULTI_SCREEN)
    rs = AD.rows(_ASK_MULTI_SCREEN)
    assert [(r["digit"], r["label"], r["check"]) for r in rs] == [
        ("1", "Cheese", False), ("2", "Olives", True),
        ("3", "Onions", False), ("4", "Peppers", True),
        ("5", "Type something", False), ("", "Submit", None),
        ("6", "Chat about this", None)]
    assert [r["cursor"] for r in rs] == [True] + [False] * 6
    qs = [{"question": "Which toppings?"}, {"question": "Other thing?"}]
    assert AD.current_question(_ASK_MULTI_SCREEN, qs) == 0
    # the column-0 scrollback echo is outside the chip-bar region
    assert "scrollback" not in AD.region(_ASK_MULTI_SCREEN)
    assert AD.review_open(_ASK_REVIEW_SCREEN)
    assert not AD.dialog_open(_ASK_REVIEW_SCREEN)
    assert AD.current_question(_ASK_REVIEW_SCREEN, qs) is None
    assert not AD.dialog_open("❯ composer\n  -- INSERT --")


class _PlanFE(_FakeFE):
    """_FakeFE plus a reactive simulation of the ExitPlanMode approval dialog
    (live captures 2026-07-18): "Would you like to proceed?" + numbered rows,
    where a decision digit selects immediately, the "Tell Claude what to
    change" digit only FOCUSES its editable row (typed text + CR rejects with
    feedback), and Escape rejects outright."""

    OPTIONS = ("Yes, and bypass permissions", "Yes, manually approve edits",
               "No, refine with Ultraplan on Claude Code on the web",
               "Tell Claude what to change")

    def __init__(self, options=OPTIONS):
        super().__init__()
        self.options = list(options)
        self.open = True
        self.cursor = 0
        self.decided = None
        self.fb = None

    def send_key(self, win, *keys):
        ok = super().send_key(win, *keys)
        for k in keys:
            if not self.open:
                continue
            if k == "escape":
                self.decided, self.open = "esc", False
            elif k.isdigit() and 1 <= int(k) <= len(self.options):
                label = self.options[int(k) - 1]
                if label.startswith("Tell Claude"):
                    self.cursor = int(k) - 1          # focus, not select
                else:
                    self.decided, self.open = label, False
        return ok

    def send_text(self, win, text):
        ok = super().send_text(win, text)
        if self.open and self.options[self.cursor].startswith("Tell Claude"):
            self.fb, self.open = text, False
        return ok

    def get_text(self, win, extent="screen"):
        if not self.open:
            return "❯ composer\n  -- INSERT --"
        rows = ["scrollback noise", "",
                "   Claude has written up a plan and is ready to execute. "
                "Would you like to proceed?", ""]
        for i, o in enumerate(self.options):
            rows.append(("   ❯ " if i == self.cursor else "     ")
                        + "%d. %s" % (i + 1, o))
            if o.startswith("Tell Claude"):
                rows.append("        shift+tab to approve with this feedback")
        return "\n".join(rows)


_PLAN_PEND = {"tool_use_id": "toolu_p1", "plan": "# Plan\n1. do the thing",
              "planFilePath": "/tmp/plan.md"}


def _plan_env(monkeypatch, sid, win, fe):
    _inject_fe(monkeypatch, fe)
    monkeypatch.setattr(DS.plandialog, "POLL_S", 0.01)
    monkeypatch.setenv("KITTY_WINDOW_ID", win)
    A.session_start({"session_id": sid, "cwd": "/w", "transcript_path": ""})
    S.kv_set(DS.P.mirror_log(sid), "plan-pending", dict(_PLAN_PEND))


def test_post_plan_options_reads_live_labels(dash, monkeypatch):
    # the option labels vary with the session's permission mode, so the card
    # fetches them from the LIVE screen — read-only, no key pressed
    fe = _PlanFE()
    _plan_env(monkeypatch, "pl1", "51", fe)
    code, body = _post(dash + "/api/session/pl1/plan-options",
                       {"tool_use_id": "toolu_p1"})
    assert code == 200
    opts = json.loads(body)["options"]
    assert [o["label"] for o in opts] == list(_PlanFE.OPTIONS)
    assert [o["feedback"] for o in opts] == [False, False, False, True]
    assert fe.keyed == [] and fe.decided is None


def test_post_plan_decide_verifies_the_label(dash, monkeypatch):
    fe = _PlanFE()
    _plan_env(monkeypatch, "pl2", "52", fe)
    # label drift (the dialog was replaced since the page fetched options):
    # refused, nothing pressed
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/pl2/plan-decision",
              {"tool_use_id": "toolu_p1", "digit": "1",
               "label": "Yes, and auto-accept edits"})
    assert e.value.code == 409
    assert json.loads(e.value.read())["step"] == "option"
    assert fe.decided is None
    # matching label: pressed, dialog resolves
    code, body = _post(dash + "/api/session/pl2/plan-decision",
                       {"tool_use_id": "toolu_p1", "digit": "2",
                        "label": "Yes, manually approve edits"})
    assert code == 200 and json.loads(body) == {"ok": True, "kind": "decide"}
    assert fe.decided == "Yes, manually approve edits"


def test_post_plan_feedback_and_dismiss(dash, monkeypatch):
    fe = _PlanFE()
    _plan_env(monkeypatch, "pl3", "53", fe)
    code, body = _post(dash + "/api/session/pl3/plan-decision",
                       {"tool_use_id": "toolu_p1",
                        "feedback": "shorter\nplease"})
    assert code == 200 and json.loads(body)["kind"] == "feedback"
    # newlines collapse — the row is a single-line editor and a raw CR
    # mid-text would submit early
    assert fe.fb == "shorter please"
    # a second dialog: dismiss = the TUI's own Esc reject
    fe2 = _PlanFE()
    _plan_env(monkeypatch, "pl4", "54", fe2)
    code, body = _post(dash + "/api/session/pl4/plan-decision",
                       {"tool_use_id": "toolu_p1", "dismiss": True})
    assert code == 200 and json.loads(body)["kind"] == "dismiss"
    assert fe2.decided == "esc"


def test_post_plan_guards_and_open_bail_heals(dash, monkeypatch):
    fe = _PlanFE()
    _plan_env(monkeypatch, "pl5", "55", fe)
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/pl5/plan-decision",
              {"tool_use_id": "toolu_STALE", "dismiss": True})
    assert e.value.code == 409
    assert "expired" in json.loads(e.value.read())["error"]
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/pl5/plan-decision",
              {"tool_use_id": "toolu_p1"})
    assert e.value.code == 400
    # dialog resolved in the terminal → `open` bail 409 AND the stash is
    # self-healed so the page's card clears on the next SSE tick
    fe.open = False
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/pl5/plan-options",
              {"tool_use_id": "toolu_p1"})
    assert e.value.code == 409
    assert json.loads(e.value.read())["step"] == "open"
    assert S.kv_at(DS.P.state_db(DS.P.mirror_log("pl5")),
                   "plan-pending") is None
    # …and with the stash gone the next call is a clean "no pending plan"
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/pl5/plan-options",
              {"tool_use_id": "toolu_p1"})
    assert "no pending" in json.loads(e.value.read())["error"]


# the real captured plan dialog (live session 2026-07-18) the parsers pin
_PLAN_SCREEN = """\
   Here is Claude's plan:
  ╌╌╌╌╌╌╌╌
   Plan: Create /private/tmp/plan-test.txt

   Steps

   1. Write /private/tmp/plan-test.txt with the content PLANNED.
   2. Verify with cat /private/tmp/plan-test.txt.
  ╌╌╌╌╌╌╌╌

   Claude has written up a plan and is ready to execute. Would you like to proceed?

   ❯ 1. Yes, and bypass permissions
     2. Yes, manually approve edits
     3. No, refine with Ultraplan on Claude Code on the web
     4. Tell Claude what to change
        shift+tab to approve with this feedback

   ctrl+g to edit in Vim · ~/.config/plans/make-a-tiny-plan.md"""


def test_plandialog_parsers_pin_the_real_screen():
    PD = DS.plandialog
    assert PD.dialog_open(_PLAN_SCREEN)
    rs = PD.rows(_PLAN_SCREEN)
    # the plan's own numbered STEPS are above the proceed anchor — they must
    # not parse as decision rows (the region starts at the anchor)
    assert [(r["digit"], r["label"], r["feedback"]) for r in rs] == [
        ("1", "Yes, and bypass permissions", False),
        ("2", "Yes, manually approve edits", False),
        ("3", "No, refine with Ultraplan on Claude Code on the web", False),
        ("4", "Tell Claude what to change", True)]
    assert [r["cursor"] for r in rs] == [True, False, False, False]
    assert not PD.dialog_open("❯ composer\n  -- INSERT --")


def test_post_message_clear_draft_kills_then_pastes(dash, monkeypatch):
    # resending an edited message after a mid-turn cancel-edit: the TUI still
    # holds the restored draft, so clear_draft kills the line (ctrl+u to
    # start + ctrl+k to end) and delivers the text as a BRACKETED PASTE
    # (paste_text) — a raw send here drops leading bytes (the measured mangle)
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setattr(DS, "DRAFT_CLEAR_GAP_S", 0)
    monkeypatch.setenv("KITTY_WINDOW_ID", "71")
    A.session_start({"session_id": "cd1", "cwd": "/w", "transcript_path": ""})
    code, body = _post(dash + "/api/session/cd1/message",
                       {"text": "edited message", "clear_draft": True})
    assert code == 200 and json.loads(body)["ok"] is True
    assert fe.keyed == [("71", ("ctrl+u",)), ("71", ("ctrl+k",))]
    assert fe.pasted == [("71", "edited message")]    # atomic paste, not send
    assert fe.sent == []
    # a normal send also pastes (atomic), but with NO kill keys first
    fe.keyed.clear(); fe.pasted.clear()
    _post(dash + "/api/session/cd1/message", {"text": "plain"})
    assert fe.keyed == []
    assert fe.pasted == [("71", "plain")] and fe.sent == []


def test_post_interrupt_refuses_stale_or_missing_window(dash, monkeypatch):
    # same live-tag discipline as stop/message: an Escape into a reused
    # window id would interrupt an unrelated session
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "12")
    A.session_start({"session_id": "intr2", "cwd": "/w", "transcript_path": ""})
    fe.wins["intr2"] = None                   # the claude_session tag is gone
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/intr2/interrupt", {})
    assert e.value.code == 409
    assert fe.keyed == []


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
