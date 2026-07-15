# core/codefmt.py — the bash/python source tokenizer + pretty-printer.
#
# Extracted from core/render.py (which keeps the rendering PRIMITIVES: width
# math, palette/SGR, ANSI strip/wrap, the security-critical neutralize(), inline
# markdown). This module owns the command-display pipeline: splitting a bash
# command into bash / embedded-Python segments (heredocs, `python -c`), marking
# command words for highlighting, reflowing dense one-liners (`format_code`),
# and the highlight-and-wrap `render()` the mirror paints `code` ops with.
# Imports render one-directionally for the shared palette (`pick`, RST) and
# width primitives (`dwidth`, `dsplit`) — no cycle.
import re

from core.render import dsplit, dwidth, pick, RST
from core.render import lexer as R_lexer   # pygments lexer singletons (one owner)


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
    segs = []
    for lang, text in _split_heredocs(code):
        segs.extend(_split_python_c(text) if lang == "bash" else [(lang, text)])
    toks = []
    for lang, text in segs:
        # render.lexer: the module-level singleton cache — lexer construction
        # compiles token tables and used to run per call, per language.
        raw = [(tt, val) for _, tt, val in R_lexer(lang).get_tokens_unprocessed(text)]
        toks.extend(_mark_bash_commands(raw) if lang == "bash" else raw)
    return toks


# --- pretty-printing (width-INDEPENDENT, done once at op creation) -------------
# A dense one-liner is hard to read in the mirror. Before highlighting, reflow the
# command into multi-line form: reformat embedded Python with `ast`, and break dense
# bash at its top-level control operators. This is width-independent (real newlines,
# which the renderer then wraps further if needed), so it belongs at op creation — see
# core.ops.code(). Best-effort throughout: any doubt → return the text unchanged.


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
        toks = [(str(tt), v) for _, tt, v in R_lexer("bash").get_tokens_unprocessed(text)]
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
    # Tabs out before tokenising — same rationale as render.wrap_gutter: the column
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
