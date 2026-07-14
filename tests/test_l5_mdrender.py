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
    cases = {
        "python": "import os\ndef greet(n):\n    return f'hi {n}'  # c\n",
        "java": 'public class Foo { int n = 3; // c\n String s = "x"; }\n',
        "kotlin": 'fun greet(n: String): String {\n  val x = 42  // c\n  return "hi"\n}\n',
        "bash": '#!/bin/bash\nset -e\nfor i in 1 2 3; do echo "$i"; done  # loop\n',
    }
    for lexer, src in cases.items():
        s = CODE.CodeStreamer(lexer)
        s.feed(src)
        text, bg = s.close()[0]
        assert bg is None, lexer + " must have no background panel"
        assert R.COL["kw"] in text, lexer + " keyword coloured"
        assert R.COL["cmt"] in text, lexer + " comment coloured"
        # source preserved verbatim (no reformat).
        assert R.strip_ansi(text).rstrip("\n") == src.rstrip("\n")


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
                   ("grep -n def a.py\nprintf hi\nsed -n 1,5p b.java", "java")]:
        assert code_source(c) == exp, c
    for c in ["cat foo.txt", "bat foo.py", "python foo.py",
              "cat foo.py > o", "rm a.py",
              # the PATTERN/SCRIPT arg must not masquerade as the file
              "grep 'foo.py' x.txt", "sed 's/a/b.py/' notes.txt",
              # recursive grep (dir last, no extension) opts out
              "grep -r pattern src/",
              # a TRANSFORM pipe (not head/tail) still disqualifies — output derived
              "cat foo.py | grep x", "cat foo.py | awk '{print}'"]:
        assert code_source(c) is None, c


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
    with open(_os.path.join(GOLDEN, "render-%s.ansi" % name)) as f:
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
