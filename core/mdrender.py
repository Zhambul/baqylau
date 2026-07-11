# core/mdrender.py — AST-driven markdown → styled-ANSI renderer for the mirror.
#
# The mirror renders command output as width-INDEPENDENT paint ops (core/ops.py):
# a producer emits already-styled text carrying only zero-width SGR + LOGICAL
# newlines, and the renderer (claude-mirror.py) soft-wraps it at the live pane
# width and reflows on SIGWINCH (core/render.wrap_gutter). So a markdown renderer
# that runs in a producer (the claude-stream.py tailer) must obey the same
# contract: emit styled text, never wrap to a column.
#
# We drive the `wenmode` CommonMark parser (pure-Python, zero deps) and subclass
# its BaseRenderer so each mdast node becomes styled ANSI — reusing the existing
# core/render.py primitives (BANNER, COL, hyperlink, DIM, pick) so headings/bold/
# code match the rest of the mirror. This replaces (and supersets) the line-
# oriented regex subset in core/render.markdown(): real nesting, ordered lists,
# fenced blocks (syntax-highlighted by language via pygments), blockquotes, plus
# two wiki conventions the raw parser doesn't know — YAML frontmatter and
# Obsidian `[[wikilinks]]`. Why an AST parser and not glow/rich: those bake a
# fixed width into their output, which the mirror's reflow model can't consume.
#
# wenmode is OPTIONAL (probed for, like pygments). If it is absent we degrade to
# the per-line core/render.markdown() subset — the feature never breaks the
# tailer, it just renders less.
import re

from core import render as R

try:                                    # optional dependency — degrade, never raise
    from wenmode import Wenmode
    from wenmode.presets import streaming as _STREAMING
    from wenmode.renderers import BaseRenderer
    AVAILABLE = True
except Exception:                       # not installed / import error
    AVAILABLE = False
    BaseRenderer = object               # so the class body below still imports


_QUOTE = R.DIM + "▏ " + R.RST           # blockquote rail
# Obsidian wikilinks: [[target]] or [[target|alias]] — not CommonMark, so wenmode
# leaves them as literal text. Colour them like a link (display the alias if given).
_WIKILINK = re.compile(r"\[\[([^\]\|]+?)(?:\|([^\]]+?))?\]\]")
# Leading YAML frontmatter block (--- … ---) at the very start of a document.
_FRONTMATTER = re.compile(r"^---\n(.*?)\n---\n?", re.DOTALL)


def _wikilinks(s):
    return _WIKILINK.sub(
        lambda m: R.COL["func"] + (m.group(2) or m.group(1)).strip() + R.COL["def"], s)


def _indent(text, prefix, first=None):
    """Prefix the first LOGICAL line with `first` (default `prefix`) and every
    following logical line with `prefix` — logical newlines only, so wrap_gutter
    still reflows the result at paint time."""
    lines = text.split("\n")
    return "\n".join((first if i == 0 and first is not None else prefix) + ln
                     for i, ln in enumerate(lines))


def _highlight_code(value, lang):
    """Syntax-highlight a fenced code block by language via pygments (reusing the
    render.pick token→colour map), indented two spaces. No/unknown language, or
    pygments absent, degrades to a dim monospace block."""
    body = (value or "").rstrip("\n")
    if lang:
        try:
            from pygments.lexers import get_lexer_by_name
            lx = get_lexer_by_name(lang.strip().lower())
            out = []
            for ttype, val in lx.get_tokens(body):
                out.append(R.pick(str(ttype)) + val)
            body = "".join(out).rstrip("\n") + R.RST
            return _indent(body, "  ")
        except Exception:
            pass                        # unknown lexer / pygments missing -> dim
    return _indent(R.COL["cmt"] + body + R.RST, "  ")


def _render_frontmatter(block):
    """Render a YAML frontmatter body as a dim key/value header."""
    out = []
    for ln in block.split("\n"):
        if not ln.strip():
            continue
        if ":" in ln and not ln.startswith(" "):
            k, _, v = ln.partition(":")
            out.append(R.DIM + k.strip() + R.RST + "  " + R.COL["cmt"] + v.strip() + R.RST)
        else:
            out.append(R.COL["cmt"] + ln.strip() + R.RST)
    return "\n".join(out)


if AVAILABLE:

    class OpsRenderer(BaseRenderer):
        """Render mdast nodes to styled ANSI (zero-width SGR + logical newlines).

        Block spacing is owned by the `root`/`list`/`blockquote` handlers (a blank
        line between top-level blocks, single-spaced list items) — leaf block
        handlers return their content WITHOUT trailing blanks.
        """

    def _kids(r, n, c):
        return r.render_children(n.children, c)

    @OpsRenderer.register('root')
    def _root(r, n, c):
        # A blank line between top-level blocks — the breathing room raw
        # concatenation lacked. Drop empties so we never stack two blanks.
        parts = [p for p in (r.render_node(ch, c).rstrip("\n") for ch in n.children) if p]
        return "\n\n".join(parts)

    @OpsRenderer.register('text')
    def _text(r, n, c):
        return _wikilinks(n.value or "")

    @OpsRenderer.register('paragraph')
    def _para(r, n, c):
        return _kids(r, n, c)

    @OpsRenderer.register('heading')
    def _heading(r, n, c):
        # All levels -> bold amber banner (matches render.markdown headings); a
        # leading run of faint dots keeps h2/h3/… visually subordinate.
        lead = (R.DIM + ("·" * (n.depth - 1)) + " " + R.RST) if n.depth > 1 else ""
        return lead + R.BANNER + _kids(r, n, c) + R.RST

    @OpsRenderer.register('strong')
    def _strong(r, n, c):
        return "\033[1m" + _kids(r, n, c) + "\033[22m"

    @OpsRenderer.register('emphasis')
    def _emph(r, n, c):
        return "\033[3m" + _kids(r, n, c) + "\033[23m"

    @OpsRenderer.register('delete')                 # GFM strikethrough
    def _del(r, n, c):
        return "\033[9m" + _kids(r, n, c) + "\033[29m"

    @OpsRenderer.register('inlineCode')
    def _icode(r, n, c):
        return R.COL["builtin"] + (n.value or "") + R.COL["def"]

    @OpsRenderer.register('link')
    def _link(r, n, c):
        inner = _kids(r, n, c)
        url = getattr(n, "url", "") or ""
        return R.hyperlink(url, R.COL["func"] + inner + R.COL["def"]) if url else inner

    @OpsRenderer.register('image')
    def _image(r, n, c):
        alt = _kids(r, n, c) or (getattr(n, "alt", "") or "")
        return R.DIM + "🖼 " + alt + R.RST

    @OpsRenderer.register('break')
    def _break(r, n, c):
        return "\n"

    @OpsRenderer.register('thematicBreak')
    def _hr(r, n, c):
        return R.DIM + "─────────" + R.RST      # short logical marker; not width-aware

    @OpsRenderer.register('blockquote')
    def _quote(r, n, c):
        inner = "\n\n".join(p for p in (r.render_node(ch, c).rstrip("\n")
                                        for ch in n.children) if p)
        return _indent(inner, _QUOTE)

    @OpsRenderer.register('code')                    # fenced / indented code block
    def _code(r, n, c):
        return _highlight_code(getattr(n, "value", ""), getattr(n, "lang", None))

    @OpsRenderer.register('listItem')
    def _item(r, n, c):
        # The item's block children, single-newline separated. The marker +
        # indentation is applied by the parent `list` handler (it owns the ordinal).
        parts = [r.render_node(ch, c).rstrip("\n") for ch in n.children]
        return "\n".join(p for p in parts if p)

    @OpsRenderer.register('list')
    def _list(r, n, c):
        ordered = bool(getattr(n, "ordered", False))
        start = getattr(n, "start", None) or 1
        out = []
        for i, item in enumerate(n.children):
            body = r.render_node(item, c)
            marker = ("%d." % (start + i)) if ordered else "•"
            colored = (R.COL["op"] + marker + R.RST) + " "
            out.append(_indent(body, " " * (len(marker) + 1), first=colored))
        return "\n".join(out)


class MarkdownStreamer:
    """Incremental markdown → styled-ANSI, block by block.

    The tailer feeds text as it arrives; we hold the trailing incomplete block
    and only render complete ones, so fenced code / multi-line constructs are
    never cut mid-block. Output is a list of styled strings — one per flush —
    each ready for O.gut(); a blank line is prepended to every flush after the
    first so consecutive gut ops stay visually separated. On close() the buffered
    remainder is flushed.

    If wenmode is unavailable, degrades to the render.markdown() line subset.
    """

    def __init__(self):
        self.buf = ""
        self.wen = None
        self._started = False           # have we consumed the (optional) frontmatter?
        self._emitted = False           # emit a leading blank before all but the first
        if AVAILABLE:
            try:
                self.wen = Wenmode(_STREAMING, renderer=OpsRenderer())
            except Exception:
                self.wen = None

    def feed(self, text):
        self.buf += text
        cut = self._safe_cut()
        if cut <= 0:
            return []
        region, self.buf = self.buf[:cut], self.buf[cut:]
        return self._emit(region)

    def close(self):
        region, self.buf = self.buf, ""
        return self._emit(region) if region.strip() else []

    def _emit(self, region):
        s = self._render(region).rstrip("\n")
        if not s.strip():
            return []
        if self._emitted:
            s = "\n" + s                # blank gutter line separating gut ops
        self._emitted = True
        return [s]

    def _render(self, region):
        if self.wen is not None:
            try:
                pre = ""
                if not self._started:   # frontmatter only ever leads the document
                    stripped = region.lstrip("\n")
                    m = _FRONTMATTER.match(stripped)
                    if m:
                        pre = _render_frontmatter(m.group(1)) + "\n\n"
                        region = stripped[m.end():]
                self._started = True
                out = self.wen.render(region).rstrip("\n")
                whole = (pre + out).rstrip("\n")
                return whole if whole.strip() else ""
            except Exception:
                pass                    # fall through to the subset renderer
        self._started = True
        sub = _wikilinks(R.markdown(R.unescape(region))).rstrip("\n")
        return sub if sub.strip() else ""

    def _safe_cut(self):
        """Byte offset after the last blank line that is NOT inside a ``` / ~~~
        fenced block — the largest prefix of `buf` that forms complete blocks."""
        fence = False
        pos = cut = 0
        for ln in self.buf.splitlines(keepends=True):
            s = ln.strip()
            if s.startswith("```") or s.startswith("~~~"):
                fence = not fence
            pos += len(ln)
            if not fence and s == "":
                cut = pos
        return cut
