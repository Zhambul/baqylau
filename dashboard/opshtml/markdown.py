# dashboard/opshtml/markdown.py — a small, safe Markdown -> HTML subset.
#
# md_html + its inline/fence/table helpers, for the memory-note render and the
# assistant message bubbles. Escapes through ansi._esc; emits only the markup it
# writes itself.
import re

from dashboard.opshtml.ansi import ansi_html, _esc




# --- markdown subset (md_html) ------------------------------------------------
# A dependency-free presenter for CONVERSATION text (assistant messages, user
# prompts, teammate mail). Two rules force the shape: the no-build/no-deps rule
# (docs/dashboard.md) rules OUT a markdown library, and the security rule (the
# ansi_html escape discipline above) rules OUT any "escape later" design. So
# escaping is the FIRST thing done to any text that becomes page content: block
# STRUCTURE is detected on the raw lines (the sigils #-*>`[]()| are ASCII and
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
# a pipe-table delimiter row: cells of :?-+:? split by |, optional outer pipes.
# The regex alone also matches a bare "---" (zero pipes) — _md_tsep additionally
# requires a "|" so it can never collide with _MD_HR.
_MD_TSEP = re.compile(r"^ {0,3}\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)*\|?\s*$")
# inline: same battle-tested emphasis shapes as render.markdown (emphasis must
# hug non-space text, so "2 * 3" and a bare "*" are left alone), plus code
# spans and http(s)-only links.
_MD_CODE = re.compile(r"`([^`\n]+?)`")
_MD_LINK = re.compile(r"\[([^\]\n]*)\]\(([^)\s]+)\)")
_MD_BOLD = re.compile(r"\*\*(\S.*?\S|\S)\*\*|__(\S.*?\S|\S)__")
_MD_ITAL = re.compile(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])"
                      r"|(?<![\w_])_(?!\s)(.+?)(?<!\s)_(?![\w_])")
# a bare URL in prose (autolinked). Runs on ESCAPED text, so raw <> can't
# occur — the tempered class stops at their &lt;/&gt; entities instead (an
# adjacent "<" belongs to the prose, not the URL; mid-URL &amp; still passes);
# \x00/\x01 are excluded so a URL can never swallow a stashed
# code-span/link placeholder.
_MD_URL = re.compile(r"https?://(?:(?!&lt;|&gt;)[^\s\x00\x01])+")
# what _trim_url peels off a bare URL's tail: prose punctuation the sentence
# owns, not the URL — the &amp; entity (a raw trailing "&") FIRST, since it
# also ends in ";" and peeling just the ";" would strand a broken "&amp",
# then the raw single chars (quotes stay raw under escape(quote=False)).
_URL_TRAIL = ("&amp;", ".", ",", ";", ":", "!", "?", "*", "'", "\"")


def _trim_url(url):
    """Split a bare-URL match into (url, trailing prose punctuation): in
    'see https://x.test.' the period is the sentence's, '<https://x>' arrives
    escaped as '&lt;…&gt;', and a ')' is peeled only while unbalanced so a
    wiki-style '…/Foo_(bar)' path survives while '(see https://x)' drops it."""
    trail = ""
    while True:
        for t in _URL_TRAIL:
            if url.endswith(t):
                url, trail = url[:-len(t)], t + trail
                break
        else:
            if url.endswith(")") and url.count("(") < url.count(")"):
                url, trail = url[:-1], ")" + trail
            else:
                return url, trail


def _md_inline(text):
    """Inline markup for one line of RAW text. ESCAPE FIRST — html.escape()
    runs before any tag is layered on, so every substitution below only
    rearranges safe bytes and no raw byte reaches the page. Order: stash code
    spans (their * _ [ ] must not re-interpret), then links — markdown
    [label](url) and bare-URL autolinks, http(s)-only either way; any other
    scheme stays literal escaped text — stashed too, so emphasis can't chew a
    URL's _ or * and the autolink pass can't re-match inside a built href;
    then bold/italic over the remaining prose, then restore the stashes."""
    text = _esc(text)
    codes = []
    text = _MD_CODE.sub(
        lambda m: codes.append(m.group(1)) or "\x00%d\x00" % (len(codes) - 1), text)
    links = []

    def _emph(t):
        t = _MD_BOLD.sub(lambda m: "<strong>%s</strong>" % (m.group(1) or m.group(2)), t)
        return _MD_ITAL.sub(lambda m: "<em>%s</em>" % (m.group(1) or m.group(2)), t)

    def _stash(url, label):
        # url is already escaped, so only quotes still need attribute-arming
        # (html.escape()ing again would double-escape its &amp; entities).
        links.append("<a href=\"%s\" target=\"_blank\" rel=\"noopener\">%s</a>"
                     % (url.replace("\"", "&quot;"), label))
        return "\x01%d\x01" % (len(links) - 1)

    def _link(m):
        label, url = m.group(1), m.group(2)
        # http(s) only — an (escaped) scheme like "javascript:" fails this
        # test and the whole match stays literal text. Emphasis inside the
        # label renders here, before the anchor is stashed away from _emph.
        if url.startswith(("http://", "https://")):
            return _stash(url, _emph(label))
        return m.group(0)

    def _auto(m):
        url, trail = _trim_url(m.group(0))
        if not url.split("://", 1)[1]:         # trimmed down to a bare scheme
            return m.group(0)
        return _stash(url, url) + trail

    text = _MD_LINK.sub(_link, text)
    text = _MD_URL.sub(_auto, text)
    text = _emph(text)
    text = re.sub(r"\x01(\d+)\x01", lambda m: links[int(m.group(1))], text)
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
    return "<pre class=\"md-code\">%s</pre>" % _esc(body)


def _md_para(lines):
    return "<p>%s</p>" % "<br>".join(_md_inline(x) for x in lines)


def _md_tsep(line):
    """True when `line` is a table delimiter row (|---|:---:|…)."""
    return "|" in line and bool(_MD_TSEP.match(line))


def _md_cells(line):
    """Split one table row into raw cell texts. \\| is a literal pipe (stashed
    through the split, restored raw — _md_inline escapes it at the leaf); one
    leading/trailing pipe is decoration, not an empty cell. Subset limitation:
    a bare | inside a backtick code span still splits the cell."""
    line = line.strip().replace("\\|", "\x00")
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip().replace("\x00", "|") for c in line.split("|")]


def _md_table_start(lines, i):
    """Two-line lookahead (the one block that needs it): a header row with a
    pipe, over a delimiter row with the SAME cell count (the GFM rule — a
    mismatch means it isn't a table and stays a paragraph)."""
    return (i + 1 < len(lines) and "|" in lines[i] and _md_tsep(lines[i + 1])
            and len(_md_cells(lines[i])) == len(_md_cells(lines[i + 1])))


def _md_table(lines, i):
    """Consume a pipe table at `lines[i]` -> (html, next_i). Alignment comes
    from the delimiter colons as a CLOSED class vocabulary (ta-c/ta-r; left is
    the default) — never from input text. Body rows are padded/truncated to
    the header's column count (GFM behavior); a pipe-less or blank line ends
    the table. Every cell rides _md_inline, so escaping is unchanged."""
    head, seps = _md_cells(lines[i]), _md_cells(lines[i + 1])
    aligns = ["ta-c" if s.startswith(":") and s.endswith(":")
              else "ta-r" if s.endswith(":") else "" for s in seps]

    def row(tag, cells):
        return "<tr>%s</tr>" % "".join(
            "<%s%s>%s</%s>" % (tag, " class=\"%s\"" % aligns[k] if aligns[k] else "",
                               _md_inline(c), tag)
            for k, c in enumerate(cells))

    body, i = [], i + 2
    while i < len(lines) and "|" in lines[i] and lines[i].strip():
        body.append(row("td", (_md_cells(lines[i]) + [""] * len(head))[:len(head)]))
        i += 1
    return ("<div class=\"md-tbl\"><table><thead>%s</thead><tbody>%s</tbody>"
            "</table></div>" % (row("th", head), "".join(body))), i


def _md_special(line):
    """True when `line` opens a block that ends the current paragraph."""
    return bool(_MD_HEAD.match(line) or _MD_HR.match(line) or _MD_UL.match(line)
                or _MD_OL.match(line) or _MD_QUOTE.match(line)
                or _MD_FENCE.match(line))


def md_html(text):
    """Render a markdown SUBSET to safe HTML (headings, bold/italic, inline &
    fenced code, un/ordered lists, blockquotes, http(s) links, rules, pipe
    tables, paragraphs). Escape-FIRST and single-level; malformed input NEVER
    raises — the outer guard returns escaped plain text. See the section
    header above."""
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
            if _md_table_start(lines, i):
                tbl, i = _md_table(lines, i)
                out.append(tbl)
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
            while i < n and lines[i].strip() and not _md_special(lines[i]) \
                    and not _md_table_start(lines, i):
                para.append(lines[i]); i += 1
            out.append(_md_para(para))
        return "".join(out)
    except Exception:
        return "<p>%s</p>" % _esc(text or "").replace("\n", "<br>")
