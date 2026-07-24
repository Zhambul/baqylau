# L0 — dashboard/: the ops→HTML presenter and the HTTP server. In-process:
# the server runs on an ephemeral 127.0.0.1 port in a thread (never through
# serve() — no singleton lock, no real port constant), session data is seeded
# through the REAL product APIs (core.ops.emit, core.state, core.audit) under
# the autouse hermetic CLAUDE_AUDIT_DIR + tmp-path mirror prefixes, exactly
# like test_l0_sessionapi.py.
import gzip
import json
import os
import shutil
import subprocess
import sys
import textwrap
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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


def test_ansi_html_osc8_unsafe_scheme_is_plain_text():
    # OSC 8 is one of the two survivors of neutralize() and op text is RAW
    # command output, so a printed `\x1b]8;;javascript:…` (or data:) must NOT
    # become a clickable href in the dashboard origin (XSS-on-click). Only
    # http(s) opens an anchor — the same gate _md_inline applies; any other
    # scheme drops to the link's plain escaped label with no <a>.
    for scheme in ("javascript:alert(1)", "data:text/html,<script>x</script>",
                   "vbscript:msgbox", "file:///etc/passwd"):
        seq = "\x1b]8;;%s\x1b\\click\x1b]8;;\x1b\\" % scheme
        h = opshtml.ansi_html(seq)
        assert "<a " not in h and "href" not in h
        assert "click" in h                       # the label still renders
        assert "javascript:" not in h and "<script>" not in h


def test_text_presentation_pins_emoji_capable_glyphs():
    # docs/dashboard.md *No emoji*: the terminal's own vocabulary contains
    # EMOJI-CAPABLE codepoints (⚠ ▶ ✉ …) that a browser missing the text glyph
    # renders from the COLOUR-emoji font. The presenter appends U+FE0E so they
    # stay monochrome — without touching the producers' audited strings.
    h = opshtml.ansi_html("⚠ audit: claude-cmd-fmt.py: ValueError: boom")
    assert h.startswith("⚠︎ audit:")
    # idempotent (a re-render never stacks selectors) and never touches a glyph
    # that has no emoji form (the app's own ◷ ❖ ◈ … vocabulary)
    assert opshtml.text_presentation(h) == h
    assert opshtml.text_presentation("◷ ❖ ◈ ◉ ✦ ⧉ ✕ 1 2 #") == "◷ ❖ ◈ ◉ ✦ ⧉ ✕ 1 2 #"
    # and it reaches the other text leaves, not just op text
    assert "▶︎" in opshtml.md_html("▶ run it")


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


def test_op_items_drop_producer_source_stamped_ops():
    # The web mirror is main-agent-only: any op carrying a producer-source
    # stamp (core/ops.py "src" — sub:/team:/codex:) never becomes a stream
    # item; unstamped (main-session / pre-stamp history) ops render as before.
    items = opshtml.op_items(
        [{"t": "label", "s": "agent hdr", "c": [1, 2, 3], "g": "s1", "src": "sub:a1"},
         {"t": "gut", "s": "agent body", "c": [1, 2, 3], "src": "team:t1"},
         {"t": "line", "s": "codex line", "src": "codex:review"},
         {"t": "line", "s": "main line"}], "k")
    assert [it["html"] for it in items] == ['<pre class="opl">main line</pre>']


def test_op_items_keep_web_flagged_stamped_ops():
    # A subagent's ⇢ prompt / ⇠ result blocks carry BOTH src and web=1
    # (core/ops.py "web"): the stamp says agent, web says surface it in the
    # main mirror anyway. They survive; the agent's other stamped ops don't.
    items = opshtml.op_items(
        [{"t": "label", "s": "a ⇢ prompt", "c": [1, 2, 3], "g": "s1",
          "src": "sub:a1", "web": 1},
         {"t": "gut", "s": "do the thing", "c": [1, 2, 3], "g": "s1",
          "src": "sub:a1", "web": 1},
         {"t": "gut", "s": "intermediate work", "c": [1, 2, 3], "src": "sub:a1"},
         {"t": "label", "s": "a ⇠ result", "c": [1, 2, 3], "g": "s2",
          "src": "sub:a1", "web": 1}], "k")
    assert [(it["g"], it["t"]) for it in items] == \
        [("s1", "label"), ("s1", "gut"), ("s2", "label")]


def test_ops_label_gut_web_field():
    # core/ops.py sets the web override only when asked; default off.
    assert O.label("h", (1, 2, 3), web=True).get("web") == 1
    assert O.gut("b", (1, 2, 3), web=True).get("web") == 1
    assert "web" not in O.label("h", (1, 2, 3))
    assert "web" not in O.gut("b", (1, 2, 3))


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


def test_md_html_pipe_table():
    h = opshtml.md_html("| Engine | WER |\n|---|---|\n| Nova-3 | ~5.3% |\n"
                        "| Whisper | ~7.4% |")
    assert '<div class="md-tbl"><table><thead>' in h
    assert "<tr><th>Engine</th><th>WER</th></tr>" in h
    assert "<tr><td>Nova-3</td><td>~5.3%</td></tr>" in h
    assert "<tr><td>Whisper</td><td>~7.4%</td></tr>" in h


def test_md_html_table_alignment_and_cells():
    # colons -> the closed class vocabulary; cells ride _md_inline (escaped,
    # emphasis works); \| is a literal pipe; ragged rows pad/truncate to the
    # header width.
    h = opshtml.md_html("| a | b | c |\n|:---:|---:|---|\n"
                        "| **x** | <script>y</script> | l \\| r | extra |\n"
                        "| short |")
    assert '<th class="ta-c">a</th>' in h and '<th class="ta-r">b</th>' in h
    assert "<th>c</th>" in h                              # left = no class
    assert '<td class="ta-c"><strong>x</strong></td>' in h
    assert "<script>" not in h and "&lt;script&gt;y&lt;/script&gt;" in h
    assert "<td>l | r</td>" in h
    assert "extra" not in h                               # truncated to 3 cols
    assert ('<tr><td class="ta-c">short</td><td class="ta-r"></td><td></td>'
            "</tr>") in h


def test_md_html_table_needs_delimiter_and_matching_width():
    # a pipe line with no delimiter row underneath stays a paragraph...
    assert "<table>" not in opshtml.md_html("a | b\nplain text")
    # ...as does a header/delimiter cell-count mismatch (the GFM rule)...
    assert "<table>" not in opshtml.md_html("| a | b |\n|---|---|---|")
    # ...and a bare --- is still an <hr>, never a table delimiter.
    assert "<hr>" in opshtml.md_html("---")


def test_md_html_table_interrupts_paragraph():
    # the two-line lookahead: a table directly under a text line must not get
    # swallowed into the paragraph; a pipe-less line ends the table.
    h = opshtml.md_html("intro line\n| a | b |\n|---|---|\n| 1 | 2 |\nafter")
    assert "<p>intro line</p>" in h and "<p>after</p>" in h
    assert "<tr><td>1</td><td>2</td></tr>" in h


def test_md_html_bare_url_autolinks():
    # a bare http(s) URL in prose becomes an anchor (label = the URL); the
    # sentence's trailing punctuation, a wrapping (…) / <…>, and a raw
    # trailing & stay prose — but a wiki-style balanced (…) path survives.
    h = opshtml.md_html("go to https://x.test/signup and sign up")
    assert ('<a href="https://x.test/signup" target="_blank" rel="noopener">'
            "https://x.test/signup</a>") in h
    h = opshtml.md_html("read https://x.test/a. then (see https://x.test/b) "
                        "or <https://x.test/c> or https://x.test/d&")
    for u in ("https://x.test/a", "https://x.test/b", "https://x.test/c",
              "https://x.test/d"):
        assert '<a href="%s"' % u in h
    assert "/a." not in h and "/b)" not in h and "/c&" not in h and "/d&" not in h
    wiki = opshtml.md_html("https://x.test/wiki/Foo_(bar)")
    assert '<a href="https://x.test/wiki/Foo_(bar)"' in wiki


def test_md_html_bare_url_emphasis_and_escaping_safe():
    # autolinked URLs are stashed before the emphasis pass, so a URL's _ / *
    # can't be chewed into <em>/<strong> — while emphasis AROUND one still
    # renders; & in a query survives as exactly ONE entity in the href.
    h = opshtml.md_html("see https://x.test/p?a=1&b=2 and **https://x.test/q__r__s**")
    assert '<a href="https://x.test/p?a=1&amp;b=2"' in h
    assert "&amp;amp;" not in h
    assert '<strong><a href="https://x.test/q__r__s"' in h and "<em>" not in h


def test_md_html_bare_url_leaves_code_and_links_alone():
    # inside a code span a URL stays literal text, and a markdown link's href
    # must not be autolinked a second time.
    h = opshtml.md_html("run `curl https://x.test/a` now")
    assert "<code>curl https://x.test/a</code>" in h and "<a " not in h
    h = opshtml.md_html("see [docs](https://x.test/d)")
    assert h.count("<a ") == 1


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


def test_msg_html_question_bubble():
    # the AskUserQuestion the transcript records: a `claude ▸ asks you` bubble
    # (no rewind ↶ — not a re-runnable prompt), options rendered as a list
    h = opshtml.msg_html("question", "Cats or dogs?\n- Cats\n- Dogs")
    assert 'class="msg question"' in h
    assert "claude ▸ asks you" in h
    assert "<li>Cats</li>" in h and "<li>Dogs</li>" in h
    assert 'class="rw"' not in h                       # no rewind affordance


def test_msg_html_answer_structured_card():
    # a submitted answer with structured qa pairs renders per-question sections
    # (header chip + question text) with the picked answer HIGHLIGHTED (.ansv),
    # NOT the flat recap markdown
    qa = [{"q": "Which fruit?", "header": "Pick", "values": ["Banana"]},
          {"q": "Which planet?", "header": "", "values": ["Mars", "Venus"]}]
    h = opshtml.msg_html("answer", "Your questions have been answered: …", "", qa)
    assert 'class="msg answer"' in h and "you ▸ answered" in h
    assert 'class="ansqa"' in h and h.count('class="ansq"') == 2
    assert "Which fruit?" in h and '<span class="ansv">Banana</span>' in h
    assert '<span class="anshdr">Pick</span>' in h
    # a multiSelect answer is SEPARATE chips, not one lumped string
    assert '<span class="ansv">Mars</span>' in h \
        and '<span class="ansv">Venus</span>' in h
    assert "Mars, Venus" not in h
    assert "<div class=\"md\">" not in h                 # structured, not flat md


def test_msg_html_answer_escapes_and_falls_back():
    # no usable pairs → falls back to the flat recap markdown (escape-first)
    h = opshtml.msg_html("answer", "answered: **x**", "", None)
    assert 'class="msg answer"' in h and "<div class=\"md\">" in h
    # a script tag in a picked answer is neutralized, never live
    qa = [{"q": "q", "header": "", "values": ["<script>alert(1)</script>"]}]
    h2 = opshtml.msg_html("answer", "recap", "", qa)
    assert "<script>" not in h2 and "&lt;script&gt;" in h2


def test_msg_html_recap_bubble():
    # Claude Code's away-summary recap: an `↩ recap` bubble (no rewind ↶ — it
    # isn't a re-runnable prompt), body rendered as markdown.
    h = opshtml.msg_html("recap", "Fixed the **bug**; next is QA.")
    assert 'class="msg recap"' in h
    assert "↩ recap" in h
    assert "<strong>bug</strong>" in h
    assert 'class="rw"' not in h                       # no rewind affordance


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


def test_tool_html_presenter_error_degrades_to_none(monkeypatch):
    # The docstring promises None on a bad shape so the caller keeps its
    # escaped-JSON fallback — a sub-presenter that RAISES (its single-owner
    # shape helpers can, on an unexpected input) must degrade to None too, not
    # propagate out of the timeline enrichment.
    def boom(*a, **k):
        raise ValueError("bad shape")
    monkeypatch.setattr(opshtml, "_read_html", boom)
    assert opshtml.tool_html("Read", {"file_path": "x.py"}) is None


def test_tool_output_html_only_bash():
    assert opshtml.tool_output_html("plain", False, "Read") is None
    h = opshtml.tool_output_html("\x1b[31mred\x1b[0m ok", False, "Bash")
    assert h is not None and "<pre class=\"oc\">" in h and "color:rgb(" in h


# ------------------------------------------------------------------ the server

@pytest.fixture
def dash(monkeypatch, tmp_path):
    monkeypatch.setattr(P, "PREFIX", str(tmp_path) + "/claude-mirror-")
    monkeypatch.setattr(P, "HISTORY_DIR", str(tmp_path / "park"))
    # the durable global prefs DB (dashboard/prefs.py reads P.DASH_PREFS_DB
    # fresh each call) — relocate so the suite never touches real ~/.claude
    monkeypatch.setattr(P, "DASH_PREFS_DB", str(tmp_path / "dash-prefs.db"))
    # composer-attachment staging (paths.UPLOADS_DIR is import-time-captured
    # under ~/.claude) — relocate so the upload endpoint never writes real home
    monkeypatch.setattr(P, "UPLOADS_DIR", str(tmp_path / "uploads"))
    # Isolate the global tab DB: core.tabs.TABDB is import-time-captured from
    # /tmp, so without this every `API.tab_states()` read (the busy-tab guards,
    # the notification watcher) sees the HOST machine's live kitty windows. A
    # rewind test using window "36" would spuriously hit a real awaiting-bg tab
    # and bail busy. Tests that need specific tab states monkeypatch
    # DS.API.tab_states directly; this just makes the default empty + hermetic.
    from core import tabs as _tabs
    monkeypatch.setattr(_tabs, "TABDB", str(tmp_path / "claude-kitty-tab.db"))
    # Hermetic default: never enumerate the REAL kitty windows from the read
    # path (that would demote test sessions to not-live when the suite runs
    # inside a live kitty session). None = "can't enumerate → keep the state-DB
    # liveness signal"; a demotion test overrides this with a controlled map.
    monkeypatch.setattr(DS.launch, "_live_windows", lambda: None)
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
    # cache-bust: the index's sub-resource URLs carry ?v=<BOOT_ID> so a restart
    # forces remote browsers/CDNs off a stale app.js/style.css
    assert ("/static/app.js?v=" + DS.BOOT_ID) in body
    assert ("/static/style.css?v=" + DS.BOOT_ID) in body
    code, _ = _get(dash + "/static/app.js")
    assert code == 200
    # the ?v= is a cache key only — the file still serves with the query present
    code, _ = _get(dash + "/static/app.js?v=" + DS.BOOT_ID)
    assert code == 200
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(dash + "/static/secret.txt")          # not on the whitelist
    assert e.value.code == 404


def test_app_js_initializes_close_state(dash):
    """Regression guard for THE "still not closing" bug: the ✕ handler does
    `S.closePend[sid] = optPending(...)` and reconcileCloses does
    `Object.keys(S.closePend)` on every sessions tick — if `closePend` is not
    initialized in the `S` state object it is `undefined`, and BOTH throw a
    TypeError ("Cannot convert undefined or null to object" / "set property of
    undefined"), the second BEFORE `closeSession` runs, so /stop never fires and
    the close silently does nothing. It shipped uninitialized once (found only
    once the js.error frontend-audit row pointed at app.js:878). A pure static
    check on the served bundle — no JS engine needed."""
    code, body = _get(dash + "/static/app.js")
    assert code == 200
    # the S state literal must declare closePend (and closing) as containers
    assert "closePend: {}" in body, "S.closePend must be initialized (see the bug)"
    assert "closing: new Set()" in body
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


def test_sessions_stats_cache_by_db_sig(dash, monkeypatch):
    """The list poll memoizes stats_at by _db_sig (DB file + -wal stat):
    repeat polls with no writes must not re-open the DB, and a product-API
    write — which may land only in the WAL, never touching the main file's
    stat — must invalidate on the next poll."""
    A.session_start({"session_id": "dashc", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("dashc")
    S.incr(log, commands=1)
    calls = []
    real = DS.API.stats_at
    monkeypatch.setattr(DS.API, "stats_at",
                        lambda p: calls.append(p) or real(p))
    row = next(r for r in _get_json(dash + "/api/sessions")
               if r["sid"] == "dashc")
    assert row["stats"].get("commands") == 1
    n = len(calls)
    assert n >= 1
    _get_json(dash + "/api/sessions")
    assert len(calls) == n             # unchanged DB → served from the memo
    S.incr(log, commands=1)            # a WAL-only write must still invalidate
    row = next(r for r in _get_json(dash + "/api/sessions")
               if r["sid"] == "dashc")
    assert row["stats"].get("commands") == 2 and len(calls) > n


def test_sessions_last_active_fallback_chain(dash, tmp_path):
    """The list card's recency chip: `last_active` is the transcript's mtime
    (the file grows on every turn), else the audit ended_at, else the state
    DB's mtime, else started_at — started_at alone read as staleness on a
    live session an hour into its work."""
    # transcript present → its mtime wins
    tr = tmp_path / "tr.jsonl"
    tr.write_text("{}\n")
    os.utime(tr, (1_000_000, 1_000_000))
    A.session_start({"session_id": "dla1", "cwd": "/w",
                     "transcript_path": str(tr)})
    # transcript gone + ended → the audit ended_at
    A.session_start({"session_id": "dla2", "cwd": "/w",
                     "transcript_path": str(tmp_path / "gone.jsonl")})
    A.session_end({"session_id": "dla2"}, "other")
    # no transcript, still open, state DB on disk → the state DB's mtime
    A.session_start({"session_id": "dla3", "cwd": "/w", "transcript_path": ""})
    S.incr(P.mirror_log("dla3"), commands=1)
    os.utime(P.state_db(P.mirror_log("dla3")), (2_000_000, 2_000_000))
    # nothing at all → started_at
    A.session_start({"session_id": "dla4", "cwd": "/w", "transcript_path": ""})

    rows = {r["sid"]: r for r in _get_json(dash + "/api/sessions")}
    assert rows["dla1"]["last_active"] == 1_000_000
    assert rows["dla2"]["last_active"] == rows["dla2"]["ended_at"] > 0
    assert rows["dla3"]["last_active"] == 2_000_000
    assert rows["dla4"]["last_active"] == rows["dla4"]["started_at"] > 0


def test_stats_active_counts_only_live_sessions(dash):
    """Stats Pulse `active` is GENUINE liveness (sessions_payload's live), NOT
    `ended_at IS NULL`. A session that died without a clean SessionEnd keeps
    ended_at=NULL in the audit corpus forever (Claude Code fires no hook on
    cancel/kill/crash, and a reboot wipes /tmp), and must NOT inflate the active
    tally past what the list page shows (docs/dashboard.md *Stats / Insights*).
    active + ended therefore no longer partitions sessions — a stranded row is
    neither."""
    # two genuinely-live sessions: an audit row + a live /tmp state DB
    for sid in ("sa1", "sa2"):
        A.session_start({"session_id": sid, "cwd": "/w", "transcript_path": ""})
        S.incr(P.mirror_log(sid), commands=1)      # creates the live state DB
    # a stranded session: audit row, ended_at NULL, but NO live state DB
    A.session_start({"session_id": "sast", "cwd": "/w", "transcript_path": ""})
    # a cleanly-ended session (SessionEnd sets ended_at; no live state DB)
    A.session_start({"session_id": "sadone", "cwd": "/w", "transcript_path": ""})
    A.session_end({"session_id": "sadone"}, "other")

    DS.lists._STATS_AGG["v"] = None                 # bypass the wall-clock memo
    win = _get_json(dash + "/api/stats")["windows"]["all"]
    assert win["sessions"] == 4
    assert win["active"] == 2      # only the two live ones, NOT the stranded row
    assert win["ended"] == 1       # only the clean SessionEnd


def test_resumable_endpoint_dir_scoped_enriched(dash, monkeypatch):
    """GET /api/resumable is the new-session resume picker's source: the
    directory's recent sessions, each enriched with the model/effort/account it
    ran under (docs/dashboard.md *Resume picker*). Directory-scoped (canon
    cwd), capped at RESUMABLE_MAX, `limit` clamped, blank cwd → []."""
    # a known account registry so the label resolves without the real
    # accounts.tsv (plugins.accounts reads ~/.config otherwise)
    monkeypatch.setattr(DS.plugins, "accounts",
                        lambda: [{"slug": "acc1", "label": "Account One",
                                  "alias": "acc1"}])
    A.session_start({"session_id": "rz1", "cwd": "/proj", "transcript_path": ""})
    A.session_start({"session_id": "rz2", "cwd": "/proj", "transcript_path": ""})
    # rz2 ran under acc1 — the account kv the statusline stashes; writing it also
    # creates the state DB _session_slug reads
    S.kv_set(P.mirror_log("rz2"), "account", {"slug": "acc1"})
    A.session_start({"session_id": "rz3", "cwd": "/other", "transcript_path": ""})

    rows = _get_json(dash + "/api/resumable?cwd=/proj")
    sids = [r["sid"] for r in rows]
    assert set(sids) == {"rz1", "rz2"}              # /other excluded (dir-scoped)
    for r in rows:
        assert set(r) >= {"sid", "title", "last_active", "live",
                          "model", "effort", "account"}
        assert set(r["account"]) == {"slug", "label"}
    by = {r["sid"]: r for r in rows}
    assert by["rz2"]["account"] == {"slug": "acc1", "label": "Account One"}
    # no stashed account → the empty-slug default
    assert by["rz1"]["account"] == {"slug": "", "label": "default"}

    # limit is clamped to [1, RESUMABLE_MAX]
    assert len(_get_json(dash + "/api/resumable?cwd=/proj&limit=1")) == 1
    assert len(_get_json(dash + "/api/resumable?cwd=/proj&limit=999")) == 2
    # a blank/unknown dir has nothing to resume
    assert _get_json(dash + "/api/resumable") == []
    assert _get_json(dash + "/api/resumable?cwd=/nope") == []


def test_resumable_search_across_history(dash):
    """?q= searches the directory's WHOLE history (title + sid), not just the
    loaded rows — the fix for 'search does not search all history'. Here we match
    on the sid substring (a title needs a transcript); a miss returns []."""
    A.session_start({"session_id": "srch-alpha-1", "cwd": "/s",
                     "transcript_path": ""})
    A.session_start({"session_id": "srch-beta-2", "cwd": "/s",
                     "transcript_path": ""})
    A.session_start({"session_id": "other-gamma", "cwd": "/s",
                     "transcript_path": ""})
    got = {r["sid"] for r in _get_json(dash + "/api/resumable?cwd=/s&q=srch")}
    assert got == {"srch-alpha-1", "srch-beta-2"}          # both srch-* match
    one = [r["sid"] for r in _get_json(dash + "/api/resumable?cwd=/s&q=beta")]
    assert one == ["srch-beta-2"]                          # narrowed to one
    assert _get_json(dash + "/api/resumable?cwd=/s&q=nomatch") == []  # a miss


def test_http_backlog_endpoint(dash):
    """/backlog is the gzip-able GET twin of the SSE fresh-connect payload —
    the same merged_backlog output ({last, mpos, oldest, items}); the page
    fetches it first and hands the cursors to the SSE, which then only
    streams increments (SSE frames are never compressed — docs/dashboard.md,
    *Lazy backlog*)."""
    A.session_start({"session_id": "dashbl", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("dashbl")
    O.emit(log, O.label("▶ one", (1, 2, 3), g="b1"),
           O.code("echo hi", g="b1"), O.gut("hi", (1, 2, 3), g="b1"))
    d = _get_json(dash + "/api/session/dashbl/backlog")
    assert d["last"] >= 3 and d["oldest"] == 0
    assert d["items"] and all("html" in it for it in d["items"])
    # the cursor contract: an SSE connected with these cursors has nothing
    # left to replay — the ops tail past `last` is empty
    tail = _get_json(dash + "/api/session/dashbl/ops?after=%d" % d["last"])
    assert tail["items"] == []


def test_live_windows_memoized_by_ttl(monkeypatch):
    """_live_windows runs ONE `kitten @ ls` per _LIVE_TTL window and serves
    the memo in between (the ~21ms subprocess was the server's largest
    recurring cost when the TTL sat under the 1s tick). Read-side only by
    design — control-plane POSTs never touch this map, they re-scan via
    fe.window_for_session at action time."""
    calls = []
    win = {"id": 7, "user_vars": {"claude_session": "sX"}}
    class FE:
        def ls(self):
            calls.append(1)
            return [{"tabs": [{"windows": [win]}]}]
        def iter_windows(self, tree=None):
            for osw in tree or self.ls():
                for t in osw.get("tabs", []):
                    for w in t.get("windows", []):
                        yield osw, t, w
    monkeypatch.setattr(DS.launch, "_frontend", lambda: FE())
    monkeypatch.setattr(DS.launch, "_LIVE_WINS", {"ts": -1e9, "val": None})
    assert DS.launch._live_windows() == {"sX": "7"}
    assert DS.launch._live_windows() == {"sX": "7"}      # within TTL → memo, no scan
    assert len(calls) == 1                        # ONE ls per TTL (tree reused)
    DS.launch._LIVE_WINS["ts"] -= DS.launch._LIVE_TTL + 1       # age the memo past the TTL
    assert DS.launch._live_windows() == {"sX": "7"}
    assert len(calls) == 2


def test_live_windows_empty_ls_is_cant_tell(monkeypatch):
    """A transient `kitten @ ls` failure surfaces as an EMPTY tree (kitten_ls
    swallows every failure into [] and never raises), which must be treated as
    can't-tell (None), NOT as an authoritative 'no live tabs'. Trusting {} on a
    hiccup demoted every running session to not-live, flashing its dashboard
    card to 'gone' while it was working."""
    class FE:
        def ls(self):
            return []                     # the swallowed-failure signature
        def iter_windows(self, tree=None):
            raise AssertionError("must not iterate an empty tree")
    monkeypatch.setattr(DS.launch, "_frontend", lambda: FE())
    monkeypatch.setattr(DS.launch, "_LIVE_WINS", {"ts": -1e9, "val": None})
    assert DS.launch._live_windows() is None            # not {} → no wrongful demotion


def _notifier_for_asking(monkeypatch, screen, delay=999):
    """A Notifier wired hermetically to one red 'asking' tab on window '9':
    controllable tab states, a fake frontend returning `screen["txt"]`, and
    every home-touching dependency (session-end / composer / mute / audit /
    payload) stubbed. Returns (n, cur, asking, sent, audited)."""
    win = "9"
    asking = next(s for s, k in DS.NOTIFY_STATES.items() if k == "asking")
    cur = {"states": {}}
    monkeypatch.setattr(DS.API, "tab_states", lambda: dict(cur["states"]))
    monkeypatch.setattr(DS, "_session_ended", lambda sid: False)
    monkeypatch.setattr(DS, "_composing", lambda sid: False)
    monkeypatch.setattr(DS.prefs, "notify_muted", lambda sid: False)
    monkeypatch.setattr(DS, "NOTIFY_TELEGRAM", True)
    monkeypatch.setattr(DS, "NOTIFY_DELAY_S", delay)
    audited = []
    monkeypatch.setattr(DS.A, "state_file",
                        lambda *a, **k: audited.append(a))

    class FE:
        def get_text(self, w, extent="screen"):
            return screen["txt"]

    n = DS.Notifier()
    n.fe = FE()
    n.winmap = {win: {"sid": "sX"}}
    n._payload = lambda kind, state, row: {
        "kind": kind, "state": state, "sid": row["sid"]}
    sent = []
    n._telegram = lambda entry, *a: sent.append(entry)
    n._webpush = lambda entry: False   # no push subscribed → Telegram is the path
    n.push = lambda ev, pl: None
    return n, cur, asking, sent, audited


def test_notify_suppressed_when_answering_dialog_at_terminal(monkeypatch):
    """A red 'asking' tab whose TERMINAL dialog region CHANGES (you typed a
    free-text answer / toggled a selection) drops the armed Telegram alert:
    answering at the keyboard moves neither the tab off red nor the transcript,
    so the dialog-region diff is the only 'I'm on it' signal."""
    screen = {"txt": "☒ Q\n❯ 1. Yes\n  2. No\nEnter to select"}
    n, cur, asking, sent, audited = _notifier_for_asking(monkeypatch, screen)
    n.scan()                                 # baseline (prev is None)
    cur["states"] = {"9": asking}
    n.scan()                                 # arm + region baseline
    assert n.pending.get("9", {}).get("ask_region")
    screen["txt"] = "☒ Q\n  1. Yes\n❯ 2. No\nEnter to select"
    n.scan()                                 # region moved → suppressed
    assert "9" not in n.pending and sent == []
    assert any(a[2] == "notify-suppress" for a in audited)
    monkeypatch.setattr(DS, "NOTIFY_DELAY_S", 0)
    n.scan()                                 # nothing left to fire
    assert sent == []


def test_notify_fires_when_dialog_untouched(monkeypatch):
    """The guard is precise: a STABLE dialog region (you walked away) still
    fires after the grace window — the baseline sighting alone never suppresses."""
    screen = {"txt": "☒ Q\n❯ 1. Yes\n  2. No\nEnter to select"}
    n, cur, asking, sent, _ = _notifier_for_asking(monkeypatch, screen, delay=0)
    n.scan()                                 # baseline
    cur["states"] = {"9": asking}
    n.scan()                                 # arm, baseline, fire (delay 0)
    assert sent and sent[0]["sid"] == "sX"


def _escalation_notifier(monkeypatch, clock):
    """A bare Notifier wired for device-first/escalation timing tests: a
    controllable monotonic `clock`, one 'done' tab on window '7', _watching off,
    _telegram/_webpush recorded by the caller (returned as (sent, pushed))."""
    monkeypatch.setattr(DS, "NOTIFY_DELAY_S", 0.0)
    monkeypatch.setattr(DS, "ESCALATE_S", 300.0)
    monkeypatch.setattr(DS, "NOTIFY_TELEGRAM", True)
    monkeypatch.setattr(DS, "NOTIFY_WEBPUSH", True)
    monkeypatch.setattr(DS, "NOTIFY_TELEGRAM_ALWAYS", False)
    monkeypatch.setattr(DS.prefs, "notify_muted", lambda sid: False)
    monkeypatch.setattr(DS.time, "monotonic", lambda: clock[0])
    n = DS.Notifier()
    monkeypatch.setattr(n, "_watching", lambda *a: None)
    n._payload = lambda kind, state, row: {
        "kind": kind, "state": state, "sid": row["sid"]}
    n.push = lambda ev, pl: None
    n.winmap = {"7": {"sid": "s7", "cwd": "/w", "transcript_path": ""}}
    return n


def test_device_push_first_then_telegram_escalation(monkeypatch):
    """Device-first, Telegram-if-ignored: after the grace the ON-DEVICE push
    fires and Telegram is held back; only if ESCALATE_S later you STILL did
    nothing with the session does the Telegram nudge fire."""
    clock = [0.0]
    n = _escalation_notifier(monkeypatch, clock)
    sent, pushed = [], []
    monkeypatch.setattr(n, "_telegram", lambda e, *a: sent.append(e))
    monkeypatch.setattr(n, "_webpush", lambda e: (pushed.append(e), True)[1])
    states = {"7": "working"}
    monkeypatch.setattr(DS.API, "tab_states", lambda: dict(states))
    n.scan()                                     # baseline
    states["7"] = "awaiting-response"
    n.scan()                                     # arm + stage1 device push
    assert len(pushed) == 1 and sent == []       # pushed to device, no Telegram yet
    assert n.pending["7"].get("notified") is not None
    clock[0] = 299
    n.scan()                                     # before escalate_at → still quiet
    assert sent == []
    clock[0] = 301
    n.scan()                                     # escalation window passed → Telegram
    assert len(sent) == 1 and "7" not in n.pending


def test_escalation_cancelled_when_you_act(monkeypatch):
    """If you act on the session (here: the tab leaves done) after the device
    push but before the escalation, the Telegram nudge NEVER fires."""
    clock = [0.0]
    n = _escalation_notifier(monkeypatch, clock)
    sent, pushed = [], []
    monkeypatch.setattr(n, "_telegram", lambda e, *a: sent.append(e))
    monkeypatch.setattr(n, "_webpush", lambda e: (pushed.append(e), True)[1])
    states = {"7": "working"}
    monkeypatch.setattr(DS.API, "tab_states", lambda: dict(states))
    n.scan()
    states["7"] = "awaiting-response"
    n.scan()                                     # stage1 device push
    assert len(pushed) == 1 and "7" in n.pending
    states["7"] = "working"                      # you answered / it resumed
    clock[0] = 500
    n.scan()                                     # cancel loop drops it, no escalation
    assert "7" not in n.pending and sent == []


def test_no_device_falls_back_to_telegram_immediately(monkeypatch):
    """With nothing to push to (_webpush → False), Telegram is the IMMEDIATE
    fallback at stage 1 — no on-device channel to escalate from."""
    clock = [0.0]
    n = _escalation_notifier(monkeypatch, clock)
    sent = []
    monkeypatch.setattr(n, "_telegram", lambda e, reason=None: sent.append(reason))
    monkeypatch.setattr(n, "_webpush", lambda e: False)
    states = {"7": "working"}
    monkeypatch.setattr(DS.API, "tab_states", lambda: dict(states))
    n.scan()
    states["7"] = "awaiting-response"
    n.scan()                                     # no device → Telegram now
    assert sent == ["no-device"] and "7" not in n.pending   # reason audited


def test_telegram_always_sends_both_at_stage1(monkeypatch):
    """CLAUDE_DASH_NOTIFY_TELEGRAM_ALWAYS forces BOTH channels at the first send
    (no escalation wait) — the opt-out of device-first/escalate."""
    clock = [0.0]
    n = _escalation_notifier(monkeypatch, clock)
    monkeypatch.setattr(DS, "NOTIFY_TELEGRAM_ALWAYS", True)
    sent, pushed = [], []
    monkeypatch.setattr(n, "_telegram", lambda e, reason=None: sent.append(reason))
    monkeypatch.setattr(n, "_webpush", lambda e: (pushed.append(e), True)[1])
    states = {"7": "working"}
    monkeypatch.setattr(DS.API, "tab_states", lambda: dict(states))
    n.scan()
    states["7"] = "awaiting-response"
    n.scan()                                     # both fire at once, no escalation
    assert len(pushed) == 1 and sent == ["always"] and "7" not in n.pending


def test_mru_push_targets_picks_most_recent_device(monkeypatch):
    """The on-device push goes to the subscriptions of the device with the
    newest presence beat — not every subscription (the whole point: one device,
    the one you're working on)."""
    subs = [{"endpoint": "https://push/mac", "keys": {}, "device": "mac"},
            {"endpoint": "https://push/ipad", "keys": {}, "device": "ipad"}]
    monkeypatch.setattr(DS.prefs, "push_subscriptions", lambda: subs)
    clock = [100.0]
    monkeypatch.setattr(DS.time, "monotonic", lambda: clock[0])
    DS._DEVICE_SEEN.clear()
    DS._mark_device("ipad")
    clock[0] = 200
    DS._mark_device("mac")                       # mac is now most-recent
    targets, decision = DS._mru_push_targets()
    assert [s["endpoint"] for s in targets] == ["https://push/mac"]
    # the decision dict feeds the notify-route audit: chosen device + every
    # candidate's presence age, so "wrong device buzzed" is answerable
    assert decision["target"] == "mac" and decision["legacy"] is False
    ages = {c["device"]: c["age_s"] for c in decision["candidates"]}
    assert ages["mac"] == 0.0 and ages["ipad"] == 100.0
    clock[0] = 300
    DS._mark_device("ipad")                      # ...and now the iPad
    assert [s["endpoint"] for s in DS._mru_push_targets()[0]] == ["https://push/ipad"]
    DS._DEVICE_SEEN.clear()


def test_mru_push_targets_legacy_untagged_sends_all(monkeypatch):
    """A subscription with no device tag (a client from before device routing)
    can't be routed, so it degrades to send-all (decision `legacy:True`) —
    nothing silently lost."""
    subs = [{"endpoint": "https://push/x", "keys": {}},
            {"endpoint": "https://push/y", "keys": {}}]
    monkeypatch.setattr(DS.prefs, "push_subscriptions", lambda: subs)
    targets, decision = DS._mru_push_targets()
    assert targets == subs and decision["legacy"] is True and decision["target"] is None


def test_webpush_audits_route_decision(monkeypatch):
    """_webpush emits a `notify-route` row naming the chosen device + every
    candidate's presence age — so a 'wrong device buzzed' is answerable from the
    DB (the whole point of the audit-coverage pass)."""
    monkeypatch.setattr(DS.webpush, "enabled", lambda: True)
    subs = [{"endpoint": "https://p/mac", "keys": {}, "device": "mac", "label": "macOS"},
            {"endpoint": "https://p/ipad", "keys": {}, "device": "ipad", "label": "iPad"}]
    monkeypatch.setattr(DS.prefs, "push_subscriptions", lambda: subs)
    DS._DEVICE_SEEN.clear()
    DS._mark_device("mac")                       # mac is the MRU device
    audited = []
    monkeypatch.setattr(DS.A, "state_file", lambda *a, **k: audited.append(a))
    n = DS.Notifier()
    n._webpush_send = lambda *a: None            # don't actually hit the network
    ok = n._webpush({"sid": "s7", "kind": "done", "title": "t", "project": "p"})
    assert ok is True
    routes = [a[3] for a in audited if a[2] == "notify-route"]
    assert len(routes) == 1
    assert routes[0]["target"] == "mac" and routes[0]["sid"] == "s7"
    ages = {c["device"]: c["age_s"] for c in routes[0]["candidates"]}
    assert set(ages) == {"mac", "ipad"} and ages["mac"] == 0.0
    DS._DEVICE_SEEN.clear()


def test_webpush_send_row_carries_device(monkeypatch):
    """Each `web-push` `send` row names the target `device`, the on-device analog
    of the route decision — so a delivery is attributable to a device."""
    class R:
        ok, gone, status = True, False, 201
    monkeypatch.setattr(DS.webpush, "send", lambda sub, payload: R())
    audited = []
    monkeypatch.setattr(DS.A, "state_file", lambda *a, **k: audited.append(a))
    n = DS.Notifier()
    n._webpush_send([{"endpoint": "https://p/mac", "keys": {}, "device": "mac"}],
                    {"sid": "s7", "kind": "done", "badge": 1})
    sends = [a[3] for a in audited if a[2] == "web-push" and a[3].get("action") == "send"]
    assert len(sends) == 1 and sends[0]["device"] == "mac"
    assert sends[0]["ok"] is True and sends[0]["endpoint"] == "https://p/mac"


def test_notify_lifecycle_audit_rows(monkeypatch):
    """The deferred lifecycle is fully audited: `notify-arm` (phase arm) on the
    transition, `notify-arm` (phase escalate) when the on-device push arms the
    Telegram nudge, and `telegram-notify` with the `reason` that explains WHY
    Telegram fired (escalation)."""
    clock = [0.0]
    n = _escalation_notifier(monkeypatch, clock)
    sent, pushed = [], []
    monkeypatch.setattr(n, "_telegram",
                        lambda e, reason=None: sent.append((e, reason)))
    monkeypatch.setattr(n, "_webpush", lambda e: (pushed.append(e), True)[1])
    audited = []
    monkeypatch.setattr(DS.A, "state_file", lambda *a, **k: audited.append((a[2], a[3])))
    states = {"7": "working"}
    monkeypatch.setattr(DS.API, "tab_states", lambda: dict(states))
    n.scan()                                     # baseline
    states["7"] = "awaiting-response"
    n.scan()                                     # arm + stage1 push + escalate-armed
    arms = [c for act, c in audited if act == "notify-arm"]
    assert any(c.get("phase") == "arm" for c in arms)
    assert any(c.get("phase") == "escalate" for c in arms)
    clock[0] = 301
    n.scan()                                     # escalation → telegram
    assert sent and sent[-1][1] == "escalation"


def _notifier_for_done(monkeypatch, screen, delay=999):
    """A Notifier wired hermetically to one green 'done' tab on window '9': a
    fake ANSI-capable frontend returning `screen["txt"]` and every home-touching
    dependency stubbed. Returns (n, cur, done, sent, audited)."""
    win = "9"
    done = next(s for s, k in DS.NOTIFY_STATES.items() if k == "done")
    cur = {"states": {}}
    monkeypatch.setattr(DS.API, "tab_states", lambda: dict(cur["states"]))
    monkeypatch.setattr(DS, "_session_ended", lambda sid: False)
    monkeypatch.setattr(DS, "_composing", lambda sid: False)
    monkeypatch.setattr(DS.prefs, "notify_muted", lambda sid: False)
    monkeypatch.setattr(DS, "NOTIFY_TELEGRAM", True)
    monkeypatch.setattr(DS, "NOTIFY_DELAY_S", delay)
    audited = []
    monkeypatch.setattr(DS.A, "state_file", lambda *a, **k: audited.append(a))

    class FE:
        focused = False               # flip to simulate the kitty tab in front

        def get_text(self, w, extent="screen", ansi=False):
            return screen["txt"]

        def tab_focused(self, w, tree=None):
            return self.focused

    n = DS.Notifier()
    n.fe = FE()
    n.winmap = {win: {"sid": "sX"}}
    n._payload = lambda kind, state, row: {
        "kind": kind, "state": state, "sid": row["sid"]}
    sent = []
    n._telegram = lambda entry, *a: sent.append(entry)
    n._webpush = lambda entry: False   # no push subscribed → Telegram is the path
    n.push = lambda ev, pl: None
    return n, cur, done, sent, audited


_DONE_RULE = "\x1b[38:2:136:136:136m" + "─" * 100


def _done_screen(input_line):
    return (_DONE_RULE + "\n" + input_line + "\n" + _DONE_RULE + "\n"
            + "\x1b[m  status line\n")


def test_notify_suppressed_when_replying_at_terminal(monkeypatch):
    """A green 'done' tab whose `❯` input box gains REAL (non-faint) text drops
    the armed Telegram alert: you typing a reply at the keyboard moves neither
    the tab off green nor the transcript, so the input-box content is the only
    'I'm continuing the conversation in the kitty tab' signal."""
    screen = {"txt": _done_screen("\x1b[m❯\xa0\x1b[22;2mghost suggestion")}
    n, cur, done, sent, audited = _notifier_for_done(monkeypatch, screen)
    n.scan()                                 # baseline (prev is None)
    cur["states"] = {"9": done}
    n.scan()                                 # arm (box holds only a ghost)
    assert "9" in n.pending
    screen["txt"] = _done_screen("\x1b[m❯\xa0my typed reply")
    n.scan()                                 # real input → suppressed
    assert "9" not in n.pending and sent == []
    assert any(a[2] == "notify-suppress"
               and a[3].get("reason") == "terminal-input" for a in audited)
    monkeypatch.setattr(DS, "NOTIFY_DELAY_S", 0)
    n.scan()                                 # nothing left to fire
    assert sent == []


def test_notify_fires_when_input_box_ghost_only(monkeypatch):
    """The 'done' guard is precise: a box holding only a FAINT ghost suggestion
    (you never touched the keyboard) still fires after the grace window — the
    pre-filled suggestion must never look like the user replying."""
    screen = {"txt": _done_screen("\x1b[m❯\xa0\x1b[22;2mghost suggestion")}
    n, cur, done, sent, _ = _notifier_for_done(monkeypatch, screen, delay=0)
    n.scan()                                 # baseline
    cur["states"] = {"9": done}
    n.scan()                                 # arm + fire (delay 0, ghost ignored)
    assert sent and sent[0]["sid"] == "sX"


def test_notify_suppressed_when_kitty_tab_focused(monkeypatch):
    """At SEND time, if the session's kitty tab is FRONTMOST on your screen
    (`Frontend.tab_focused`), the off-device alert is dropped — you're already
    looking at it. A dashboard-spawned tab in a backgrounded kitty is is_active
    but NOT is_focused, so tab_focused (which keys on is_focused) never yields a
    false suppress there; this test drives the focused=True case directly."""
    screen = {"txt": _done_screen("\x1b[m❯\xa0")}   # empty box (no input suppress)
    n, cur, done, sent, audited = _notifier_for_done(monkeypatch, screen, delay=0)
    n.fe.focused = True                      # kitty tab is frontmost
    n.scan()                                 # baseline
    cur["states"] = {"9": done}
    n.scan()                                 # arm + fire-time focus check → suppress
    assert sent == []
    assert any(a[2] == "notify-suppress"
               and a[3].get("reason") == "tab-focused" for a in audited)


def test_notify_suppressed_when_web_viewing(monkeypatch):
    """At SEND time, if a browser is actively VIEWING the session (a fresh
    /api/session/<sid>/viewing heartbeat within CLAUDE_DASH_VIEW_TTL_S), the
    off-device alert is dropped — you're watching the dashboard."""
    screen = {"txt": _done_screen("\x1b[m❯\xa0")}
    n, cur, done, sent, audited = _notifier_for_done(monkeypatch, screen, delay=0)
    DS._VIEWING.pop("sX", None)
    DS._mark_viewing("sX")                   # a fresh viewing beat
    try:
        n.scan()                             # baseline
        cur["states"] = {"9": done}
        n.scan()                             # arm + fire-time viewing check → suppress
        assert sent == []
        assert any(a[2] == "notify-suppress"
                   and a[3].get("reason") == "web-viewing" for a in audited)
    finally:
        DS._VIEWING.pop("sX", None)


def test_web_viewing_presence_expires(monkeypatch):
    """The viewing presence is TTL'd: a beat marks the sid fresh, and once the
    deadline passes (`_web_viewing` GC's it) presence is gone — so the alert
    reverts to firing when you stop watching."""
    monkeypatch.setattr(DS, "VIEW_TTL_S", 20)
    clock = [1000.0]
    monkeypatch.setattr(DS.time, "monotonic", lambda: clock[0])
    DS._VIEWING.pop("sZ", None)
    assert DS._web_viewing("sZ") is False
    DS._mark_viewing("sZ")
    assert DS._web_viewing("sZ") is True
    clock[0] += 21                            # past the TTL
    assert DS._web_viewing("sZ") is False
    assert "sZ" not in DS._VIEWING            # GC'd on the miss


def test_notify_done_suppressed_when_seen_earlier_then_left(monkeypatch):
    """The user's rule: 'if I've SEEN the final message on the dashboard, no
    notification.' A done arm is checked EVERY scan while armed (not only at
    send time), so a single glance during the grace cancels it even after you
    navigate away — you don't need to be pinged about a result you already read."""
    screen = {"txt": _done_screen("\x1b[m❯\xa0")}   # empty box, not focused
    n, cur, done, sent, audited = _notifier_for_done(monkeypatch, screen, delay=999)
    n.scan()                                  # baseline
    cur["states"] = {"9": done}
    n.scan()                                  # arm — not watching yet
    assert "9" in n.pending
    DS._VIEWING.pop("sX", None)
    DS._mark_viewing("sX")                    # you GLANCE at the final message
    n.scan()                                  # per-scan 'seen it' → dropped
    DS._VIEWING.pop("sX", None)               # the glance is over; you moved on
    assert "9" not in n.pending
    assert any(a[2] == "notify-suppress"
               and a[3].get("reason") == "web-viewing" for a in audited)
    monkeypatch.setattr(DS, "NOTIFY_DELAY_S", 0)
    n.scan()                                  # grace passes — still nothing fires
    assert sent == []


def test_notify_asking_still_fires_after_earlier_glance(monkeypatch):
    """Deliberate asymmetry vs `done`: for an ASKING arm a mere earlier glance
    does NOT suppress — seeing the question isn't answering it, so if you looked
    then walked away without answering, the reminder must still fire. (Only
    looking RIGHT NOW at send time, or answering at the terminal, suppresses an
    ask.)"""
    screen = {"txt": "☒ Q\n❯ 1. Yes\n  2. No\nEnter to select"}
    n, cur, asking, sent, _ = _notifier_for_asking(monkeypatch, screen, delay=999)
    n.scan()                                  # baseline
    cur["states"] = {"9": asking}
    n.scan()                                  # arm
    assert "9" in n.pending
    DS._VIEWING.pop("sX", None)
    DS._mark_viewing("sX")                    # you GLANCE at the ask on the dashboard
    n.scan()                                  # NOT cancelled — asking ignores a glance
    DS._VIEWING.pop("sX", None)               # ...and you leave without answering
    assert "9" in n.pending
    monkeypatch.setattr(DS, "NOTIFY_DELAY_S", 0)
    n.scan()                                  # send time, not looking now → fires
    assert sent and sent[0]["sid"] == "sX"


def _pump_global(r, got):
    """Collect (event, data) frames from a global-SSE response into `got`."""
    def pump():
        pending = None
        try:
            for raw in r:
                line = raw.decode("utf-8", "replace").rstrip("\n")
                if line.startswith("event: "):
                    pending = line[len("event: "):]
                elif line.startswith("data: ") \
                        and pending in ("sessions", "sessions-delta"):
                    got.append((pending, line[len("data: "):]))
        except Exception:
            pass                               # stream torn down by r.close()
    threading.Thread(target=pump, daemon=True).start()


def test_global_sse_diff_is_paused_blind(dash, monkeypatch):
    """The global stream's per-row change detection (_row_key) ignores
    stats['paused'] — the scorebar's ~1/s awaiting-pause accumulator made the
    snapshot differ on EVERY tick, forcing a full resend + client list
    re-render per second on an idle dashboard. A paused-only bump must push
    NOTHING (no snapshot, no delta); a real change must — and its row still
    carries the exact paused value (only the DIFF is paused-blind)."""
    import time
    monkeypatch.setattr(DS, "GLOBAL_TICK_S", 0.05)
    A.session_start({"session_id": "dashg", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("dashg")
    S.incr(log, commands=1)
    got = []
    r = _req(dash + "/events")
    _pump_global(r, got)
    try:
        wait_until(lambda: len(got) == 1, desc="initial sessions snapshot")
        assert got[0][0] == "sessions"
        S.incr(log, paused=1.25)               # the scorebar's awaiting bump
        time.sleep(0.5)                        # many ticks — must stay silent
        assert len(got) == 1
        S.incr(log, commands=1)                # a real change still pushes
        wait_until(lambda: len(got) >= 2, desc="push after a real change")
        ev, data = got[-1]
        rows = json.loads(data)
        rows = rows["rows"] if ev == "sessions-delta" else rows
        row = next(x for x in rows if x["sid"] == "dashg")
        assert row["stats"].get("commands") == 2
        assert row["stats"].get("paused") == 1.25
    finally:
        r.close()


def test_global_sse_delta_and_resync(dash, monkeypatch):
    """The wire protocol (docs/dashboard.md, *The list renders once, then
    patches*): a content-only change rides a `sessions-delta` carrying ONLY
    the changed rows; a membership change (new session) forces a full
    `sessions` resync — a delta can't express insertion. Wire rows are
    stripped of the server-side paths (`transcript_path`, `log`) on both the
    SSE and /api/sessions."""
    monkeypatch.setattr(DS, "GLOBAL_TICK_S", 0.05)
    A.session_start({"session_id": "dashd1", "cwd": "/w",
                     "transcript_path": "/w/t1.jsonl"})
    A.session_start({"session_id": "dashd2", "cwd": "/w",
                     "transcript_path": "/w/t2.jsonl"})
    for row in _get_json(dash + "/api/sessions"):
        assert "transcript_path" not in row and "log" not in row
    got = []
    r = _req(dash + "/events")
    _pump_global(r, got)
    try:
        wait_until(lambda: len(got) == 1, desc="initial snapshot")
        S.incr(P.mirror_log("dashd1"), commands=1)     # content-only change
        # the DB-file creation and the counter commit can land on different
        # ticks (two deltas) — wait for the delta that carries the value
        def delta_rows():
            for ev, data in got[1:]:
                if ev == "sessions-delta":
                    rows = json.loads(data)["rows"]
                    row = next((x for x in rows if x["sid"] == "dashd1"), None)
                    if row and row["stats"].get("commands") == 1:
                        return rows
            return None
        wait_until(lambda: delta_rows() is not None,
                   desc="delta carrying the row change")
        rows = delta_rows()
        assert [x["sid"] for x in rows] == ["dashd1"]  # ONLY the changed row
        assert "transcript_path" not in rows[0] and "log" not in rows[0]
        assert all(ev == "sessions-delta" for ev, _ in got[1:])  # no resyncs
        n = len(got)
        A.session_start({"session_id": "dashd3", "cwd": "/w",
                         "transcript_path": ""})       # membership change
        wait_until(lambda: len(got) > n, desc="resync after a new session")
        ev, data = got[-1]
        assert ev == "sessions"                        # full snapshot, not delta
        assert any(x["sid"] == "dashd3" for x in json.loads(data))
    finally:
        r.close()


def test_ops_endpoint_is_main_agent_only(dash, monkeypatch):
    """core.ops.emit stamps the producer source (ambient set_src/$CLAUDE_OPS_SRC
    or the explicit src= kwarg) and the dashboard's ops payload drops stamped
    ops — while the raw stream (what the terminal mirror paints) keeps them."""
    # Isolate the module-level ambient stamp; the env var itself is only
    # touched via monkeypatch so nothing leaks into later subprocess spawns.
    monkeypatch.setattr(O, "_SRC", None)
    monkeypatch.setattr(O, "_SRC_INIT", True)
    A.session_start({"session_id": "dashsrc", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("dashsrc")
    O.emit(log, O.label("lead header", (1, 2, 3)))            # main: unstamped
    O.emit(log, O.line("agent monitor line"), src="sub:a1")   # explicit kwarg
    monkeypatch.setattr(O, "_SRC", "team:t1")                 # ambient (set_src)
    O.emit(log, O.line("teammate line"))
    monkeypatch.setattr(O, "_SRC", None)
    # the lazy $CLAUDE_OPS_SRC read (what a spawned tailer relies on)
    monkeypatch.setenv("CLAUDE_OPS_SRC", "codex:review")
    monkeypatch.setattr(O, "_SRC_INIT", False)
    O.emit(log, O.line("codex line"))
    monkeypatch.setattr(O, "_SRC", None)
    monkeypatch.setattr(O, "_SRC_INIT", True)

    _last, raw = S.ops_after(log, 0)
    assert [op.get("src") for op in raw] == \
        [None, "sub:a1", "team:t1", "codex:review"]
    d = _get_json(dash + "/api/session/dashsrc/ops?after=0")
    assert d["last"] == 4, "stamped ops still advance the cursor"
    assert len(d["items"]) == 1 and "lead header" in d["items"][0]["html"], \
        "only the main session's op survives to the web stream"


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


def test_tasks_card_payload_and_sse(dash):
    """session_payload carries the pinned tasks card's list (the `tasks` kv
    task_fmt.py snapshots from the on-disk task dir — docs/dashboard.md, *Web
    tasks*; NOT live-gated, a parked session keeps its final list), and the
    per-session SSE announces it as a `tasks` event on the slow cadence."""
    A.session_start({"session_id": "tsk1", "cwd": "/w", "transcript_path": ""})
    tasks = [{"id": "1", "subject": "Ship it", "status": "completed",
              "blocks": [], "blockedBy": []},
             {"id": "2", "subject": "Test it", "status": "in_progress",
              "activeForm": "Testing it", "blocks": [], "blockedBy": ["1"]}]
    S.kv_set(P.mirror_log("tsk1"), "tasks", {"tasks": tasks})
    ov = _get_json(dash + "/api/session/tsk1")
    assert [t["id"] for t in ov["tasks"]] == ["1", "2"]
    assert ov["tasks"][0]["status"] == "completed"
    data = _sse_event(dash + "/events/session/tsk1?after=0&mpos=0", "tasks")
    assert data and json.loads(data)["tasks"][1]["activeForm"] == "Testing it"
    # an emptied list reads as None — the card hides
    S.kv_set(P.mirror_log("tsk1"), "tasks", {"tasks": []})
    assert _get_json(dash + "/api/session/tsk1")["tasks"] is None


def test_ask_draft_persist_payload_and_sse(dash, monkeypatch):
    """The web ask card's UNSUBMITTED selections survive a device switch (docs/
    dashboard.md, *Web ask*): POST /ask-draft writes the `ask-draft` kv (a pure
    state write — no terminal keys), the session snapshot carries `ask_draft`,
    and the per-session SSE re-broadcasts it as an `ask-draft` event so a peer
    card tracks the edits. A stale tool_use_id is refused."""
    monkeypatch.setenv("KITTY_WINDOW_ID", "55")
    A.session_start({"session_id": "adr1", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("adr1")
    qs = [{"question": "Which fruit?", "header": "Fruit",
           "options": [{"label": "Apple"}, {"label": "Banana"}],
           "multiSelect": False}]
    S.kv_set(log, "ask-pending", {"tool_use_id": "tu1", "questions": qs})
    ov = _get_json(dash + "/api/session/adr1")
    assert ov["ask"]["tool_use_id"] == "tu1" and ov["ask_draft"] is None
    # persist a selection — no terminal write, so no frontend needed
    body = {"tool_use_id": "tu1", "origin": "devA",
            "answers": [{"selected": ["Banana"], "other": ""}]}
    code, resp = _post(dash + "/api/session/adr1/ask-draft", body)
    assert code == 200 and json.loads(resp)["ok"]
    draft = S.kv_get(log, "ask-draft")
    assert draft["answers"][0]["selected"] == ["Banana"] and draft["origin"] == "devA"
    assert _get_json(dash + "/api/session/adr1")["ask_draft"]["answers"][0][
        "selected"] == ["Banana"]
    data = _sse_event(dash + "/events/session/adr1?after=0&mpos=0", "ask-draft")
    assert data and json.loads(data)["draft"]["origin"] == "devA"
    # a draft for a REPLACED/gone question is refused (409), draft untouched
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/adr1/ask-draft",
              {"tool_use_id": "OLD", "origin": "devA",
               "answers": [{"selected": [], "other": ""}]})
    assert e.value.code == 409
    assert S.kv_get(log, "ask-draft")["answers"][0]["selected"] == ["Banana"]


def test_ask_draft_tolerates_non_dict_answers(dash, monkeypatch):
    # answers is only LENGTH-validated; a non-dict element (malformed body) must
    # not reach `.get()` and raise AttributeError -> 500. It normalizes to an
    # empty selection (the old inline isinstance guard on `selected` was inert).
    A.session_start({"session_id": "adr3", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("adr3")
    qs = [{"question": "Which fruit?", "options": [{"label": "Apple"}],
           "multiSelect": False}]
    S.kv_set(log, "ask-pending", {"tool_use_id": "tu1", "questions": qs})
    code, resp = _post(dash + "/api/session/adr3/ask-draft",
                       {"tool_use_id": "tu1", "origin": "d", "answers": ["oops"]})
    assert code == 200 and json.loads(resp)["ok"]
    assert S.kv_get(log, "ask-draft")["answers"] == [{"selected": [], "other": ""}]


def test_ask_payload_carries_preamble_html(dash, monkeypatch, tmp_path):
    """The ask card shows Claude's prose LEAD-IN to the question (docs/
    dashboard.md, *Web ask*): the ask payload gains `preamble_html` — the
    preceding assistant message, rendered with the msg-bubble md_html (bold
    survives, html-escaped). Empty when the ask has no framing text."""
    monkeypatch.setenv("KITTY_WINDOW_ID", "77")
    tr = tmp_path / "pre.jsonl"
    tr.write_text("".join(json.dumps(o) + "\n" for o in [
        {"type": "assistant", "message": {"id": "m1", "content": [
            {"type": "text", "text": "Two **separate** problems here."}]}},
        {"type": "assistant", "message": {"id": "m2", "content": [
            {"type": "tool_use", "id": "tuX", "name": "AskUserQuestion",
             "input": {"questions": [
                 {"question": "Which?", "options": [{"label": "A"}]}]}}]}},
    ]), encoding="utf-8")
    A.session_start({"session_id": "pre1", "cwd": "/w",
                     "transcript_path": str(tr)})
    log = P.mirror_log("pre1")
    qs = [{"question": "Which?", "options": [{"label": "A"}]}]
    S.kv_set(log, "ask-pending", {"tool_use_id": "tuX", "questions": qs})
    ask = _get_json(dash + "/api/session/pre1")["ask"]
    assert ask["tool_use_id"] == "tuX"
    assert "<strong>separate</strong>" in ask["preamble_html"]
    assert "Two" in ask["preamble_html"]
    # a question whose tool_use_id has no framing text -> empty, never absent
    S.kv_set(log, "ask-pending", {"tool_use_id": "gone", "questions": qs})
    ask2 = _get_json(dash + "/api/session/pre1")["ask"]
    assert ask2["preamble_html"] == ""


def test_composer_draft_persist_payload_and_sse(dash, monkeypatch):
    """The web composer's UNSENT message survives a device switch / reopen /
    return-to-session (docs/dashboard.md, *Web composer draft*): POST
    /composer-draft writes the `composer-draft` kv (a pure state write — no
    terminal keys), the session snapshot carries `composer_draft`, and the
    per-session SSE re-broadcasts it as a `composer-draft` event so a peer box
    tracks the edits. An emptied box deletes the stash (the card clears)."""
    monkeypatch.setenv("KITTY_WINDOW_ID", "56")
    A.session_start({"session_id": "cdr1", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("cdr1")
    O.emit(log, O.label("hi", (1, 2, 3)))    # a live session has a state DB
    assert _get_json(dash + "/api/session/cdr1")["composer_draft"] is None
    # persist a half-typed message — no terminal write, so no frontend needed
    code, resp = _post(dash + "/api/session/cdr1/composer-draft",
                       {"text": "half a thought", "origin": "devA"})
    assert code == 200 and json.loads(resp)["ok"]
    draft = S.kv_get(log, "composer-draft")
    assert draft["text"] == "half a thought" and draft["origin"] == "devA"
    assert _get_json(dash + "/api/session/cdr1")["composer_draft"]["text"] \
        == "half a thought"
    data = _sse_event(dash + "/events/session/cdr1?after=0&mpos=0",
                      "composer-draft")
    assert data and json.loads(data)["draft"]["origin"] == "devA"
    # an emptied / whitespace-only box clears the stash → payload None (the
    # kv keeps an empty-text TOMBSTONE so a later stale seq can be rejected —
    # _composer_draft reads a tombstone as None either way)
    code, _ = _post(dash + "/api/session/cdr1/composer-draft",
                    {"text": "   ", "origin": "devA"})
    assert code == 200
    assert ((S.kv_get(log, "composer-draft") or {}).get("text") or "") == ""
    assert _get_json(dash + "/api/session/cdr1")["composer_draft"] is None


def test_composer_draft_stale_seq_ignored(dash, monkeypatch):
    """The clear-on-send must win over a debounced save that races it over a
    slow link (docs/dashboard.md, *Web composer draft*; the "draft didn't clear
    after send" report, 2026-07-19). Each write carries a wall-clock `seq`; a
    write OLDER than what's stored is dropped, and the clear keeps a seq'd
    tombstone so a late straggler can't resurrect the just-sent draft."""
    monkeypatch.setenv("KITTY_WINDOW_ID", "57")
    A.session_start({"session_id": "cds1", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("cds1")
    O.emit(log, O.label("hi", (1, 2, 3)))
    # the clear (seq 100) lands first, then a stale save (seq 90) arrives late
    _post(dash + "/api/session/cds1/composer-draft",
          {"text": "", "origin": "d", "seq": 100})
    code, resp = _post(dash + "/api/session/cds1/composer-draft",
                       {"text": "resurrected!", "origin": "d", "seq": 90})
    assert code == 200 and json.loads(resp).get("stale") is True
    assert _get_json(dash + "/api/session/cds1")["composer_draft"] is None
    # a genuinely newer save (seq 110) is honored
    code, _ = _post(dash + "/api/session/cds1/composer-draft",
                    {"text": "typed again", "origin": "d", "seq": 110})
    assert _get_json(dash + "/api/session/cds1")["composer_draft"]["text"] \
        == "typed again"


def test_composer_draft_stale_seq_atomic_under_concurrency(dash, monkeypatch):
    """The seq guard must hold when the racing writes land in two CONCURRENT
    server threads, not just in order (the ThreadingHTTPServer TOCTOU: a queued
    send's clear lost to its own in-flight debounced save because the guard's
    read and its write straddled the peer thread's write, 2026-07-22). The
    higher-seq CLEAR must always win regardless of which thread commits last —
    the compare-and-set is one BEGIN IMMEDIATE, so the lower-seq save can never
    resurrect the just-sent draft."""
    monkeypatch.setenv("KITTY_WINDOW_ID", "59")
    A.session_start({"session_id": "cdc1", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("cdc1")
    O.emit(log, O.label("hi", (1, 2, 3)))
    url = dash + "/api/session/cdc1/composer-draft"
    # Fire the two racing writes together many times: a lower-seq SAVE (the
    # debounced draft) and a higher-seq CLEAR (the send). The clear must win
    # every round. Under the old read-then-write guard the save would sometimes
    # commit last and stick; the atomic CAS makes the invariant deterministic.
    for i in range(40):
        base = 1000 + i * 10
        # prime a stored draft older than both so neither is rejected on read
        _post(url, {"text": "old", "origin": "d", "seq": base})
        def fire(seq, text):
            _post(url, {"text": text, "origin": "d", "seq": seq})
        save = threading.Thread(target=fire, args=(base + 1, "resurrect"))
        clear = threading.Thread(target=fire, args=(base + 2, ""))
        save.start(); clear.start()
        save.join(); clear.join()
        assert _get_json(dash + "/api/session/cdc1")["composer_draft"] is None, \
            "round %d: the lower-seq save resurrected a cleared draft" % i


def test_composer_queue_persist_payload_and_sse(dash, monkeypatch):
    """The pending ⧗ queued-message chips survive a reload (docs/dashboard.md,
    *Web composer queue*; the "gone even from the queue after refresh" report):
    POST /composer-queue writes the `composer-queue` kv (a pure state write —
    no terminal keys), the snapshot carries `composer_queue`, and the SSE
    re-broadcasts it. An empty list deletes the stash."""
    monkeypatch.setenv("KITTY_WINDOW_ID", "58")
    A.session_start({"session_id": "cq1", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("cq1")
    O.emit(log, O.label("hi", (1, 2, 3)))
    assert _get_json(dash + "/api/session/cq1")["composer_queue"] is None
    code, resp = _post(dash + "/api/session/cq1/composer-queue",
                       {"items": [{"text": "do X"}, {"text": "then Y"}],
                        "origin": "devA"})
    assert code == 200 and json.loads(resp)["ok"]
    q = _get_json(dash + "/api/session/cq1")["composer_queue"]
    assert [it["text"] for it in q["items"]] == ["do X", "then Y"]
    data = _sse_event(dash + "/events/session/cq1?after=0&mpos=0",
                      "composer-queue")
    assert data and json.loads(data)["queue"]["origin"] == "devA"
    # an empty list (all delivered / hidden) deletes the stash → payload None
    code, _ = _post(dash + "/api/session/cq1/composer-queue",
                    {"items": [], "origin": "devA"})
    assert code == 200
    assert _get_json(dash + "/api/session/cq1")["composer_queue"] is None


def test_composer_queue_tolerates_non_string_text(dash, monkeypatch):
    # a non-string `text` (malformed body) must not raise AttributeError on
    # .strip() -> 500; both the filter and the value str() it. A number stays a
    # chip (its str), a falsy 0 drops out.
    A.session_start({"session_id": "cq2", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("cq2")
    O.emit(log, O.label("hi", (1, 2, 3)))     # materialize the state DB
    code, resp = _post(dash + "/api/session/cq2/composer-queue",
                       {"items": [{"text": 5}, {"text": "real"}, {"text": 0},
                                  "notadict"], "origin": "d"})
    assert code == 200 and json.loads(resp)["ok"]
    q = S.kv_get(log, "composer-queue")
    assert [it["text"] for it in q["items"]] == ["5", "real"]


def test_composer_queue_reconciles_delivered_chips(dash, monkeypatch, tmp_path):
    """A ⧗ chip whose message has ALREADY been delivered is reconciled out of
    the snapshot server-side (docs/dashboard.md, *Web composer queue*; the "still
    shows as queued after it was delivered" report). The client-side drain only
    matches NEW stream items, so a chip persisted by a client that then closed /
    reloaded before delivery re-seeded from the kv forever — buildQueueBar found
    the delivered prompt already in the backlog with no fresh item to drain it.
    `_composer_queue` now drops any chip whose prompt appears among the
    transcript's delivered prompts (exact, or the tolerant attachment-prefix
    match `@path\\n<text>`), while a still-pending chip survives."""
    tr = tmp_path / "cq.jsonl"
    tr.write_text("".join(json.dumps(o) + "\n" for o in [
        # two DELIVERED queued messages (the TUI's queued_command attachment,
        # surfaced as prompts) — one plain, one with a leading @path mention.
        {"type": "attachment", "attachment": {
            "type": "queued_command", "commandMode": "prompt",
            "prompt": "deliver me"}},
        {"type": "attachment", "attachment": {
            "type": "queued_command", "commandMode": "prompt",
            "prompt": "@img.png\nwith attach"}},
    ]), encoding="utf-8")
    A.session_start({"session_id": "cq3", "cwd": "/w",
                     "transcript_path": str(tr)})
    log = P.mirror_log("cq3")
    O.emit(log, O.label("hi", (1, 2, 3)))     # materialize the state DB
    S.kv_set(log, "composer-queue", {"items": [
        {"text": "deliver me"},        # exact match -> reconciled out
        {"text": "with attach"},       # @path-prefix match -> reconciled out
        {"text": "still pending"},     # not delivered -> survives
    ], "origin": "devA"})
    q = _get_json(dash + "/api/session/cq3")["composer_queue"]
    assert [it["text"] for it in q["items"]] == ["still pending"]
    assert q["origin"] == "devA"         # unrelated fields preserved
    # and when EVERY chip has been delivered, the payload collapses to None
    S.kv_set(log, "composer-queue", {"items": [{"text": "deliver me"}],
                                     "origin": "devA"})
    assert _get_json(dash + "/api/session/cq3")["composer_queue"] is None


def test_ns_prefs_roundtrip(dash):
    """The new-session form's last-used {cwd, model, effort} live on the backend
    now (docs/dashboard.md, *New-session prefs*) so a launch on one device
    pre-selects on the next: GET /api/ns-prefs is {} until a POST remembers a
    launch, then reads it back. model/effort are validated against the launch
    allowlists — a bad value is dropped, never stored."""
    assert _get_json(dash + "/api/ns-prefs") == {}
    code, resp = _post(dash + "/api/ns-prefs",
                       {"cwd": "/proj", "model": "opus", "effort": "high"})
    assert code == 200 and json.loads(resp)["ok"]
    assert _get_json(dash + "/api/ns-prefs") == {
        "cwd": "/proj", "model": "opus", "effort": "high"}
    # a bad effort is dropped, the good fields still persist
    _post(dash + "/api/ns-prefs",
          {"cwd": "/proj2", "model": "sonnet", "effort": "bogus"})
    assert _get_json(dash + "/api/ns-prefs") == {"cwd": "/proj2",
                                                 "model": "sonnet"}


def test_hide_dir_prefs_roundtrip_and_validation(dash):
    """Hiding a directory from the list page (docs/dashboard.md *Hidden
    directories*): POST /api/dirs/hide stamps time.time() into the durable global
    prefs store (dashboard/prefs.py — not a session or terminal write) keyed by
    the list's group key, and returns the full {group_key: hidden_at} map; GET
    /api/dirs/hidden reads it back (durable across requests). The re-appear rule
    (a session started after hidden_at un-hides the group) is client-side over
    the wire rows' started_at, so the server contract is just: stamp stored,
    served, and a non-string key refused. The EMPTY string is a VALID key — the
    'no project' aggregate group — not a bad request."""
    assert _get_json(dash + "/api/dirs/hidden") == {}
    t0 = time.time()
    code, body = _post(dash + "/api/dirs/hide", {"cwd": "/w/proj"})
    d = json.loads(body)
    assert code == 200 and d["ok"] is True
    assert d["hidden"]["/w/proj"] >= t0
    # served back over GET, durable through the store
    assert _get_json(dash + "/api/dirs/hidden")["/w/proj"] == d["hidden"]["/w/proj"]
    # a re-hide (a re-appeared group hidden again) overwrites with a NEWER stamp
    time.sleep(0.01)
    code2, body2 = _post(dash + "/api/dirs/hide", {"cwd": "/w/proj"})
    assert code2 == 200
    assert json.loads(body2)["hidden"]["/w/proj"] > d["hidden"]["/w/proj"]
    # the "" key (the projectless aggregate group) is ACCEPTED and stored
    code3, body3 = _post(dash + "/api/dirs/hide", {"cwd": ""})
    assert code3 == 200 and "" in json.loads(body3)["hidden"]
    assert "" in _get_json(dash + "/api/dirs/hidden")
    # a non-string / missing cwd IS refused (400), the store untouched
    for bad in (5, None):
        with pytest.raises(urllib.error.HTTPError) as e:
            _post(dash + "/api/dirs/hide", {"cwd": bad} if bad is not None else {})
        assert e.value.code == 400
    assert set(_get_json(dash + "/api/dirs/hidden")) == {"/w/proj", ""}


def test_prefs_mutate_map_accumulates_atomically(monkeypatch, tmp_path):
    # mutate_map is a single-transaction read-modify-write: successive mutations
    # ACCUMULATE (no lost update), and it degrades to the intended map even when
    # the store can't open. Both hide_dir and set_notify_muted ride it.
    from dashboard import prefs
    monkeypatch.setattr(P, "DASH_PREFS_DB", str(tmp_path / "prefs.db"))
    assert prefs.hide_dir("/a", 1.0) == {"/a": 1.0}
    assert prefs.hide_dir("/b", 2.0) == {"/a": 1.0, "/b": 2.0}   # /a not lost
    assert prefs.hidden_dirs() == {"/a": 1.0, "/b": 2.0}
    assert prefs.set_notify_muted("s1", True) == {"s1": True}
    assert prefs.set_notify_muted("s2", True) == {"s1": True, "s2": True}
    assert prefs.set_notify_muted("s1", False) == {"s2": True}   # un-mute deletes
    assert prefs.notify_muted("s2") is True and prefs.notify_muted("s1") is False
    # the web-rename override rides mutate_map too — sticky, per-sid, no delete
    assert prefs.set_renamed_title("sidA", "picked") == {"sidA": "picked"}
    assert prefs.set_renamed_title("sidB", "other") == {"sidA": "picked",
                                                         "sidB": "other"}
    assert prefs.renamed_title("sidA") == "picked"
    assert prefs.renamed_title("nope") == ""       # never renamed
    # degraded (unopenable store — dirname is a FILE): still returns the
    # intended map, never raises
    afile = tmp_path / "afile"
    afile.write_text("x")
    monkeypatch.setattr(P, "DASH_PREFS_DB", str(afile / "no.db"))
    assert prefs.mutate_map("k", lambda d: d.__setitem__("x", 1)) == {"x": 1}


def test_hide_dir_behind_post_guard(dash, monkeypatch):
    """The hide POST is a control-plane write like every other — a missing
    X-Claude-Dash header is rejected (403) and READONLY disables it (403)."""
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/dirs/hide", {"cwd": "/w/proj"}, header=None)
    assert e.value.code == 403
    monkeypatch.setattr(DS, "READONLY", True)
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/dirs/hide", {"cwd": "/w/proj"})
    assert e.value.code == 403


def _reject_rows():
    """The `web-reject` state_files rows (_post_guard rejections). Same
    spool-drain dance as _hint_rows."""
    import sqlite3
    A._CONN = None
    A._FAILED = False
    A._connect()
    con = sqlite3.connect(A.db_path())
    try:
        return [(p, json.loads(c)) for (p, c) in con.execute(
            "SELECT path, content FROM state_files WHERE action='web-reject' "
            "ORDER BY ts")]
    finally:
        con.close()


def test_guard_rejection_is_audited(dash, monkeypatch):
    # THE close-blind-spot fix: a control POST that fails _post_guard (a missing
    # X-Claude-Dash header) previously vanished — no audit row at all, so a
    # browser /stop that never passed the guard was invisible server-side. Now
    # every guard reject writes a `web-reject` row naming the path + reason.
    monkeypatch.setenv("KITTY_WINDOW_ID", "77")
    A.session_start({"session_id": "rej1", "cwd": "/w", "transcript_path": ""})
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/rej1/stop", {}, header=None)
    assert e.value.code == 403
    rows = _reject_rows()
    hit = [r for r in rows if r[0].endswith("/session/rej1/stop")]
    assert hit and hit[-1][1]["code"] == 403 and "header" in hit[-1][1]["why"]


def test_hide_dir_refused_when_directory_has_a_live_session(dash):
    """A directory with at least one ACTIVE (live) session can't be hidden — the
    server 409s on the SAME grouping the list uses (dir_live_sessions over
    sessions_payload), the authoritative guard behind the disabled ✕
    (docs/dashboard.md *Hidden directories*). A group with only parked / no
    sessions still hides, and the same directory becomes hideable once its
    session parks."""
    # a LIVE session in /w — its state DB exists (any writer creates it), so
    # sessions_payload reports live=True (the fixture's _live_windows→None keeps
    # the state-DB liveness signal, no window demotion)
    A.session_start({"session_id": "hidelive", "cwd": "/w", "transcript_path": ""})
    S.kv_set(P.mirror_log("hidelive"), "seed", 1)      # create the state DB → live
    # hiding /w is refused (409) and the store is left untouched
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/dirs/hide", {"cwd": "/w"})
    assert e.value.code == 409
    assert "/w" not in _get_json(dash + "/api/dirs/hidden")
    # the guard is TARGETED — a different directory with no live session still hides
    code, _ = _post(dash + "/api/dirs/hide", {"cwd": "/other"})
    assert code == 200 and "/other" in _get_json(dash + "/api/dirs/hidden")
    # park the session (its live state DB gone) → /w becomes hideable
    os.remove(P.state_db(P.mirror_log("hidelive")))
    code, body = _post(dash + "/api/dirs/hide", {"cwd": "/w"})
    assert code == 200 and "/w" in json.loads(body)["hidden"]


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


def _state_rows(action):
    """state_files rows for an action, oldest-first. Same spool-drain dance as
    _reject_rows (the audit conn is per-process; a fresh connect flushes)."""
    import sqlite3
    A._CONN = None
    A._FAILED = False
    A._connect()
    con = sqlite3.connect(A.db_path())
    try:
        return [json.loads(c) for (c,) in con.execute(
            "SELECT content FROM state_files WHERE action=? ORDER BY ts",
            (action,))]
    finally:
        con.close()


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
    # The web copy/view flows call collect()/view_payload DIRECTLY, bypassing
    # the terminal claude-copy.py entry's audit rows — so each must leave its own
    # trace (docs/dashboard.md, the web-copy/web-view schema rows).
    copies = _state_rows("web-copy")
    assert {"gid": "cg", "what": "cmd", "chars": len("echo copyme")} in copies
    assert any(c["what"] == "out" and c["chars"] == len("outline")
               for c in copies)
    views = _state_rows("web-view")
    assert {"gid": "vg", "ok": True} in views
    assert {"gid": "missing", "ok": False} in views


def _sf_rows_full(action):
    """(session_id, content) for a state_files action, oldest-first — the
    session-filing check the plain _state_rows can't make."""
    import sqlite3
    A._CONN = None
    A._FAILED = False
    A._connect()
    con = sqlite3.connect(A.db_path())
    try:
        return [(s, json.loads(c)) for (s, c) in con.execute(
            "SELECT session_id, content FROM state_files WHERE action=? "
            "ORDER BY ts", (action,))]
    finally:
        con.close()


def test_input_validation_rejects_are_audited(dash):
    """Every control-plane INPUT reject (a bad/empty body field) leaves an
    `ok:False` state_files row under the handler's OWN action, FILED UNDER THE
    SESSION — closing the silent-4xx class (`_reject_input`'s reason for being,
    now reached from the session-scoped handlers too, not just the session-less
    ones). One representative bad body per handler."""
    A.session_start({"session_id": "rj9", "cwd": "/w", "transcript_path": ""})

    def bad(path, body):
        with pytest.raises(urllib.error.HTTPError) as e:
            _post(dash + path, body)
        assert 400 <= e.value.code < 500

    base = "/api/session/rj9/"
    bad(base + "message", {"text": "   "})                  # whitespace only
    bad(base + "rename", {"name": "   "})                   # empty after strip
    bad(base + "rewind-to", {"text": "x", "mode": "nope"})  # bad mode
    bad(base + "composer-draft", {"text": 5})               # not a string
    bad(base + "composer-queue", {"items": "x"})            # not a list
    bad(base + "hint-audit", {"phase": "bogus"})            # bad phase
    bad("/api/upload", {"sid": "rj9"})                      # missing name/data
    # ask-draft's answer-count check needs a pending stash to reach it
    S.kv_set(P.mirror_log("rj9"), "ask-pending",
             {"tool_use_id": "tuZ", "questions": [{"question": "q"}]})
    bad(base + "ask-draft", {"tool_use_id": "tuZ", "answers": []})  # wrong count
    checks = {"web-send": "empty text", "web-rename": "empty name",
              "web-rewind-to": "bad mode", "composer-draft": "bad text",
              "composer-queue": "bad items", "web-hint": "bad phase",
              "web-upload": "bad fields", "ask-draft": "answer count"}
    for action, why in checks.items():
        hit = [(s, c) for (s, c) in _sf_rows_full(action)
               if c.get("ok") is False and c.get("why") == why]
        assert hit, "no audited reject for %s (%s)" % (action, why)
        assert hit[-1][0] == "rj9", \
            "%s reject not filed under sid: %r" % (action, hit[-1][0])


def test_http_monitors_endpoint(dash, tmp_path):
    """The monitors tab's data path: plugins.monitors merges the MAIN transcript
    (Monitor tool_use + its 'Monitor started (task X)' result + queue-operation
    events) with the audit streams lifecycle state (kind='monitor'). The endpoint
    returns one monitor per task with command/description/events/state; the
    session overview carries the cheap monitor_count for the tab badge."""
    tp = tmp_path / "mon-sess.jsonl"
    tp.write_text(
        json.dumps({"type": "assistant", "message": {"id": "m1", "content": [
            {"type": "tool_use", "id": "t1", "name": "Monitor",
             "input": {"command": "tail -f build.log", "description": "watch build",
                       "persistent": True}}]}}) + "\n" +
        json.dumps({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1",
             "content": "Monitor started (task mtask1, persistent — runs until…)"}]}}) + "\n" +
        json.dumps({"type": "queue-operation", "content":
                    "<task-notification>\n<task-id>mtask1</task-id>\n"
                    "<summary>Monitor event</summary>\n<event>build ok</event>\n"
                    "</task-notification>"}) + "\n" +
        json.dumps({"type": "queue-operation", "content":
                    "<task-notification>\n<task-id>mtask1</task-id>\n"
                    "<status>completed</status>\n"
                    "<summary>Monitor \"watch build\" stream ended</summary>\n"
                    "</task-notification>"}) + "\n")
    log = P.mirror_log("mons1")
    A.session_start({"session_id": "mons1", "cwd": "/w", "transcript_path": str(tp)})
    rid = A.stream_start(log, "monitor", task_id="mtask1")
    A.stream_end(rid, "monitor-process-exited", lines_emitted=2)
    d = _get_json(dash + "/api/session/mons1/monitors")
    mons = d["monitors"]
    assert len(mons) == 1
    m = mons[0]
    assert m["task"] == "mtask1"
    assert m["command"] == "tail -f build.log"
    assert m["description"] == "watch build"
    assert m["persistent"] is True
    assert m["live"] is False and m["end_reason"] == "monitor-process-exited"
    assert m["event_count"] == 1            # the `event`, not the stream-ended status
    assert m["started_at"] and m["ended_at"]
    kinds = [("status" if "status" in e else "event") for e in m["events"]]
    assert kinds == ["event", "status"]
    # the session overview carries the cheap badge count (streams, no parse)
    assert _get_json(dash + "/api/session/mons1")["monitor_count"] == 1


def test_http_jobs_endpoint(dash):
    """The jobs tab's data path: sessionapi.jobs merges the audit streams state
    (kind='bg') with the command from the mirror ops copy-group, and the output
    is read from those same ops via /copy/<task>/out (a bg job's output is in the
    ops, not the transcript). The overview carries the cheap job_count badge."""
    A.session_start({"session_id": "jobs1", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("jobs1")
    # the mirror block a bg launch paints: header + command + output, group=taskId
    O.emit(log, O.label("▷ background", (211, 204, 173), g="bgt1"),
           O.code("sleep 30; echo done", g="bgt1"),
           O.gut("line one\nline two", (211, 204, 173), g="bgt1"))
    rid = A.stream_start(log, "bg", task_id="bgt1")
    A.stream_end(rid, "writer-gone", lines_emitted=2)
    d = _get_json(dash + "/api/session/jobs1/jobs")
    jobs = d["jobs"]
    assert len(jobs) == 1
    j = jobs[0]
    assert j["task"] == "bgt1"
    # the command is the ops `code` op text (bash pretty-printed — `;` → newlines)
    assert "sleep 30" in j["command"] and "echo done" in j["command"]
    assert j["live"] is False and j["end_reason"] == "writer-gone"
    assert j["started_at"] and j["ended_at"]
    # the overview carries the cheap badge count
    assert _get_json(dash + "/api/session/jobs1")["job_count"] == 1
    # the drill-down reads the job's OUTPUT from the same ops via /copy/<task>/out
    code, out = _get(dash + "/api/session/jobs1/copy/bgt1/out")
    assert code == 200 and "line one" in out and "line two" in out


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
    # the model·effort card chip rides free off the ctx probe's model id;
    # haiku has no adaptive-reasoning default and no session effort here, so
    # the card shows model-only
    assert ag["model"] == "haiku-4.5" and "effort" not in ag
    data = _sse_event(dash + "/events/session/ctxS?after=0&mpos=0", "ctx")
    assert data and json.loads(data)["ctx"]["pct"] == 50


def test_agent_card_model_effort(dash, tmp_path):
    """An agent card carries its running model (shortened) + effort — the web
    echo of the terminal mirror's op tag. An adaptive-reasoning model with no
    session effort set falls to its own default (opus-4.8 -> high); the chip
    also rides the live `agents` SSE event."""
    tp = tmp_path / "meff-main.jsonl"
    tp.write_text(json.dumps({"type": "assistant", "message": {
        "id": "m1", "model": "claude-opus-4-8",
        "usage": {"input_tokens": 1000, "output_tokens": 5}}}) + "\n")
    A.session_start({"session_id": "meffS", "cwd": "/w",
                     "transcript_path": str(tp)})
    atp = tmp_path / "agent-agM.jsonl"
    atp.write_text(json.dumps({"type": "assistant", "isSidechain": True,
                               "message": {"id": "a1", "model": "claude-opus-4-8",
                                           "usage": {"input_tokens": 40000,
                                                     "output_tokens": 9}}}) + "\n")
    A.stream_start(P.mirror_log("meffS"), "subagent", agent_id="agM",
                   src_path=str(atp))
    ov = _get_json(dash + "/api/session/meffS")
    ag = next(a for a in ov["agents"] if a["agent_id"] == "agM")
    assert ag["model"] == "opus-4.8" and ag["effort"] == "high"
    sse = _sse_event(dash + "/events/session/meffS?after=0&mpos=0", "agents")
    assert sse
    agS = next(a for a in json.loads(sse) if a["agent_id"] == "agM")
    assert agS["model"] == "opus-4.8" and agS["effort"] == "high"


def test_git_chip_payloads(dash, tmp_path):
    """sessions rows and the overview carry the cwd's checkout state {branch,
    worktree, root, dirty}, branch/worktree read from the .git files directly
    (never a git subprocess): a main checkout resolves HEAD's ref short name,
    a linked worktree (a .git FILE pointing into .../worktrees/<name>) carries
    the worktree name PLUS root — the owning main checkout, the list page's
    grouping key (root||cwd), so worktree sessions file under their project —
    and a detached HEAD shows a 7-char sha, and a non-checkout cwd carries
    None. These synthetic .git dirs aren't real checkouts, so the dirty probe
    (`git status`, the branch chip's `*`) resolves to None = unknown — the
    degraded shape is itself the contract."""
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
    assert rows["gitA"]["git"] == {"branch": "feat/x", "worktree": None,
                                   "root": None, "dirty": None}
    assert rows["gitB"]["git"] == {"branch": "abcdef0", "worktree": "wt1",
                                   "root": str(repo), "dirty": None}
    assert rows["gitC"]["git"] is None
    ov = _get_json(dash + "/api/session/gitB")
    assert ov["git"] == {"branch": "abcdef0", "worktree": "wt1",
                         "root": str(repo), "dirty": None}
    # group_dir is the list's grouping key: the frozen start_cwd resolved to its
    # linked-worktree OWNER. A main checkout / non-checkout groups under itself;
    # a worktree groups under its owning checkout (== git.root here). start_cwd
    # itself is server-internal (it only feeds group_dir) — never on the wire.
    assert rows["gitA"]["group_dir"] == rows["gitA"]["cwd"]
    assert rows["gitB"]["group_dir"] == rows["gitB"]["git"]["root"] == str(repo)
    assert rows["gitC"]["group_dir"] == rows["gitC"]["cwd"]
    assert "start_cwd" not in rows["gitA"]
    # HEAD is re-read each call: a branch switch shows without cache eviction
    (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    rows = {r["sid"]: r for r in _get_json(dash + "/api/sessions")}
    assert rows["gitA"]["git"]["branch"] == "main"


def test_group_dir_pins_to_start_cwd(dash, tmp_path):
    """The list groups on group_dir = the session's FROZEN original cwd
    (start_cwd), NOT the live cwd — so an agent's mid-session `cd` (which
    session_paths folds into the live cwd) can't move a card between groups.
    Regression for the reported 'cd changes the main-page aggregation' bug."""
    start = tmp_path / "proj"
    start.mkdir()
    moved = tmp_path / "elsewhere"
    moved.mkdir()
    A.session_start({"session_id": "pin1", "cwd": str(start),
                     "transcript_path": ""})
    # the agent cd's: session_paths re-stamps the LIVE cwd on the next event
    A.session_paths({"session_id": "pin1", "cwd": str(moved),
                     "transcript_path": ""})
    row = {r["sid"]: r for r in _get_json(dash + "/api/sessions")}["pin1"]
    assert os.path.basename(row["cwd"]) == "elsewhere"      # live cwd followed the cd
    assert os.path.basename(row["group_dir"]) == "proj"     # group pinned to start
    assert row["cwd"] != row["group_dir"]


def test_git_dirty_marker(dash, tmp_path):
    """the git payload's dirty flag over a REAL checkout: clean -> False,
    an untracked file -> True (any `git status --porcelain` output counts,
    the status-line `*` convention). Two separate cwds because the probe is
    TTL-cached per cwd (DIRTY_TTL_S) — a same-cwd flip inside the test would
    need a TTL wait."""
    if not shutil.which("git"):
        pytest.skip("no git binary")
    env = {"HOME": str(tmp_path), "PATH": os.environ.get("PATH", "")}
    for name, mess in (("clean", False), ("dirty", True)):
        repo = tmp_path / name
        repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)],
                       check=True, env=env)
        subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t",
                        "-c", "user.name=t", "commit", "-q", "--allow-empty",
                        "-m", "seed"], check=True, env=env)
        if mess:
            (repo / "untracked.txt").write_text("x\n")
        A.session_start({"session_id": "gd-" + name, "cwd": str(repo),
                         "transcript_path": ""})
    rows = {r["sid"]: r for r in _get_json(dash + "/api/sessions")}
    assert rows["gd-clean"]["git"] == {"branch": "main", "worktree": None,
                                       "root": None, "dirty": False}
    assert rows["gd-dirty"]["git"] == {"branch": "main", "worktree": None,
                                       "root": None, "dirty": True}


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


def test_session_title_falls_back_to_slash_command(tmp_path):
    # A short slash-command session (first prompt is a <command-*> wrapper) with
    # no summary/ai-title/plain prompt gets the /command as its name instead of
    # a bare sid (docs/session-naming-findings.md, *Fallbacks*).
    from plugins.claude_code import transcript as TR
    p = _tw(tmp_path, "c1.jsonl",
            {"type": "user", "message": {"content":
             "<command-message>slack-monitor</command-message>\n"
             "<command-name>/slack-monitor</command-name>"}})
    assert TR.session_title(p) == "/slack-monitor"
    # command-args ride along when present
    q = _tw(tmp_path, "c2.jsonl",
            {"type": "user", "message": {"content":
             "<command-name>/task</command-name>\n"
             "<command-args>fix the flaky test</command-args>"}})
    assert TR.session_title(q) == "/task fix the flaky test"
    # a later plain prompt (or a summary) still WINS over the command fallback
    r = _tw(tmp_path, "c3.jsonl",
            {"type": "user", "message": {"content": "<command-name>/plugin</command-name>"}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "ok"}]}},
            {"type": "user", "message": {"content": "now do the real thing"}})
    assert TR.session_title(r) == "now do the real thing"


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


def test_conversation_surfaces_ask_answer(tmp_path):
    """An AskUserQuestion answer is a tool_result, not plain user text, so it
    landed in `blocks` and never showed in the dashboard mirror (the "my answer
    didn't appear in this session" report, 2026-07-19). It's surfaced as a
    distinct `answer` record — keyed off the toolUseResult sidecar's `answers`,
    so a Bash tool_result stays out (docs/dashboard.md, *Web ask*)."""
    from plugins.claude_code import transcript as TR
    p = _tw(tmp_path, "ans.jsonl",
            {"type": "assistant", "message": {"id": "m1", "content": [
                {"type": "tool_use", "id": "aq1", "name": "AskUserQuestion",
                 "input": {"questions": []}}]}},
            {"type": "user", "toolUseResult": {"answers": [{}], "questions": []},
             "message": {"content": [
                {"type": "tool_result", "tool_use_id": "aq1",
                 "content": 'Your questions have been answered: "Scope"='
                            '"Fix all four now".'}]}},
            # a plain Bash tool_result must NOT be surfaced (no `answers`)
            {"type": "assistant", "message": {"id": "m2", "content": [
                {"type": "tool_use", "id": "b1", "name": "Bash",
                 "input": {"command": "ls"}}]}},
            {"type": "user", "toolUseResult": {"stdout": "x"},
             "message": {"content": [
                {"type": "tool_result", "tool_use_id": "b1", "content": "x"}]}})
    recs, _ = TR.conversation(p, 0)
    kinds = [r["kind"] for r in recs]
    assert "answer" in kinds and kinds.count("answer") == 1
    ans = next(r for r in recs if r["kind"] == "answer")
    assert ans["text"].startswith("Your questions have been answered")


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


def test_http_history_negative_blocks_does_not_crash(dash):
    # a negative ?blocks made _cut_blocks return len(entries) and _snap index
    # entries[len] → IndexError → 500. Now clamped positive: a clean 200.
    ids = _blocks("hnb1", 4)
    d = _get_json(dash + "/api/session/hnb1/history?before=%d&blocks=-1" % ids[3])
    assert isinstance(d["items"], list)       # 200, not a 500 IndexError


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


def test_notifier_refires_after_empty_tab_table(monkeypatch):
    # When the tab table momentarily EMPTIES (all sessions closed), self.prev
    # becomes {}. Treating an empty prev as a fresh baseline (the old `not prev`)
    # swallowed the very next transition into red/green. Only the true first
    # scan (prev is None) is a baseline; an empty {} is a real state.
    n = DS.Notifier()
    n.winmap = {"7": {"sid": "s7", "cwd": "/w/proj",
                      "transcript_path": "/w/t.jsonl"}}
    monkeypatch.setattr(DS, "session_title", lambda p: "t" if p else "")
    q = n.register()
    seq = [{"7": "working"}, {}, {"7": "awaiting-command"}]
    monkeypatch.setattr(DS.API, "tab_states", lambda: seq.pop(0))
    n.scan()                                  # baseline (prev is None)
    n.scan()                                  # table empties -> prev == {}
    n.scan()                                  # -> asking: MUST still fire
    got = []
    while not q.empty():
        got.append(q.get_nowait())
    assert [(ev, p["kind"]) for ev, p in got] == [("notify", "asking")]
    n.unregister(q)


def test_notifier_telegram_deferred_arm_cancel_fire(monkeypatch, tmp_path):
    """The deferred off-device (Telegram) alert (docs/dashboard.md *Telegram
    alerts*): a red/green transition ARMS a pending entry; it only FIRES if the
    tab is still in that state past NOTIFY_DELAY_S (you didn't react), and it is
    CANCELLED the moment the tab leaves that state before then. Driven with a
    controllable monotonic clock so the timing is deterministic, not slept."""
    monkeypatch.setattr(P, "DASH_PREFS_DB", str(tmp_path / "prefs.db"))
    monkeypatch.setattr(DS, "NOTIFY_DELAY_S", 30.0)
    monkeypatch.setattr(DS, "NOTIFY_TELEGRAM", True)
    monkeypatch.setattr(DS, "session_title", lambda p: "t" if p else "")
    clock = [0.0]
    monkeypatch.setattr(DS.time, "monotonic", lambda: clock[0])
    sent = []
    n = DS.Notifier()
    monkeypatch.setattr(n, "_telegram", lambda entry, *a: sent.append(entry))
    n.winmap = {
        "7": {"sid": "s7", "cwd": "/w/proj", "transcript_path": "/w/t.jsonl"},
        "8": {"sid": "s8", "cwd": "/w/proj2", "transcript_path": "/w/t2.jsonl"}}
    states = {"7": "working", "8": "working"}
    monkeypatch.setattr(DS.API, "tab_states", lambda: dict(states))
    n.scan()                                   # baseline — never news
    states["7"], states["8"] = "awaiting-command", "awaiting-response"
    n.scan()                                   # both transition -> both armed
    assert sent == [] and set(n.pending) == {"7", "8"}
    clock[0] = 10.0                            # win8 reacts before the delay
    states["8"] = "working"
    n.scan()
    assert "8" not in n.pending and sent == []
    clock[0] = 40.0                            # win7 still red past the delay
    n.scan()
    assert [e["sid"] for e in sent] == ["s7"] and sent[0]["kind"] == "asking"
    assert "7" not in n.pending                # popped — fires exactly once
    n.scan()
    assert [e["sid"] for e in sent] == ["s7"]


def test_notifier_telegram_dropped_when_session_closed(monkeypatch, tmp_path):
    """Closing a session (you were satisfied and moved on) must cancel its
    pending alert even if the tab row lingers red/green: the audit `ended_at`
    is the signal, dropped in the cancel pass so nothing fires past the delay."""
    monkeypatch.setattr(P, "DASH_PREFS_DB", str(tmp_path / "prefs.db"))
    monkeypatch.setattr(DS, "NOTIFY_DELAY_S", 30.0)
    monkeypatch.setattr(DS, "NOTIFY_TELEGRAM", True)
    monkeypatch.setattr(DS, "session_title", lambda p: "t")
    clock = [0.0]
    monkeypatch.setattr(DS.time, "monotonic", lambda: clock[0])
    sent = []
    n = DS.Notifier()
    monkeypatch.setattr(n, "_telegram", lambda entry, *a: sent.append(entry))
    n.winmap = {"9": {"sid": "s9", "cwd": "/w/p", "transcript_path": "/w/t.jsonl"}}
    A.session_start({"session_id": "s9", "cwd": "/w/p", "transcript_path": ""})
    states = {"9": "working"}
    monkeypatch.setattr(DS.API, "tab_states", lambda: dict(states))
    n.scan()                                   # baseline
    states["9"] = "awaiting-response"
    n.scan()                                   # -> done, armed
    assert set(n.pending) == {"9"}
    # the user closes the session on the dashboard; the tab row lingers green
    A.session_end({"session_id": "s9"})
    clock[0] = 5.0
    n.scan()                                   # ended -> dropped before the delay
    assert "9" not in n.pending
    clock[0] = 40.0
    n.scan()
    assert sent == []                          # never fired — session was closed


def test_notifier_telegram_suppressed_while_composing(monkeypatch, tmp_path):
    """An unsent web composer draft = you're working on a reply, so the pending
    alert is cancelled (don't nag about a session you're already handling).
    Clearing the draft after that does NOT resurrect the popped alert."""
    monkeypatch.setattr(P, "DASH_PREFS_DB", str(tmp_path / "prefs.db"))
    monkeypatch.setattr(DS, "NOTIFY_DELAY_S", 30.0)
    monkeypatch.setattr(DS, "NOTIFY_TELEGRAM", True)
    monkeypatch.setattr(DS, "session_title", lambda p: "t")
    clock = [0.0]
    monkeypatch.setattr(DS.time, "monotonic", lambda: clock[0])
    draft = {"s7": {"text": "half-written reply"}}   # sid -> draft (or absent)
    monkeypatch.setattr(DS, "_composer_draft", lambda sid: draft.get(sid))
    sent = []
    n = DS.Notifier()
    monkeypatch.setattr(n, "_telegram", lambda entry, *a: sent.append(entry))
    n.winmap = {"7": {"sid": "s7", "cwd": "/w/p", "transcript_path": "/w/t.jsonl"}}
    states = {"7": "working"}
    monkeypatch.setattr(DS.API, "tab_states", lambda: dict(states))
    n.scan()                                   # baseline
    states["7"] = "awaiting-response"
    n.scan()                                   # -> done, armed
    clock[0] = 5.0
    n.scan()                                   # composing -> dropped
    assert "7" not in n.pending
    draft.clear()                              # cleared the draft (still didn't send)
    clock[0] = 40.0
    n.scan()
    assert sent == []                          # stays quiet — the entry was popped


def test_notifier_telegram_muted_and_disabled(monkeypatch, tmp_path):
    """A muted session (the ◉/○ opt-out) never fires even when it sits red past
    the delay — the mute is checked at SEND time. And CLAUDE_DASH_NOTIFY_TELEGRAM
    off (DS.NOTIFY_TELEGRAM False) arms nothing at all."""
    monkeypatch.setattr(P, "DASH_PREFS_DB", str(tmp_path / "prefs.db"))
    monkeypatch.setattr(DS, "NOTIFY_DELAY_S", 0.0)  # fire on the next scan
    monkeypatch.setattr(DS, "session_title", lambda p: "t")
    clock = [0.0]
    monkeypatch.setattr(DS.time, "monotonic", lambda: clock[0])
    sent = []
    n = DS.Notifier()
    monkeypatch.setattr(n, "_telegram", lambda entry, *a: sent.append(entry))
    n.winmap = {"7": {"sid": "s7", "cwd": "/w/p", "transcript_path": "/w/t.jsonl"}}
    states = {"7": "working"}
    monkeypatch.setattr(DS.API, "tab_states", lambda: dict(states))

    # muted -> armed but never sent
    monkeypatch.setattr(DS, "NOTIFY_TELEGRAM", True)
    DS.prefs.set_notify_muted("s7", True)
    n.scan()                                   # baseline
    states["7"] = "awaiting-command"
    n.scan()                                   # arm + immediately past delay
    assert sent == []                          # suppressed by the mute
    DS.prefs.set_notify_muted("s7", False)     # un-mute -> next fire lands

    # master switch off -> nothing even arms
    n2 = DS.Notifier()
    monkeypatch.setattr(n2, "_telegram", lambda entry, *a: sent.append(entry))
    n2.winmap = n.winmap
    monkeypatch.setattr(DS, "NOTIFY_TELEGRAM", False)
    states["7"] = "working"
    n2.scan()
    states["7"] = "awaiting-command"
    n2.scan()
    assert sent == [] and n2.pending == {}


def test_telegram_send_invokes_notify_cmd(monkeypatch, tmp_path):
    """_telegram Popens the reused notify script (CLAUDE_DASH_NOTIFY_CMD) with a
    single message argv carrying the project + deep link — the reuse of the
    global `notify` skill. A recorder script stands in for notify.py."""
    rec = tmp_path / "rec.txt"
    script = tmp_path / "recorder.py"
    script.write_text(
        "import sys, pathlib\n"
        "pathlib.Path(%r).write_text(sys.argv[1] if len(sys.argv) > 1 else '')\n"
        % str(rec))
    monkeypatch.setattr(DS, "NOTIFY_CMD", str(script))
    # the deep link points at the PUBLIC proxied origin, not the 127.0.0.1 bind
    monkeypatch.setattr(DS, "NOTIFY_URL_BASE", "https://dash.example")
    n = DS.Notifier()
    n._telegram({"kind": "done", "sid": "s9", "project": "proj", "title": "all green"})
    wait_until(rec.exists, desc="recorder ran")
    msg = rec.read_text()
    assert "proj is done" in msg and "all green" in msg
    # ?s=<sid> query param, NOT a #fragment (Telegram drops the fragment)
    assert "https://dash.example/?s=s9" in msg and "#" not in msg


def test_notify_mute_endpoint_roundtrip_and_validation(dash):
    """POST /api/session/<sid>/notify flips the per-session Telegram opt-out in
    the durable global prefs store and surfaces it in the session meta
    (`notify_muted`), live or parked; a non-bool `muted` is refused (400)."""
    A.session_start({"session_id": "nm1", "cwd": "/w", "transcript_path": ""})
    assert _get_json(dash + "/api/session/nm1")["notify_muted"] is False
    code, body = _post(dash + "/api/session/nm1/notify", {"muted": True})
    d = json.loads(body)
    assert code == 200 and d["ok"] is True and d["muted"] is True
    assert _get_json(dash + "/api/session/nm1")["notify_muted"] is True
    assert DS.prefs.notify_muted("nm1") is True
    code, body = _post(dash + "/api/session/nm1/notify", {"muted": False})
    assert json.loads(body)["muted"] is False
    assert _get_json(dash + "/api/session/nm1")["notify_muted"] is False
    for bad in (1, "yes", None):
        with pytest.raises(urllib.error.HTTPError) as e:
            _post(dash + "/api/session/nm1/notify",
                  {"muted": bad} if bad is not None else {})
        assert e.value.code == 400


def test_notify_mute_behind_post_guard(dash, monkeypatch):
    """The mute POST is a control-plane write — a missing X-Claude-Dash header
    is 403 and READONLY disables it."""
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/nm2/notify", {"muted": True}, header=None)
    assert e.value.code == 403
    monkeypatch.setattr(DS, "READONLY", True)
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/nm2/notify", {"muted": True})
    assert e.value.code == 403


def test_viewing_heartbeat_marks_presence(dash):
    """POST /api/session/<sid>/viewing (an empty body) marks the session as
    being watched — `_web_viewing` flips true — so the deferred alert can
    suppress. Behind the control-plane guard: a missing header is 403."""
    DS._VIEWING.pop("vh1", None)
    assert DS._web_viewing("vh1") is False
    code, body = _post(dash + "/api/session/vh1/viewing", {})
    assert code == 200 and json.loads(body)["ok"] is True
    assert DS._web_viewing("vh1") is True
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/vh1/viewing", {}, header=None)
    assert e.value.code == 403
    DS._VIEWING.pop("vh1", None)


def test_presence_beat_marks_device_and_viewing(dash):
    """POST /api/presence marks BOTH device presence (for on-device push
    routing) and, when a sid rides along, session viewing (for suppression).
    Behind the control-plane guard: a missing header is 403."""
    DS._DEVICE_SEEN.pop("devQ", None)
    DS._VIEWING.pop("pv1", None)
    code, body = _post(dash + "/api/presence", {"device": "devQ", "sid": "pv1"})
    assert code == 200 and json.loads(body)["ok"] is True
    assert DS._device_seen("devQ") != float("-inf")   # device recorded
    assert DS._web_viewing("pv1") is True             # session viewing recorded
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/presence", {"device": "x"}, header=None)
    assert e.value.code == 403
    DS._DEVICE_SEEN.pop("devQ", None)
    DS._VIEWING.pop("pv1", None)


def test_add_push_subscription_stores_device_and_label(monkeypatch, tmp_path):
    """A subscription is stored WITH its device id + label so the notifier can
    route to the most-recently-used device (webpush.send ignores the extras)."""
    monkeypatch.setattr(P, "DASH_PREFS_DB", str(tmp_path / "prefs.db"))
    sub = {"endpoint": "https://push/dev1", "keys": {"p256dh": "k", "auth": "a"}}
    DS.prefs.add_push_subscription(sub, device="mac-1", label="macOS")
    stored = DS.prefs.push_subscriptions()
    assert len(stored) == 1
    assert stored[0]["device"] == "mac-1" and stored[0]["label"] == "macOS"
    assert stored[0]["endpoint"] == "https://push/dev1"   # wire fields intact


class _FakeFE:
    """A usable Frontend stub capturing control-plane writes (injected via
    monkeypatching frontends.get in the server module)."""

    def __init__(self, send_ok=True, launch_ok=True):
        self.sent = []
        self.pasted = []
        self.launched = []
        self.closed = []
        self.keyed = []
        self.titled = []
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

    def get_text(self, win, extent="screen"):
        # screens pop in order; the last one sticks (a stable final state)
        if len(self.screens) > 1:
            return self.screens.pop(0)
        return self.screens[0] if self.screens else ""

    screens = ()

    def export_env(self):
        pass

    def close_tab(self, win):
        self.closed.append(win)
        return True

    def set_tab_title(self, win, title):
        self.titled.append((win, title))
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


def test_clear_clipboard_image_only_when_image(monkeypatch):
    """The clipboard-image guard empties the macOS clipboard ONLY when it holds
    an image flavor (so Claude Code can't auto-attach it to a bracketed paste,
    docs/dashboard.md *Clipboard-image guard*) — a text-only clipboard is left
    untouched, and it never runs off macOS."""
    calls = []

    def fake_run(argv, **kw):
        calls.append(argv)
        r = type("R", (), {})()
        r.stdout = fake_run.info if argv[2:3] == ["clipboard info"] else ""
        return r
    monkeypatch.setattr(DS.sys, "platform", "darwin")
    monkeypatch.setattr(DS.subprocess, "run", fake_run)
    # an image on the clipboard → detected and cleared
    fake_run.info = "«class PNGf», 70, «class utf8», 3"
    calls.clear()
    assert DS._clear_clipboard_image() is True
    assert any('set the clipboard to ""' in " ".join(c) for c in calls)
    # a text-only clipboard → left alone (no set-clipboard command issued)
    fake_run.info = "«class utf8», 12"
    calls.clear()
    assert DS._clear_clipboard_image() is False
    assert not any("set the clipboard" in " ".join(c) for c in calls)
    # off macOS → never even probes
    monkeypatch.setattr(DS.sys, "platform", "linux")
    calls.clear()
    assert DS._clear_clipboard_image() is False and calls == []


def test_post_message_runs_clipboard_guard(dash, monkeypatch):
    """A composer send empties an image clipboard BEFORE the bracketed paste (the
    fix for the spurious-screenshot bug) and still delivers the message."""
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "42")
    A.session_start({"session_id": "msgclip", "cwd": "/w", "transcript_path": ""})
    calls = []
    monkeypatch.setattr(DS, "_clear_clipboard_image",
                        lambda: calls.append(1) or True)
    code, _ = _post(dash + "/api/session/msgclip/message", {"text": "hi"})
    assert code == 200
    assert calls                                 # the clipboard-image guard ran
    assert fe.pasted == [("42", "hi")]           # …and the message still delivered


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


# ------------------------------------------------------------ attachments (uploads)

def _b64_png(b=b"\x89PNG\r\n\x1a\nfake"):
    import base64 as _b
    return _b.b64encode(b).decode()


def test_post_upload_writes_file_and_returns_path(dash, monkeypatch):
    """POST /api/upload stages the bytes under paths.UPLOADS_DIR/<sid>/ and
    hands back the absolute path (the composer's @-mention target) + an
    is_image flag for the thumbnail decision."""
    A.session_start({"session_id": "up1", "cwd": "/w", "transcript_path": ""})
    code, body = _post(dash + "/api/upload",
                       {"sid": "up1", "name": "shot.png", "mime": "image/png",
                        "data": _b64_png()})
    d = json.loads(body)
    assert code == 200 and d["ok"] and d["is_image"] is True
    assert d["path"].startswith(str(P.UPLOADS_DIR)) and os.path.isfile(d["path"])
    assert "up1" in d["path"] and d["path"].endswith("-shot.png")
    with open(d["path"], "rb") as f:
        assert f.read().startswith(b"\x89PNG")


def test_post_upload_sanitizes_traversal_name(dash):
    """A hostile filename can't escape the per-session dir — the basename is
    slugged, so `../../etc/x` lands as a plain file inside UPLOADS_DIR."""
    code, body = _post(dash + "/api/upload",
                       {"sid": "", "name": "../../etc/passwd", "mime": "text/plain",
                        "data": _b64_png(b"hi")})
    d = json.loads(body)
    assert code == 200
    assert os.path.realpath(d["path"]).startswith(os.path.realpath(str(P.UPLOADS_DIR)))
    assert "/etc/passwd" not in d["path"]


def test_post_upload_bad_base64_is_400(dash):
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/upload",
              {"name": "x.png", "mime": "image/png", "data": "not@@base64"})
    assert e.value.code == 400


def test_post_upload_over_cap_rejected(dash):
    # Content-Length past UPLOAD_MAX is rejected by the guard before any decode.
    # The guard closes the connection without draining the oversize body (the
    # _reject contract), so the client sees either a clean 413 or a reset —
    # both are "refused", which is the contract under test.
    raw = json.dumps({"name": "big", "mime": "image/png",
                      "data": "A" * (DS.UPLOAD_MAX + 10)}).encode()
    with pytest.raises((urllib.error.HTTPError, urllib.error.URLError)) as e:
        _post(dash + "/api/upload", raw=raw)
    if isinstance(e.value, urllib.error.HTTPError):
        assert e.value.code == 413


def test_post_upload_admits_body_over_post_max(dash):
    # the raised cap is the whole point: a payload well past the 64 KiB
    # control-plane POST_MAX still uploads (a real screenshot is ~MBs)
    big = _b64_png(b"\x89PNG\r\n\x1a\n" + b"x" * (DS.POST_MAX * 2))
    code, body = _post(dash + "/api/upload",
                       {"name": "big.png", "mime": "image/png", "data": big})
    assert code == 200 and os.path.isfile(json.loads(body)["path"])


def test_post_message_with_attachment_prepends_mention(dash, monkeypatch):
    """A message carrying vetted attachment paths delivers them as leading
    @-mentions ahead of the text — the TUI-native attach — over the same
    bracketed paste."""
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "51")
    A.session_start({"session_id": "att1", "cwd": "/w", "transcript_path": ""})
    _, body = _post(dash + "/api/upload",
                    {"sid": "att1", "name": "a.png", "mime": "image/png",
                     "data": _b64_png()})
    path = json.loads(body)["path"]
    code, _ = _post(dash + "/api/session/att1/message",
                    {"text": "look", "attachments": [path]})
    assert code == 200
    assert fe.pasted == [("51", "@%s\nlook" % path)]


def test_post_message_attachment_only_no_text(dash, monkeypatch):
    """A screenshot with no words is a valid message (the mention alone)."""
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "52")
    A.session_start({"session_id": "att2", "cwd": "/w", "transcript_path": ""})
    _, body = _post(dash + "/api/upload",
                    {"sid": "att2", "name": "a.png", "mime": "image/png",
                     "data": _b64_png()})
    path = json.loads(body)["path"]
    code, _ = _post(dash + "/api/session/att2/message",
                    {"text": "", "attachments": [path]})
    assert code == 200 and fe.pasted == [("52", "@" + path)]


def test_post_message_rejects_attachment_outside_uploads(dash, monkeypatch, tmp_path):
    """An @-path the server didn't stage (anywhere outside UPLOADS_DIR) is
    silently dropped — a page can't smuggle an arbitrary filesystem path into
    a mention. With no text left, that's an empty message → 400."""
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "53")
    A.session_start({"session_id": "att3", "cwd": "/w", "transcript_path": ""})
    evil = tmp_path / "secret.txt"
    evil.write_text("x")
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/att3/message",
              {"text": "", "attachments": [str(evil)]})
    assert e.value.code == 400
    assert fe.pasted == []


def test_post_new_session_prompt_carries_attachment(dash, monkeypatch):
    """The new-session launch prompt gets the @-mentions prepended too (covers
    the form AND the parked resume-&-send path)."""
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    _, body = _post(dash + "/api/upload",
                    {"name": "a.png", "mime": "image/png", "data": _b64_png()})
    path = json.loads(body)["path"]
    code, _ = _post(dash + "/api/sessions/new",
                    {"cwd": str(REPO), "prompt": "start", "attachments": [path]})
    assert code == 200
    (cwd, argv) = fe.launched[-1]
    assert ("@%s\nstart" % path) in " ".join(str(w) for w in argv)


def test_post_upload_control_plane_guarded(dash):
    """/api/upload is a control-plane write like every other — a missing
    X-Claude-Dash header is a 403."""
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/upload", {"name": "x", "mime": "image/png",
              "data": _b64_png()}, header=None)
    assert e.value.code == 403


def test_post_message_blocked_while_dialog_open(dash, monkeypatch):
    """A composer send while a modal dialog (AskUserQuestion/ExitPlanMode) is
    up would paste INTO the dialog and be lost (the "my queued message vanished
    mid ask" report, 2026-07-19) — it's refused with a 409 `modal` and NO
    paste, pointing the user at the card. Cleared once the dialog is gone."""
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "88")
    A.session_start({"session_id": "msgm", "cwd": "/w", "transcript_path": ""})
    S.kv_set(P.mirror_log("msgm"), "ask-pending",
             {"tool_use_id": "tu9", "questions": [{"question": "?",
              "options": [{"label": "A"}], "multiSelect": False}]})
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/msgm/message", {"text": "into the void"})
    assert e.value.code == 409
    assert json.loads(e.value.read())["modal"] is True
    assert fe.pasted == []                       # nothing typed into the dialog
    # dialog answered/gone → the send goes through
    S.kv_del(P.mirror_log("msgm"), "ask-pending")
    code, body = _post(dash + "/api/session/msgm/message", {"text": "now ok"})
    assert code == 200 and fe.pasted == [("88", "now ok")]


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


def _clientfail_rows(sid):
    """Read the `web-clientfail` state_files rows written to the hermetic
    in-process audit DB. A dashboard REQUEST runs in its own thread, so its
    audit write SPOOLS (the cached _CONN is bound to another thread) rather than
    hitting the DB — the same degrade path production relies on, drained by the
    next process to open the DB. Force that drain here (fresh _connect ingests
    the spool) before reading."""
    import sqlite3
    A._CONN = None
    A._FAILED = False
    A._connect()                     # drains spool.jsonl into the DB
    con = sqlite3.connect(A.db_path())
    try:
        return [json.loads(c) for (c,) in con.execute(
            "SELECT content FROM state_files WHERE session_id=? "
            "AND action='web-clientfail' ORDER BY ts", (sid,))]
    finally:
        con.close()


def test_client_fail_beacon_records_transport_and_http(dash, monkeypatch):
    """A "send failed" toast is a CLIENT-side fetch rejection the server can't
    see (it audits `web-send` + returns 200 BEFORE the response travels back —
    a lost response toasts a failure over a send that SUCCEEDED). The page
    beacons what IT saw as a `web-clientfail` row: `kind:transport` (the fetch
    itself rejected — the audit-blind case) vs `kind:http` (a server error
    status; a paired failure row exists). Audit-only: 200, no terminal writes.
    docs/dashboard.md, *Client-observed send failures*."""
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "88")
    A.session_start({"session_id": "cf1", "cwd": "/w", "transcript_path": ""})
    O.emit(P.mirror_log("cf1"), O.label("hi", (1, 2, 3)))   # materialize state DB
    # transport failure (the lost-response class): no status, kind coerced
    code, body = _post(dash + "/api/session/cf1/client-fail",
                       {"gesture": "send", "kind": "transport",
                        "error": "Failed to fetch", "chars": 10})
    assert code == 200 and json.loads(body)["ok"] is True
    # a beacon never types into the terminal
    assert fe.pasted == [] and fe.sent == []
    # http failure carries the status through
    _post(dash + "/api/session/cf1/client-fail",
          {"gesture": "resume", "kind": "http", "error": "send failed",
           "status": 502})
    rows = _clientfail_rows("cf1")
    assert rows[0] == {"gesture": "send", "kind": "transport",
                       "error": "Failed to fetch", "chars": 10}
    assert rows[1]["gesture"] == "resume" and rows[1]["kind"] == "http"
    assert rows[1]["status"] == 502


def test_client_fail_beacon_defaults_bad_kind_and_guards(dash, monkeypatch):
    """An unknown/absent `kind` defaults to `transport` (the conservative
    audit-blind reading), and the beacon is behind the control-plane POST guard
    like every write — a missing X-Claude-Dash header is a 403."""
    A.session_start({"session_id": "cf2", "cwd": "/w", "transcript_path": ""})
    O.emit(P.mirror_log("cf2"), O.label("hi", (1, 2, 3)))
    code, _ = _post(dash + "/api/session/cf2/client-fail", {"gesture": "send"})
    assert code == 200
    assert _clientfail_rows("cf2")[0]["kind"] == "transport"
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/cf2/client-fail",
              {"gesture": "send"}, header=None)
    assert e.value.code == 403


def _hint_rows(sid):
    """The `web-hint` state_files rows (the optimistic-UI lifecycle beacons).
    Same spool-drain dance as _clientfail_rows — a request-thread audit write
    spools; force the drain before reading."""
    import sqlite3
    A._CONN = None
    A._FAILED = False
    A._connect()
    con = sqlite3.connect(A.db_path())
    try:
        return [json.loads(c) for (c,) in con.execute(
            "SELECT content FROM state_files WHERE session_id=? "
            "AND action='web-hint' ORDER BY ts", (sid,))]
    finally:
        con.close()


def test_hint_audit_records_op_lifecycle(dash, monkeypatch):
    """The optimistic-UI beacon (docs/dashboard.md, *Optimistic UI & the
    web-hint audit*): every op (composer | close | answer | plan) beacons its
    shown → reconciled/dropped/stale lifecycle as `web-hint` rows so a stuck
    greyed state is debuggable. Audit-only: 200, no terminal writes, and the op
    + phase (+ optional wait_ms/reason/chars) round-trip into content."""
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    A.session_start({"session_id": "wh1", "cwd": "/w", "transcript_path": ""})
    O.emit(P.mirror_log("wh1"), O.label("hi", (1, 2, 3)))   # materialize state DB
    # composer bubble (op omitted → defaults to composer), then its reconcile
    code, body = _post(dash + "/api/session/wh1/hint-audit",
                       {"phase": "shown", "chars": 12})
    assert code == 200 and json.loads(body)["ok"] is True
    _post(dash + "/api/session/wh1/hint-audit",
          {"op": "composer", "phase": "reconciled", "chars": 12, "wait_ms": 340})
    # a card op with a dropped reason, and a close op
    _post(dash + "/api/session/wh1/hint-audit",
          {"op": "answer", "phase": "dropped", "reason": "failed"})
    _post(dash + "/api/session/wh1/hint-audit", {"op": "close", "phase": "stale"})
    # a beacon never types into the terminal
    assert fe.pasted == [] and fe.sent == []
    rows = _hint_rows("wh1")
    assert rows[0] == {"op": "composer", "phase": "shown", "chars": 12}
    assert rows[1]["op"] == "composer" and rows[1]["phase"] == "reconciled"
    assert rows[1]["wait_ms"] == 340
    assert rows[2] == {"op": "answer", "phase": "dropped", "reason": "failed"}
    assert rows[3] == {"op": "close", "phase": "stale"}


def test_hint_audit_guards_bad_op_and_phase(dash, monkeypatch):
    """A bad phase or op is a 400 that now leaves an `ok:False` web-hint reject
    row (via `_reject_input`, filed under the session — no longer a silent 4xx);
    the beacon is behind the control-plane POST guard like every write (missing
    header → 403, a `web-reject` row, NOT a web-hint one)."""
    A.session_start({"session_id": "wh2", "cwd": "/w", "transcript_path": ""})
    O.emit(P.mirror_log("wh2"), O.label("hi", (1, 2, 3)))
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/wh2/hint-audit", {"phase": "bogus"})
    assert e.value.code == 400
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/wh2/hint-audit",
              {"op": "nonsense", "phase": "shown"})
    assert e.value.code == 400
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/wh2/hint-audit",
              {"phase": "shown"}, header=None)
    assert e.value.code == 403
    # the two bad-body 400s each left an audited reject; the guard 403 did NOT
    # write a web-hint row (it's a web-reject).
    rows = _hint_rows("wh2")
    assert rows == [{"ok": False, "why": "bad phase", "phase": "'bogus'"},
                    {"ok": False, "why": "bad op", "op": "'nonsense'"}]


def _client_rows(sid):
    """The `web-client` frontend-audit rows. Same spool-drain dance as
    _hint_rows — a request-thread audit write spools; force the drain."""
    import sqlite3
    A._CONN = None
    A._FAILED = False
    A._connect()
    con = sqlite3.connect(A.db_path())
    try:
        return [json.loads(c) for (c,) in con.execute(
            "SELECT content FROM state_files WHERE session_id=? "
            "AND action='web-client' ORDER BY ts", (sid,))]
    finally:
        con.close()


def test_client_log_records_frontend_audit_batch(dash, monkeypatch):
    """The frontend audit sink (docs/dashboard.md, *Frontend audit (clientlog)*):
    a BATCH of browser events lands as one `web-client` state_files row each,
    scoped to each event's OWN sid — the ground truth a control request the
    server never saw (a tunnel-dropped /stop) leaves ONLY on the client. Each row
    keeps the event name + its scalar fields + the shared connection snapshot; a
    session-less event (a boot record, a launch) lands under sid=''. Audit-only:
    200, no terminal writes."""
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    A.session_start({"session_id": "cl1", "cwd": "/w", "transcript_path": ""})
    O.emit(P.mirror_log("cl1"), O.label("hi", (1, 2, 3)))   # materialize state DB
    body = {
        "client": "abc123",
        "device": "dev-abc",
        "conn": {"online": True, "view": "session", "es": 2, "conn": 1},
        "events": [
            {"t": 1000, "sid": "cl1", "ev": "close.begin", "via": "header", "es": 2},
            {"t": 1100, "sid": "cl1", "ev": "close.fail",
             "kind": "transport", "aborted": True, "ms": 12000},
            {"t": 1200, "sid": "cl1", "ev": "sse.drop", "s": "session"},
            # the wider vocabulary the server records generically (any ev name +
            # its scalars): a numeric-field js.error, a session-view stuck, a
            # launch latency, and a session-less boot/stale build record
            {"t": 1150, "sid": "cl1", "ev": "js.error",
             "msg": "TypeError: x", "src": "static/app.js", "line": 878, "col": 28},
            {"t": 1250, "sid": "cl1", "ev": "meta.stuck", "tries": 12},
            {"t": 1350, "sid": "", "ev": "launch.hit", "ms": 2200, "quiet": False},
            {"t": 1300, "sid": "", "ev": "boot",
             "origin": "https://baqylau.zhambyl.top", "build": "b1"},
            {"t": 1400, "sid": "", "ev": "stale", "was": "b1", "now": "b2"},
        ],
    }
    code, resp = _post(dash + "/api/clientlog", body)
    assert code == 200 and json.loads(resp)["ok"] is True
    assert fe.pasted == [] and fe.sent == []       # telemetry never types
    rows = _client_rows("cl1")
    assert [r["ev"] for r in rows] == [
        "close.begin", "close.fail", "sse.drop", "js.error", "meta.stuck"]
    assert rows[0]["via"] == "header" and rows[0]["client"] == "abc123"
    # device attribution (the frontend side of notification device-routing)
    assert rows[0]["device"] == "dev-abc"
    assert rows[0]["t"] == 1000
    assert rows[0]["conn"] == {"online": True, "view": "session", "es": 2, "conn": 1}
    assert rows[1]["kind"] == "transport" and rows[1]["aborted"] is True
    js = next(r for r in rows if r["ev"] == "js.error")
    assert js["line"] == 878 and js["col"] == 28 and js["src"] == "static/app.js"
    assert next(r for r in rows if r["ev"] == "meta.stuck")["tries"] == 12
    # session-less rows (boot with its loaded build, stale, launch) land under ''
    less = {r["ev"]: r for r in _client_rows("")}
    assert less["boot"]["origin"] == "https://baqylau.zhambyl.top"
    assert less["boot"]["build"] == "b1"
    assert less["stale"] == {"ev": "stale", "client": "abc123", "device": "dev-abc",
                             "t": 1400, "was": "b1", "now": "b2",
                             "conn": {"online": True, "view": "session",
                                      "es": 2, "conn": 1}}
    assert less["launch.hit"]["ms"] == 2200 and less["launch.hit"]["quiet"] is False


def test_client_log_caps_guards_and_sanitizes(dash, monkeypatch):
    """The sink is bounded + guarded: a non-list `events` is 400; more than
    CLIENTLOG_MAX events are truncated; non-dict / blank-`ev` events are skipped;
    string fields are capped; and it sits behind the control-plane POST guard
    (missing X-Claude-Dash header → 403)."""
    A.session_start({"session_id": "cl2", "cwd": "/w", "transcript_path": ""})
    O.emit(P.mirror_log("cl2"), O.label("hi", (1, 2, 3)))
    # a non-list events payload is a 400
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/clientlog", {"events": "nope"})
    assert e.value.code == 400
    # an oversized batch is truncated to CLIENTLOG_MAX rows
    events = [{"sid": "cl2", "ev": "spam"} for _ in range(DS.CLIENTLOG_MAX + 20)]
    code, _ = _post(dash + "/api/clientlog", {"events": events})
    assert code == 200 and len(_client_rows("cl2")) == DS.CLIENTLOG_MAX
    # junk events skipped; a long string field capped
    A.session_start({"session_id": "cl3", "cwd": "/w", "transcript_path": ""})
    O.emit(P.mirror_log("cl3"), O.label("hi", (1, 2, 3)))
    _post(dash + "/api/clientlog", {"events": [
        "not-a-dict", {"sid": "cl3", "ev": ""},        # both skipped
        {"sid": "cl3", "ev": "boot", "big": "x" * 5000}]})
    rows = _client_rows("cl3")
    assert len(rows) == 1 and rows[0]["ev"] == "boot"
    assert len(rows[0]["big"]) == DS.CLIENTLOG_STR_MAX
    # behind the control-plane guard
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/clientlog", {"events": []}, header=None)
    assert e.value.code == 403


def test_post_command_sends_slash_text(dash, monkeypatch):
    # the quick-command row types the TUI's OWN slash commands — exact text,
    # bracketed paste like the composer (never send_text). Blank screens: no
    # switch-confirm menu opens, so model/effort reply confirm="none" with no
    # key pressed
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setattr(DS.confirmdialog, "OPEN_TIMEOUT_S", 0.05)
    monkeypatch.setenv("KITTY_WINDOW_ID", "61")
    A.session_start({"session_id": "qc1", "cwd": "/w", "transcript_path": ""})
    code, body = _post(dash + "/api/session/qc1/command", {"cmd": "compact"})
    assert code == 200 and json.loads(body) == {"ok": True, "queued": False,
                                                "tab": ""}
    code, body = _post(dash + "/api/session/qc1/command",
                       {"cmd": "model", "arg": "sonnet[1m]"})
    assert code == 200 and json.loads(body)["confirm"] == "none"
    code, body = _post(dash + "/api/session/qc1/command",
                       {"cmd": "effort", "arg": "low"})
    assert code == 200 and json.loads(body)["confirm"] == "none"
    assert fe.pasted == [("61", "/compact"), ("61", "/model sonnet[1m]"),
                         ("61", "/effort low")]
    assert fe.sent == [] and fe.keyed == []


# the switch-confirm menu as the TUI paints it (observed live 2026-07-18):
# indented ❯-cursored numbered options, Yes first — but the digit is resolved
# from the labels, never assumed. (_CONFIRM_SCREEN further down in this file
# is the rewind confirm pane — a different dialog.)
_SWITCH_CONFIRM_SCREEN = """\
 Change effort level?        Your next response will be slower

 This conversation is cached for the current effort level.

 ❯ 1. Yes, switch to low
     2. No, go back
"""


def test_post_command_answers_switch_confirm_menu(dash, monkeypatch):
    # /effort opened the TUI's are-you-sure menu (the prompt-cache warning) —
    # the server presses its own Yes digit and verifies the menu closed;
    # unanswered, the web click looked dead (reported live 2026-07-18)
    fe = _FakeFE()
    fe.screens = [_SWITCH_CONFIRM_SCREEN, ""]   # menu up → gone after Yes
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "65")
    A.session_start({"session_id": "qc5", "cwd": "/w", "transcript_path": ""})
    code, body = _post(dash + "/api/session/qc5/command",
                       {"cmd": "effort", "arg": "low"})
    assert code == 200 and json.loads(body)["confirm"] == "confirmed"
    assert fe.pasted == [("65", "/effort low")]
    assert fe.keyed == [("65", ("1",))]


def test_post_command_stuck_confirm_menu_reports_failed(dash, monkeypatch):
    # the menu never closes after Yes: still 200 (the command WAS typed) but
    # confirm="failed" so the page tells the user to answer in the terminal;
    # the menu is left open — never Escaped away
    fe = _FakeFE()
    fe.screens = [_SWITCH_CONFIRM_SCREEN]       # sticks forever
    _inject_fe(monkeypatch, fe)
    monkeypatch.setattr(DS.confirmdialog, "STEP_TIMEOUT_S", 0.05)
    monkeypatch.setenv("KITTY_WINDOW_ID", "66")
    A.session_start({"session_id": "qc6", "cwd": "/w", "transcript_path": ""})
    code, body = _post(dash + "/api/session/qc6/command",
                       {"cmd": "effort", "arg": "low"})
    assert code == 200 and json.loads(body)["confirm"] == "failed"
    assert fe.keyed == [("66", ("1",))]


def test_session_detail_effort_from_settings(dash, tmp_path):
    # the effort quick-button's label: the SAVED effortLevel (every applied
    # /effort writes itself through to settings — per-session effort is
    # readable from nowhere else), resolved for the session's cwd via the
    # plugins.effort_default fan-out; here the hermetic config dir's
    # settings.json is the only layer
    cfg = os.environ["CLAUDE_CONFIG_DIR"]
    with open(os.path.join(cfg, "settings.json"), "w") as fh:
        json.dump({"effortLevel": "xhigh"}, fh)
    A.session_start({"session_id": "eff1", "cwd": str(tmp_path),
                     "transcript_path": ""})
    code, body = _get(dash + "/api/session/eff1")
    assert code == 200 and json.loads(body)["effort"] == "xhigh"


def test_session_detail_effort_per_account_config(dash, tmp_path, monkeypatch):
    # a session under a switcher account (statusline-stashed slug) resolves
    # THAT account's config dir (configs/<slug>/settings.json), not the
    # ambient one — each subscription account carries its own effortLevel
    from plugins.claude_code import account as ACC
    cfg = tmp_path / "configs" / "c9"
    cfg.mkdir(parents=True)
    (cfg / "settings.json").write_text(json.dumps({"effortLevel": "max"}))
    monkeypatch.setattr(ACC, "CONFIGS_DIR", str(tmp_path / "configs"))
    ambient = os.environ["CLAUDE_CONFIG_DIR"]
    with open(os.path.join(ambient, "settings.json"), "w") as fh:
        json.dump({"effortLevel": "low"}, fh)
    A.session_start({"session_id": "eff2", "cwd": str(tmp_path),
                     "transcript_path": ""})
    S.kv_set(P.mirror_log("eff2"), "account", {"slug": "c9", "label": "c9"})
    code, body = _get(dash + "/api/session/eff2")
    assert code == 200 and json.loads(body)["effort"] == "max"


def test_confirm_find_menu_shape_not_prose():
    # detection is by SHAPE: a ❯-cursored numbered list with Yes+No labels.
    # The bare composer prompt and scrollback prose that happens to enumerate
    # Yes/No must NOT match (a false press would type a digit into the chat)
    from dashboard import confirmdialog as cd
    assert cd.find_menu(_SWITCH_CONFIRM_SCREEN) == "1"
    assert cd.find_menu("") is None
    assert cd.find_menu("some output\n❯ \n") is None          # bare prompt
    assert cd.find_menu("1. Yes, option A\n2. No, option B\n") is None  # no ❯
    assert cd.find_menu(" ❯ 1. Restore code\n   2. No, go back\n") is None


def test_ask_current_question_longest_match():
    # only ONE question shows at a time, but if question i's stripped text is a
    # substring of question j's, a FIRST-match scan returns i while j is on
    # screen and drive()'s wait for j never resolves. The most specific
    # (longest) matching question is the one displayed.
    from dashboard import askdialog as ad
    qs = [{"question": "Pick a color"}, {"question": "Pick a color scheme"}]
    # ☐ anchors the region; "Enter to select" is the pane footer
    showing_j = "☐ chips\nPick a color scheme\n1. dark\nEnter to select"
    assert ad.current_question(showing_j, qs) == 1
    showing_i = "☐ chips\nPick a color\n1. red\nEnter to select"
    assert ad.current_question(showing_i, qs) == 0
    # the review pane repeats every question's text — still None
    assert ad.current_question("☐ x\nReview your answers\nPick a color", qs) is None


def test_ask_dialog_open_when_chip_bar_scrolled_off():
    # On a NARROW/SHORT window a tall dialog overflows the viewport and the
    # ☐/☒ chip bar scrolls off the top while the footer survives — get_text
    # returns only the visible screen. A chip-bar-only anchor returned "" and
    # false-bailed step:open on a genuinely-open dialog (session 819627e5).
    from dashboard import askdialog as ad
    # exactly what the errors-row `screen` capture showed: options + footer,
    # no ☐/☒ anywhere.
    off_screen = ("     approval.\n  3. Just diagnose\n  4. Type something.\n"
                  "──────────────────────────────\n  5. Chat about this\n\n"
                  "Enter to select · Tab/Arrow \nkeys to navigate · Esc to \ncancel\n")
    assert "☐" not in off_screen and "☒" not in off_screen
    assert ad.dialog_open(off_screen)                 # footer fallback anchors
    # the option/action rows are still parseable from the wider region
    labels = [r["label"] for r in ad.rows(off_screen)]
    assert "Type something." in labels and ad.CHAT_LABEL in labels
    # the chip-bar path stays primary (excludes transcript above the bar)
    with_bar = "prose above\n☐ Q1  ☒ Q2\n1. red\nEnter to select"
    assert ad.region(with_bar).startswith("☐ Q1")
    # a screen with neither chip bar nor footer is genuinely no-dialog
    assert not ad.dialog_open("just some transcript text\nno dialog here")
    assert ad.region("just some transcript text") == ""


def test_post_command_bad_vocabulary_is_400(dash, monkeypatch):
    # fixed vocabulary: unknown command, missing/dirty model arg (a shell
    # metacharacter must never reach the terminal), unknown effort level,
    # and compact-with-arg all reject without a keystroke
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "62")
    A.session_start({"session_id": "qc2", "cwd": "/w", "transcript_path": ""})
    for bad in ({"cmd": "clear"}, {"cmd": "model"},
                {"cmd": "model", "arg": "opus; rm -rf /"},
                {"cmd": "model", "arg": "opus[2m]"},
                {"cmd": "effort", "arg": "turbo"},
                {"cmd": "compact", "arg": "focus on the tests"}):
        with pytest.raises(urllib.error.HTTPError) as e:
            _post(dash + "/api/session/qc2/command", bad)
        assert e.value.code == 400
    assert fe.pasted == []


def test_post_command_dialog_and_queue_tabs(dash, monkeypatch):
    # awaiting-command (red — a modal dialog is up) refuses: pasted text
    # would land IN the dialog, its digits deciding it. A busy tab queues
    # like any typed input and the reply says so.
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "63")
    A.session_start({"session_id": "qc3", "cwd": "/w", "transcript_path": ""})
    states = {"63": "awaiting-command"}
    monkeypatch.setattr(DS.API, "tab_states", lambda: dict(states))
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/qc3/command", {"cmd": "compact"})
    assert e.value.code == 409
    assert fe.pasted == []
    states["63"] = "working"
    code, body = _post(dash + "/api/session/qc3/command",
                       {"cmd": "effort", "arg": "high"})
    assert code == 200
    # queued: NO confirm watch (the command runs at the turn boundary — no
    # menu to wait for now), so no `confirm` field and no screen reads
    assert json.loads(body) == {"ok": True, "queued": True, "tab": "working"}
    assert fe.pasted == [("63", "/effort high")]
    assert fe.keyed == []


def test_post_command_no_window_is_409(dash, monkeypatch):
    _inject_fe(monkeypatch, _FakeFE())
    monkeypatch.delenv("KITTY_WINDOW_ID", raising=False)   # headless session
    A.session_start({"session_id": "qc4", "cwd": "/w", "transcript_path": ""})
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/qc4/command", {"cmd": "compact"})
    assert e.value.code == 409


def _rename_transcript(tmp_path, sid, *objs):
    # a transcript at the REAL layout (…/projects/<hash>/<sid>.jsonl) — the
    # set_session_title recognition gate refuses anything else
    d = tmp_path / "projects" / "-w-proj"
    d.mkdir(parents=True, exist_ok=True)
    p = d / (sid + ".jsonl")
    p.write_text(_jl(*objs))
    return str(p)


def test_post_rename_appends_and_retitles_live(dash, monkeypatch, tmp_path):
    from plugins.claude_code import transcript as TR
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "42")
    tp = _rename_transcript(tmp_path, "ren1",
                            {"type": "user", "message": {"content": "hi"}},
                            {"type": "ai-title", "aiTitle": "auto title"})
    A.session_start({"session_id": "ren1", "cwd": "/w", "transcript_path": tp})
    code, body = _post(dash + "/api/session/ren1/rename", {"name": "my session"})
    assert code == 200
    assert json.loads(body) == {"ok": True, "title": "my session",
                                "tab_retitled": True}
    # the appended record IS the /rename channel: last line, sessionId from
    # the filename stem, and it round-trips through the title parser
    with open(tp) as fh:
        rec = json.loads(fh.read().splitlines()[-1])
    assert rec == {"type": "agent-name", "agentName": "my session",
                   "sessionId": "ren1"}
    assert TR.session_title(tp) == "my session"
    assert fe.titled == [("42", "my session")]


def test_post_rename_parked_no_window_still_appends(dash, monkeypatch,
                                                    tmp_path):
    # DELIBERATELY unlike post_message: no live window is NOT an error — the
    # JSONL rename still lands, only the tab retitle degrades
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.delenv("KITTY_WINDOW_ID", raising=False)
    tp = _rename_transcript(tmp_path, "ren2",
                            {"type": "user", "message": {"content": "hi"}})
    A.session_start({"session_id": "ren2", "cwd": "/w", "transcript_path": tp})
    code, body = _post(dash + "/api/session/ren2/rename", {"name": "parked one"})
    assert code == 200
    d = json.loads(body)
    assert d["ok"] is True and d["tab_retitled"] is False
    with open(tp) as fh:
        assert json.loads(fh.read().splitlines()[-1])["agentName"] == "parked one"
    assert fe.titled == []


def test_post_rename_no_terminal_still_appends(dash, monkeypatch, tmp_path):
    # ...and no terminal at all (dashboard outside kitty) is not an error
    # either — post_message's 503 deliberately does not apply here
    _inject_fe(monkeypatch, _NoTermFE())
    monkeypatch.setenv("KITTY_WINDOW_ID", "5")
    tp = _rename_transcript(tmp_path, "ren3",
                            {"type": "user", "message": {"content": "hi"}})
    A.session_start({"session_id": "ren3", "cwd": "/w", "transcript_path": tp})
    code, body = _post(dash + "/api/session/ren3/rename", {"name": "still works"})
    assert code == 200 and json.loads(body)["tab_retitled"] is False
    with open(tp) as fh:
        assert json.loads(fh.read().splitlines()[-1])["agentName"] == "still works"


def test_post_rename_empty_name_is_400(dash, monkeypatch, tmp_path):
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "9")
    tp = _rename_transcript(tmp_path, "ren4",
                            {"type": "user", "message": {"content": "hi"}})
    A.session_start({"session_id": "ren4", "cwd": "/w", "transcript_path": tp})
    with open(tp) as fh:
        before = fh.read()
    for bad in ({}, {"name": "   "}, {"name": "\x1b\x07\n \x00"}, {"name": 7}):
        with pytest.raises(urllib.error.HTTPError) as e:
            _post(dash + "/api/session/ren4/rename", bad)
        assert e.value.code == 400
    with open(tp) as fh:
        assert fh.read() == before
    assert fe.titled == []


def test_post_rename_no_transcript_is_409(dash, monkeypatch, tmp_path):
    _inject_fe(monkeypatch, _FakeFE())
    monkeypatch.setenv("KITTY_WINDOW_ID", "9")
    A.session_start({"session_id": "ren5", "cwd": "/w", "transcript_path": ""})
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/ren5/rename", {"name": "x"})
    assert e.value.code == 409
    # a recorded path that no longer exists: 409, and NEVER created just to
    # name it (the "a" open would)
    gone = str(tmp_path / "projects" / "-w-proj" / "ren5b.jsonl")
    A.session_start({"session_id": "ren5b", "cwd": "/w",
                     "transcript_path": gone})
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/ren5b/rename", {"name": "x"})
    assert e.value.code == 409
    assert not os.path.exists(gone)


def test_post_rename_unsupported_transcript_is_409(dash, monkeypatch,
                                                   tmp_path):
    # a transcript_path OUTSIDE the projects/ layout (a codex standalone
    # host's rollout) must never receive a Claude agent-name record
    _inject_fe(monkeypatch, _FakeFE())
    monkeypatch.setenv("KITTY_WINDOW_ID", "9")
    d = tmp_path / "rollouts"
    d.mkdir()
    tp = str(d / "rollout-ren6.jsonl")
    with open(tp, "w") as fh:
        fh.write(_jl({"type": "session_meta"}))
    A.session_start({"session_id": "ren6", "cwd": "/w", "transcript_path": tp})
    with open(tp) as fh:
        before = fh.read()
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/ren6/rename", {"name": "x"})
    assert e.value.code == 409
    with open(tp) as fh:
        assert fh.read() == before


def test_post_rename_strips_controls_and_caps(dash, monkeypatch, tmp_path):
    # control bytes (the OSC/CSI injection class) never enter the stored
    # name or the set-tab-title arg; over-long names cap at RENAME_MAX
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "11")
    tp = _rename_transcript(tmp_path, "ren7",
                            {"type": "user", "message": {"content": "hi"}})
    A.session_start({"session_id": "ren7", "cwd": "/w", "transcript_path": tp})
    code, body = _post(dash + "/api/session/ren7/rename",
                       {"name": "a\x1b]2;evil\x07b\nc"})
    stored = json.loads(body)["title"]
    assert stored == "a ]2;evil b c"
    long = "x" * (DS.RENAME_MAX + 300)
    code, body = _post(dash + "/api/session/ren7/rename", {"name": long})
    assert json.loads(body)["title"] == "x" * DS.RENAME_MAX
    with open(tp) as fh:
        rec = json.loads(fh.read().splitlines()[-1])
    assert rec["agentName"] == "x" * DS.RENAME_MAX
    assert fe.titled[-1] == ("11", "x" * DS.RENAME_MAX)


def test_post_rename_updates_session_payload_title(dash, monkeypatch,
                                                   tmp_path):
    # the (path, size) title cache self-invalidates on the append — the very
    # next GET shows the new name (list + header payloads share session_title)
    _inject_fe(monkeypatch, _FakeFE())
    monkeypatch.setenv("KITTY_WINDOW_ID", "12")
    tp = _rename_transcript(tmp_path, "ren8",
                            {"type": "ai-title", "aiTitle": "auto"})
    A.session_start({"session_id": "ren8", "cwd": "/w", "transcript_path": tp})
    assert _get_json(dash + "/api/session/ren8")["title"] == "auto"
    _post(dash + "/api/session/ren8/rename", {"name": "picked by hand"})
    assert _get_json(dash + "/api/session/ren8")["title"] == "picked by hand"


def test_post_rename_override_survives_tail_window_rollback(dash, monkeypatch,
                                                           tmp_path):
    # THE ROLLBACK FIX: the /rename `agent-name` scrolls out of session_title's
    # 64KB tail-window in a long session while fresh ai-title rows sit near EOF,
    # so the transcript ladder reverts to the auto title. The durable override
    # (prefs `renamed-title`) is what keeps the DASHBOARD title from rolling back.
    from plugins.claude_code import transcript as TR
    _inject_fe(monkeypatch, _FakeFE())
    monkeypatch.setenv("KITTY_WINDOW_ID", "77")
    tp = _rename_transcript(tmp_path, "ren9",
                            {"type": "ai-title", "aiTitle": "auto"})
    A.session_start({"session_id": "ren9", "cwd": "/w", "transcript_path": tp})
    code, _ = _post(dash + "/api/session/ren9/rename", {"name": "kept name"})
    assert code == 200
    # simulate time passing: append enough fresh ai-title rows to push the
    # appended agent-name past the tail-window (the real-world rollback trigger)
    with open(tp, "a") as fh:
        filler = json.dumps({"type": "ai-title", "aiTitle": "auto"}) + "\n"
        while os.path.getsize(tp) <= TR.TITLE_TAIL_B:
            fh.write(filler)
    # the transcript layer has "rolled back" — the rename is out of the tail
    assert TR.title_and_rename(tp)[1] == ""
    # ...but the dashboard still shows the rename, sourced from the durable override
    assert _get_json(dash + "/api/session/ren9")["title"] == "kept name"
    # and a FRESH in-tail rename still supersedes the override (last rename wins)
    _post(dash + "/api/session/ren9/rename", {"name": "renamed again"})
    assert _get_json(dash + "/api/session/ren9")["title"] == "renamed again"


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


def test_post_guard_accepts_beacon_by_allowlisted_origin(dash, monkeypatch):
    # navigator.sendBeacon (the pagehide clientlog flush — flushClog) can't set
    # X-Claude-Dash, so a HEADERLESS POST is accepted when it carries a present,
    # allowlisted Origin — a cross-origin page can forge neither, so the Origin
    # allowlist is the CSRF gate (docs/dashboard.md *Frontend audit (clientlog)*).
    monkeypatch.setattr(DS, "ALLOWED_ORIGINS", DS.ALLOWED_ORIGINS | {dash})
    ep = dash + "/api/session/beacon1/hint-audit"
    body = {"op": "close", "phase": "shown"}
    code, _ = _post(ep, body, header=None, origin=dash)   # the sendBeacon shape
    assert code == 200
    with pytest.raises(urllib.error.HTTPError) as e:       # headerless + NO origin
        _post(ep, body, header=None)                       # still rejected (unchanged)
    assert e.value.code == 403
    with pytest.raises(urllib.error.HTTPError) as e:       # headerless + bad origin
        _post(ep, body, header=None, origin="https://evil.test")
    assert e.value.code == 403


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
    assert code == 200 and json.loads(body) == {"ok": True, "win": ""}
    # claude runs through the user's interactive login shell (kitty's own env
    # has no user PATH / aliases); the prompt is a POSITIONAL arg, never
    # interpolated into the fixed command string.
    cwd, argv = fe.launched[0]
    assert cwd == str(tmp_path)
    sh, flags, script, dollar0 = argv[:4]
    from plugins.claude_code import account as ACCT
    assert os.path.basename(sh) in ACCT.LAUNCH_SHELLS
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
    """Wraps the server's audit handle: records one action's state_file rows
    in-memory (a watch thread's audit write cross-thread would land in the
    spool, invisible to a same-process DB read) and delegates everything else
    to the real module."""

    def __init__(self, real, action="web-launch-steal-watch"):
        self.real, self.action, self.rows = real, action, []

    def __getattr__(self, name):
        return getattr(self.real, name)

    def state_file(self, log, path, action, content=""):
        if action == self.action:
            self.rows.append(content)
        return self.real.state_file(log, path, action, content)


@pytest.fixture(autouse=True)
def _fast_launch_wake(monkeypatch):
    """Every successful /api/sessions/new spawns a _launch_wake poller thread;
    at the product's 15s budget one would outlive its test and keep polling the
    shared audit DB while later tests run. Clamp the budget module-wide; the
    wake tests below re-raise it themselves."""
    monkeypatch.setattr(DS.launch, "LAUNCHWAKE_MAX_S", 0.2)
    monkeypatch.setattr(DS.launch, "LAUNCHWAKE_POLL_S", 0.01)


def _watch_rig(monkeypatch, fronts, bundle="app.term"):
    """Wire the steal watch for a test: a _FakeFE with an OS app id, a
    scripted _front_app sequence (call 1 = the pre-launch capture, the rest =
    the watch polls; the last value repeats), a fast poll cadence, a recorded
    audit. Returns (fe, rows) — rows collects the watch's audit content."""
    fe = _FakeFE()
    fe.bundle_id = bundle
    seq = list(fronts)
    monkeypatch.setattr(DS.launch, "_front_app",
                        lambda: seq.pop(0) if len(seq) > 1 else seq[0])
    monkeypatch.setattr(DS.launch, "STEALWATCH_POLL_S", 0.005)
    aud = _WatchAudit(DS.launch.A)
    monkeypatch.setattr(DS.launch, "A", aud)
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
    monkeypatch.setattr(DS.launch, "_front_app", lambda: probed.append(1) or "x")
    code, _ = _post(dash + "/api/sessions/new", {"cwd": str(tmp_path)})
    assert code == 200 and probed == []


def test_launch_wake_pushes_and_audits(dash, monkeypatch, tmp_path):
    # the post-launch wake watch: the launched session's SessionStart appears
    # → ONE NOTIFIER `wake` naming the sid (the page's fast jump, matched by
    # the window id kitty printed at launch) + one web-launch-wake audit row
    # carrying the measured launch→appearance latency
    fe = _FakeFE()
    fe.launch_ok = "88"                     # kitty printed the new window's id
    _inject_fe(monkeypatch, fe)
    monkeypatch.setattr(DS.launch, "LAUNCHWAKE_MAX_S", 5.0)
    aud = _WatchAudit(DS.launch.A, "web-launch-wake")
    monkeypatch.setattr(DS.launch, "A", aud)
    # prime core.audit's process-wide sqlite conn from THIS thread: the first
    # audit write binds the conn to its creating thread, and if the POST
    # handler's thread claims it, the session_start below silently degrades
    # to the spool — a DB the watcher's read never sees (same per-process
    # caching story as conftest._fresh_audit_conn)
    monkeypatch.delenv("KITTY_WINDOW_ID", raising=False)
    A.session_start({"session_id": "prime0", "cwd": "/elsewhere",
                     "transcript_path": ""})
    q = DS.NOTIFIER.register()
    try:
        code, body = _post(dash + "/api/sessions/new", {"cwd": str(tmp_path)})
        assert code == 200 and json.loads(body) == {"ok": True, "win": "88"}
        # SessionStart lands while the watcher is polling
        monkeypatch.setenv("KITTY_WINDOW_ID", "88")
        A.session_start({"session_id": "wake1", "cwd": str(tmp_path),
                         "transcript_path": ""})
        ev, payload = q.get(timeout=5)
        assert ev == "wake"
        assert payload["sid"] == "wake1" and payload["win"] == "88"
        wait_until(lambda: aud.rows, desc="wake audit row")
        assert aud.rows[0]["ok"] is True and aud.rows[0]["sid"] == "wake1"
        assert aud.rows[0]["waited_s"] >= 0
    finally:
        DS.NOTIFIER.unregister(q)


def test_launch_wake_timeout_audits_without_push(dash, monkeypatch, tmp_path):
    # no session ever appears → the watcher gives up at its budget, audits the
    # timeout (sid empty, ok False) and pushes NOTHING — a wake with no sid
    # would have nothing for the page to jump to
    fe = _FakeFE()                          # launch_ok True → no window id
    _inject_fe(monkeypatch, fe)
    aud = _WatchAudit(DS.launch.A, "web-launch-wake")
    monkeypatch.setattr(DS.launch, "A", aud)
    q = DS.NOTIFIER.register()
    try:
        code, body = _post(dash + "/api/sessions/new", {"cwd": str(tmp_path)})
        assert code == 200 and json.loads(body) == {"ok": True, "win": ""}
        wait_until(lambda: aud.rows, desc="wake timeout audit row")
        assert aud.rows[0]["ok"] is False and aud.rows[0]["sid"] == ""
        assert q.empty()
    finally:
        DS.NOTIFIER.unregister(q)


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
    assert code == 200 and json.loads(body) == {"ok": True, "win": ""}


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


def _stop_rows(sid):
    """The `web-stop` state_files rows (the close attempt/done pair), ts-ordered.
    Same spool-drain dance as _hint_rows."""
    import sqlite3
    A._CONN = None
    A._FAILED = False
    A._connect()
    con = sqlite3.connect(A.db_path())
    try:
        return [json.loads(c) for (c,) in con.execute(
            "SELECT content FROM state_files WHERE session_id=? "
            "AND action='web-stop' ORDER BY ts", (sid,))]
    finally:
        con.close()


def test_post_stop_closes_tab(dash, monkeypatch):
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "55")
    A.session_start({"session_id": "stop1", "cwd": "/w", "transcript_path": ""})
    code, body = _post(dash + "/api/session/stop1/stop", {})
    assert code == 200 and json.loads(body) == {"ok": True}
    assert fe.closed == ["55"]
    # the close is audited as an attempt BEFORE close_tab then a done outcome —
    # so a close_tab that hangs leaves a lone `attempt` (the stuck-close signal)
    rows = _stop_rows("stop1")
    assert [r.get("phase") for r in rows] == ["attempt", "done"]
    assert rows[0]["win"] == "55" and rows[1] == {"win": "55", "phase": "done",
                                                  "ok": True}


def test_post_stop_failed_close_still_audits_attempt(dash, monkeypatch):
    # A close that FAILS (close_tab → False) must still leave the attempt row
    # plus a done(ok:false): the gap this closes is a close that vanishes from
    # the audit, not just one that succeeds.
    fe = _FakeFE()
    monkeypatch.setattr(fe, "close_tab", lambda win: False)
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "60")
    A.session_start({"session_id": "stopf", "cwd": "/w", "transcript_path": ""})
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/stopf/stop", {})
    assert e.value.code == 502
    rows = _stop_rows("stopf")
    assert [r.get("phase") for r in rows] == ["attempt", "done"]
    assert rows[1]["ok"] is False


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
    # backdate past the just-started grace (_within_live_grace) so the missing-
    # window demotion applies — this test is the LEAKED/CRASHED lingering
    # session, not a brand-new launch (that case is covered separately below)
    A._connect().execute("UPDATE sessions SET started_at=? WHERE session_id=?",
                         (time.time() - 3600, "ghost"))
    A._connect().commit()
    log = P.mirror_log("ghost")
    O.emit(log, O.label("x", (1, 2, 3)))       # create the state DB (state-DB live)
    # window enumeration returns a map WITHOUT this sid → tab is closed
    monkeypatch.setattr(DS.launch, "_live_windows", lambda: {"other": "99"})
    row = next(r for r in _get_json(dash + "/api/sessions") if r["sid"] == "ghost")
    assert row["live"] is False                # demoted — the requirement
    ov = _get_json(dash + "/api/session/ghost")
    assert ov["live"] is False and ov["kitty_window_id"] == ""
    # when the tab IS open (sid in the map) it stays live and controllable
    monkeypatch.setattr(DS.launch, "_live_windows", lambda: {"ghost": "11"})
    row = next(r for r in _get_json(dash + "/api/sessions") if r["sid"] == "ghost")
    assert row["live"] is True
    ov = _get_json(dash + "/api/session/ghost")
    assert ov["live"] is True and ov["kitty_window_id"] == "11"


def test_fresh_session_within_grace_stays_live(dash, monkeypatch):
    # a JUST-started session whose pane isn't tagged claude_session=<sid> yet
    # (the startup tag-race: A.session_start writes the sessions row before
    # split.tag_window runs, and _live_windows is memoized on top) must NOT be
    # demoted to not-live during _LIVE_GRACE_S — else the card flashes "parked"
    # and the detail header (meta fetched once) froze on it, uncloseable.
    monkeypatch.setenv("KITTY_WINDOW_ID", "12")
    A.session_start({"session_id": "fresh", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("fresh")
    O.emit(log, O.label("x", (1, 2, 3)))       # create the state DB (state-DB live)
    # window map WITHOUT this sid — the pane hasn't been tagged yet — but the
    # session started just now, so the grace keeps it live and controllable
    monkeypatch.setattr(DS.launch, "_live_windows", lambda: {"other": "99"})
    row = next(r for r in _get_json(dash + "/api/sessions") if r["sid"] == "fresh")
    assert row["live"] is True                 # inside the grace — not demoted
    ov = _get_json(dash + "/api/session/fresh")
    # live (no parked flash), but the control plane's window resolves from the
    # live tag map — still "" until the pane is actually tagged (kitty_window_id
    # fills in the moment tag_window lands; the client re-renders on that flip)
    assert ov["live"] is True and ov["kitty_window_id"] == ""
    # once the grace has elapsed, the same missing-window state DOES demote
    A._connect().execute("UPDATE sessions SET started_at=? WHERE session_id=?",
                         (time.time() - 3600, "fresh"))
    A._connect().commit()
    row = next(r for r in _get_json(dash + "/api/sessions") if r["sid"] == "fresh")
    assert row["live"] is False                # past the grace — demoted


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


def test_post_interrupt_refuses_on_open_dialog(dash, monkeypatch):
    # a red "asking you" tab means a MODAL DIALOG is open (AskUserQuestion /
    # ExitPlanMode / a permission prompt). An Esc there DECLINES the dialog
    # rather than interrupting a turn — it once killed the answer the user was
    # giving via the web ask card ("User declined to answer questions",
    # 2026-07-20). Refuse with a 409 and press NO key; the card is the response.
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "90")
    A.session_start({"session_id": "intrd", "cwd": "/w", "transcript_path": ""})
    monkeypatch.setattr(DS.API, "tab_states", lambda: {"90": "awaiting-command"})
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/intrd/interrupt", {})
    assert e.value.code == 409
    assert fe.keyed == []                        # no Escape reached the dialog


def test_post_rewind_refuses_on_open_dialog(dash, monkeypatch):
    # the cancel-edit / rewind gesture must NOT fire on a red tab: its Esc-Esc
    # (cancel-edit) or typed /rewind would land in the open ask/plan/permission
    # dialog and dismiss or corrupt it. 409, and neither keys nor text sent.
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "91")
    A.session_start({"session_id": "rewd", "cwd": "/w", "transcript_path": ""})
    monkeypatch.setattr(DS.API, "tab_states", lambda: {"91": "awaiting-command"})
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/rewd/rewind", {})
    assert e.value.code == 409
    assert fe.keyed == [] and fe.sent == []


def test_post_rewind_to_refuses_on_open_dialog(dash, monkeypatch):
    # full web rewind on a red tab: a dialog is open, so /rewind must not be
    # typed into it (previously covered incidentally by the busy-tab guard; now
    # an explicit dialog refusal since awaiting-command left BUSY_TABS)
    fe = _MenuFE(prompts=["p"])
    _rewind_env(monkeypatch, "rwtd", "92", fe)
    monkeypatch.setattr(DS.API, "tab_states", lambda: {"92": "awaiting-command"})
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/rwtd/rewind-to",
              {"text": "p", "mode": "both", "ups": 1})
    assert e.value.code == 409
    assert fe.sent == [] and fe.state == "idle"     # nothing typed into the dialog


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
    """_FakeFE plus a reactive simulation of the v2.1.215 AskUserQuestion
    dialog (re-measured live 2026-07-19): a header-chip bar, one pane per
    question (numbered options; multiSelect checkboxes; a numbered "Type
    something" row; multiSelect an unnumbered "Next"/"Submit" advance row),
    "Chat about this" below a rule, and the review pane. Key semantics as
    measured: DIGITS ARE INERT — selection is up/down to a row + Enter; Enter
    on a single-select option selects+advances (a sole single-select question
    submits outright), Enter on a multiSelect option toggles, Enter on the
    multiSelect advance row moves to the next tab; typing goes into the focused
    Type row (send_text's CR commits it); left/right/Tab move tabs (left
    no-ops at the first); the review's "Submit answers" row + Enter submits.
    This renders the classic (no-preview) layout; the parser's handling of the
    side-by-side preview layout is pinned against real captures in
    test_askdialog_parsers_pin_the_real_screens."""

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

    def _labels(self, qi):
        q = self.questions[qi]
        return [o["label"] for o in q.get("options") or []]

    def _type_label(self, qi):
        return self.typed[qi] or ("Type something"
                                  + ("" if self.questions[qi].get("multiSelect")
                                     else "."))

    # cursor-navigable rows of a pane, as ("kind", payload) in screen order:
    # options, the Type row, (multi) the unnumbered advance row, then Chat
    def _kinds(self, qi):
        ks = [("opt", i) for i in range(len(self._labels(qi)))]
        ks.append(("type", None))
        if self.questions[qi].get("multiSelect"):
            ks.append(("advance", None))
        ks.append(("chat", None))
        return ks

    def _advance(self):
        self.tab += 1
        self.cursor = 0
        if self.tab >= len(self.questions) \
                and len(self.questions) == 1 \
                and not self.questions[0].get("multiSelect"):
            self._finish()              # sole single-select: no review pane

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
        qi = self.tab
        kinds = self._kinds(qi)
        if self.cursor < len(kinds) and kinds[self.cursor][0] == "type":
            self.typed[qi] = text                    # types inline
            if self.questions[qi].get("multiSelect"):
                self.checks[qi].add("__typed__")     # the CR checks it
            else:
                self.single[qi] = text               # the CR selects+advances
                self._advance()
        return ok

    def send_key(self, win, *keys):
        ok = super().send_key(win, *keys)
        for k in keys:
            if not self.open:
                continue
            if self.tab >= len(self.questions):      # review pane
                if k == "up":
                    self.cursor = max(0, self.cursor - 1)
                elif k == "down":
                    self.cursor = min(1, self.cursor + 1)
                elif k == "enter" and self.cursor == 0:
                    self._finish()                   # "Submit answers"
                continue                    # left/right/Tab/digits all inert
            qi = self.tab
            q = self.questions[qi]
            kinds = self._kinds(qi)
            # FORWARD-ONLY: left/right/Tab do NOT switch questions in this build
            # (measured live 2026-07-22, session 3fd325d9 — inert from every
            # row); the only way forward is Enter (auto-advance / the "Next"
            # row). up/down move the row cursor.
            if k == "up":
                self.cursor = max(0, self.cursor - 1)
            elif k == "down":
                self.cursor = min(len(kinds) - 1, self.cursor + 1)
            elif k == "enter":
                kind, payload = kinds[self.cursor]
                if kind == "opt":
                    label = self._labels(qi)[payload]
                    if q.get("multiSelect"):
                        self.checks[qi] ^= {label}   # toggle
                    else:
                        self.single[qi] = label
                        self._advance()
                elif kind == "type":
                    if q.get("multiSelect"):
                        self.checks[qi] ^= {"__typed__"}
                    elif self.typed[qi]:
                        self.single[qi] = self.typed[qi]
                        self._advance()
                elif kind == "advance":
                    self._advance()                  # "Next"/"Submit"
                elif kind == "chat":
                    self.chatted = True
                    self.open = False
            # digits inert
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
                              ("❯ " if self.cursor == 0 else "  ")
                              + "1. Submit answers",
                              ("❯ " if self.cursor == 1 else "  ")
                              + "2. Cancel"])
        qi, q = self.tab, self.questions[self.tab]
        labels = self._labels(qi)
        multi = q.get("multiSelect")
        # question text WRAPS like the real TUI's (the 555-char live ask)
        lines = [bar, ""] \
            + (textwrap.wrap(q.get("question") or "", 48) or [""]) + [""]
        for idx, (kind, payload) in enumerate(self._kinds(qi)):
            cur = "❯ " if idx == self.cursor else "  "
            if kind == "opt":
                lb = labels[payload]
                chk = ("[✔] " if lb in self.checks[qi] else "[ ] ") \
                    if multi else ""
                lines.append("%s%d. %s%s" % (cur, payload + 1, chk, lb))
            elif kind == "type":
                chk = ("[✔] " if "__typed__" in self.checks[qi] else "[ ] ") \
                    if multi else ""
                lines.append("%s%d. %s%s"
                             % (cur, len(labels) + 1, chk,
                                self._type_label(qi)))
            elif kind == "advance":
                lines.append("%s   %s"
                             % (cur, "Submit" if qi == len(self.questions) - 1
                                else "Next"))
            elif kind == "chat":
                lines += ["────────",
                          "%s%d. Chat about this" % (cur, len(labels) + 2)]
        lines += ["", "Enter to select · ↑/↓ to navigate · "
                  "Tab to switch questions · Esc to cancel"]
        return "\n".join(lines)


def _ask_env(monkeypatch, sid, win, fe, questions, tid="toolu_a1"):
    _inject_fe(monkeypatch, fe)
    monkeypatch.setattr(DS.askdialog, "POLL_S", 0.01)
    monkeypatch.setattr(DS.askdialog, "KEY_GAP_S", 0)
    # the open-check polls up to STEP_TIMEOUT_S now (like every other step) —
    # keep the dialog-dismissed "open" bail from burning the full budget
    monkeypatch.setattr(DS.askdialog, "STEP_TIMEOUT_S", 0.1)
    monkeypatch.setattr(DS.askdialog, "SUBMIT_TIMEOUT_S", 0.1)
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
    # one single-select question: cursor+Enter selects AND submits (no review)
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


# the exact failing shape of session 3fd325d9 (2026-07-22): a MIDDLE
# multiSelect answered with a custom "other", followed by a THIRD question —
# the pane must advance PAST the multiSelect to reach question 3. The old
# blind `right` advance was eaten by the custom-text row's edit focus, so
# question 3 "never became current"; _advance_multi uses the "Next" row.
_ASK_3Q_MID_MULTI = [
    {"question": "Teleport where?", "header": "Teleport", "multiSelect": False,
     "options": [{"label": "Beach"}, {"label": "City"}]},
    {"question": "Which snacks? (pick any)", "header": "Snacks",
     "multiSelect": True,
     "options": [{"label": "Coffee"}, {"label": "Fruit"}]},
    {"question": "Pick a superpower — or type your own.", "header": "Power",
     "multiSelect": False,
     "options": [{"label": "Flight"}, {"label": "Teleportation"}]}]


def test_post_answer_middle_multiselect_custom_advances(dash, monkeypatch):
    fe = _AskFE(_ASK_3Q_MID_MULTI)
    _ask_env(monkeypatch, "ask8", "48", fe, _ASK_3Q_MID_MULTI)
    code, body = _post(dash + "/api/session/ask8/answer",
                       {"tool_use_id": "toolu_a1",
                        "answers": [{"selected": ["City"], "other": ""},
                                    {"selected": ["Fruit"], "other": "test"},
                                    {"selected": ["Flight"], "other": ""}]})
    assert code == 200 and json.loads(body) == {"ok": True, "chat": False}
    assert fe.submitted == {"Teleport where?": "City",
                            "Which snacks? (pick any)": "Fruit, test",
                            "Pick a superpower — or type your own.": "Flight"}


def test_post_answer_recovers_dialog_stuck_midflow(dash, monkeypatch):
    # the 3fd325d9 RETRY: left/right/Tab don't switch questions in this build,
    # so a dialog already sitting on a LATER question (a prior half-answer, or
    # a terminal-side answer) cannot be walked back to question 1. The old
    # `left`×len normalize no-oped and the first wait bailed "question 1 never
    # became current"; the forward-only drive answers from the CURRENT question
    # instead, recovering it. Q1 keeps whatever already set it (no back-nav).
    fe = _AskFE(_ASK_3Q_MID_MULTI)
    fe.single[0] = "City"          # Q1 already answered (in the terminal)
    fe.tab = 2                     # dialog stuck on Q3
    _ask_env(monkeypatch, "ask9", "49", fe, _ASK_3Q_MID_MULTI)
    code, body = _post(dash + "/api/session/ask9/answer",
                       {"tool_use_id": "toolu_a1",
                        "answers": [{"selected": ["City"], "other": ""},
                                    {"selected": ["Fruit"], "other": ""},
                                    {"selected": ["Teleportation"], "other": ""}]})
    assert code == 200 and json.loads(body) == {"ok": True, "chat": False}
    # Q3 answered from the web; Q1 kept its terminal value; Q2 never became
    # current so it stays empty (the dialog was already past it)
    assert fe.submitted["Pick a superpower — or type your own."] == "Teleportation"
    assert fe.submitted["Teleport where?"] == "City"


_ASK_LONGQ = [{"question": "Which rename mechanism should the dashboard "
               "use? The research doc (docs/session-naming-findings.md) "
               "found two channels: appending a record to the session's "
               "transcript JSONL (works for live AND parked sessions, but "
               "the live kitty tab title won't change until next resume), "
               "or typing the command into the live TUI like the composer "
               "does (fully native, but only works for live sessions).",
               "header": "Mechanism",
               "multiSelect": False,
               "options": [{"label": "JSONL append"}, {"label": "TUI"}]},
              {"question": "Where should the rename affordance live?",
               "header": "Placement", "multiSelect": False,
               "options": [{"label": "Header"}, {"label": "Cards"}]}]


def test_post_answer_wrapped_long_question(dash, monkeypatch):
    # the live 2026-07-18 bail: a 555-char question WRAPS across screen
    # lines, and the old exact line-set match never saw it become current
    # ("question 1 never became current" at step `question`)
    fe = _AskFE(_ASK_LONGQ)
    _ask_env(monkeypatch, "ask7", "47", fe, _ASK_LONGQ)
    code, _ = _post(dash + "/api/session/ask7/answer",
                    {"tool_use_id": "toolu_a1",
                     "answers": [{"selected": ["JSONL append"], "other": ""},
                                 {"selected": ["Header"], "other": ""}]})
    assert code == 200
    assert fe.submitted == {_ASK_LONGQ[0]["question"]: "JSONL append",
                            _ASK_LONGQ[1]["question"]: "Header"}


def test_post_answer_multi_diffs_against_screen(dash, monkeypatch):
    # Enter TOGGLES the cursored box — boxes the user pre-checked in the
    # terminal must be reconciled (unwanted ones toggled OFF), never re-flipped
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


def test_post_answer_chat_delivers_message(dash, monkeypatch):
    """A TYPED answer on a preview-layout question is routed by the card through
    'Chat about this' AND carries the typed text as `message` — the server
    dismisses the dialog then delivers the text as a normal message so the
    custom answer reaches the session (docs/dashboard.md, *Web ask*)."""
    fe = _AskFE(_ASK_1S)
    _ask_env(monkeypatch, "askc", "47", fe, _ASK_1S)
    code, body = _post(dash + "/api/session/askc/answer",
                       {"tool_use_id": "toolu_a1", "chat": True,
                        "message": "figure out which ones are available"})
    assert code == 200 and json.loads(body)["message_sent"] is True
    assert fe.chatted
    assert fe.pasted == [("47", "figure out which ones are available")]


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

# real v2.1.215 capture (2026-07-19): the SIDE-BY-SIDE preview layout an ask
# with option `preview`s renders — a box bleeds onto the option lines (rows()
# must strip it), a "Notes: press n" hint row is NOT a cursor stop, and "Chat
# about this" is UNNUMBERED (it carried a digit in the classic layout)
_ASK_PREVIEW_SCREEN = """\
────
←  ☒ Reappear trigger  ☐ Unhide UI  ✔ Submit  →

How do you want to unhide a directory manually / see what's hidden?

❯ 1. Collapsed 'Hidden (N)'       ┌──────────────────────────────────────────┐
    strip                         │ ───────────────                          │
  2. No manual UI needed          │ Hidden (2)                               │
                                  │   baqylau        ↩                       │
                                  └──────────────────────────────────────────┘

                                  Notes: press n to add notes

────
  Chat about this

Enter to select · ↑/↓ to navigate · n to add notes · Tab to switch questions · Esc to cancel"""


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
    # a long question WRAPS across screen lines — flattened match (the live
    # "question 1 never became current" bail, 2026-07-18)
    wrapped = _ASK_MULTI_SCREEN.replace(
        "Which toppings?",
        "Which toppings should the kitchen put on\nthe pizza tonight?")
    long_qs = [{"question": "Which toppings should the kitchen put on the "
                            "pizza tonight?"}]
    assert AD.current_question(wrapped, long_qs) == 0
    # the review pane's answer recap repeats the question texts — it must
    # still read as "no current question"
    review_qs = [{"question": "Cats or dogs?"}, {"question": "Tea or coffee?"}]
    assert AD.current_question(_ASK_REVIEW_SCREEN, review_qs) is None
    # the column-0 scrollback echo is outside the chip-bar region
    assert "scrollback" not in AD.region(_ASK_MULTI_SCREEN)
    assert AD.review_open(_ASK_REVIEW_SCREEN)
    assert not AD.dialog_open(_ASK_REVIEW_SCREEN)
    assert AD.current_question(_ASK_REVIEW_SCREEN, qs) is None
    assert not AD.dialog_open("❯ composer\n  -- INSERT --")
    # the side-by-side preview layout: labels stripped of the bled-in box, the
    # "Notes" hint dropped, and an UNNUMBERED "Chat about this" row surfaced
    assert AD.dialog_open(_ASK_PREVIEW_SCREEN)
    prs = AD.rows(_ASK_PREVIEW_SCREEN)
    assert [(r["digit"], r["label"]) for r in prs] == [
        ("1", "Collapsed 'Hidden (N)'"), ("2", "No manual UI needed"),
        ("", "Chat about this")]
    assert [r["cursor"] for r in prs] == [True, False, False]
    pv_qs = [{"question": "When should a hidden directory reappear on the "
                          "main page?"},
             {"question": "How do you want to unhide a directory manually / "
                          "see what's hidden?"}]
    assert AD.current_question(_ASK_PREVIEW_SCREEN, pv_qs) == 1


def test_askdialog_typed_answer_fails_fast_without_type_row():
    """The preview layout has no numbered "Type something" row, so a typed
    ('other') answer is undeliverable — the driver must fail FAST with step
    "type" instead of walking the cursor forever ("cursor never reached Type
    row", 2026-07-19). The web card routes typed answers via chat instead."""
    AD = DS.askdialog
    fe = _FakeFE()
    fe.screens = [_ASK_PREVIEW_SCREEN]
    # 2 options → the (absent) Type row would be digit 3
    with pytest.raises(AD.AskError) as e:
        AD._require_type_row(fe, "1", "3")
    assert e.value.step == "type"
    # a present option digit is fine (no raise)
    AD._require_type_row(fe, "1", "2")


def test_cursor_to_reaches_chat_in_two_cursor_preview_layout():
    """The preview layout bleeds the last option's ❯ onto the "Chat about this"
    row below it, so with the cursor genuinely ON Chat, BOTH rows render ❯
    (verified live 2026-07-20 — down from the last option lands on Chat). The
    old _cursor_to read only the FIRST cursor row (the option) and dead-looped
    ("cursor never reached Chat row"); checking EVERY cursored row fixes it
    without breaking option targeting (the down-from-top walk stops at the clean
    single-❯ option before descending into the two-❯ state)."""
    AD = DS.askdialog

    class _PreviewNavFE:
        # rows: options 1..3 then an unnumbered "Chat about this"; idx 3 = Chat.
        # On Chat, the LAST option (idx-2 row) ALSO shows ❯ — the render bleed.
        def __init__(self):
            self.idx = 2                       # start on the last option

        def send_key(self, win, *keys):
            for k in keys:
                if k == "down":
                    self.idx = min(3, self.idx + 1)
                elif k == "up":
                    self.idx = max(0, self.idx - 1)
            return True

        def get_text(self, win, extent="screen"):
            labels = ["Hide all", "Keep", "Keep stop"]
            lines = [" ☐ Q ", ""]
            for i, lb in enumerate(labels):
                on = self.idx == i or (self.idx == 3 and i == 2)   # bleed
                lines.append(("❯ " if on else "  ") + "%d. %s" % (i + 1, lb))
            lines += ["────",
                      ("❯ " if self.idx == 3 else "  ") + "Chat about this",
                      "Enter to select · ↑/↓ to navigate · Esc to cancel"]
            return "\n".join(lines)

    def nul(*_a, **_k):
        return None
    fe = _PreviewNavFE()
    screen = AD._cursor_to(fe, "1", lambda r: r["label"] == AD.CHAT_LABEL,
                           nul, "Chat row")
    assert fe.idx == 3                                     # landed on Chat
    assert any(r["label"] == "Chat about this" and r["cursor"]
               for r in AD.rows(screen))
    # option targeting still stops at the clean option row, NOT over into Chat
    fe2 = _PreviewNavFE()
    AD._cursor_to(fe2, "1", AD._by_digit("3"), nul, "opt 3")
    assert fe2.idx == 2                                    # option 3, not Chat


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
        "seven_day": {"used_percentage": 23, "resets_at": 1784500000000},
        # a model-scoped window (none exist as of CLI 2.1.215 — this is the
        # forward contract) is captured generically; garbage entries are not
        "seven_day_fable": {"used_percentage": 81, "resets_at": 1784500000},
        "Bad Key!": {"used_percentage": 50},
        "no_pct": {"resets_at": 1784500000},
        "not_a_dict": 7}}
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
    # the model window rode along; the garbage entries did not
    assert u["seven_day_fable"] == 81
    assert u["seven_day_fable_reset"] == 1784500000.0
    assert "Bad Key!" not in u and "no_pct" not in u and "not_a_dict" not in u
    # account-wide pair first, then model windows (the bar display order —
    # dict order survives the kv's json round-trip)
    wins = [k for k in u if isinstance(u[k], int)]
    assert wins == ["five_hour", "seven_day", "seven_day_fable"]
    # a payload with no rate_limits leaves the last good usage in place
    SL.capture(json.dumps({"session_id": "slcap"}).encode())
    assert S.kv_get(log, "usage")["five_hour"] == 11
    # delegate runs with the same stdin and its exit code is returned
    assert SL.run(["sh", "-c", "cat >/dev/null; exit 3"], raw) == 3
    assert SL.run([], raw) == 0                                 # bare shim → 0


def _set_started(sid, ts, ended=None):
    """Stamp a controlled started_at (and optional ended_at) onto a seeded
    session row — session_start records wall-clock, but the heatmap/punch/window
    buckets need deterministic timestamps."""
    import sqlite3
    conn = sqlite3.connect(A.db_path())
    conn.execute("UPDATE sessions SET started_at=?, ended_at=? WHERE session_id=?",
                 (ts, ended, sid))
    conn.commit()
    conn.close()


def test_stats_payload_aggregates_cross_session(dash, monkeypatch):
    """GET /api/stats: whole-corpus aggregates (stats_payload over
    sessionapi.activity_stats). Sessions are the unit; per-project grouping,
    per-window pulse counts, daily heatmap buckets, and the day×hour punch card
    all fold from the audit sessions/otel/errors tables."""
    monkeypatch.setattr(DS, "STATS_TTL_S", 0)          # defeat the wall-clock memo
    now = time.time()
    # three sessions in /proj/alpha (one 40d old → outside the 7d/30d windows),
    # one in /proj/beta; one alpha session still open (no ended_at).
    seed = [("stA1", "/proj/alpha", now - 1 * 3600, now),         # today, ended
            ("stA2", "/proj/alpha", now - 2 * 86400, None),       # 2d ago, active
            ("stA3", "/proj/alpha", now - 40 * 86400, now),       # 40d ago, ended
            ("stB1", "/proj/beta",  now - 1 * 86400, now)]        # 1d ago, ended
    for sid, cwd, st, en in seed:
        A.session_start({"session_id": sid, "cwd": cwd, "transcript_path": ""})
        _set_started(sid, st, en)
    # stA2 is GENUINELY live (a /tmp state DB), not merely ended_at=NULL — the
    # pulse `active` counts real liveness (sessions_payload), so an open row
    # without a live DB (a stranded crash/kill) would NOT count.
    S.incr(P.mirror_log("stA2"), commands=1)
    # tokens + cost land on one alpha session
    A.otel("stA1", [{"metric": "token", "query_source": "main", "type": "input",
                     "value": 1000},
                    {"metric": "token", "query_source": "main", "type": "output",
                     "value": 500},
                    {"metric": "cost", "query_source": "main", "type": "", "value": 0.25}])
    A.error("stB1", "boom", {"where": "test"})          # one error under beta

    DS.lists._STATS_AGG["v"] = None                     # bypass the wall-clock memo
    d = _get_json(dash + "/api/stats")
    assert d["total_sessions"] == 4
    # windows: all=4, 30d=3 (drops the 40d-old alpha), 7d=3
    assert d["windows"]["all"]["sessions"] == 4
    assert d["windows"]["30d"]["sessions"] == 3
    assert d["windows"]["7d"]["sessions"] == 3
    # active = genuinely-live sessions (stA2 in every window that includes it)
    assert d["windows"]["7d"]["active"] == 1
    assert d["windows"]["30d"]["active"] == 1
    assert d["windows"]["all"]["active"] == 1
    assert d["windows"]["all"]["ended"] == 3
    # token/cost totals (summed across the otel rows)
    assert d["windows"]["all"]["tokens"] == 1500
    assert abs(d["windows"]["all"]["cost"] - 0.25) < 1e-9
    assert d["windows"]["all"]["errors"] == 1
    # per-project grouping (basename of the group_dir); alpha has 3, beta 1
    by = {p["name"]: p for p in d["projects"]}
    assert by["alpha"]["sessions"] == 3 and by["beta"]["sessions"] == 1
    assert by["alpha"]["tokens"] == 1500 and abs(by["alpha"]["cost"] - 0.25) < 1e-9
    assert by["beta"]["errors"] == 1
    # top-projects bar list in the pulse window, ranked by sessions
    top = d["windows"]["all"]["projects"]
    assert top[0]["name"] == "alpha" and top[0]["sessions"] == 3
    # heatmap daily buckets + punch-card triples are well-formed
    assert d["daily"] and all(len(x) == 2 and x[1] >= 1 for x in d["daily"])
    assert d["punch"] and all(0 <= dow <= 6 and 0 <= hr <= 23 and n >= 1
                              for dow, hr, n in d["punch"])


def test_accounts_payload_aggregates_usage(dash, monkeypatch, tmp_path):
    # /api/accounts returns the registry + newest usage per account slug
    monkeypatch.setattr(DS.plugins, "accounts", lambda: [
        {"slug": "", "label": "default", "alias": "claude"},
        {"slug": "c2", "label": "claude-01", "alias": "c2"}])
    A.session_start({"session_id": "accs1", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("accs1")
    S.kv_set(log, "account", {"slug": "c2", "label": "claude-01"})
    S.kv_set(log, "usage", {"five_hour": 40, "seven_day": 55,
                            "seven_day_fable": 80, "ts": 100})
    rows = _get_json(dash + "/api/accounts")
    by = {r["slug"]: r for r in rows}
    # the served usage is EFFECTIVE (sessionapi.effective_usage): ts=100 is
    # ancient with no resets → every window rolled over → zeroed (the
    # model-scoped fable window exactly like the account-wide pair), so a
    # stale snapshot can never render its old % with a 'resets now' countdown
    assert by["c2"]["usage"]["five_hour"] == 0
    assert by["c2"]["usage"]["seven_day"] == 0
    assert by["c2"]["usage"]["seven_day_fable"] == 0
    assert by[""]["usage"] is None                 # default has no captured usage
    # server-computed effective 5h and the limit-hit flag (none)
    assert by["c2"]["five_hour_eff"] == 0
    assert by["c2"]["limit_hit"] is None and by[""]["limit_hit"] is None


def test_accounts_payload_serves_fresh_eff_and_limit_hit(dash, monkeypatch):
    monkeypatch.setattr(DS.plugins, "accounts", lambda: [
        {"slug": "c1", "label": "oboard", "alias": "c1"}])
    A.session_start({"session_id": "accs2", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("accs2")
    now = time.time()
    S.kv_set(log, "account", {"slug": "c1", "label": "oboard"})
    S.kv_set(log, "usage", {"five_hour": 95, "five_hour_reset": now + 8000,
                            "ts": now})
    S.kv_set(log, "limit-hit", {"slug": "c1", "ts": now,
                                "resets_at": now + 8000, "msg": "limit"})
    by = {r["slug"]: r for r in _get_json(dash + "/api/accounts")}
    assert by["c1"]["five_hour_eff"] == 95         # un-rolled → face value
    assert by["c1"]["limit_hit"]["msg"] == "limit"  # active stamp is served
    # an EXPIRED stamp is dropped from the payload (the pill must clear)
    S.kv_set(log, "limit-hit", {"slug": "c1", "ts": now - 9000,
                                "resets_at": now - 10, "msg": "old"})
    by = {r["slug"]: r for r in _get_json(dash + "/api/accounts")}
    assert by["c1"]["limit_hit"] is None


def test_accounts_payload_serves_sched_signals(dash, monkeypatch):
    # the new-session picker's load-balancing signals ride the payload:
    # sched_score (weekly-quota perishability) + sched_ok (5h safety gate).
    monkeypatch.setattr(DS.plugins, "accounts", lambda: [
        {"slug": "c1", "label": "oboard", "alias": "c1"},
        {"slug": "c2", "label": "claude-01", "alias": "c2"}])
    now = time.time()
    for sid, slug, five, seven, reset in [
            ("scd1", "c1", 40, 30, now + 6 * 3600),      # quota left, resets soon
            ("scd2", "c2", 40, 30, now + 5 * 86400)]:    # same, resets far off
        A.session_start({"session_id": sid, "cwd": "/w", "transcript_path": ""})
        log = P.mirror_log(sid)
        S.kv_set(log, "account", {"slug": slug, "label": slug})
        S.kv_set(log, "usage", {"five_hour": five, "five_hour_reset": now + 8000,
                                "seven_day": seven, "seven_day_reset": reset,
                                "ts": now})
    by = {r["slug"]: r for r in _get_json(dash + "/api/accounts")}
    # both clear the 5h gate; the soon-resetting account is more perishable
    assert by["c1"]["sched_ok"] is True and by["c2"]["sched_ok"] is True
    assert by["c1"]["sched_score"] > by["c2"]["sched_score"]


def test_accounts_payload_files_limit_hit_under_its_own_slug(dash, monkeypatch):
    # After a rate-limit migration the adopted session runs under the NEW
    # account (its `account` kv), but the limit-hit stamp in the same state DB
    # still describes the OLD one — it must surface on the blocked account's
    # pill, not the healthy one's (and the migration target picker keys off
    # the same aggregation).
    monkeypatch.setattr(DS.plugins, "accounts", lambda: [
        {"slug": "c1", "label": "oboard", "alias": "c1"},
        {"slug": "c2", "label": "claude-01", "alias": "c2"}])
    A.session_start({"session_id": "accs3", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("accs3")
    now = time.time()
    S.kv_set(log, "account", {"slug": "c1", "label": "oboard"})
    S.kv_set(log, "usage", {"five_hour": 10, "five_hour_reset": now + 8000,
                            "ts": now})
    S.kv_set(log, "limit-hit", {"slug": "c2", "ts": now, "model": "fable",
                                "resets_at": now + 8000, "msg": "limit"})
    by = {r["slug"]: r for r in _get_json(dash + "/api/accounts")}
    assert by["c1"]["limit_hit"] is None           # the healthy account is clean
    assert by["c2"]["limit_hit"]["msg"] == "limit"  # the blocked one shows it
    assert by["c2"]["limit_hit"]["model"] == "fable"  # scope rides through
    assert by["c1"]["usage"]["five_hour"] == 10    # usage stays with the session


def test_accounts_payload_merges_model_windows(dash, monkeypatch):
    # The per-model weekly windows (plugins.model_windows — the OAuth /usage
    # fetch) are MERGED into each account's usage alongside the tokenless 5h/7d
    # snapshot, so the generic bar renderer paints a third bar. five_hour_eff
    # keeps keying off the tokenless snapshot, never the merged-in window.
    monkeypatch.setattr(DS.plugins, "accounts", lambda: [
        {"slug": "c1", "label": "oboard", "alias": "c1"},
        {"slug": "c2", "label": "claude-01", "alias": "c2"}])
    monkeypatch.setattr(DS.plugins, "model_windows", lambda cache=None: {
        "c1": {"seven_day_fable": 91, "seven_day_fable_reset": time.time() + 8000},
        "c2": {"seven_day_fable": 100, "seven_day_fable_reset": time.time() + 8000}})
    A.session_start({"session_id": "accs4", "cwd": "/w", "transcript_path": ""})
    now = time.time()
    S.kv_set(P.mirror_log("accs4"), "account", {"slug": "c1", "label": "oboard"})
    S.kv_set(P.mirror_log("accs4"), "usage",
             {"five_hour": 14, "five_hour_reset": now + 8000,
              "seven_day": 62, "seven_day_reset": now + 8000, "ts": now})
    by = {r["slug"]: r for r in _get_json(dash + "/api/accounts")}
    # c1 has a captured snapshot → third bar rides alongside the account-wide pair
    assert by["c1"]["usage"]["seven_day_fable"] == 91
    assert by["c1"]["usage"]["five_hour"] == 14
    assert by["c1"]["five_hour_eff"] == 14         # from the tokenless snapshot
    # c2 has NO captured snapshot, only the fetched model window → still shown
    assert by["c2"]["usage"]["seven_day_fable"] == 100


def test_accounts_payload_live_window_clears_model_limit_hit(dash, monkeypatch):
    # A MODEL-scoped limit-hit stamp has no reset epoch, so limit_hit_active
    # assumes a week of blockage — but the live per-model window is the fresher
    # truth: below 100% means the cap cleared (Anthropic mid-week resets), so
    # the pill drops; AT 100% the stamp stays.
    monkeypatch.setattr(DS.plugins, "accounts", lambda: [
        {"slug": "c2", "label": "claude-01", "alias": "c2"}])
    win = {"c2": {"seven_day_fable": 100,
                  "seven_day_fable_reset": time.time() + 8000}}
    monkeypatch.setattr(DS.plugins, "model_windows", lambda cache=None: win)
    A.session_start({"session_id": "accs5", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("accs5")
    now = time.time()
    S.kv_set(log, "account", {"slug": "c2", "label": "claude-01"})
    S.kv_set(log, "limit-hit", {"slug": "c2", "ts": now - 3600,
                                "model": "fable", "msg": "fable limit"})
    by = {r["slug"]: r for r in _get_json(dash + "/api/accounts")}
    assert by["c2"]["limit_hit"]["msg"] == "fable limit"   # 100% → still blocked
    win["c2"]["seven_day_fable"] = 3                       # the cap reset mid-week
    by = {r["slug"]: r for r in _get_json(dash + "/api/accounts")}
    assert by["c2"]["limit_hit"] is None                   # live window wins
    assert by["c2"]["usage"]["seven_day_fable"] == 3
    # a NON-model (session-wide) stamp is never touched by model windows
    S.kv_set(log, "limit-hit", {"slug": "c2", "ts": now,
                                "resets_at": now + 8000, "msg": "5h limit"})
    by = {r["slug"]: r for r in _get_json(dash + "/api/accounts")}
    assert by["c2"]["limit_hit"]["msg"] == "5h limit"


def test_post_new_session_account_picker(dash, monkeypatch, tmp_path):
    from plugins.claude_code import account as ACC
    tsv = tmp_path / "accounts.tsv"
    tsv.write_text("c1\toboard\tsvc-1\nc2\tclaude-01\tsvc-2\n")
    monkeypatch.setattr(ACC, "ACCOUNTS_TSV", str(tsv))
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


def test_post_migrate_spawns_the_manual_migrator(dash, monkeypatch, tmp_path):
    """The header's ⇆ migrate button: POST /api/session/<sid>/migrate picks
    the other account (manual → no % ceiling) and spawns the relimit migrator
    in mode=manual. 409 when the registry holds no other account."""
    from plugins.claude_code import account as ACC
    tsv = tmp_path / "accounts.tsv"
    tsv.write_text("c1\toboard\tsvc-1\nc2\tclaude-01\tsvc-2\n")
    monkeypatch.setattr(ACC, "ACCOUNTS_TSV", str(tsv))
    A.session_start({"session_id": "migs1", "cwd": "/w", "transcript_path": ""})
    S.kv_set(P.mirror_log("migs1"), "account", {"slug": "c1", "label": "oboard"})
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    spawned = []
    monkeypatch.setattr(
        DS.SP, "spawn_detached",
        lambda path, argv, log, **kw: spawned.append((path, list(argv), kw))
        or object())
    code, body = _post(dash + "/api/session/migs1/migrate", {})
    assert code == 200 and json.loads(body) == {"ok": True, "to": "c2"}
    path, argv, kw = spawned[0]
    assert path.endswith("claude-relimit.py")
    # trailing "" is the model rung (empty here — no transcript model → keep the
    # current model; the ladder downgrade path is covered in test_l2_relimit)
    assert argv[1:] == ["migs1", "c2", "c2", "/w", "manual", ""]
    assert kw["purpose"] == "relimit:c2 (web)"
    # the success row carries the pick trace (chosen target + reasoning)
    migs = [c for (s, c) in _sf_rows_full("web-migrate") if s == "migs1"]
    assert migs[-1]["ok"] is True and migs[-1]["pick"]["chosen"]["slug"] == "c2"
    # no other account in the registry → 409, nothing spawned
    tsv.write_text("c1\toboard\tsvc-1\n")
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/migs1/migrate", {})
    assert e.value.code == 409 and len(spawned) == 1
    # the REFUSAL is now reconstructible — a `pick` trace naming every account
    # weighed and why (the subtle gap the automatic `relimit-pick` closed, now
    # on the manual ⇆ path too), not a bare "no target".
    migs = [c for (s, c) in _sf_rows_full("web-migrate") if s == "migs1"]
    assert migs[-1]["ok"] is False and migs[-1]["reason"] == "no target"
    assert migs[-1]["pick"]["chosen"] is None
    assert any(cand["slug"] == "c1" for cand in migs[-1]["pick"]["candidates"])
    # a sid this machine has never seen → 404, nothing spawned (the migrator
    # can't tell "parked" from "never existed" — an unknown sid would launch
    # a doomed --resume tab)
    tsv.write_text("c1\toboard\tsvc-1\nc2\tclaude-01\tsvc-2\n")
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/00000000-0000-0000-0000-000000000000"
                     "/migrate", {})
    assert e.value.code == 404 and len(spawned) == 1


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


def test_post_new_session_refuses_resume_of_live_session(dash, monkeypatch,
                                                         tmp_path):
    """A resume-launch for a sid that ALREADY has a live tab is refused (409),
    not launched a second time — the duplicate-tab / two-processes-on-one-
    transcript guard. A stale page can resume-launch a live session; the
    server backstops it. The refusal lands a web-launch ok:False row carrying
    the live window so the page can focus/message it instead."""
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    sid = "16fdc14a-b64f-4243-8885-8888aaaa0e03a"
    fe.wins[sid] = "413"                       # simulate a live claude_session tag
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/sessions/new",
              {"cwd": str(tmp_path), "resume": sid, "prompt": "hi"})
    assert e.value.code == 409
    assert fe.launched == []                   # nothing was launched
    # a fresh (non-resume) launch in the same dir is unaffected
    _post(dash + "/api/sessions/new", {"cwd": str(tmp_path), "prompt": "new"})
    assert fe.launched and fe.launched[-1][1][-1] == "new"


def test_post_new_session_refuses_resume_of_missing_transcript(dash, monkeypatch,
                                                               tmp_path):
    """A resume-launch for a session whose transcript .jsonl is GONE is refused
    (410), not launched into a tab that would instantly die (`claude --resume`
    finds no conversation). The refusal lands a web-launch ok:False row with
    why=transcript missing. A sid with a PRESENT transcript resumes normally,
    and an UNKNOWN sid (no audit row / no known path) is left to the CLI."""
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    # session_start stamps kitty_window_id from the env; clear it so the seeded
    # rows don't read as live (window_for_session) and trip the 409 live guard.
    monkeypatch.delenv("KITTY_WINDOW_ID", raising=False)
    gone_sid = "9c1e2f34-aaaa-4bbb-8ccc-0123456789ab"
    A.session_start({"session_id": gone_sid, "cwd": str(tmp_path),
                     "transcript_path": str(tmp_path / "gone.jsonl")})  # never written
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/sessions/new",
              {"cwd": str(tmp_path), "resume": gone_sid, "prompt": "hi"})
    assert e.value.code == 410
    assert fe.launched == []                    # nothing was launched
    # a session WITH a present transcript resumes normally
    tp = _tw(tmp_path, "there.jsonl", {"type": "user", "message": {"content": "hi"}})
    ok_sid = "9c1e2f34-aaaa-4bbb-8ccc-0123456789ac"
    A.session_start({"session_id": ok_sid, "cwd": str(tmp_path), "transcript_path": tp})
    _post(dash + "/api/sessions/new",
          {"cwd": str(tmp_path), "resume": ok_sid, "prompt": "go"})
    assert fe.launched and fe.launched[-1][1][4:] == ["--resume", ok_sid, "go"]
    # an UNKNOWN sid (no row) is NOT pre-rejected — the CLI decides
    unk = "9c1e2f34-aaaa-4bbb-8ccc-0123456789ad"
    _post(dash + "/api/sessions/new",
          {"cwd": str(tmp_path), "resume": unk, "prompt": "x"})
    assert fe.launched[-1][1][4:] == ["--resume", unk, "x"]


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


# ------------------------------------------------------------------ dictation
# docs/dashboard.md *Web dictation* — the server's whole role is the feature
# probe and the key→grant-JWT trade; audio never touches it. Generic guard
# rejections (missing header / bad Origin / READONLY) are the shared
# _post_guard, covered above.

def test_http_dictate_probe_tracks_key_file(dash, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_DICTATE_KEY_FILE", str(tmp_path / "dg-key"))
    assert _get_json(dash + "/api/dictate") == {"available": False}
    (tmp_path / "dg-key").write_text("sekret\n")
    assert _get_json(dash + "/api/dictate") == {"available": True}


def test_post_dictate_token_no_key_is_501(dash, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_DICTATE_KEY_FILE", str(tmp_path / "absent"))
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/dictate/token", {"sample_rate": 48000})
    assert e.value.code == 501


def test_post_dictate_token_rejects_bogus_rates(dash, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_DICTATE_KEY_FILE", str(tmp_path / "dg-key"))
    (tmp_path / "dg-key").write_text("sekret")
    # missing, wrong-typed (incl. bool — a Python int subclass), out-of-range
    for rate in (None, "48000", True, 7999, 500000):
        with pytest.raises(urllib.error.HTTPError) as e:
            _post(dash + "/api/dictate/token", {"sample_rate": rate})
        assert e.value.code == 400, rate


def test_post_dictate_token_mints_and_builds_url(dash, tmp_path, monkeypatch):
    # the grant call goes to a fake server (CLAUDE_DICTATE_GRANT_URL — the
    # env-knob convention): assert the on-disk key arrives as Token auth and
    # the response carries the JWT + a fully-assembled listen URL, key-free
    seen = {}

    class Grant(BaseHTTPRequestHandler):
        def do_POST(self):
            seen["auth"] = self.headers.get("Authorization")
            body = json.dumps({"access_token": "jwt-abc",
                               "expires_in": 30}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):          # keep pytest output clean
            pass

    gsrv = ThreadingHTTPServer(("127.0.0.1", 0), Grant)
    threading.Thread(target=gsrv.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("CLAUDE_DICTATE_KEY_FILE", str(tmp_path / "dg-key"))
        monkeypatch.setenv("CLAUDE_DICTATE_KEYTERMS_FILE",
                           str(tmp_path / "terms"))
        monkeypatch.setenv("CLAUDE_DICTATE_GRANT_URL",
                           "http://127.0.0.1:%d/grant"
                           % gsrv.server_address[1])
        (tmp_path / "dg-key").write_text("sekret\n")
        (tmp_path / "terms").write_text("scorebar\n# a comment\n\ntailer\n")
        code, body = _post(dash + "/api/dictate/token", {"sample_rate": 48000})
        assert code == 200
        out = json.loads(body)
        assert out["token"] == "jwt-abc" and out["expires_in"] == 30
        assert seen["auth"] == "Token sekret"
        url = out["ws_url"]
        assert url.startswith("wss://api.deepgram.com/v1/listen?")
        assert "model=nova-3" in url and "interim_results=true" in url
        assert "encoding=linear16" in url and "sample_rate=48000" in url
        assert "smart_format=true" in url and "channels=1" in url
        assert url.count("keyterm=") == 2
        assert "keyterm=scorebar" in url and "keyterm=tailer" in url
        assert "sekret" not in body        # the key never reaches the page
    finally:
        gsrv.shutdown()
        gsrv.server_close()


def test_dictate_keyterms_project_layering(tmp_path, monkeypatch):
    # The merge structure (no vocabulary policy here): nearest project file →
    # outer project file → the user-global file; every file parses the same
    # (#-comments, blanks); first occurrence wins the dedup; empty cwd (and
    # the endpoint's degraded bad-cwd) = global only.
    from dashboard import dictate
    outer = tmp_path / "proj"
    inner = outer / "sub"
    (outer / ".claude").mkdir(parents=True)
    (inner / ".claude").mkdir(parents=True)
    (outer / ".claude" / "deepgram-keyterms").write_text("alpha\nshared\n")
    (inner / ".claude" / "deepgram-keyterms").write_text(
        "# comment\n\nnearest\nshared\n")
    g = tmp_path / "global-terms"
    g.write_text("shared\nglobaly\n")
    monkeypatch.setenv("CLAUDE_DICTATE_KEYTERMS_FILE", str(g))
    # pin the user config dir (the walk's tail) into the tmp tree — a real
    # ~/.claude/deepgram-keyterms on the dev machine must not leak in
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "usercfg"))
    terms = dictate.keyterms(str(inner))
    assert terms == ["nearest", "shared", "alpha", "globaly"]
    assert dictate.keyterms("") == ["shared", "globaly"]


def test_dictate_keyterms_cap_prefers_nearest(tmp_path, monkeypatch):
    from dashboard import dictate
    proj = tmp_path / "p"
    (proj / ".claude").mkdir(parents=True)
    (proj / ".claude" / "deepgram-keyterms").write_text(
        "\n".join("near%d" % i for i in range(dictate.KEYTERMS_MAX)))
    g = tmp_path / "g"
    g.write_text("evicted-global")
    monkeypatch.setenv("CLAUDE_DICTATE_KEYTERMS_FILE", str(g))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "usercfg"))
    terms = dictate.keyterms(str(proj))
    assert len(terms) == dictate.KEYTERMS_MAX
    assert "evicted-global" not in terms      # the FARTHEST layer falls off


def test_dictate_available_degrades_on_bad_key_file(tmp_path, monkeypatch):
    from dashboard import dictate
    # a non-UTF-8 key file raises UnicodeDecodeError (a ValueError) out of
    # _read — available() must degrade to False (feature invisible), never let
    # it escape the probe. A missing file is False; a good file is True.
    keyf = tmp_path / "key"
    keyf.write_bytes(b"\xff\xfe\x00bad")
    monkeypatch.setenv("CLAUDE_DICTATE_KEY_FILE", str(keyf))
    assert dictate.available() is False
    monkeypatch.setenv("CLAUDE_DICTATE_KEY_FILE", str(tmp_path / "nope"))
    assert dictate.available() is False
    keyf.write_text("dg-key-123")
    monkeypatch.setenv("CLAUDE_DICTATE_KEY_FILE", str(keyf))
    assert dictate.available() is True


def test_post_dictate_token_cwd_keys_project_vocab(dash, tmp_path, monkeypatch):
    # the endpoint contract: a valid cwd layers project terms ahead of global
    # in the minted ws_url; a bogus/missing cwd degrades to global-only, 200
    class Grant(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.dumps({"access_token": "jwt-x",
                               "expires_in": 30}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    gsrv = ThreadingHTTPServer(("127.0.0.1", 0), Grant)
    threading.Thread(target=gsrv.serve_forever, daemon=True).start()
    try:
        proj = tmp_path / "proj"
        (proj / ".claude").mkdir(parents=True)
        (proj / ".claude" / "deepgram-keyterms").write_text("projterm\n")
        g = tmp_path / "g"
        g.write_text("globalterm\n")
        monkeypatch.setenv("CLAUDE_DICTATE_KEY_FILE", str(tmp_path / "k"))
        (tmp_path / "k").write_text("sekret")
        monkeypatch.setenv("CLAUDE_DICTATE_KEYTERMS_FILE", str(g))
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "usercfg"))
        monkeypatch.setenv("CLAUDE_DICTATE_GRANT_URL",
                           "http://127.0.0.1:%d/" % gsrv.server_address[1])
        code, body = _post(dash + "/api/dictate/token",
                           {"sample_rate": 48000, "cwd": str(proj)})
        url = json.loads(body)["ws_url"]
        assert code == 200
        assert url.index("keyterm=projterm") < url.index("keyterm=globalterm")
        for bad in (str(tmp_path / "nope"), 123, None):
            code, body = _post(dash + "/api/dictate/token",
                               {"sample_rate": 48000, "cwd": bad})
            url = json.loads(body)["ws_url"]
            assert code == 200 and "projterm" not in url \
                and "keyterm=globalterm" in url, bad
    finally:
        gsrv.shutdown()
        gsrv.server_close()


def test_post_dictate_token_grant_failure_is_502(dash, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_DICTATE_KEY_FILE", str(tmp_path / "dg-key"))
    (tmp_path / "dg-key").write_text("sekret")
    # nothing listens here — the grant call fails fast, the page gets a 502
    monkeypatch.setenv("CLAUDE_DICTATE_GRANT_URL", "http://127.0.0.1:9/grant")
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/dictate/token", {"sample_rate": 48000})
    assert e.value.code == 502


def test_canon_cwd_collapses_symlinked_repo(tmp_path):
    """canon_cwd resolves a symlinked repo path so the list groups one project
    under one entry (the baqylau rename left ~/code/personal/kitty as a symlink
    to .../baqylau; pre-move sessions record the /kitty spelling). Empty stays
    empty — realpath('') would be the dashboard's OWN cwd."""
    real = tmp_path / "baqylau"
    real.mkdir()
    link = tmp_path / "kitty"
    link.symlink_to(real, target_is_directory=True)
    assert DS.canon_cwd(str(link)) == str(real)
    assert DS.canon_cwd(str(link / ".claude" / "worktrees" / "x")) \
        == str(real / ".claude" / "worktrees" / "x")   # nested under the symlink
    assert DS.canon_cwd(str(real)) == str(real)         # already-canonical unchanged
    assert DS.canon_cwd("") == ""                       # never the process cwd


def test_group_dir_resolves_worktree_owner(tmp_path):
    """_group_dir — the list's grouping-key resolver — maps a session's cwd to
    the directory it files under: a linked-worktree cwd resolves to its OWNING
    main checkout (so N worktrees of one repo aggregate as one project), a main
    checkout / non-checkout resolves to itself, '' stays ''. File-reads only, no
    dirty subprocess. Fed start_cwd (the frozen original), so a later cd can't
    change it."""
    repo = tmp_path / "repo"
    (repo / ".git" / "worktrees" / "wt1").mkdir(parents=True)
    (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    wt = tmp_path / "wt1"
    wt.mkdir()
    (wt / ".git").write_text("gitdir: %s\n" % (repo / ".git" / "worktrees" / "wt1"))
    assert DS._group_dir(str(wt)) == str(repo)           # worktree -> owning checkout
    assert DS._group_dir(str(repo)) == str(repo)         # main checkout -> itself
    plain = tmp_path / "plain"
    assert DS._group_dir(str(plain)) == str(plain)       # non-checkout -> itself
    assert DS._group_dir("") == ""


# --------------------------------------------------- suggestion (ghost) probe
# Pure parse over an ANSI get-text capture: the faint (SGR 2) input line
# between the grey divider rules is Claude Code's greyish "suggested answer"
# ghost text (docs/dashboard.md, *Web ghost suggestion*); real (normal-weight)
# input is NOT surfaced. Fixtures mirror the live capture (window 291): the
# input box is `❯` + NBSP + content, framed by `─` rules.
from dashboard import suggestion as SUG   # noqa: E402

_RULE = "\x1b[m\x1b[38:2:136:136:136m" + "─" * 100


def _screen(input_line):
    return ("\x1b[m  some prior turn output\n"
            + _RULE + "\n" + input_line + "\n" + _RULE + "\n"
            + "\x1b[m  \x1b[36m[Opus 4.8]\x1b[38:2:153:153:153m │ status line\n")


def test_suggestion_parse_faint_ghost():
    s = _screen("\x1b[m❯\xa0\x1b[22;2mapply the MODULES filesystem-scan fix")
    assert SUG.parse(s) == "apply the MODULES filesystem-scan fix"


def test_suggestion_parse_real_input_is_none():
    # normal-weight text on the input line is the user's own line, not a ghost
    assert SUG.parse(_screen("\x1b[m❯\xa0hello there this is typed")) is None


def test_suggestion_parse_empty_box_is_none():
    assert SUG.parse(_screen("\x1b[m❯\xa0")) is None


def test_suggestion_parse_no_box_is_none():
    assert SUG.parse("just\noutput\nlines") is None
    assert SUG.parse("") is None
    assert SUG.parse(None) is None


def test_suggestion_parse_wrapped_ghost_joins_lines():
    # a long suggestion wraps onto a continuation line inside the box; both
    # faint lines join into one whitespace-normalized string
    s = (_RULE + "\n"
         + "\x1b[m❯\xa0\x1b[22;2mapply the MODULES filesystem-scan fix and\n"
         + "\x1b[m  \x1b[22;2mthen re-run the suite\n"
         + _RULE + "\n")
    assert SUG.parse(s) == "apply the MODULES filesystem-scan fix and then re-run the suite"


# suggestion.typed is the COMPLEMENT of parse: the REAL (non-faint) input-box
# text — the tell that the user is composing a reply AT THE TERMINAL, the signal
# the deferred Telegram alert's 'done' arm suppresses on.
def test_suggestion_typed_real_input():
    assert SUG.typed(_screen("\x1b[m❯\xa0hello there this is typed")) \
        == "hello there this is typed"


def test_suggestion_typed_ghost_only_is_none():
    # a faint ghost suggestion is NOT the user typing — typed() ignores it
    assert SUG.typed(_screen("\x1b[m❯\xa0\x1b[22;2mapply the fix")) is None


def test_suggestion_typed_empty_and_no_box_is_none():
    assert SUG.typed(_screen("\x1b[m❯\xa0")) is None
    assert SUG.typed("just\noutput\nlines") is None
    assert SUG.typed("") is None
    assert SUG.typed(None) is None
