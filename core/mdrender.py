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
# core/render.py primitives (BANNER, COL, hyperlink, DIM) so headings/bold/code
# match the rest of the mirror. This replaces (and supersets) the line-oriented
# regex subset in core/render.markdown(): real nesting, ordered lists, fenced
# blocks, blockquotes. Why an AST parser and not glow/rich: those bake a fixed
# width into their output, which the mirror's reflow model can't consume.
#
# wenmode is OPTIONAL (probed for, like pygments). If it is absent we degrade to
# the per-line core/render.markdown() subset — the feature never breaks the
# tailer, it just renders less.
from core import render as R

try:                                    # optional dependency — degrade, never raise
    from wenmode import Wenmode
    from wenmode.presets import streaming as _STREAMING
    from wenmode.renderers import BaseRenderer
    AVAILABLE = True
except Exception:                       # not installed / import error
    AVAILABLE = False
    BaseRenderer = object               # so the class body below still imports


BULLET = R.COL["op"] + "•" + R.RST      # cyan bullet, matching render.markdown()
_QUOTE = R.DIM + "▏ " + R.RST           # blockquote rail


def _indent(text, prefix, first=None):
    """Prefix the first line with `first` (default `prefix`) and every following
    LOGICAL line with `prefix` — logical newlines only, so wrap_gutter still
    reflows the result at paint time."""
    lines = text.split("\n")
    out = []
    for i, ln in enumerate(lines):
        out.append((first if i == 0 and first is not None else prefix) + ln)
    return "\n".join(out)


if AVAILABLE:

    class OpsRenderer(BaseRenderer):
        """Render mdast nodes to styled ANSI (zero-width SGR + logical newlines).

        Handlers return strings; BaseRenderer concatenates them. Block handlers
        end their output with a single '\\n' so adjacent blocks separate.
        """

    def _children(r, n, c):
        return r.render_children(n.children, c)

    @OpsRenderer.register('text')
    def _text(r, n, c):
        return n.value or ""

    @OpsRenderer.register('paragraph')
    def _para(r, n, c):
        return _children(r, n, c) + "\n"

    @OpsRenderer.register('heading')
    def _heading(r, n, c):
        # All levels -> bold amber banner (matches render.markdown headings). The
        # depth is shown with a leading run of faint dots so h2/h3 stay legible.
        lead = (R.DIM + ("·" * (n.depth - 1)) + R.RST) if n.depth > 1 else ""
        return lead + R.BANNER + _children(r, n, c) + R.RST + "\n"

    @OpsRenderer.register('strong')
    def _strong(r, n, c):
        return "\033[1m" + _children(r, n, c) + "\033[22m"

    @OpsRenderer.register('emphasis')
    def _emph(r, n, c):
        return "\033[3m" + _children(r, n, c) + "\033[23m"

    @OpsRenderer.register('delete')                 # GFM strikethrough
    def _del(r, n, c):
        return "\033[9m" + _children(r, n, c) + "\033[29m"

    @OpsRenderer.register('inlineCode')
    def _icode(r, n, c):
        return R.COL["builtin"] + (n.value or "") + R.COL["def"]

    @OpsRenderer.register('link')
    def _link(r, n, c):
        inner = _children(r, n, c)
        url = getattr(n, "url", "") or ""
        return R.hyperlink(url, R.COL["func"] + inner + R.COL["def"]) if url else inner

    @OpsRenderer.register('image')
    def _image(r, n, c):
        alt = _children(r, n, c) or (getattr(n, "alt", "") or "")
        return R.DIM + "🖼 " + alt + R.RST

    @OpsRenderer.register('break')
    def _break(r, n, c):
        return "\n"

    @OpsRenderer.register('thematicBreak')
    def _hr(r, n, c):
        return R.DIM + "─────────" + R.RST + "\n"     # short logical marker; not width-aware

    @OpsRenderer.register('blockquote')
    def _quote(r, n, c):
        return _indent(_children(r, n, c).rstrip("\n"), _QUOTE) + "\n"

    @OpsRenderer.register('code')                    # fenced / indented code block
    def _code(r, n, c):
        body = (n.value or "").rstrip("\n")
        lang = getattr(n, "lang", None)
        tag = (R.DIM + lang + R.RST + "\n") if lang else ""
        # MVP: dim, indented, verbatim. (Future: per-language pygments via
        # render.format_code for bash/python.)
        return tag + _indent(R.COL["cmt"] + body + R.RST, "  ") + "\n"

    @OpsRenderer.register('listItem')
    def _item(r, n, c):
        # Render the item's block children, single-newline separated. The marker
        # + indentation is applied by the parent `list` handler (it knows the
        # ordinal), so here we just produce the item body.
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
            pad = " " * (len(marker) + 1)
            out.append(_indent(body, pad, first=colored))
        return "\n".join(out) + "\n"


class MarkdownStreamer:
    """Incremental markdown → styled-ANSI, block by block.

    The tailer feeds text as it arrives; we hold the trailing incomplete block
    and only render complete ones, so fenced code / multi-line constructs are
    never cut mid-block. Output is a list of styled strings — one per flush —
    each ready for O.gut(). On close() the buffered remainder is flushed.

    If wenmode is unavailable, degrades to the render.markdown() line subset.
    """

    def __init__(self):
        self.buf = ""
        self.wen = None
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
        return self._render(region)

    def close(self):
        region, self.buf = self.buf, ""
        return self._render(region) if region.strip() else []

    def _render(self, region):
        if self.wen is not None:
            try:
                out = self.wen.render(region).rstrip("\n")
                return [out] if out.strip() else []
            except Exception:
                pass                    # fall through to the subset renderer
        sub = R.markdown(R.unescape(region)).rstrip("\n")
        return [sub] if sub.strip() else []

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
