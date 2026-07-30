# L5 — markdown renderer (core/mdrender.py) + detection (plugins tools.md_source).
#
# core/mdrender turns markdown into styled ANSI for the mirror. The contract it
# MUST hold (like everything a producer emits): zero-width SGR + LOGICAL newlines
# only, never a hard wrap to a column — so wrap_gutter can reflow it at paint time.
# These are pure-Python unit tests (no mirror subprocess needed).
import sys

from conftest import REPO

if REPO not in sys.path:
    sys.path.insert(0, REPO)

from core import mdrender as M            # noqa: E402
from core import render as R              # noqa: E402
from plugins.claude_code.tools import md_source  # noqa: E402

SAMPLE = """# Title

Some **bold** and *italic* and `code` and a [link](https://x.com).

- one
- two
  - nested

1. first
2. second

> a quote
> second line

```python
x = 1
y = 2
```

## Sub heading

done.
"""


def _segments(md, chunks=1):
    """Feed `md` through the streamer in `chunks` pieces, return the (text, bg) segments."""
    s = M.MarkdownStreamer()
    out = []
    n = max(1, len(md) // chunks)
    for i in range(0, len(md), n):
        out += s.feed(md[i:i + n])
    out += s.close()
    return out


def _render_all(md, chunks=1):
    """Just the styled text of every segment (bg dropped)."""
    return [t for t, _bg in _segments(md, chunks)]


def test_wenmode_available():
    # The dev env pins wenmode; if this fails the fallback path is silently in use.
    assert M.AVAILABLE, "wenmode not importable — install requirements-dev.txt"


def test_styling_present():
    joined = "".join(_render_all(SAMPLE))
    assert R.BANNER in joined, "heading should be bold-amber banner"
    assert "\033[1m" in joined and "\033[22m" in joined, "bold on/off"
    assert "\033[3m" in joined and "\033[23m" in joined, "italic on/off"
    assert "•" in joined, "unordered bullet"
    assert "1." in joined and "2." in joined, "ordered list ordinals"
    assert "\x1b]8;;https://x.com" in joined, "OSC-8 hyperlink for the link"
    assert R.COL["builtin"] in joined, "inline code colour"


def test_width_independent_no_hard_wrap():
    # A long paragraph must NOT be pre-wrapped to a column — only logical newlines,
    # which the streamer emits at markdown block boundaries. Strip ANSI, and assert
    # no visible line was cut near a small column count.
    para = "word " * 60
    md = "# H\n\n" + para.strip() + "\n"
    joined = "".join(_render_all(md))
    for line in R.strip_ansi(joined).split("\n"):
        assert len(line) < 120 or "word" not in line or line.count("word") > 25, \
            "paragraph appears hard-wrapped to a column"
    # The 60-word paragraph should survive as ONE logical line (no mid-paragraph \n).
    body = R.strip_ansi(joined).split("\n")
    assert any(l.count("word") == 60 for l in body), "paragraph was split across lines"


def test_streaming_matches_oneshot():
    # Chunked feeding must produce the same content as one-shot (block buffering
    # holds incomplete blocks); fenced code spanning chunk boundaries must not split.
    # Each block becomes its own gut op (own line in the mirror), so join with \n.
    whole = R.strip_ansi("\n".join(_render_all(SAMPLE, chunks=1)))
    pieces = R.strip_ansi("\n".join(_render_all(SAMPLE, chunks=7)))
    assert "x = 1" in pieces and "y = 2" in pieces, "code block content lost when chunked"
    assert whole.split() == pieces.split(), "chunked render differs from one-shot"


def test_fallback_when_wenmode_absent(monkeypatch):
    # Force the no-wenmode path: the streamer degrades to render.markdown(), still
    # emitting the inline subset (bold, heading), never raising.
    s = M.MarkdownStreamer()
    monkeypatch.setattr(s, "wen", None)
    segs = s.feed("# Hi\n\nsome **bold** text\n") + s.close()
    joined = "".join(t for t, _bg in segs)
    assert joined, "fallback produced nothing"
    assert "\033[1m" in joined, "fallback should still bold"


def test_empty_and_whitespace():
    assert M.MarkdownStreamer().close() == []
    s = M.MarkdownStreamer()
    assert s.feed("\n\n   \n") == []


def test_frontmatter_rendered_as_header():
    md = "---\ntitle: My Doc\ntags: [a, b]\n---\n\n# Heading\n\nbody\n"
    joined = "".join(_render_all(md))
    plain = R.strip_ansi(joined)
    assert "title" in plain and "My Doc" in plain, "frontmatter key/value shown"
    assert "---" not in plain, "frontmatter fence should not render as a rule"
    # The dim key colour is applied (not a heading banner).
    assert R.DIM + "title" in joined


def test_wikilinks_styled_and_unbracketed():
    md = "See [[casino-router]] and [[target|the alias]].\n"
    joined = "".join(_render_all(md))
    plain = R.strip_ansi(joined)
    assert "[[" not in plain and "]]" not in plain, "brackets stripped"
    assert "casino-router" in plain, "target shown"
    assert "the alias" in plain and "target" not in plain, "alias shown, not target"
    assert R.COL["func"] + "casino-router" in joined, "wikilink coloured like a link"


def test_fenced_code_highlighted_by_language():
    md = "```java\npublic interface Foo {}\n```\n"
    joined = "".join(_render_all(md))
    assert R.COL["kw"] + "public" in joined, "java keyword highlighted"
    assert "java\n" not in R.strip_ansi(joined), "language tag not shown as a bare line"


def test_fenced_code_is_its_own_bg_panel():
    md = "before\n\n```python\nx = 1\n```\n\nafter\n"
    segs = _segments(md)
    bgs = [bg for _t, bg in segs]
    assert M.CODE_BG in bgs, "code block should carry the CODE_BG panel background"
    # prose segments carry no background.
    for text, bg in segs:
        if "x = 1" not in R.strip_ansi(text):
            assert bg is None, "prose must not have a background"
    # The bg fills to the pane width at paint time (wrap_gutter), not in the producer.
    code_text = next(t for t, bg in segs if bg == M.CODE_BG)
    prefix = R.fg(120, 120, 120) + "│ " + R.RST
    painted = R.wrap_gutter(code_text, 40, prefix, 2, bg=M.CODE_BG)
    bg_on = "\033[48;2;%d;%d;%dm" % M.CODE_BG
    assert bg_on in painted, "background SGR emitted at paint time"
    # every painted row reaches the pane edge (width 40): visible width == 40.
    for row in painted.split("\n"):
        assert R.dwidth(R.strip_ansi(row)) == 40, "code row not filled to pane width"


def test_gut_without_bg_is_byte_identical():
    # The bg feature must not perturb ordinary gut ops (golden stability).
    prefix = R.fg(120, 120, 120) + "│ " + R.RST
    a = R.wrap_gutter("hello world\nsecond line", 30, prefix, 2)
    b = R.wrap_gutter("hello world\nsecond line", 30, prefix, 2, bg=None)
    assert a == b


def test_blocks_are_blank_separated():
    md = "# H\n\npara one\n\npara two\n"
    plain = R.strip_ansi("".join(_render_all(md)))
    # A blank line between each block (heading / para / para).
    assert "\n\n" in plain, "blocks should be blank-line separated"


def test_gfm_table_rendered_with_rail():
    md = "| A | B |\n|---|---|\n| 1 | 2 |\n"
    out = "".join(_render_all(md))
    plain = R.strip_ansi(out)
    # Cells separated by a │ rail; header row bold; a dim rule under the header.
    assert "│" in plain, "table cells should be joined by a │ rail"
    assert "A" in plain and "B" in plain and "1" in plain and "2" in plain
    assert "\033[1m" in out, "header row should be bold"
    # Each row is ONE logical line (reflow-safe) — the body row has no interior \n.
    assert "1 │ 2" in plain


def test_nested_code_in_list_keeps_its_lines():
    # A fenced block inside a loose list item must not collapse (the safe-cut used
    # to split at the blank line and re-parse the indented fence standalone).
    md = "- item\n\n  ```py\n  x = 1\n  y = 2\n  ```\n"
    for chunks in (1, 8):                     # whole, and byte-dribbled
        plain = R.strip_ansi("\n".join(_render_all(md, chunks=chunks)))
        assert "x = 1" in plain and "y = 2" in plain, f"code lost (chunks={chunks})"
        # both code lines survive on their own logical lines under the bullet
        assert "x = 1\n" in plain + "\n" and "item" in plain


def test_task_list_checkboxes():
    plain = R.strip_ansi("\n".join(_render_all("- [ ] todo\n- [x] done\n- plain\n")))
    assert "☐ todo" in plain and "☑ done" in plain
    assert "• plain" in plain, "a non-checkbox item still gets a bullet"


def test_footnotes_reference_and_definition():
    md = "See ref[^1] here.\n\n[^1]: the note body\n"
    plain = R.strip_ansi("\n".join(_render_all(md)))
    assert "ref[1] here" in plain, "reference [^1] should render as [1]"
    assert "1. the note body" in plain, "definition should render as a labelled line"
    assert "[^1]" not in plain, "raw [^ syntax should not survive"


# ---- JSON ---------------------------------------------------------------------

from core import jsonrender as JSON  # noqa: E402


def test_json_pretty_printed_and_coloured():
    s = JSON.JsonStreamer()
    raw = '{"name":"adapter","count":3,"on":true,"tags":["a","b"],"n":null}'
    s.feed(raw[:15]); s.feed(raw[15:])          # chunked
    segs = s.close()
    assert len(segs) == 1
    text, bg = segs[0]
    assert bg is None, "JSON has no background panel"
    plain = R.strip_ansi(text)
    assert '\n  "name"' in plain, "pretty-printed with 2-space indent"
    assert R.COL["func"] + '"name"' in text, "keys coloured (blue)"
    assert R.COL["str"] + '"adapter"' in text, "string values coloured (green)"
    assert R.COL["kw"] + "true" in text, "booleans coloured"


def test_json_invalid_falls_back_to_raw():
    s = JSON.JsonStreamer()
    s.feed("this is not json\njust a log line\n")
    segs = s.close()
    assert len(segs) == 1
    text, bg = segs[0]
    assert bg is None, "non-JSON must not get a panel"
    assert "not json" in R.strip_ansi(text)


def test_jsonl_each_line_pretty_printed():
    s = JSON.JsonStreamer()
    s.feed('{"a":1}\n{"b":2}\n{"c":[1,2]}')
    segs = s.close()
    assert len(segs) == 1
    text, bg = segs[0]
    assert bg is None
    plain = R.strip_ansi(text)
    # every doc pretty-printed (indent=2), blank-line separated
    assert '"a": 1' in plain and '"b": 2' in plain and '"c"' in plain
    assert '\n\n' in plain, "JSONL docs are blank-line separated"
    assert R.COL["func"] + '"a"' in text, "keys coloured per doc"


def test_jsonl_with_a_bad_line_falls_back_to_verbatim():
    # A single non-JSON line taints the whole stream -> raw, never a partial view.
    s = JSON.JsonStreamer()
    s.feed('{"a":1}\nnot json here\n{"b":2}')
    plain = R.strip_ansi("".join(t for t, _ in s.close()))
    assert "not json here" in plain and '"a": 1' not in plain


def test_json_partial_is_not_rendered_until_close():
    s = JSON.JsonStreamer()
    assert s.feed('{"a":') == [], "JSON never renders on a partial buffer"
    assert s.feed('1}') == []
    assert len(s.close()) == 1


# ---- YAML ---------------------------------------------------------------------

from core import yamlrender as YAML  # noqa: E402


def test_yaml_coloured_no_reformat():
    s = YAML.YamlStreamer()
    src = "# a config\nname: adapters-api\nreplicas: 3\nitems:\n  - a\n  - b\n"
    s.feed(src)
    segs = s.close()
    assert len(segs) == 1
    text, bg = segs[0]
    assert bg is None, "YAML has no background panel"
    # Raw structure preserved byte-for-byte (comments kept, keys not reordered).
    assert R.strip_ansi(text).rstrip("\n") == src.rstrip("\n")
    assert R.COL["func"] + "name" in text, "keys coloured (blue)"
    assert R.COL["cmt"] + "# a config" in text, "comment coloured (grey)"


def test_yaml_fallback_without_pygments(monkeypatch):
    # render_yaml returns None if the lexer is unavailable; the streamer still emits
    # the raw text verbatim.
    monkeypatch.setattr(YAML, "render_yaml", lambda _t: None)
    s = YAML.YamlStreamer()
    s.feed("a: 1\nb: 2\n")
    out = s.close()
    assert out and "a: 1" in R.strip_ansi(out[0][0])


# ---- source code --------------------------------------------------------------

from core import coderender as CODE  # noqa: E402


def test_code_highlighted_per_language():
    # (source, the STRUCTURAL colour that language must show) — a comment is
    # always required on top. Markup has no keywords (html's tags land on `def`,
    # scss's $vars on `var`), so the structural colour is per-language rather
    # than a uniform "kw".
    cases = {
        "python": ("import os\ndef greet(n):\n    return f'hi {n}'  # c\n", "kw"),
        "java": ('public class Foo { int n = 3; // c\n String s = "x"; }\n', "kw"),
        "kotlin": ('fun greet(n: String): String {\n  val x = 42  // c\n  return "hi"\n}\n',
                   "kw"),
        "bash": ('#!/bin/bash\nset -e\nfor i in 1 2 3; do echo "$i"; done  # loop\n',
                 "kw"),
        "javascript": ("import os from 'os';\nfunction greet(n) {\n"
                       "  const x = 42; // c\n  return `hi ${n}`;\n}\n", "kw"),
        "jsx": ('const A = () => (\n  <div className="x">{/* c */}hi</div>\n);\n',
                "kw"),
        "typescript": ("interface P { n: number }\nfunction f(p: P): string {\n"
                       "  // c\n  return 'x';\n}\n", "kw"),
        "tsx": ("const A = (p: {n: number}) => <b>{p.n}</b>; // c\n", "kw"),
        "html": ('<!doctype html>\n<!-- c -->\n<div class="x">hi</div>\n', "def"),
        "css": ("/* c */\n.x { color: #fff; margin: 0 auto; }\n", "kw"),
        "scss": ("$c: #fff;\n// c\n.x { color: $c; }\n", "var"),
        "less": ("@c: #fff;\n/* c */\n.x { color: @c; }\n", "kw"),
    }
    for lexer, (src, structural) in cases.items():
        s = CODE.CodeStreamer(lexer)
        s.feed(src)
        text, bg = s.close()[0]
        assert bg is None, lexer + " must have no background panel"
        assert R.COL[structural] in text, lexer + " " + structural + " coloured"
        assert R.COL["cmt"] in text, lexer + " comment coloured"
        # source preserved verbatim (no reformat).
        assert R.strip_ansi(text).rstrip("\n") == src.rstrip("\n")


def test_code_langs_keys_are_suffix_unique():
    """No LANGS key may be a suffix of another: two consumers match an extension
    with `endswith` over the table (tools._lexer_match, opshtml.tools._lexer_for),
    so a key that is a suffix of another would make dict ORDER decide the lexer.
    The leading dot is what keeps the families apart (.cjs/.js, .tsx/.ts,
    .kts/.ts) — this pins that every family member has its own row."""
    keys = list(CODE.LANGS)
    for k in keys:
        for other in keys:
            assert other == k or not k.endswith(other), (k, other)


def test_code_fallback_without_pygments(monkeypatch):
    monkeypatch.setattr(CODE, "render_code", lambda _t, _l: None)
    s = CODE.CodeStreamer("python")
    s.feed("x = 1\n")
    out = s.close()
    assert out and "x = 1" in R.strip_ansi(out[0][0])


def test_code_source_detection():
    from plugins.claude_code.tools import code_source
    for c, exp in [("cat foo.py", "python"), ("cat Main.java", "java"),
                   ("head -50 App.kt", "kotlin"), ("tail deploy.sh", "bash"),
                   ("< s.py", "python"),
                   # sed/grep of a source file: lexer from the trailing FILE arg
                   ("sed -n '80,130p' dispatch.py", "python"),
                   ("grep -n def app.py", "python"),
                   ("grep foo Main.java", "java"),
                   # a trailing truncation pipe (| head / | tail) is stripped
                   ("grep server_bind r.py | head -40", "python"),
                   ("cat App.kt | tail -20", "kotlin"),
                   ("grep x file.py | head | tail -5", "python"),
                   # a pipeline broken across a line (trailing | / \\ continuation)
                   ("grep -n def base.py |\nhead", "python"),
                   ("grep foo \\\n  Main.java", "java"),
                   # multi-statement: the LAST statement's file picks the lexer
                   ("grep -n def a.py\nprintf hi\nsed -n 1,5p b.java", "java"),
                   # the JS/TS family — each extension its own lexer, and the
                   # family members must NOT collapse onto the base extension
                   ("sed -n 60,110p tests/jsdom/domshim.js", "javascript"),
                   ("cat bundle.mjs", "javascript"), ("cat cfg.cjs", "javascript"),
                   ("grep -n useState App.jsx", "jsx"),
                   ("cat main.ts", "typescript"), ("head -20 node.mts", "typescript"),
                   ("cat Panel.tsx", "tsx"),
                   # .kts stays kotlin (it does not end with the .ts key)
                   ("cat build.gradle.kts", "kotlin"),
                   # web markup
                   ("head -20 index.html", "html"), ("< page.htm", "html"),
                   ("tail -5 app.css", "css"), ("cat theme.scss", "scss"),
                   ("sed -n 1,40p vars.less", "less")]:
        assert code_source(c) == exp, c
    for c in ["cat foo.txt", "bat foo.py", "python foo.py",
              "cat foo.py > o", "rm a.py",
              # the PATTERN/SCRIPT arg must not masquerade as the file
              "grep 'foo.py' x.txt", "sed 's/a/b.py/' notes.txt",
              "grep 'foo.js' x.txt", "node app.js",
              # recursive grep (dir last, no extension) opts out
              "grep -r pattern src/",
              # a TRANSFORM pipe (not head/tail) still disqualifies — output derived
              "cat foo.py | grep x", "cat foo.py | awk '{print}'"]:
        assert code_source(c) is None, c


def test_read_command_names_kind_file_and_reader():
    """The Read-one-liner seam both Bash hooks consult returns the whole triple —
    (ReadSpec(kind, detection value), file, reader tag). A sed of a .js used to
    fall through to a streamed fg block because the extension had no lexer; the
    file/reader halves always matched (sed is a tailarg reader), so only the LANGS
    row was missing. The KIND is what lets the expansion pick a renderer instead
    of assuming a lexer."""
    from plugins.claude_code.tools import read_command
    assert read_command("sed -n 60,110p tests/jsdom/domshim.js") == (
        ("code", "javascript"), "tests/jsdom/domshim.js", "sed")
    assert read_command("grep -n color app/theme.scss") == (
        ("code", "scss"), "app/theme.scss", "grep")
    assert read_command("cat notes.txt") == (None, None, None)


def test_read_command_takes_a_markdown_slice():
    """A sed/grep SLICE of a markdown file collapses to a Read one-liner too (the
    md kind's READ plane) — `sed -n 120,400p CLAUDE.md` was the reported miss: it
    streamed as a raw fg dump because only the `code` kind was read-eligible and
    .md has no lexer. Whole-document readers (cat/head/tail, `< x.md`) are NOT
    read-eligible: they keep streaming live through MarkdownStreamer."""
    from plugins.claude_code.tools import read_command
    assert read_command("sed -n 120,400p /p/CLAUDE.md") == (
        ("md", True), "/p/CLAUDE.md", "sed")
    assert read_command("grep -n '^## ' docs/dashboard.md") == (
        ("md", True), "docs/dashboard.md", "grep")
    assert read_command("sed -n 1,5p notes.markdown")[0] == ("md", True)
    for whole in ["cat CLAUDE.md", "head -50 CLAUDE.md", "tail -20 x.md",
                  "< r.md", "cat < r.md"]:
        assert read_command(whole) == (None, None, None), whole
    # …and a kind with no read plane at all never collapses (json/yaml stream).
    for other in ["cat data.json", "sed -n 1p c.yml", "cat notes.txt"]:
        assert read_command(other) == (None, None, None), other


def test_read_command_ignores_a_trailing_stderr_redirect():
    """A trailing `2>/dev/null` must not occupy the FILE slot of a tailarg reader.

    `sed -n 1,80p note.md 2>/dev/null` — the idiom for reading a file that may not
    exist — silently streamed as a raw fg block instead of collapsing to
    `Read(note.md)`: sed/grep are TAILARG readers (the file must be the last
    argument, so `grep 'foo.py' x.txt` can't masquerade as python), and the
    redirect had taken that position. A redirect is shell SYNTAX, not an argument.
    `cat` never had the bug — a WHOLE reader takes the file anywhere."""
    from plugins.claude_code.tools import read_command
    assert read_command("sed -n 1,80p /w/note.md 2>/dev/null") == (
        ("md", True), "/w/note.md", "sed")
    assert read_command("sed -n 1,120p /d/availability.md 2>&1") == (
        ("md", True), "/d/availability.md", "sed")
    assert read_command("grep -n foo app.py 2>/dev/null") == (
        ("code", "python"), "app.py", "grep")
    assert read_command("grep -n foo app.py 2>>errors.log") == (
        ("code", "python"), "app.py", "grep")
    # the anti-masquerade guard the tailarg rule exists for still holds — the
    # PATTERN is never taken as the file, and a recursive grep still opts out
    assert read_command("grep 'foo.py' x.txt 2>/dev/null") == (None, None, None)
    assert read_command("grep -r pat src/ 2>/dev/null") == (None, None, None)
    assert read_command("grep -ril p ~/w/ --include=*.md 2>/dev/null") == \
        (None, None, None)
    # a STDOUT redirect still disqualifies outright (the output never reaches the
    # pane, so there is nothing to collapse), with or without a stderr one
    assert read_command("sed -n 1,80p note.md > out.txt") == (None, None, None)
    assert read_command("sed -n 1,80p note.md 2>/dev/null > out.txt") == \
        (None, None, None)
    # …and a redirect must not be read as the file when there is no other arg
    assert read_command("sed -n 1,80p 2>/dev/null") == (None, None, None)


def test_read_command_honours_both_env_gates(monkeypatch):
    """CLAUDE_MIRROR_CMD_READ=0 turns the whole collapse off (the historical
    escape hatch), and a kind's OWN CLAUDE_MIRROR_* gate turns off just that
    kind's — with its rendering off there is nothing to collapse into, so the
    command streams instead."""
    from plugins.claude_code.tools import read_command
    monkeypatch.setenv("CLAUDE_MIRROR_CMD_READ", "0")
    assert read_command("sed -n 1,5p x.md") == (None, None, None)
    assert read_command("sed -n 1,5p x.py") == (None, None, None)
    monkeypatch.delenv("CLAUDE_MIRROR_CMD_READ")
    monkeypatch.setenv("CLAUDE_MIRROR_MD", "0")
    assert read_command("sed -n 1,5p x.md") == (None, None, None)
    assert read_command("sed -n 1,5p x.py")[0] == ("code", "python"), "code unaffected"
    monkeypatch.setenv("CLAUDE_MIRROR_CODE", "0")
    assert read_command("sed -n 1,5p x.py") == (None, None, None)


def test_read_plane_and_body_builders_agree():
    """CONTRACT: every read-eligible RENDER_KINDS entry has its own body builder
    in cmd_fmt._READ_BODY, and vice versa. cmd_pre skips live streaming on
    read_command's verdict alone, so a kind the renderer can't build a body for
    would degrade to a verbatim block (_read_body_plain) — correct, but silently
    worse than the kind's own rendering. Adding a read plane and forgetting the
    builder is the drift this pins."""
    from plugins.claude_code import tools as CT
    from plugins.claude_code import cmd_fmt as CF
    eligible = {k.name for k in CT.RENDER_KINDS
                if k.read_readers or k.read_tailarg_readers}
    assert eligible == {"md", "code"}
    assert eligible == set(CF._READ_BODY)


def test_file_op_lexer_covers_the_web_extensions():
    """The file-op click-to-view expand keys its lexer off the same table
    (file_fmt._lexer, splitext-keyed), so a Read/Write of a .js/.ts/.css
    highlights in the mirror too — not just a command that streams one."""
    from plugins.claude_code.file_fmt import _lexer
    for path, exp in [("/a/b/domshim.js", "javascript"), ("/a/Panel.tsx", "tsx"),
                      ("/a/main.ts", "typescript"), ("/a/index.html", "html"),
                      ("/a/style.css", "css"), ("/a/build.gradle.kts", "kotlin"),
                      ("/a/notes.txt", None)]:
        assert _lexer(path) == exp, path


# ---- golden byte-identity ------------------------------------------------------
# Pinned before the BufferedStreamer/pick-override refactor: the rendered ANSI for
# representative json/jsonl/yaml/code snippets must stay byte-identical (files in
# tests/golden/render-*.ansi), and the token->colour ladders must keep their exact
# per-token mapping (the ladders' ORDER matters — startswith checks overlap).

import os as _os  # noqa: E402

GOLDEN = _os.path.join(_os.path.dirname(__file__), "golden")

GOLDEN_JSON = ('{"name":"adapter","count":3,"pi":1.5e-3,"on":true,"off":false,'
               '"n":null,"tags":["a",""],"nest":{"k":[1,{"d":2}]},"u":"café \\"q\\""}')
GOLDEN_JSONL = '{"a":1}\n[2,3]\n{"b":"x"}'
GOLDEN_YAML = """# top comment
name: adapters-api   # trailing comment
replicas: 3
ratio: 0.25
enabled: true
empty: null
anchor: &base
  key: 'single'
alias: *base
items:
  - plain scalar
  - "double quoted"
  - 42
block: |
  line one
  line two
"""
GOLDEN_CODE = ("import os\n\ndef greet(name):\n    x = 42  # answer\n"
               "    return f'hi {name}' + os.sep\n")


def _golden(name):
    with open(_os.path.join(GOLDEN, "render-%s.ansi" % name), encoding="utf-8") as f:
        return f.read()


def test_golden_rendered_output_byte_identical():
    for name, got in [("json", JSON.render_json(GOLDEN_JSON)),
                      ("jsonl", JSON.render_json(GOLDEN_JSONL)),
                      ("yaml", YAML.render_yaml(GOLDEN_YAML)),
                      ("code", CODE.render_code(GOLDEN_CODE, "python"))]:
        assert got == _golden(name), "%s render drifted from golden" % name


# Every token prefix the ladders discriminate, incl. the overlap/order-sensitive
# ones (Token.Literal.* vs the yaml Literal->str fallback; Token.Name.* vs
# Name.Tag). Values pinned from the pre-refactor forked ladders. "Cmd" (a
# synthetic type only render.pick's command path uses) is deliberately excluded:
# no lexer emits it into these renderers.
LADDER_TOKENS = [
    "Token.Name.Tag", "Token.Name.Variable", "Token.Name.Builtin",
    "Token.Name.Function", "Token.Name.Label", "Token.Literal.String.Double",
    "Token.String", "Token.Literal.Number.Integer", "Token.Number.Float",
    "Token.Keyword.Constant", "Token.Punctuation.Indicator", "Token.Operator",
    "Token.Comment.Single", "Token.Literal.Scalar.Plain", "Token.Literal",
    "Token.Text.Whitespace", "Token.Error"]

JSON_LADDER = {"Token.Name.Tag": "func", "Token.Name.Variable": "def",
               "Token.Name.Builtin": "def", "Token.Name.Function": "def",
               "Token.Name.Label": "def", "Token.Literal.String.Double": "str",
               "Token.String": "str", "Token.Literal.Number.Integer": "num",
               "Token.Number.Float": "num", "Token.Keyword.Constant": "kw",
               "Token.Punctuation.Indicator": "op", "Token.Operator": "op",
               "Token.Comment.Single": "def", "Token.Literal.Scalar.Plain": "def",
               "Token.Literal": "def", "Token.Text.Whitespace": "def",
               "Token.Error": "def"}
YAML_LADDER = dict(JSON_LADDER, **{
    "Token.Comment.Single": "cmt", "Token.Literal.Scalar.Plain": "str",
    "Token.Literal": "str"})


def test_pick_ladders_pinned_per_token():
    inv = {v: k for k, v in R.COL.items()}
    for tok in LADDER_TOKENS:
        assert inv[JSON._pick(tok)] == JSON_LADDER[tok], "json _pick(%s)" % tok
        assert inv[YAML._pick(tok)] == YAML_LADDER[tok], "yaml _pick(%s)" % tok


def test_buffered_streamers_share_contract(monkeypatch):
    # feed buffers (returns []), close renders once; a None render falls back to
    # R.emphasize(R.unescape(raw)); whitespace-only input emits nothing.
    monkeypatch.setattr(YAML, "render_yaml", lambda _t: None)
    monkeypatch.setattr(CODE, "render_code", lambda _t, _l: None)
    for mk in (JSON.JsonStreamer, YAML.YamlStreamer,
               lambda: CODE.CodeStreamer("python")):
        s = mk()
        assert s.feed("   \n") == []
        assert s.close() == [], "whitespace-only buffer must emit nothing"
        s = mk()
        raw = "plain \\x1b[31mnot-real\\x1b[0m text"   # renders via the fallback
        s.feed(raw[:7]); s.feed(raw[7:])
        out = s.close()
        assert out == [(R.emphasize(R.unescape(raw)), None)]
        assert s.buf == "", "close() must drain the buffer"


# ---- detection ---------------------------------------------------------------

def test_md_source_positive():
    for c in ["cat README.md", "head -50 notes.md", "tail -n 20 a.markdown",
              "cat docs/x.md", "cat 'my file.md'", "< r.md", "cat < r.md",
              # last statement is a clean md read (multi-statement keys off it)
              "echo hi && cat x.md", "cat README.md | head -40"]:
        assert md_source(c), c


def test_md_source_negative():
    for c in ["bat README.md", "glow x.md", "cat x.md | grep foo", "grep x notes.md",
              "rm old.md", "npm run build", "cat x.txt", "less README.md",
              "cat a.md > b.txt", "mdcat r.md", "cat $(ls).md"]:
        assert not md_source(c), c


def test_json_source():
    from plugins.claude_code.tools import json_source
    for c in ["cat data.json", "cat dir/x.json", "< r.json", "cat < r.json",
              "cat events.jsonl", "cat logs.ndjson"]:   # JSON Lines / NDJSON too
        assert json_source(c), c
    for c in ["head data.json", "tail x.json", "jq . x.json", "cat x.json | jq",
              "bat x.json", "cat x.json > y.txt", "cat x.md", "cat data.json && echo hi"]:
        assert not json_source(c), c


def test_yaml_source():
    from plugins.claude_code.tools import yaml_source
    for c in ["cat config.yml", "cat x.yaml", "head -20 c.yml", "tail c.yaml", "< d.yml"]:
        assert yaml_source(c), c
    for c in ["cat x.json", "cat x.md", "cat x.yml | grep foo", "bat x.yml",
              "cat x.yml > o.txt", "yq . x.yml"]:
        assert not yaml_source(c), c


def test_render_kinds_registry():
    """The registry IS the detector: one table-driven pass across every kind,
    covering positives, plumbing-guard negatives, redirect forms, and the code
    kind's trailing-arg (sed/grep) rule — the same behaviors the per-kind
    wrappers (md_source & co) expose."""
    from plugins.claude_code import tools as CT
    by = {k.name: k for k in CT.RENDER_KINDS}
    # priority order + env gates are part of the registry contract
    assert [k.name for k in CT.RENDER_KINDS] == ["md", "json", "yaml", "code"]
    assert [k.env for k in CT.RENDER_KINDS] == [
        "CLAUDE_MIRROR_MD", "CLAUDE_MIRROR_JSON",
        "CLAUDE_MIRROR_YAML", "CLAUDE_MIRROR_CODE"]
    for kind, cmd, exp in [
        # positives, incl. quoted args and stdin redirects
        ("md", "cat README.md", True), ("md", "cat 'my file.md'", True),
        ("md", "< r.md", True), ("md", "head -50 notes.md", True),
        ("json", "cat data.json", True), ("json", "< r.json", True),
        ("yaml", "tail c.yaml", True), ("yaml", "< d.yml", True),
        ("code", "cat foo.py", "python"), ("code", "< s.py", "python"),
        # tail-arg readers: only the code kind has them
        ("code", "sed -n '80,130p' dispatch.py", "python"),
        ("code", "grep -n def app.py", "python"),
        ("md", "grep x notes.md", None), ("yaml", "sed -n 1p c.yml", None),
        # json is cat-only (a partial document is invalid)
        ("json", "head data.json", None), ("json", "tail x.json", None),
        # plumbing guard: pipes / output redirects / substitution disqualify
        ("md", "cat x.md | grep foo", None), ("md", "cat a.md > b.txt", None),
        ("code", "cat foo.py | awk '{print}'", None),
        ("json", "cat $(ls).json", None),
        # truncation pipes and multi-statement key off the effective read
        ("md", "cat README.md | head -40", True),
        ("code", "grep x file.py | head | tail -5", "python"),
        ("code", "grep -n def a.py\nprintf hi\nsed -n 1,5p b.java", "java"),
        # non-allowlisted readers
        ("md", "bat README.md", None), ("code", "python foo.py", None),
    ]:
        got = by[kind].detect(cmd)
        if exp is None:
            assert not got, (kind, cmd)
        elif exp is True:
            assert got, (kind, cmd)
        else:
            assert got == exp, (kind, cmd)


def test_render_kinds_read_plane():
    """The registry's SECOND plane (read_match — the Read-one-liner path): the same
    skeleton, per-kind read reader sets. md admits only its FRAGMENT readers (a
    slice is a Read; a whole document keeps streaming), code admits every reader it
    streams plus the bare `< file` form, json/yaml none."""
    from plugins.claude_code import tools as CT
    by = {k.name: k for k in CT.RENDER_KINDS}
    for kind, cmd, exp in [
        # md: fragment readers only
        ("md", "sed -n 120,400p CLAUDE.md", True),
        ("md", "grep -n '^## ' docs/x.md", True),
        ("md", "sed -n 1,5p 'my notes.markdown'", True),
        ("md", "cat CLAUDE.md", None), ("md", "head -3 x.md", None),
        ("md", "< r.md", None), ("md", "cat < r.md", None),
        ("md", "cat x.md | grep foo", None),      # plumbing guard still applies
        ("md", "grep foo x.md > hits.txt", None),
        # code: whole + fragment readers, and the redirect forms
        ("code", "sed -n '80,130p' dispatch.py", "python"),
        ("code", "cat foo.py", "python"), ("code", "head -50 App.kt", "kotlin"),
        ("code", "< s.py", "python"), ("code", "cat < s.py", "python"),
        # a styling reader is not an allowlisted one even via the redirect form
        ("code", "bat < r.py", None), ("code", "bat r.py", None),
        # stream-only kinds
        ("json", "cat data.json", None), ("json", "sed -n 1p x.json", None),
        ("yaml", "cat c.yml", None), ("yaml", "sed -n 1p c.yml", None),
    ]:
        got = by[kind].read_match(cmd)[0]
        if exp is None:
            assert not got, (kind, cmd)
        elif exp is True:
            assert got, (kind, cmd)
        else:
            assert got == exp, (kind, cmd)
    # the two planes are independent: md STREAMS what it does not collapse
    assert by["md"].detect("cat CLAUDE.md") and not by["md"].read_match("cat CLAUDE.md")[0]
    assert by["md"].read_match("sed -n 1p x.md")[0] and not by["md"].detect("sed -n 1p x.md")
