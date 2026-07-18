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
import json
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


def _code_block(text, ind="  "):
    """Source text -> a highlighted `<pre class="oc">` — the shared body of the
    `code` op branch and the Bash tool presenter (single owner of "how a command
    block looks in HTML"). Neutralised, then run through codefmt.render at the
    unwrapped CODE_W (the page owns wrapping) and ansi_html (which escapes)."""
    try:
        painted = CF.render(R.neutralize(text or ""), CODE_W, ind)
    except Exception:
        painted = R.neutralize(text or "")
    return "<pre class=\"oc\">%s</pre>" % ansi_html(painted)


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
        return _code_block(op.get("s", ""), op.get("ind", "  "))
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
    paint nothing, same as the terminal renderer). Used for the click-to-view
    stashes, where the terminal block shape is wanted verbatim."""
    out = []
    for op in ops:
        if isinstance(op, dict):
            h = op_html(op, key)
            if h:
                out.append(h)
    return out


def op_items(ops, key=""):
    """A batch of ops -> [{g, t, html}, …] for the SESSION STREAM: the app
    folds same-`g` items into one collapsible block (the label ops become the
    block's summary chips), so a finished command reads as one line instead
    of a wall. `rule`/`blank` ops are dropped here — they are terminal-width
    spacing, and the web's block cards separate themselves."""
    out = []
    for op in ops:
        if not isinstance(op, dict):
            continue
        t = op.get("t")
        if t in ("rule", "blank"):
            continue
        h = op_html(op, key)
        if h:
            out.append({"g": op.get("g") or None, "t": t, "html": h})
    return out


def view_html(ops, key=""):
    """A click-to-view stash (the kv `view:<gid>` op list) -> one HTML block
    the app inserts under the clicked line."""
    return ("<div class=\"view-block\">%s</div>"
            % "".join(ops_html(ops, key)))


# --- markdown subset (md_html) ------------------------------------------------
# A dependency-free presenter for CONVERSATION text (assistant messages, user
# prompts, teammate mail). Two rules force the shape: the no-build/no-deps rule
# (docs/dashboard.md) rules OUT a markdown library, and the security rule (the
# ansi_html escape discipline above) rules OUT any "escape later" design. So
# escaping is the FIRST thing done to any text that becomes page content: block
# STRUCTURE is detected on the raw lines (the sigils #-*>`[]() are ASCII and
# emit nothing themselves), but every fragment that actually reaches the output
# is html.escape()d at its leaf — _md_inline escapes before layering emphasis,
# _md_fence escapes (directly, or via ansi_html on the highlighter's ANSI).
# No raw byte (<script> included) ever passes through to the page. It is a
# SUBSET on purpose: correctness of escaping beats markdown completeness, and
# any malformed input degrades to escaped plain text (md_html's outer guard).
_MD_HEAD = re.compile(r"^(#{1,4})\s+(.*?)\s*#*\s*$")
_MD_HR = re.compile(r"^ {0,3}([-*_])(?:\s*\1){2,}\s*$")     # --- / *** / ___
_MD_UL = re.compile(r"^ {0,3}[-*+]\s+(.*)$")
_MD_OL = re.compile(r"^ {0,3}\d+[.)]\s+(.*)$")
_MD_QUOTE = re.compile(r"^ {0,3}>\s?(.*)$")
_MD_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})\s*([\w.+-]*)\s*$")
# inline: same battle-tested emphasis shapes as render.markdown (emphasis must
# hug non-space text, so "2 * 3" and a bare "*" are left alone), plus code
# spans and http(s)-only links.
_MD_CODE = re.compile(r"`([^`\n]+?)`")
_MD_LINK = re.compile(r"\[([^\]\n]*)\]\(([^)\s]+)\)")
_MD_BOLD = re.compile(r"\*\*(\S.*?\S|\S)\*\*|__(\S.*?\S|\S)__")
_MD_ITAL = re.compile(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])"
                      r"|(?<![\w_])_(?!\s)(.+?)(?<!\s)_(?![\w_])")


def _md_inline(text):
    """Inline markup for one line of RAW text. ESCAPE FIRST — html.escape()
    runs before any tag is layered on, so every substitution below only
    rearranges safe bytes and no raw byte reaches the page. Order: stash code
    spans (their * _ [ ] must not re-interpret), then links (http(s)-only — any
    other scheme stays literal escaped text), then bold/italic, then restore
    the stashed code as <code> chips."""
    text = html.escape(text, quote=False)
    codes = []
    text = _MD_CODE.sub(
        lambda m: codes.append(m.group(1)) or "\x00%d\x00" % (len(codes) - 1), text)

    def _link(m):
        label, url = m.group(1), m.group(2)
        # url is already escaped; http(s) only — an (escaped) scheme like
        # "javascript:" fails this test and the whole match stays literal text.
        if url.startswith(("http://", "https://")):
            return "<a href=\"%s\" target=\"_blank\" rel=\"noopener\">%s</a>" \
                   % (html.escape(url, quote=True), label)
        return m.group(0)

    text = _MD_LINK.sub(_link, text)
    text = _MD_BOLD.sub(lambda m: "<strong>%s</strong>" % (m.group(1) or m.group(2)), text)
    text = _MD_ITAL.sub(lambda m: "<em>%s</em>" % (m.group(1) or m.group(2)), text)
    return re.sub(r"\x00(\d+)\x00",
                  lambda m: "<code>%s</code>" % codes[int(m.group(1))], text)


def _md_fence(body, lang):
    """A fenced code block -> one <pre class="md-code">. Highlight through the
    single lexer owner (render.lexer via coderender.render_code) to ANSI, then
    reuse ansi_html — the same round-trip _gutbody uses, and the same reason:
    the producer never ran here, and pygments may be absent (render_code then
    returns None and we fall back to plain escaped text)."""
    if lang:
        try:
            from core import coderender as C
            hi = C.render_code(body, lang.lower())
            if hi is not None:
                return "<pre class=\"md-code\">%s</pre>" % ansi_html(hi)
        except Exception:
            pass                           # unknown lexer / no pygments -> plain
    return "<pre class=\"md-code\">%s</pre>" % html.escape(body, quote=False)


def _md_para(lines):
    return "<p>%s</p>" % "<br>".join(_md_inline(x) for x in lines)


def _md_special(line):
    """True when `line` opens a block that ends the current paragraph."""
    return bool(_MD_HEAD.match(line) or _MD_HR.match(line) or _MD_UL.match(line)
                or _MD_OL.match(line) or _MD_QUOTE.match(line)
                or _MD_FENCE.match(line))


def md_html(text):
    """Render a markdown SUBSET to safe HTML (headings, bold/italic, inline &
    fenced code, un/ordered lists, blockquotes, http(s) links, rules,
    paragraphs). Escape-FIRST and single-level; malformed input NEVER raises —
    the outer guard returns escaped plain text. See the section header above."""
    try:
        # Block STRUCTURE is read from raw lines (sigils are ASCII); every leaf
        # that reaches output escapes itself (_md_inline / _md_fence).
        lines = (text or "").split("\n")
        out, i, n = [], 0, len(lines)
        while i < n:
            line = lines[i]
            m = _MD_FENCE.match(line)
            if m:                                  # fenced code (to closing fence/EOF)
                fence, lang, i = m.group(1)[0], m.group(2), i + 1
                body = []
                while i < n and not (lines[i].lstrip().startswith(fence * 3)
                                     and _MD_FENCE.match(lines[i])):
                    body.append(lines[i]); i += 1
                i += 1                             # consume the closing fence
                out.append(_md_fence("\n".join(body), lang))
                continue
            if _MD_HR.match(line):
                out.append("<hr>"); i += 1; continue
            m = _MD_HEAD.match(line)
            if m:
                lv = len(m.group(1))
                out.append("<h%d>%s</h%d>" % (lv, _md_inline(m.group(2)), lv))
                i += 1; continue
            if _MD_QUOTE.match(line):
                q = []
                while i < n and _MD_QUOTE.match(lines[i]):
                    q.append(_MD_QUOTE.match(lines[i]).group(1)); i += 1
                out.append("<blockquote>%s</blockquote>"
                           % "<br>".join(_md_inline(x) for x in q))
                continue
            if _MD_UL.match(line):
                items = []
                while i < n and _MD_UL.match(lines[i]):
                    items.append(_MD_UL.match(lines[i]).group(1)); i += 1
                out.append("<ul>%s</ul>"
                           % "".join("<li>%s</li>" % _md_inline(x) for x in items))
                continue
            if _MD_OL.match(line):
                items = []
                while i < n and _MD_OL.match(lines[i]):
                    items.append(_MD_OL.match(lines[i]).group(1)); i += 1
                out.append("<ol>%s</ol>"
                           % "".join("<li>%s</li>" % _md_inline(x) for x in items))
                continue
            if not line.strip():
                i += 1; continue
            para = []
            while i < n and lines[i].strip() and not _md_special(lines[i]):
                para.append(lines[i]); i += 1
            out.append(_md_para(para))
        return "".join(out)
    except Exception:
        return "<p>%s</p>" % html.escape(text or "", quote=False).replace("\n", "<br>")


def msg_html(kind, text, sender=""):
    """A main-thread CONVERSATION block for the merged web stream — not an op
    (the terminal mirror deliberately omits main-agent messages: the main
    pane already shows them; the web has no main pane, so the dashboard
    interleaves them — docs/dashboard.md). kind: prompt | message | teammsg.
    The body rides md_html (readable markdown), which is escape-first like
    everything else here — the same neutralize() analog."""
    who = {"prompt": "you", "message": "claude"}.get(kind) \
        or ("✉ " + (sender or "team"))
    extra = ""
    if kind == "prompt":
        # the web rewind picker needs the prompt's RAW text (the rendered
        # markdown is lossy): data-txt is what the page POSTs to /rewind-to
        # and prefills the composer with after a restore; the ↶ button is
        # hover/pick-mode revealed by CSS and handled by feed delegation
        extra = " data-txt=\"%s\"" % html.escape(text or "", quote=True)
        who = ("%s<button class=\"rw\" title=\"rewind to here\">↶</button>"
               % html.escape(who))
    else:
        who = html.escape(who)
    return ("<div class=\"msg %s\"%s><span class=\"who\">%s</span>"
            "<div class=\"md\">%s</div></div>"
            % (html.escape(kind, quote=True), extra, who, md_html(text)))


# --- rich tool rendering (tool_html / tool_output_html) -----------------------
# The drill-down timeline lists every tool CALL; a raw JSON dump of its input is
# unscannable. These presenters render the INPUT of Claude Code's well-known
# built-in tools as structured HTML, reusing the single owners of those payload
# shapes rather than re-encoding them: plugins.claude_code.tools (the built-in
# tool payload owner — diff_rows / read_extent / FILE_RGB), core.codefmt (the
# command highlighter behind `code` ops), core.streamfmt (the file-op one-liner
# vocabulary), and core.coderender (the lexer table + highlighter). Unknown
# tools return None so the server keeps the existing escaped-JSON fallback.
# Escape discipline is unchanged — every leaf rides ansi_html / html.escape.
WRITE_CAP = 200                    # Write content lines shown before an elision
_DEFLIST_TOOLS = ("Grep", "Glob", "WebFetch", "WebSearch", "Task", "SendMessage")
_EDIT_TOOLS = ("Edit", "MultiEdit", "NotebookEdit")


def _first_line(s, n=200):
    """First line of `s`, capped at n chars — for a definition-list value whose
    full text (a Task prompt, a SendMessage body) would be a wall."""
    s = (s or "").strip()
    nl = s.find("\n")
    if nl >= 0:
        s = s[:nl]
    return s[:n] + "…" if len(s) > n else s


def _lexer_for(path):
    """Pygments lexer name for a file path's extension via coderender.LANGS (the
    single owner of the ext->lexer table), or None."""
    try:
        from core.coderender import LANGS
    except Exception:
        return None
    low = (path or "").lower()
    for ext, lexer in LANGS.items():
        if low.endswith(ext):
            return lexer
    return None


def _bash_html(inp):
    cmd = inp.get("command") or ""
    out = _code_block(cmd)
    desc = inp.get("description")
    if desc:
        out += "<div class=\"tdesc\">%s</div>" % html.escape(str(desc), quote=False)
    return out


def _edit_html(tool_name, inp):
    """Edit/MultiEdit/NotebookEdit input as a line-numbered red/green diff via
    tools.diff_rows (the single owner; empty result dict makes it fall back to a
    difflib diff over the input's old/new strings — all we have at input time)."""
    from plugins.claude_code import tools as T
    rows = T.diff_rows(tool_name, inp, {})
    if not rows:
        return None
    out = []
    if inp.get("replace_all"):
        out.append("<div class=\"tflag\">replace_all</div>")
    lines = []
    for sign, no, text in rows:
        if sign == "@":
            lines.append("<div class=\"dl sep\"><span class=\"tx\">⋮</span></div>")
            continue
        cls = {"+": "added", "-": "removed"}.get(sign, "ctx")
        ln = "" if no is None else str(no)
        lines.append("<div class=\"dl %s\"><span class=\"ln\">%s</span>"
                     "<span class=\"tx\">%s</span></div>"
                     % (cls, html.escape(ln), html.escape(text, quote=False)))
    out.append("<div class=\"tdiff\">%s</div>" % "".join(lines))
    return "".join(out)


def _write_html(inp):
    path = inp.get("file_path") or ""
    content = inp.get("content") or ""
    head = ("<div class=\"tfile\">%s</div>" % html.escape(path, quote=False)
            if path else "")
    all_lines = content.split("\n")
    shown = "\n".join(all_lines[:WRITE_CAP])
    lexer = _lexer_for(path)
    body = None
    if lexer:
        try:
            from core import coderender as C
            hi = C.render_code(shown, lexer)
            if hi is not None:
                body = "<pre class=\"oc\">%s</pre>" % ansi_html(hi)
        except Exception:
            body = None
    if body is None:
        body = "<pre class=\"oc\">%s</pre>" % html.escape(shown, quote=False)
    more = len(all_lines) - WRITE_CAP
    if more > 0:
        body += ("<div class=\"telide\">… (%d more line%s)</div>"
                 % (more, "" if more == 1 else "s"))
    return head + body


def _read_html(inp):
    """Read input as streamfmt's `verb(name)[  extent]` one-liner (the single
    owner of that shape), coloured via SGR and run through ansi_html. file_display
    resolves location against the dashboard process cwd (no session cwd here), so
    a file outside it shows its dim abbreviated dir — informative, not a bug."""
    from core import streamfmt as SF
    from plugins.claude_code import tools as T
    path = inp.get("file_path") or ""
    if not path:
        return None
    disp, _ = SF.file_display(path)
    extent = T.read_extent(None, inp)
    line = SF.file_line("Read", disp, T.FILE_RGB["Read"], extent=extent)
    return "<div class=\"tline\">%s</div>" % ansi_html(line)


def _deflist_html(inp):
    rows = []
    for k, v in inp.items():
        vs = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
        rows.append("<dt>%s</dt><dd>%s</dd>"
                    % (html.escape(str(k), quote=False),
                       html.escape(_first_line(vs), quote=False)))
    if not rows:
        return None
    return "<dl class=\"tdl\">%s</dl>" % "".join(rows)


def tool_html(tool_name, inp):
    """Rich HTML for a well-known tool's INPUT, or None (unknown tool / bad
    shape) so the caller keeps its escaped-JSON fallback. Escape-first like
    everything here — every leaf rides ansi_html or html.escape."""
    if not isinstance(inp, dict) or not inp:
        return None
    if tool_name == "Bash":
        return _bash_html(inp)
    if tool_name in _EDIT_TOOLS:
        return _edit_html(tool_name, inp)
    if tool_name == "Write":
        return _write_html(inp)
    if tool_name == "Read":
        return _read_html(inp)
    if tool_name in _DEFLIST_TOOLS:
        return _deflist_html(inp)
    return None


def tool_output_html(text, failed=False, tool_name=""):
    """Rich HTML for a tool's OUTPUT, or None when a plain escaped <pre> (the
    caller's default) already suffices. Only Bash differs: its output can carry
    ANSI (transcripts usually strip it, but harmless), so it rides ansi_html so
    preserved SGR colours render. `failed` is accepted for symmetry with the
    caller; the failure styling stays on the caller's label."""
    if tool_name == "Bash" and text:
        return "<pre class=\"oc\">%s</pre>" % ansi_html(text)
    return None
