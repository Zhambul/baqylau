# dashboard/opshtml.py — the WEB presenter of the mirror's paint-op vocabulary.
#
# Third presenter over shapes owned elsewhere (the parse/paint precedent,
# docs/sessionapi.md): core/ops.py owns the op vocabulary (t/s/c/g/lk/v/lex/
# num/bg/outer), and this module renders it to HTML the way claude-mirror.py's
# _render renders it to ANSI. Width-dependent layout (wrapping, gutter repeats,
# rule length, chip truncation) deliberately does NOT port: in HTML those are
# CSS facts (pre-wrap, border-left, hr, text-overflow), so each op maps to a
# structured block instead of painted rows.
#
# SECURITY — the neutralize() analog: op text is RAW command output (attacker-
# adjacent bytes, the @kitty-cmd replay bug class). Every character that
# reaches the page goes through html.escape() inside ansi_html(); the only
# markup emitted is what THIS module writes. Input text additionally passes
# render.neutralize() first so only SGR styling and OSC 8 hyperlinks survive
# to be interpreted — the same two survivors the terminal renderer allows.
#
# OSC 8 links: a claude-copy:///<key>/<gid>/<what> link (the mirror's ⧉ copy /
# click-to-view scheme, core/copy.py) becomes <a class="cc" data-cc="key/gid/
# what"> — the app intercepts those (copy via the server's /copy endpoint,
# view via /view). Any other URL becomes a plain target=_blank anchor.
import html
import re

from core import codefmt as CF
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
        body = html.escape(text, quote=False)
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
            else:
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


# The default ⧉ pair a g-tagged label shows when it carries no "lk" spec —
# same fallback as the terminal renderer's _LINK_TXT (a command block).
_LINK_DEFAULT = (("cmd", "⧉cmd"), ("out", "⧉out"))


def _copy_links(key, g, lk):
    pairs = lk or _LINK_DEFAULT
    out = []
    for what, glyph in pairs:
        out.append("<a class=\"cc\" data-cc=\"%s/%s/%s\">%s</a>"
                   % (html.escape(str(key), quote=True),
                      html.escape(str(g), quote=True),
                      html.escape(str(what), quote=True),
                      html.escape(str(glyph), quote=False)))
    return "<span class=\"cl\">" + " ".join(out) + "</span>"


def _gutbody(op):
    """A gut op's paint text with its lex highlight + line numbers applied —
    the web twin of the mirror's viewbody() (pygments runs server-side here
    for the same reason it runs renderer-side there: the producer hook may
    have run a python without pygments)."""
    s = op.get("s", "")
    lex = op.get("lex")
    if lex:
        try:
            from core import coderender as C
            hi = C.render_code(s, lex)
            if hi is not None:
                s = hi
        except Exception:
            pass                           # unhighlighted is still correct
    num = op.get("num")
    if num is not None:
        s = "\n".join(R.DIM + "%5d " % (num + i) + R.RST + ln
                      for i, ln in enumerate(s.split("\n")))
    return s


def op_html(op, key=""):
    """One paint op -> one HTML block ('' for unknown/empty). `key` is the
    mirror-log key (paths.sid_from_log) the ⧉ copy links need; ops render
    fine without it (labels then just drop their copy affordances, like a
    very narrow pane does)."""
    t = op.get("t")
    if t == "blank":
        return "<div class=\"ob\"></div>"
    if t == "rule":
        return "<div class=\"orule\"></div>"
    if t == "label":
        chip = ("<span class=\"chip\" style=\"background:%s\">%s</span>"
                % (_rgb(op.get("c")), ansi_html(op.get("s", ""))))
        g = op.get("g")
        if g and key:
            chip += _copy_links(key, g, op.get("lk"))
        body = "<div class=\"ol\">%s</div>" % chip
        outer = op.get("outer")
        if outer is not None:
            return ("<div class=\"og\" style=\"border-color:%s\">%s</div>"
                    % (_rgb(outer), body))
        return body
    if t == "code":
        try:
            painted = CF.render(R.neutralize(op.get("s", "")), CODE_W,
                                op.get("ind", "  "))
        except Exception:
            painted = R.neutralize(op.get("s", ""))
        return "<pre class=\"oc\">%s</pre>" % ansi_html(painted)
    if t == "gut":
        s = _gutbody(op) if (op.get("lex") or op.get("num") is not None) \
            else op.get("s", "")
        cls = "ogut panel" if op.get("bg") is not None else "ogut"
        style = "border-left-color:%s" % _rgb(op.get("c"))
        if op.get("bg") is not None:
            style += ";background:%s" % _rgb(op.get("bg"))
        v = op.get("v")
        vattr = " data-v=\"%s\"" % html.escape(str(v), quote=True) if v else ""
        body = ("<div class=\"%s\" style=\"%s\"%s><pre>%s</pre></div>"
                % (cls, style, vattr, ansi_html(s)))
        outer = op.get("outer")
        if outer is not None:
            return ("<div class=\"og\" style=\"border-color:%s\">%s</div>"
                    % (_rgb(outer), body))
        return body
    if t == "line":
        v = op.get("v")
        vattr = " data-v=\"%s\"" % html.escape(str(v), quote=True) if v else ""
        return "<pre class=\"opl\"%s>%s</pre>" % (vattr,
                                                  ansi_html(op.get("s", "")))
    return ""


def ops_html(ops, key=""):
    """A batch of ops -> [html, …] (empty strings dropped — unknown op types
    paint nothing, same as the terminal renderer)."""
    out = []
    for op in ops:
        if isinstance(op, dict):
            h = op_html(op, key)
            if h:
                out.append(h)
    return out


def view_html(ops, key=""):
    """A click-to-view stash (the kv `view:<gid>` op list) -> one HTML block
    the app inserts under the clicked line."""
    return ("<div class=\"view-block\">%s</div>"
            % "".join(ops_html(ops, key)))
