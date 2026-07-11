# L5 — markdown renderer (core/mdrender.py) + detection (plugins tools.md_source).
#
# core/mdrender turns markdown into styled ANSI for the mirror. The contract it
# MUST hold (like everything a producer emits): zero-width SGR + LOGICAL newlines
# only, never a hard wrap to a column — so wrap_gutter can reflow it at paint time.
# These are pure-Python unit tests (no mirror subprocess needed).
import re
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


def _render_all(md, chunks=1):
    """Feed `md` through the streamer in `chunks` pieces, return the joined output."""
    s = M.MarkdownStreamer()
    out = []
    n = max(1, len(md) // chunks)
    for i in range(0, len(md), n):
        out += s.feed(md[i:i + n])
    out += s.close()
    return out


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
    out = s.feed("# Hi\n\nsome **bold** text\n") + s.close()
    joined = "".join(out)
    assert joined, "fallback produced nothing"
    assert "\033[1m" in joined, "fallback should still bold"


def test_empty_and_whitespace():
    assert M.MarkdownStreamer().close() == []
    s = M.MarkdownStreamer()
    assert s.feed("\n\n   \n") == [] or all(not b.strip() for b in s.feed("\n"))


# ---- detection ---------------------------------------------------------------

def test_md_source_positive():
    for c in ["cat README.md", "head -50 notes.md", "tail -n 20 a.markdown",
              "cat docs/x.md", "cat 'my file.md'", "< r.md", "cat < r.md"]:
        assert md_source(c), c


def test_md_source_negative():
    for c in ["bat README.md", "glow x.md", "cat x.md | grep foo", "grep x notes.md",
              "rm old.md", "npm run build", "cat x.txt", "less README.md",
              "cat a.md > b.txt", "echo hi && cat x.md", "mdcat r.md", "cat $(ls).md"]:
        assert not md_source(c), c
