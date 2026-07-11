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
# fenced blocks (syntax-highlighted by language via pygments), blockquotes, GFM
# tables (dim │ rail, bold header — no column alignment, that's width-dependent),
# task-list checkboxes (☐/☑), footnotes (`[^id]`), plus two wiki conventions the
# raw parser doesn't know — YAML frontmatter and Obsidian `[[wikilinks]]`. (Task
# lists and footnotes ship no plugin in the streaming preset, so they arrive as
# plain text and are handled at the text level.) Why an AST parser and not glow/rich: those bake a
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
CODE_BG = (44, 49, 58)                  # code-block panel background (dark slate)
# Obsidian wikilinks: [[target]] or [[target|alias]] — not CommonMark, so wenmode
# leaves them as literal text. Colour them like a link (display the alias if given).
_WIKILINK = re.compile(r"\[\[([^\]\|]+?)(?:\|([^\]]+?))?\]\]")
# Leading YAML frontmatter block (--- … ---) at the very start of a document.
_FRONTMATTER = re.compile(r"^---\n(.*?)\n---\n?", re.DOTALL)
# GFM footnotes: the streaming preset ships no footnote plugin, so `[^id]`
# references and `[^id]: body` definitions arrive as plain text — handle them at
# the text level (also streaming-robust: a definition may land in a later chunk
# than its reference). A definition is a whole paragraph starting `[^id]:`.
_FOOTNOTE_DEF = re.compile(r"^\[\^([^\]]+)\]:\s*")
_FOOTNOTE_REF = re.compile(r"\[\^([^\]]+)\]")
# GFM task-list checkbox at the head of a list item ("- [ ] todo" / "- [x] done").
_CHECK = re.compile(r"^\[([ xX])\]\s+")
# A line that is (or continues) a list item — an indented line, or a bullet /
# ordered marker at column 0. Used by the streamer's block cut to avoid slicing a
# loose list before its indented continuation has arrived.
_LIST_MARK = re.compile(r"^\s*([-*+]|\d+[.)])\s")


def _in_list(line):
    return line[:1] in (" ", "\t") or bool(_LIST_MARK.match(line))


def _wikilinks(s):
    return _WIKILINK.sub(
        lambda m: R.COL["func"] + (m.group(2) or m.group(1)).strip() + R.COL["def"], s)


def _footnotes(s):
    """Colour footnote references `[^id]` as a dim `[id]`, and render a definition
    line `[^id]: body` as a dim `id.` label + faint body."""
    m = _FOOTNOTE_DEF.match(s)
    if m:
        return (R.DIM + m.group(1) + ". " + R.RST
                + R.COL["cmt"] + s[m.end():].strip() + R.COL["def"])
    return _FOOTNOTE_REF.sub(
        lambda x: R.COL["cmt"] + "[" + x.group(1) + "]" + R.COL["def"], s)


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
        return _footnotes(_wikilinks(n.value or ""))

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
            chk = _CHECK.match(body) if not ordered else None
            if chk:                                  # GFM task-list checkbox
                marker = "☑" if chk.group(1) in "xX" else "☐"
                body = body[chk.end():]
            else:
                marker = ("%d." % (start + i)) if ordered else "•"
            colored = (R.COL["op"] + marker + R.RST) + " "
            out.append(_indent(body, " " * (len(marker) + 1), first=colored))
        return "\n".join(out)

    @OpsRenderer.register('table')
    def _table(r, n, c):
        # GFM table: cells joined by a dim │ rail into one LOGICAL line per row
        # (so wrap_gutter still reflows a wide row at paint time — no column math,
        # alignment is width-dependent and out of scope). First row is the bold
        # header, followed by a dim rule; remaining rows are plain.
        rail = R.DIM + " │ " + R.RST
        rows = getattr(n, "children", []) or []
        out = []
        for i, row in enumerate(rows):
            cells = [r.render_node(cell, c).replace("\n", " ").strip()
                     for cell in (getattr(row, "children", []) or [])]
            line = rail.join(cells)
            if i == 0:                              # header row
                out.append("\033[1m" + line + "\033[22m")
                out.append(R.DIM + "─────────" + R.RST)
            else:
                out.append(line)
        return "\n".join(out)

    @OpsRenderer.register('tableCell')
    def _cell(r, n, c):
        return _kids(r, n, c)

    @OpsRenderer.register('tableRow')               # handled inline by _table
    def _row(r, n, c):
        return _kids(r, n, c)


class MarkdownStreamer:
    """Incremental markdown → styled-ANSI, block by block.

    The tailer feeds text as it arrives; we hold the trailing incomplete block
    and only render complete ones, so fenced code / multi-line constructs are
    never cut mid-block. feed()/close() return a list of `(text, bg)` segments —
    one per O.gut() the caller should emit: `bg` is None for prose and CODE_BG for
    a fenced code block (so the renderer fills it to a full-width panel). A blank
    separator segment is inserted between segments so consecutive gut ops stay
    visually separated.

    If wenmode is unavailable, degrades to the render.markdown() line subset (one
    prose segment).
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
        ops = []
        for text, bg in self._segments(region):
            text = text.rstrip("\n")
            if not text.strip() and bg is None:
                continue
            if self._emitted:
                ops.append(("", None))          # blank gutter line between gut ops
            ops.append((text, bg))
            self._emitted = True
        return ops

    def _segments(self, region):
        """Split a completed region into `(text, bg)` gut segments: consecutive
        prose blocks coalesce into one prose op; each fenced code block becomes its
        own CODE_BG panel op so the renderer can fill it to the pane width."""
        pre = []
        if not self._started:                   # frontmatter only ever leads the document
            stripped = region.lstrip("\n")
            m = _FRONTMATTER.match(stripped)
            if m:
                pre.append((_render_frontmatter(m.group(1)), None))
                region = stripped[m.end():]
        self._started = True

        if self.wen is None:                    # fallback: one prose segment
            sub = _wikilinks(R.markdown(R.unescape(region))).rstrip("\n")
            return pre + ([(sub, None)] if sub.strip() else [])

        try:
            root = self.wen.parse(region)
        except Exception:
            return pre + [(region.rstrip("\n"), None)]

        segs, prose = list(pre), []
        r = self.wen.renderer
        ctx = r.create_context(root)
        for child in getattr(root, "children", []) or []:
            if child.type == "code":            # its own full-width panel
                if prose:
                    segs.append(("\n\n".join(prose), None)); prose = []
                segs.append((_highlight_code(getattr(child, "value", ""),
                                             getattr(child, "lang", None)), CODE_BG))
            else:
                try:
                    txt = r.render_node(child, ctx).rstrip("\n")
                except Exception:
                    txt = ""
                if txt:
                    prose.append(txt)
        if prose:
            segs.append(("\n\n".join(prose), None))
        return segs

    def _safe_cut(self):
        """Byte offset after the last blank line that is a true block boundary —
        NOT inside a ``` / ~~~ fence, and NOT a blank line inside a loose list.

        A blank line inside a loose list is not a block boundary: the item can
        take an indented continuation (a nested paragraph or fenced code), and
        cutting there orphans it (a fenced code inside a list item collapsed). So:
          - if the next non-blank line is buffered, cut only when it sits at
            column 0 (an indented line is a continuation of the item above);
          - if no next line is buffered yet, cut only when the block ABOVE the
            blank can't take a continuation (it isn't a list item / indented) —
            otherwise hold, since the next feed may bring the nested block.
        close() flushes the tail regardless, so holding never loses content."""
        lines = self.buf.splitlines(keepends=True)
        fence = False
        pos = cut = 0
        prev = None                                  # last non-blank line seen
        for i, ln in enumerate(lines):
            s = ln.strip()
            if s.startswith("```") or s.startswith("~~~"):
                fence = not fence
            pos += len(ln)
            if fence or s != "":
                if s != "":
                    prev = ln
                continue
            nxt = next((lines[j] for j in range(i + 1, len(lines))
                        if lines[j].strip()), None)
            if nxt is not None:
                if nxt[:1] not in (" ", "\t"):
                    cut = pos
            elif prev is not None and not _in_list(prev):
                cut = pos                            # tail block is self-terminating
        return cut
