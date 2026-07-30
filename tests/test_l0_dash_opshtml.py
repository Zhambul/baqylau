# tests/test_l0_dash_opshtml.py — L0 dashboard: the ops->HTML presenter (ansi / ops / markdown / tools).
#
# One subject out of the former 8468-line L0 dashboard monolith; the
# shared HTTP/audit helpers live in tests/dashkit.py and the in-process
# server fixture (`dash`) in tests/conftest.py.
import sys

import pytest
from conftest import REPO

if REPO not in sys.path:
    sys.path.insert(0, REPO)

from core import ops as O
from dashboard import opshtml
import importlib.util


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


def test_ops_label_gut_bubbled_field():
    # core/ops.py sets the bubbled prose-drop signal only when asked; default off.
    assert O.label("h", (1, 2, 3), bubbled=True).get("bubbled") == 1
    assert O.gut("b", (1, 2, 3), bubbled=True).get("bubbled") == 1
    assert "bubbled" not in O.label("h", (1, 2, 3))
    assert "bubbled" not in O.gut("b", (1, 2, 3))


def test_op_items_scope_drops_a_bubbled_block_across_tools():
    # THE unified prose-drop: in agent scope, an op carrying `bubbled` (its content
    # is re-bubbled via plugins.conversation) is dropped with its whole copy group,
    # while the agent's non-bubbled activity (a command) stays — one signal for a
    # Claude subagent AND a codex sidecar, no per-tool sniffing.
    scope = {"sub:a1", "team:a1", "codex:a1"}
    items = opshtml.op_items(
        [{"t": "label", "s": "⇢ prompt", "c": [1, 2, 3], "g": "p1",
          "src": "sub:a1", "bubbled": 1, "web": 1},
         {"t": "gut", "s": "the brief", "c": [1, 2, 3], "g": "p1",
          "src": "sub:a1", "bubbled": 1, "web": 1},
         {"t": "label", "s": "▶ foreground", "c": [1, 2, 3], "g": "b1",
          "src": "sub:a1"},
         {"t": "gut", "s": "cmd output", "c": [1, 2, 3], "g": "b1",
          "src": "sub:a1"}], "k", scope=scope)
    # the bubbled prompt block (header + body, same group) is gone; the command stays
    assert [(it["g"], it["t"]) for it in items] == [("b1", "label"), ("b1", "gut")]


def test_ops_label_gut_chrome_field():
    # core/ops.py sets the host-scaffolding flag only when asked; default off.
    assert O.label("h", (1, 2, 3), chrome=True).get("chrome") == 1
    assert O.gut("b", (1, 2, 3), chrome=True).get("chrome") == 1
    assert "chrome" not in O.label("h", (1, 2, 3))
    assert "chrome" not in O.gut("b", (1, 2, 3))


def test_op_items_drops_chrome_in_every_view():
    """THE host-scaffolding drop: an op the PRODUCER marked `chrome` is the host's
    frame around a child's stream (a run banner, a `⚙ model` line, a run footer,
    the lead's own subagent launch header), and NO web view shows it — the child
    has a card that says all of it. Structural, so it holds in all three arms; the
    text sniffers stay only for ops already on disk (below).

    Its BODY goes with it through the copy group, exactly as the scope drops do."""
    from core import slots as SL
    rgb = list(SL.CODEX_PALETTE[0])
    chrome = [
        {"t": "label", "s": "codex ▶ cli", "c": rgb, "g": "c0", "chrome": 1},
        {"t": "gut", "s": "the banner's body", "c": rgb, "g": "c0"},
        {"t": "gut", "s": "⚙ gpt-5.6-luna · low", "c": rgb, "chrome": 1},
        {"t": "label", "s": "▶ cmd", "c": rgb, "g": "b1"},   # real activity
        {"t": "code", "s": "echo hi", "g": "b1"},
    ]

    def _texts(items):
        return " ".join(it.get("html", "") for it in items)

    # 1. the SESSION view (unstamped ops — the lead's own launch header lives here)
    lead = opshtml.op_items(list(chrome), "k")
    assert "codex ▶" not in _texts(lead) and "the banner's body" not in _texts(lead)
    assert "gpt-5.6-luna" not in _texts(lead)
    assert "echo" in _texts(lead)                     # activity kept
    # 2. AGENT SCOPE
    scoped = [dict(op, src="codex:cli") for op in chrome]
    sc = opshtml.op_items(scoped, "k", scope={"codex:cli"})
    assert "codex ▶" not in _texts(sc) and "gpt-5.6-luna" not in _texts(sc)
    assert "echo" in _texts(sc)
    # 3. the STANDALONE codex LEAD view
    cl = opshtml.op_items(list(chrome), "k", host_lead=True)
    assert "codex ▶" not in _texts(cl) and "gpt-5.6-luna" not in _texts(cl)
    assert "echo" in _texts(cl)


def test_op_items_scope_drops_codex_terminal_chrome():
    # A codex run's terminal-only CHROME — the `codex ▶` run banner, the
    # `⚙ model · effort` line, the `■ codex … ended` footer — must NOT show on the
    # web agent scope (model + duration live on the agent card, like a Claude
    # subagent). Dropped by actclass.codex_chrome BEFORE as_lead recolours the codex
    # palette. The run's real activity (a command) stays.
    from core import slots as SL
    rgb = list(SL.CODEX_PALETTE[0])
    scope = {"codex:cli"}
    ops = [
        {"t": "label", "s": "codex ▶ cli", "c": rgb, "src": "codex:cli"},
        {"t": "gut", "s": "⚙ gpt-5.6-luna · low", "c": rgb, "src": "codex:cli"},
        {"t": "label", "s": "▶ cmd", "c": rgb, "g": "b1", "src": "codex:cli"},
        {"t": "code", "s": "echo hi", "g": "b1", "src": "codex:cli"},
        {"t": "label", "s": "■ codex cli ended · 3.2s · 1k in", "c": rgb,
         "src": "codex:cli"},
    ]
    items = opshtml.op_items(ops, "k", scope=scope)
    html = " ".join(it.get("html", "") for it in items)
    assert "cmd" in html and "echo" in html        # real activity kept (highlighted)
    assert "codex" not in html                     # banner + footer ("codex …") dropped
    assert "gpt-5.6-luna" not in html              # ⚙ chrome dropped
    assert "ended" not in html                     # footer dropped
    # Claude ops (no codex palette) are untouched by the chrome drop
    claude = [{"t": "label", "s": "⚙ not codex", "c": [1, 2, 3], "src": "sub:a1"}]
    assert opshtml.op_items(claude, "k", scope={"sub:a1"})


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


def test_msg_html_mail_is_one_bubble_labelled_by_direction():
    """Team mail is ONE bubble in two directions — the same general message
    bubble, with the label as the only thing saying which way it went. Both
    directions read the same peer slot, and the words are core/streamfmt's
    (MAIL_FROM/MAIL_TO), the pair the producer words its chip with."""
    from core import streamfmt as SF
    inc = opshtml.msg_html("teammsg", "your brief", "team-lead")
    out = opshtml.msg_html("sendmsg", "my report", "main")
    assert 'class="msg teammsg"' in inc and 'class="msg sendmsg"' in out
    assert SF.MARK_MAIL + " " + SF.MAIL_FROM % "team-lead" in inc
    assert SF.MARK_MAIL + " " + SF.MAIL_TO % "main" in out
    # …and the body is the ordinary markdown bubble, never a command-output box
    assert "<div class=\"md\">" in out and "my report" in out


def test_msg_html_plan_pair_labels_carry_the_verdict():
    """The plan exchange is two bubbles, the ask pair's twin: the plan Claude
    proposed and the verdict on it. Two of the three outcomes have no BODY, so
    the verdict rides the LABEL (+ a class per outcome, for the hue)."""
    from dashboard.opshtml import tools as T
    plan = opshtml.msg_html("plan", "# Step one")
    assert 'class="msg plan"' in plan and T.PLAN_WHO in plan
    assert "<h1>Step one</h1>" in plan          # the ordinary markdown bubble
    for dec, label in T.PLAN_DECIDED.items():
        h = opshtml.msg_html("plandecision", "", "", None, "", (), False, dec)
        assert 'class="msg plandecision %s"' % dec in h and label in h
    # `changes` is the one outcome WITH a body — the feedback you typed, which
    # exists nowhere else in the transcript
    fb = opshtml.msg_html("plandecision", "make it **three** steps", "", None,
                          "", (), False, "changes")
    assert "<strong>three</strong>" in fb
    # …and an approval whose plan was EDITED in the dialog says so, because the
    # `plan` bubble above it is then the pre-edit text
    ed = opshtml.msg_html("plandecision", "", "", None, "", (), False,
                          "approved", True)
    assert 'class="pedit"' in ed and T.PLAN_EDITED in ed
    assert 'class="pedit"' not in opshtml.msg_html(
        "plandecision", "", "", None, "", (), False, "approved")
    # neither half is a re-runnable prompt: no rewind ↶, no data-txt
    assert "data-txt" not in plan and 'class="rw"' not in plan


def test_msg_html_prompt_stamps_tree_position():
    # data-par is the prompt's parentUuid — what the page's dropSuperseded
    # matches siblings on to drop a bubble the terminal discarded. Only prompts
    # carry it (nothing else can be superseded).
    h = opshtml.msg_html("prompt", "hi", "", None, "a1")
    assert 'data-par="a1"' in h
    assert 'data-par' not in opshtml.msg_html("prompt", "hi")
    assert 'data-par' not in opshtml.msg_html("message", "hi", "", None, "a1")


def test_msg_html_injected_prompt_is_a_system_bubble():
    """An INJECTED user turn (transcript._injected: a Stop hook's feedback, a
    loaded skill's body, another session's teammate mail) must not wear the YOU
    label — it says ⚙ system, in its own colour, with no rewind affordance. The
    `sys` class rides ALONGSIDE `prompt`: the page's focus logic keys on the kind,
    and an injected turn must not read as a turn boundary."""
    h = opshtml.msg_html("prompt", "Stop hook feedback: check the wiki", meta=True)
    assert 'class="msg prompt sys"' in h
    assert "⚙ system" in h and ">you" not in h
    # no rewind target: neither the ↶ button nor the data-txt the menu POSTs
    assert 'class="rw"' not in h and "data-txt" not in h
    # a real prompt is untouched
    y = opshtml.msg_html("prompt", "hi")
    assert 'class="msg prompt"' in y and "you" in y and 'class="rw"' in y
    # `meta` is prompt-scoped — nothing else can be an injected user turn
    assert "sys" not in opshtml.msg_html("message", "hi", meta=True)
    assert "sys" not in opshtml.msg_html("teammsg", "hi", "lead", meta=True)


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


def test_tool_html_lexer_for_matches_the_langs_families():
    """The presenter's endswith-keyed lookup over the shared coderender.LANGS
    resolves each family member to its OWN lexer (.jsx/.tsx/.cjs are not the
    base extension, .kts is not .ts) — the suffix-uniqueness the table's keys
    guarantee, checked from the consumer that depends on it."""
    from dashboard.opshtml.tools import _lexer_for
    for path, exp in [("/w/domshim.js", "javascript"), ("/w/cfg.cjs", "javascript"),
                      ("/w/App.jsx", "jsx"), ("/w/main.ts", "typescript"),
                      ("/w/Panel.tsx", "tsx"), ("/w/build.gradle.kts", "kotlin"),
                      ("/w/index.html", "html"), ("/w/style.css", "css"),
                      ("/w/notes.txt", None)]:
        assert _lexer_for(path) == exp, path


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
    monkeypatch.setattr(opshtml.tools, "_read_html", boom)
    assert opshtml.tool_html("Read", {"file_path": "x.py"}) is None


def test_tool_output_html_only_bash():
    assert opshtml.tool_output_html("plain", False, "Read") is None
    h = opshtml.tool_output_html("\x1b[31mred\x1b[0m ok", False, "Bash")
    assert h is not None and "<pre class=\"oc\">" in h and "color:rgb(" in h
