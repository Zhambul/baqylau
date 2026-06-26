#!/usr/bin/env python3
# claude_render.py — shared rendering primitives for the kitty command mirror.
#
# Extracted from claude-cmd-fmt.py so the same bash/python syntax highlighting,
# ANSI-aware gutter wrapping, escape-unescaping, and chip labels can be reused by
# claude-substream.py (which renders a subagent's transcript). Everything here is
# width-parameterised (no module-level WIDTH) so it imports cleanly.
import re


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


def wrap_gutter(text, width, gut, gw):
    cw = max(1, width - gw)                       # visible columns after the gutter
    pieces, lines = [], text.split("\n")
    for li, line in enumerate(lines):
        if li:
            pieces.append("\n")
        pieces.append(gut)
        col, active, i, n = 0, "", 0, len(line)
        while i < n:
            m = _ANSI.match(line, i)
            if m:
                seq = m.group(0)
                pieces.append(seq)
                if seq.endswith("m"):
                    active = "" if seq in ("\x1b[0m", "\x1b[m") else active + seq
                i = m.end()
                continue
            if col >= cw:
                pieces.append(RST + "\n" + gut + active)
                col = 0
            pieces.append(line[i]); col += 1; i += 1
        pieces.append(RST)
    return "".join(pieces)


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


# Highlight + word-wrap a command. Works on the LEXER token stream so wrapping
# never has to parse ANSI: we emit our own colour per word and re-assert it after
# every wrap. `width` is the pane width; `ind` is the hanging continuation indent.
def render(code, width, ind="  "):
    code = code.rstrip("\n")
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
            if col > indent and col + len(w) > width:
                out.append("\n" + ind); col = indent; cur = None
            setcol(c)
            while col + len(w) > width and len(w) > width - indent:
                take = max(1, width - col)
                out.append(w[:take]); w = w[take:]
                out.append("\n" + ind); col = indent; cur = None; setcol(c)
            out.append(w); col += len(w)
    while out and out[-1] == "\n":
        out.pop()
    out.append(RST)
    return "".join(out)
