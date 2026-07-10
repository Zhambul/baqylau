# core/render.py — shared rendering primitives for the command mirror.
# (Importable as `claude_render` via the top-level compat shim.)
#
# Extracted from claude-cmd-fmt.py so the same bash/python syntax highlighting,
# ANSI-aware gutter wrapping, escape-unescaping, and chip labels can be reused by
# claude-substream.py (which renders a subagent's transcript). Everything here is
# width-parameterised (no module-level WIDTH) so it imports cleanly.
import os
import re
import sys
import unicodedata


# --- display width ---------------------------------------------------------------
# All column accounting below counts TERMINAL CELLS, not code points: CJK and most
# emoji occupy 2 cells, combining marks / ZWJ / variation selectors occupy 0. Using
# len() here made any op containing wide text overrun the pane and knocked the `│ `
# gutter out of alignment on wrapped rows.
def dwidth(s):
    w = 0
    for ch in s:
        if ch < "\x80":                        # ASCII fast path (the common case)
            w += 1
        elif unicodedata.combining(ch) or unicodedata.category(ch) in ("Mn", "Me", "Cf"):
            pass                               # zero-width: combining, ZWJ, VS16, …
        else:
            w += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return w


def dsplit(s, avail):
    """Split `s` so the head fits in `avail` display cells: (head, tail). The head is
    empty when even the first character is wider than `avail`."""
    w = 0
    for i, ch in enumerate(s):
        cw = dwidth(ch)
        if w + cw > avail:
            return s[:i], s[i:]
        w += cw
    return s, ""


def term_width(fixed=None, floor=16, fallback=53):
    """The live pane width for a renderer (claude-mirror / claude-scorebar):
    an explicit argv override when given, else the terminal size — re-queried
    every paint so a SIGWINCH repaint sees the new width."""
    if fixed:
        return max(floor, fixed)
    try:
        return max(floor, os.get_terminal_size(sys.stdout.fileno()).columns)
    except Exception:
        return fallback


def fit(s, avail):
    """Truncate `s` to `avail` display cells, ellipsis when it doesn't fit."""
    if dwidth(s) <= avail:
        return s
    if avail > 1:
        return dsplit(s, avail - 1)[0] + "…"
    return dsplit(s, max(0, avail))[0]


# --- One Dark-ish palette (truecolor; kitty supports it) -----------------------
def fg(r, g, b):
    return f"\033[38;2;{r};{g};{b}m"


COL = {
    "kw":      fg(198, 120, 221),  # magenta — keywords (for/if/def/import/…)
    "builtin": fg(86, 182, 194),   # cyan    — builtins (echo/cd/print/len/…)
    "func":    fg(97, 175, 239),   # blue    — function names
    "str":     fg(152, 195, 121),  # green   — strings
    "var":     fg(229, 192, 123),  # yellow  — $variables
    "num":     fg(209, 154, 102),  # orange  — numbers
    "op":      fg(86, 182, 194),   # cyan    — operators / punctuation
    "cmt":     fg(92, 99, 112),    # grey    — comments
    "def":     fg(171, 178, 191),  # default foreground
}
DIM = fg(92, 99, 112)
RST = "\033[0m"


def rule(width):
    return DIM + ("─" * width) + RST


# A header/summary chip: bold dark text on a solid colour, so the kind of block is
# unmistakable at a glance.
def label(text, rgb):
    r, g, b = rgb
    return f"\033[1;38;2;24;26;30;48;2;{r};{g};{b}m {text} {RST}"


# Render escape sequences a command printed as *text* (e.g. via `cat -v`, which
# writes "^[[…m" literally) back to real ESC bytes so the pane interprets them.
# Unescapes ALL sequences, not just colours.
_ESC_UNESC = re.compile(r"\^\[|\\0?33|\\x1[bB]|\\e|\\u001[bB]|<[Ee][Ss][Cc]>")


def unescape(s):
    return _ESC_UNESC.sub("\x1b", s)


# Prefix every line with a colour-coded gutter; hard-wrap wider-than-pane lines so
# the gutter repeats on each visual row. ANSI-aware: escape sequences are copied
# verbatim (zero width) and the active SGR colour re-asserted after each wrap.
_ANSI = re.compile(r"\x1b\[[0-9;:?]*[ -/]*[@-~]|\x1b[@-Z\\-_]")


def strip_ansi(s):
    """The visible text of an ANSI-styled string (what claude-copy.py puts on the
    clipboard from a gut op — colours/emphasis are display styling, not content)."""
    return _ANSI.sub("", s)


def hyperlink(url, text):
    """Wrap `text` in an OSC 8 hyperlink (zero display width). kitty resolves a
    click through ~/.config/kitty/open-actions.conf — the mirror's ⧉ copy links
    (claude-copy:// scheme) ride on this."""
    return "\x1b]8;;" + url + "\x1b\\" + text + "\x1b]8;;\x1b\\"


def wrap_gutter(text, width, gut, gw):
    cw = max(1, width - gw)                       # visible columns after the gutter
    pieces, lines = [], text.split("\n")
    for li, line in enumerate(lines):
        # Expand tabs BEFORE any width math: the terminal advances a raw \t to
        # the next tab stop, but every counter here saw it as 1 cell — any
        # tab-containing output (git diff, Makefiles, TSV, `column -t`) overran
        # the pane and knocked the gutter out of alignment on wrapped rows.
        # (Exact tab-stop columns are unknowable anyway once the gutter shifts
        # everything; deterministic spaces are the point.)
        if "\t" in line:
            line = line.expandtabs(8)
        if li:
            pieces.append("\n")
        pieces.append(gut)
        # Soft-wrap on WORD boundaries: gather whitespace / word runs (ANSI copied
        # verbatim, zero width) and break before a word that won't fit, dropping the
        # space that would have led it. Only a single word longer than a whole row is
        # hard-broken. `active` re-asserts the live SGR colour after every wrap.
        col, active, pending_sp, i, n = 0, "", "", 0, len(line)
        while i < n:
            m = _ANSI.match(line, i)
            if m:
                seq = m.group(0)
                pieces.append(seq)
                if seq.endswith("m"):
                    active = "" if seq in ("\x1b[0m", "\x1b[m") else active + seq
                i = m.end()
                continue
            if line[i] in " \t":                  # accumulate a whitespace run
                j = i
                while j < n and line[j] in " \t" and not _ANSI.match(line, j):
                    j += 1
                pending_sp += line[i:j]; i = j
                continue
            j = i                                 # a word: a run of non-space, non-ANSI
            while j < n and line[j] not in " \t" and not _ANSI.match(line, j):
                j += 1
            word = line[i:j]; i = j
            ww = dwidth(word)
            if col > 0 and col + dwidth(pending_sp) + ww > cw:
                pieces.append(RST + "\n" + gut + active); col = 0; pending_sp = ""
            if pending_sp:                        # leading indent at a real line start is kept
                pieces.append(pending_sp); col += dwidth(pending_sp); pending_sp = ""
            while col + ww > cw:                  # single word wider than a row -> hard-break
                head, word = dsplit(word, cw - col)
                if not head and col == 0:         # one char wider than the whole row: emit it
                    head, word = word[:1], word[1:]
                pieces.append(head)
                pieces.append(RST + "\n" + gut + active); col = 0
                ww = dwidth(word)
            pieces.append(word); col += ww
        pieces.append(RST)
    return "".join(pieces)


# Section-banner lines that scripts (and Claude Code itself) print to delimit
# output — `=== title ===`, `--- title ---`, `### title ###`. Make them pop (bold +
# amber) so the eye catches section boundaries in a wall of command output. Detection
# runs on each line's VISIBLE text (ANSI stripped) and is deliberately conservative so
# we don't light up unrelated punctuation:
#   • equals family: a run of 2+ '=' followed by whitespace or end-of-line — catches
#     `=== x ===`, `======`, `== x ==`, but NOT `==123==` (valgrind) or `x == y`.
#   • dash/hash/star/tilde: must be BRACKETED — the same symbol, 3+, at BOTH ends —
#     so `--- title ---` lights up but a diff header `--- a/file` (no trailing run)
#     and a bare `-----` rule do not.
BANNER      = "\033[1m" + fg(229, 192, 123)          # bold amber
_BANNER_EQ  = re.compile(r"^\s*={2,}(?:\s.*)?$")
_BANNER_SYM = re.compile(r"^\s*([-#*~])\1{2,}\s+\S.*\S\s+\1{3,}\s*$")


def emphasize(text):
    """Highlight section-banner lines within command output (see BANNER). Returns the
    text unchanged when it holds no banner-like characters, so ordinary output is free."""
    if not text or not any(c in text for c in "=-#*~"):
        return text
    out = []
    for line in text.split("\n"):
        vis = _ANSI.sub("", line)
        if _BANNER_EQ.match(vis) or _BANNER_SYM.match(vis):
            out.append(BANNER + line + RST)
        else:
            out.append(line)
    return "\n".join(out)


# --- inline markdown -> ANSI ---------------------------------------------------
# Claude Code messages are markdown. In the mirror we render a useful subset inline so
# a message reads the way it was written: **bold**/__bold__, *italic*/_italic_,
# `code`, ATX headings (`## Title`), and `-`/`*`/`+` bullets. Width-independent (only
# adds zero-width SGR + swaps the bullet glyph), so it runs at op creation and the
# gutter wrapper's word-wrap + colour re-assertion carry the styling across rows.
# Best-effort and conservative: emphasis must hug non-space text, so `2 * 3` and a bare
# `*` are left alone. Uses granular OFF codes (22/23/39) rather than a full reset so a
# span nested inside another keeps the outer style.
_MD_H      = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_MD_BULLET = re.compile(r"^(\s*)[-*+](\s+)")
_MD_CODE   = re.compile(r"`([^`\n]+?)`")
_MD_BOLD   = re.compile(r"\*\*(\S.*?\S|\S)\*\*|__(\S.*?\S|\S)__")
_MD_ITAL   = re.compile(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])"
                        r"|(?<![\w_])_(?!\s)(.+?)(?<!\s)_(?![\w_])")


def markdown(text):
    if not text or not any(c in text for c in "*_`#-+"):
        return text
    out = []
    for line in text.split("\n"):
        h = _MD_H.match(line)
        if h:
            out.append(BANNER + h.group(2) + RST)         # heading -> bold amber
            continue
        line = _MD_BULLET.sub(lambda m: m.group(1) + COL["op"] + "•" + RST + m.group(2), line, count=1)
        codes = []                                        # stash `code` so its * / _ don't emphasise
        line = _MD_CODE.sub(lambda m: codes.append(m.group(1)) or f"\x00{len(codes) - 1}\x00", line)
        line = _MD_BOLD.sub(lambda m: "\033[1m" + (m.group(1) or m.group(2)) + "\033[22m", line)
        line = _MD_ITAL.sub(lambda m: "\033[3m" + (m.group(1) or m.group(2)) + "\033[23m", line)
        line = re.sub(r"\x00(\d+)\x00",
                      lambda m: COL["builtin"] + codes[int(m.group(1))] + COL["def"], line)
        out.append(line)
    return "\n".join(out)


def pick(ttype):
    s = str(ttype)
    if s == "Cmd":                                             return COL["func"]
    if s.startswith("Token.Comment"):                          return COL["cmt"]
    if s.startswith(("Token.Literal.String", "Token.String")): return COL["str"]
    if s.startswith("Token.Keyword"):                          return COL["kw"]
    if s.startswith("Token.Name.Builtin"):                     return COL["builtin"]
    if s.startswith("Token.Name.Function"):                    return COL["func"]
    if s.startswith("Token.Name.Variable"):                    return COL["var"]
    if s.startswith(("Token.Literal.Number", "Token.Number")): return COL["num"]
    if s.startswith(("Token.Operator", "Token.Punctuation")):  return COL["op"]
    return COL["def"]


# Split a bash command into (lang, text) segments so embedded Python gets the
# Python lexer instead of being treated as a bash string (heredocs fed to python,
# and `python -c '…'` arguments). Concatenating segments reproduces the command.
HEREDOC  = re.compile(r"""<<(-?)\s*(['"]?)([A-Za-z_]\w*)\2""")
PYC      = re.compile(r"""(\bpython[0-9.]*\b[^\n]*?\s-c\s+)(['"])(.*?)\2""", re.DOTALL)
PYINTERP = re.compile(r"\bpython[0-9.]*\b")


def _split_heredocs(code):
    lines, segs, buf, i = code.splitlines(keepends=True), [], [], 0
    n = len(lines)
    while i < n:
        line = lines[i].rstrip("\n")
        m = HEREDOC.search(line)
        if m:
            dash, delim = m.group(1) == "-", m.group(3)
            body_lang = "python" if PYINTERP.search(line) else "bash"
            buf.append(lines[i]); i += 1
            term = re.compile((r"^[ \t]*" if dash else r"^") + re.escape(delim) + r"[ \t]*$")
            body = []
            while i < n and not term.match(lines[i].rstrip("\n")):
                body.append(lines[i]); i += 1
            if body_lang == "python" and body:
                segs.append(("bash", "".join(buf))); buf = []
                segs.append(("python", "".join(body)))
            else:
                buf.extend(body)
            if i < n:
                buf.append(lines[i]); i += 1
            continue
        buf.append(lines[i]); i += 1
    if buf:
        segs.append(("bash", "".join(buf)))
    return segs


def _split_python_c(text):
    out, pos = [], 0
    for m in PYC.finditer(text):
        out.append(("bash", text[pos:m.start()] + m.group(1) + m.group(2)))
        out.append(("python", m.group(3)))
        out.append(("bash", m.group(2)))
        pos = m.end()
    out.append(("bash", text[pos:]))
    return [s for s in out if s[1]]


_SEP     = {";", ";;", "|", "||", "|&", "&", "&&", "(", "{", "!", "$(", "`"}
_KW_CMD  = {"do", "then", "else", "elif", "if", "while", "until", "time"}
_CMDWORD = re.compile(r"^[\w./@:+-]+$")


def _mark_bash_commands(toks):
    out, expect, n = [], True, len(toks)

    def nxt(j):
        k = j + 1
        while k < n and toks[k][1].strip() == "":
            k += 1
        return k

    for idx, (tt, val) in enumerate(toks):
        s, w = str(tt), val.strip()
        if w == "":
            if "\n" in val:
                expect = True
            out.append((tt, val)); continue
        if w in _SEP:
            out.append((tt, val)); expect = True; continue
        if s.startswith("Token.Keyword"):
            out.append((tt, val)); expect = w in _KW_CMD; continue
        if expect and _CMDWORD.match(w):
            k = nxt(idx)
            if k < n and toks[k][1].lstrip().startswith("="):
                out.append((tt, val)); continue
            out.append((tt, val) if s.startswith("Token.Name.Builtin") else ("Cmd", val))
            expect = False; continue
        out.append((tt, val)); expect = False
    return out


def _mixed_tokens(code):
    from pygments.lexers import BashLexer, PythonLexer
    lex, segs = {"bash": BashLexer(), "python": PythonLexer()}, []
    for lang, text in _split_heredocs(code):
        segs.extend(_split_python_c(text) if lang == "bash" else [(lang, text)])
    toks = []
    for lang, text in segs:
        raw = [(tt, val) for _, tt, val in lex[lang].get_tokens_unprocessed(text)]
        toks.extend(_mark_bash_commands(raw) if lang == "bash" else raw)
    return toks


# --- pretty-printing (width-INDEPENDENT, done once at op creation) -------------
# A dense one-liner is hard to read in the mirror. Before highlighting, reflow the
# command into multi-line form: reformat embedded Python with `ast`, and break dense
# bash at its top-level control operators. This is width-independent (real newlines,
# which the renderer then wraps further if needed), so it belongs at op creation — see
# claude_ops.code(). Best-effort throughout: any doubt → return the text unchanged.


def _fmt_python(text):
    """Reformat a Python snippet (a `-c` argument or heredoc body) via ast — turning a
    `a; b; print(c)` one-liner into real lines. Skipped when it holds comments (ast
    drops them) or won't parse. Preserves the segment's surrounding whitespace so it
    sits back inside its quotes."""
    if "#" in text:                       # ast.unparse discards comments — don't lose them
        return text
    body = text.strip()
    if not body:
        return text
    try:
        import ast
        pretty = ast.unparse(ast.parse(body))
    except Exception:
        return text
    lead = text[:len(text) - len(text.lstrip())]
    trail = text[len(text.rstrip()):]
    return lead + pretty + trail


_BASH_SPLIT = ("&&", "||", "|", ";")


def _fmt_bash(text):
    """Reflow a dense bash one-liner: break after top-level `&&` / `||` / `|` and turn
    `;` into a line break. Only touches genuine one-liners (already-multi-line commands,
    heredocs, and `case` bodies are left as the author wrote them). Operators inside
    strings/comments are skipped — the lexer classifies them, so `echo "a && b"` is safe.
    Returns the text unchanged when nothing top-level was found."""
    if "\n" in text or "<<" in text or ";;" in text:
        return text
    try:
        from pygments.lexers import BashLexer
        toks = [(str(tt), v) for _, tt, v in BashLexer().get_tokens_unprocessed(text)]
    except Exception:
        return text
    out, broke = [], False
    for s, v in toks:
        st = v.strip()
        code_tok = not s.startswith(("Token.Literal.String", "Token.String", "Token.Comment"))
        if code_tok and st in ("&&", "||", "|"):
            out.append(st + "\n"); broke = True; continue
        if code_tok and st == ";":
            out.append("\n"); broke = True; continue
        out.append(v)
    if not broke:
        return text
    res = re.sub(r"[ \t]*\n[ \t]*", "\n", "".join(out))   # trim ws around the new breaks
    return re.sub(r"\n{2,}", "\n", res).strip("\n") or text


def format_code(code):
    """Pretty-print a command for display (see above). Splits it into bash / embedded-
    Python segments — reusing the same heredoc + `python -c` splitters the highlighter
    uses — reformats each in its own language, and reassembles. Returns the input
    unchanged on any failure."""
    code = code.rstrip("\n")
    if not code:
        return code
    try:
        parts = []
        for lang, text in _split_heredocs(code):
            if lang == "python":
                parts.append(_fmt_python(text))
                continue
            for lang2, text2 in _split_python_c(text):
                parts.append(_fmt_python(text2) if lang2 == "python" else _fmt_bash(text2))
        res = "".join(parts)
        return res if res.strip() else code
    except Exception:
        return code


# Highlight + word-wrap a command. Works on the LEXER token stream so wrapping
# never has to parse ANSI: we emit our own colour per word and re-assert it after
# every wrap. `width` is the pane width; `ind` is the hanging continuation indent.
def render(code, width, ind="  "):
    # Tabs out before tokenising — same rationale as wrap_gutter: the column
    # arithmetic below counts a raw \t as 1 cell but the terminal jumps to a tab
    # stop, so tab-indented code overflowed the pane.
    code = code.rstrip("\n")
    if "\t" in code:
        code = code.expandtabs(8)
    try:
        toks = _mixed_tokens(code)
    except Exception:
        toks = [("Token.Text", code)]
    out, col, cur, indent = [], 0, None, len(ind)

    def setcol(c):
        nonlocal cur
        if c != cur:
            out.append(c); cur = c

    for ttype, val in toks:
        c = pick(ttype)
        for atom in re.findall(r"\n|[ \t]+|[^\s]+", val):
            if atom == "\n":
                out.append("\n"); col = 0; cur = None; continue
            if atom[0] in " \t":
                if col == 0:
                    out.append(atom); col += len(atom)
                elif col + len(atom) > width:
                    out.append("\n" + ind); col = indent; cur = None
                else:
                    out.append(atom); col += len(atom)
                continue
            w = atom
            ww = dwidth(w)
            if col > indent and col + ww > width:
                out.append("\n" + ind); col = indent; cur = None
            setcol(c)
            while col + ww > width and ww > width - indent:
                head, w = dsplit(w, max(1, width - col))
                if not head:                      # one char wider than the row: emit it
                    head, w = w[:1], w[1:]
                out.append(head)
                out.append("\n" + ind); col = indent; cur = None; setcol(c)
                ww = dwidth(w)
            out.append(w); col += ww
    while out and out[-1] == "\n":
        out.pop()
    out.append(RST)
    return "".join(out)
