# dashboard/opshtml/ansi.py — ANSI/SGR -> HTML + the html.escape() security core.
#
# text_presentation, _esc, ansi_html (the neutralize() analog: every byte that
# reaches the page is escaped here), the SGR state machine, and the 256/16-colour
# fallbacks. The lowest layer every other presenter builds on.
import html
import re

from core import ops as OPS
from core import render as R



# Effectively-unwrapped width for codefmt.render: the web page owns wrapping
# (CSS pre-wrap / overflow-x), so the ANSI renderer must not hard-wrap first.
# Not maxsize — codefmt's column arithmetic stays in sane integer range.
CODE_W = 4000

# 16-color SGR fallbacks (30-37 / 90-97). The repo's own producers emit only
# truecolor (render.COL / ops colour table), but tailed command output can
# carry anything — One Dark-ish values so foreign output blends in. The four
# hues that exist in the semantic table come FROM it (single-owner rule).
_BASIC = [(40, 44, 52), OPS.RED, OPS.GREEN, OPS.YELLOW,
          OPS.BLUE, (198, 120, 221), (86, 182, 194), (171, 178, 191)]
_BRIGHT = [(92, 99, 112), OPS.RED, OPS.GREEN, OPS.YELLOW,
           OPS.BLUE, (198, 120, 221), (86, 182, 194), (255, 255, 255)]


def _x256(n):
    """xterm 256-color index -> (r, g, b)."""
    if n < 8:
        return _BASIC[n]
    if n < 16:
        return _BRIGHT[n - 8]
    if n < 232:
        n -= 16
        steps = (0, 95, 135, 175, 215, 255)
        return (steps[n // 36], steps[n // 6 % 6], steps[n % 6])
    v = 8 + (n - 232) * 10
    return (v, v, v)


def _apply_sgr(st, params):
    """Fold one SGR parameter string into the style dict `st` (keys: fg/bg
    (r,g,b) tuples, bold/dim/italic/underline flags)."""
    try:
        ps = [int(x) for x in re.split(r"[;:]", params) if x] or [0]
    except ValueError:
        return
    i = 0
    while i < len(ps):
        p = ps[i]
        if p == 0:
            st.clear()
        elif p == 1:
            st["bold"] = True
        elif p == 2:
            st["dim"] = True
        elif p == 3:
            st["italic"] = True
        elif p == 4:
            st["underline"] = True
        elif p in (21, 22):
            st.pop("bold", None); st.pop("dim", None)
        elif p == 23:
            st.pop("italic", None)
        elif p == 24:
            st.pop("underline", None)
        elif p == 39:
            st.pop("fg", None)
        elif p == 49:
            st.pop("bg", None)
        elif p in (38, 48):
            key = "fg" if p == 38 else "bg"
            if i + 4 < len(ps) and ps[i + 1] == 2:
                st[key] = (ps[i + 2], ps[i + 3], ps[i + 4]); i += 4
            elif i + 2 < len(ps) and ps[i + 1] == 5:
                st[key] = _x256(ps[i + 2] % 256); i += 2
            else:
                break                      # malformed extended colour — stop
        elif 30 <= p <= 37:
            st["fg"] = _BASIC[p - 30]
        elif 90 <= p <= 97:
            st["fg"] = _BRIGHT[p - 90]
        elif 40 <= p <= 47:
            st["bg"] = _BASIC[p - 40]
        elif 100 <= p <= 107:
            st["bg"] = _BRIGHT[p - 100]
        i += 1


def _css(st):
    """Inline CSS for a style dict; '' when default."""
    parts = []
    if "fg" in st:
        parts.append("color:rgb(%d,%d,%d)" % st["fg"])
    if "bg" in st:
        parts.append("background:rgb(%d,%d,%d)" % st["bg"])
    if st.get("bold"):
        parts.append("font-weight:600")
    if st.get("dim"):
        parts.append("opacity:.55")
    if st.get("italic"):
        parts.append("font-style:italic")
    if st.get("underline"):
        parts.append("text-decoration:underline")
    return ";".join(parts)


# SGR + OSC 8 — the exact two survivors of render.neutralize(); anything else
# was already stripped before this pattern runs.
# NO EMOJI (docs/dashboard.md, *No emoji*) — the text-presentation pass.
# Several symbols the terminal producers paint are EMOJI-CAPABLE codepoints:
# their DEFAULT presentation is text (that is how they render in kitty), but a
# browser whose page fonts lack the glyph falls back to the system COLOUR-emoji
# font, so the same `⚠ audit:` line the terminal shows in amber monochrome
# sprouted a colour emoji on the page. U+FE0E (VARIATION SELECTOR-15) is the
# standard "render this as text" request and pins them monochrome.
#
# It lives HERE, in the presenter, and not at the producers: these glyphs are
# single-owned audited vocabulary (`⚠ audit: <script>: <exception>` is asserted
# verbatim by the tests and quoted by docs/audit.md), and the terminal has no
# problem to fix. Same reason the module html-escapes here rather than upstream.
# The twin of this set lives in app.js (`tp()`) for the glyphs the PAGE writes.
_VS15 = "\ufe0e"
_EMOJI_CAPABLE = re.compile(
    "([\u203c\u2049\u2194\u21a9\u21aa\u2328\u23f1\u23f2\u25aa\u25ab"
    "\u25b6\u25c0\u2600\u2601\u260e\u2611\u2618\u2699\u26a0\u26d3"
    "\u2702\u2709\u2714\u2716\u2733\u2734\u2744\u2747\u27a1])"
    "(?![\ufe0e\ufe0f])")


def text_presentation(s):
    """Pin every emoji-capable symbol in `s` to its TEXT glyph (see above).
    Idempotent — a codepoint that already carries a variation selector is left
    alone, so re-rendering never stacks selectors."""
    return _EMOJI_CAPABLE.sub("\\1" + _VS15, s)


def _esc(s, quote=False):
    """html.escape + the text-presentation pass — the escape leaf every path
    that puts TEXT on the page goes through."""
    return html.escape(text_presentation(s), quote=quote)


_TOK = re.compile(r"\x1b\[[0-9;:]*m|\x1b\]8;;[^\x1b\x07]*(?:\x07|\x1b\\)")
_CC = re.compile(r"^claude-copy:/*(.+)$")


def ansi_html(s):
    """ANSI-styled text -> HTML: every character html-escaped, SGR runs as
    <span style=…>, OSC 8 links as anchors (claude-copy links as class="cc"
    app actions). Input is neutralized first, so unknown escapes never reach
    the escape step as invisible control bytes."""
    s = R.neutralize(s or "")
    out, st, link = [], {}, None
    pos = 0

    def flush(text):
        if not text:
            return
        css = _css(st)
        body = _esc(text)
        out.append("<span style=\"%s\">%s</span>" % (css, body) if css else body)

    for m in _TOK.finditer(s):
        flush(s[pos:m.start()])
        pos = m.end()
        seq = m.group(0)
        if seq.endswith("m"):                       # SGR
            _apply_sgr(st, seq[2:-1])
            continue
        url = seq[5:-2] if seq.endswith("\x1b\\") else seq[5:-1]   # \x1b]8;;URL(ST|BEL)
        if link is not None:
            out.append("</a>")
            link = None
        if url:
            cc = _CC.match(url)
            if cc:
                out.append("<a class=\"cc\" data-cc=\"%s\">"
                           % html.escape(cc.group(1).strip("/"), quote=True))
                link = url
            elif url.startswith(("http://", "https://")):
                # http(s) ONLY — the same scheme gate _md_inline applies. Op
                # text is RAW command output and OSC 8 is one of the two
                # survivors of neutralize(), so an attacker-printed
                # `\x1b]8;;javascript:…` (or data:) would otherwise become a
                # clickable href in the dashboard origin (XSS-on-click the
                # terminal mirror can't have — a terminal has no href). Any
                # other scheme opens NO anchor; the link's visible label still
                # renders as plain escaped text via flush().
                out.append("<a href=\"%s\" target=\"_blank\" rel=\"noopener\">"
                           % html.escape(url, quote=True))
                link = url
    flush(s[pos:])
    if link is not None:
        out.append("</a>")
    return "".join(out)


def _rgb(c, fallback=(120, 132, 158)):
    try:
        r, g, b = c
        return "rgb(%d,%d,%d)" % (int(r), int(g), int(b))
    except Exception:
        return "rgb(%d,%d,%d)" % fallback
