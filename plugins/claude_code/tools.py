# plugins/claude_code/tools.py — interpreting Claude Code's OWN tool payloads.
#
# Everything here reads the shapes of Claude Code's built-in tools (Bash
# command strings, Edit/Write/MultiEdit/NotebookEdit inputs, Read results,
# structuredPatch hunks) — plugin knowledge, not core. The colour values the
# FILE_RGB verbs map to come from core.ops' semantic colour table.
import difflib, os, re, shlex

from core.ops import BLUE, GREEN, YELLOW


_STMT_SEP = re.compile(r"\n|;|&&|\|\|")             # shell statement separators
_TRUNC_PIPE = re.compile(r"\|\s*(?:head|tail)\b[^|]*$")   # a trailing | head / | tail
# Shell line-continuation: a line ending in a pipe / && / || / backslash continues
# onto the next line — that newline is NOT a statement break. Join it first so a
# pipeline split across lines (`grep … x.py |↵head`) isn't mis-cut at the newline.
_CONT_OP = re.compile(r"(\|\||&&|\|)[ \t]*\n[ \t]*")
_CONT_BSLASH = re.compile(r"\\[ \t]*\n[ \t]*")


def _statements(cmd):
    """Split a command into its ordered shell statements: join line
    continuations first (`foo \\↵bar`, `… |↵head`), then cut at `_STMT_SEP`.
    The regex split is quote-blind — a separator INSIDE a quoted string
    mis-cuts, so callers must treat any statement that fails to tokenise as
    untrustworthy (both do: they bail to their safe fallback)."""
    cmd = _CONT_BSLASH.sub(" ", cmd)                # `foo \↵bar` -> `foo bar`
    cmd = _CONT_OP.sub(r"\1 ", cmd)                 # `… |↵head` -> `… | head`
    return [p for p in _STMT_SEP.split(cmd) if p.strip()]


def _effective(cmd):
    """Reduce a command to the single read that determines the mirror's rendering.

    A multi-statement command (`grep … a.py↵echo …↵sed … b.py`, or `; && ||`
    -separated) streams every statement's output in order; the LAST statement's
    file is what a single lexer is keyed on (earlier statements/banners get that
    lexer too — imperfect but chosen). And a trailing truncation pipe
    (`… | head -40`, `| tail`) only shortens that output, so it's stripped and the
    base read still colours. A NON-truncation pipe (`| awk`, `| grep`) is left in
    place so the per-detector `|` guard rejects it — that output is transformed,
    not the file. Returns the cleaned statement."""
    parts = _statements(cmd)
    stmt = parts[-1] if parts else cmd
    prev = None
    while prev != stmt:                             # peel nested `| head | tail`
        prev, stmt = stmt, _TRUNC_PIPE.sub("", stmt)
    return stmt.strip()


def _follow_cd(stmts, cwd):
    """Statically track the working directory across a command's LEADING
    statements, so a relative redirect target in the final statement resolves
    against the directory the command actually writes in (`cd build && make >
    log` → build/log, not ./log — resolving against the hook cwd tailed a path
    that never existed, and the mirror painted "output not found").

    Returns (cwd, known). known=False = some `cd` couldn't be resolved
    statically — dynamic target (`cd "$DIR"`, `cd -`, `~`), subshell-scoped or
    backgrounded (`(cd x)`, `cd x & …`), flags, or a quote-mangled statement
    split — and the caller must then REFUSE a relative target (tee fallback)
    rather than guess: tailing a wrong-but-existing file replays its whole
    contents into the mirror as command output. A later ABSOLUTE `cd` restores
    certainty; a relative `cd` on an unknown base stays unknown."""
    known = True
    for st in stmts:
        try:
            toks = shlex.split(st, posix=False)
        except ValueError:
            known = False               # quotes span a separator: mis-split
            continue
        # `(cd` / `cd)` glue parens onto the token (posix=False), so a bare
        # `in` check missed a subshell's cd entirely and the stale base won
        if not any(t.strip("()") == "cd" for t in toks):
            continue
        # only a plain top-level `cd [target]` is trusted; any other mention
        # (subshell, backgrounded, flags, mid-statement) poisons the tracking
        if toks[0] != "cd" or len(toks) > 2 \
                or any("(" in t or ")" in t for t in toks):
            known = False
            continue
        if len(toks) == 1:              # bare `cd` = $HOME
            cwd, known = os.path.expanduser("~"), True
            continue
        t = toks[1]
        if len(t) >= 2 and t[0] in ("'", '"') and t[-1] == t[0]:
            t = t[1:-1]                 # quotes are shell syntax, not the name
        if not t or t.startswith("-") or t.startswith("~") \
                or any(c in t for c in "$`*?["):
            known = False               # flags / `cd -` / expansion: dynamic
        elif os.path.isabs(t):
            cwd, known = t, True
        elif known:
            cwd = os.path.normpath(os.path.join(cwd, t))
    return cwd, known


def parse_redirect(cmd, cwd):
    """If `cmd`'s FINAL statement sends stdout to a file (… > file / &> file /
    1>> file), return (absolute_target, append) — else None. Used by BOTH Bash
    hooks: claude-cmd-pre tails the redirect target instead of tee-ing a second
    copy, and claude-cmd-fmt points the background tailer at it (the task's own
    output file stays empty when the bytes go to the redirect). Conservative:
    only stdout (or &>) redirects, skip /dev/* and fd-dup targets (&1), give up
    on anything we can't tokenise. Last redirect in the statement wins.

    STATEMENT-SCOPED (2026-07-16): a redirect is the command's effective output
    sink only when it belongs to the LAST statement. Last-redirect-wins across
    the whole command latched onto a mid-command bookkeeping file (`… >>
    summary.txt ) & done↵wait↵sort summary.txt`) while the visible output went
    to stdout — the tee is the correct mode there, and it captures everything.
    A RELATIVE target resolves against the statically tracked cwd (`_follow_cd`
    above): `cd build && make > log` tails build/log; an untrackable `cd`
    refuses the relative target (None → tee) rather than guess.

    Tokenised with posix=False so QUOTES SURVIVE: posix mode stripped them, which
    made `grep '>' file` indistinguishable from `grep > file` — the fg tailer then
    streamed the whole existing file into the mirror as "command output". A token
    starting with a quote is a literal argument, never a redirect. Heredocs bail
    entirely (their BODY lines tokenise like real redirects and even a
    final-statement scope can be fooled by a body line), as do `>|` clobbers and
    `>(…)` process substitution — None just means the caller falls back to its
    own tee side file, which is always safe."""
    try:
        toks = shlex.split(cmd, posix=False)
    except ValueError:
        return None
    if any(t.startswith("<<") for t in toks):
        return None
    stmts = _statements(cmd)
    if not stmts:
        return None
    try:
        toks = shlex.split(stmts[-1], posix=False)
    except ValueError:
        return None                     # quotes span a separator: mis-split
    target, append, i = None, False, 0
    while i < len(toks):
        t = toks[i]
        if t[:1] in ("'", '"'):
            i += 1
            continue                    # quoted word: a literal arg, not a redirect
        if ">" in t and not t.startswith("2"):
            m = re.match(r"^(?:&|1)?(>>?)(.*)$", t)
            if m:
                rest = m.group(2)
                if rest.startswith("|") or rest.startswith("("):
                    return None         # >| clobber / >(process substitution)
                if rest:
                    target, append = rest, m.group(1) == ">>"
                elif i + 1 < len(toks):
                    nxt = toks[i + 1]
                    if ">" in nxt or nxt.startswith("("):
                        return None     # `> >(tee …)` and friends
                    target, append = nxt, m.group(1) == ">>"
                    i += 1
        i += 1
    if not target or target.startswith("&") or target.startswith("/dev/"):
        return None
    # A quoted target is unwrapped before the metachar guard below (the quotes are
    # shell syntax, not part of the filename).
    if len(target) >= 2 and target[0] in ("'", '"') and target[-1] == target[0]:
        target = target[1:-1]
        if not target:
            return None
    # shlex does NO shell expansion: a target holding $vars, backticks, globs, or a
    # leading ~ is not the path the shell will actually write to (`> "$OUT"` would
    # have us tail a literal file named $OUT). Fall back to the caller's side file.
    if any(c in target for c in "$`*?[") or target.startswith("~"):
        return None
    if not os.path.isabs(target):
        base, known = _follow_cd(stmts[:-1], cwd or os.getcwd())
        if not known:
            return None                 # effective cwd unknowable: tee fallback
        target = os.path.join(base, target)
    return target, append


# ---- content-render detection (the RENDER_KINDS registry) --------------------
#
# "Does this fg command stream a file's raw contents the mirror can pretty-render?"
# Every render kind shares one skeleton — reduce to the `_effective` read, tokenise,
# reject shell plumbing / command substitution, accept a bare `< file.ext` stdin
# redirect, then an allowlisted reader with a matching file argument — and differs
# only in its reader set, extension set, and small per-kind quirks. Those live as
# fields of a RenderKind entry below; `_detect_source` is the one skeleton. Adding
# a render mode is one new entry in RENDER_KINDS (stream.py iterates it).
#
# Readers are the plain-text ones whose stdout is the file verbatim. Deliberately
# EXCLUDED everywhere: bat/glow/mdcat/less/more (they already style their output —
# re-rendering would double-format) and jq/yq (pretty-print + colour themselves).

_MD_EXT = (".md", ".markdown", ".mdown", ".mkd")
_PLUMBING = ("|", ";", "&&", "||", "&", ">", ">>", "&>")


def _ext_match(exts):
    """word-matcher: True when the (quote-stripped, lowered) word ends in `exts`."""
    return lambda w: w.endswith(exts) or None


def _lexer_match(w):
    """word-matcher for the code kind: the pygments lexer name keyed by the word's
    extension (core.coderender.LANGS), or None. The truthy VALUE is the detection
    result — code_source returns the lexer, not a bare True."""
    from core.coderender import LANGS
    for ext, lexer in LANGS.items():
        if w.endswith(ext):
            return lexer
    return None


class RenderKind:
    """One row of the RENDER_KINDS registry.

    name            render-kind tag ("md"/"json"/"yaml"/"code") — stream.py's
                    RENDER_KIND (code suffixes its lexer: "code:python").
    env             the CLAUDE_MIRROR_* gate stream.py checks (default-on).
    readers         commands whose stdout is the file verbatim when the file is
                    ANY argument (cat/head/tail — grep/rg emit fragments, not a
                    document, so they never appear here).
    tailarg_readers commands whose FILE is the TRAILING arg only (sed/grep put a
                    SCRIPT/PATTERN arg first) — so `grep 'foo.py' x.txt` can't
                    masquerade as python and a recursive `grep -r pat src/` (dir
                    last, no extension) correctly opts out. Only the code kind
                    uses this: a sed/grep of a .md/.yml emits fragments too, but
                    colouring fragments in place is fine, reflowing them as a
                    document is not.
    match           word -> truthy detection value (True, or the lexer name) —
                    called with each candidate word quote-stripped and lowered.
    streamer        "module:Class" of the core content streamer stream.py
                    instantiates for this kind, and streamer_takes_value says
                    whether the detection value (the lexer) is its ctor arg.
    """
    def __init__(self, name, env, readers, match, streamer,
                 tailarg_readers=(), streamer_takes_value=False):
        self.name, self.env, self.match = name, env, match
        self.readers, self.tailarg_readers = readers, tailarg_readers
        self.streamer, self.streamer_takes_value = streamer, streamer_takes_value

    def detect(self, cmd):
        return _detect_source(cmd, self)


# Priority-ordered: stream.py picks the FIRST gated-on kind that detects. Per-kind
# quirks, preserved from the four original detectors:
#   md    — cat/head/tail all qualify (a truncated document still reflows fine).
#   json  — `cat` ONLY: JSON can only be pretty-printed whole (a partial document
#           is invalid), so head/tail would truncate it into garbage.
#   yaml  — coloured in place (not reparsed), so head/tail of a .yml is fine too.
#   code  — coloured in place like YAML; extension picks the lexer (the detection
#           value); sed/grep stream a file too, via the trailing-arg rule above.
RENDER_KINDS = (
    RenderKind("md", "CLAUDE_MIRROR_MD", frozenset({"cat", "head", "tail"}),
               _ext_match(_MD_EXT), "core.mdrender:MarkdownStreamer"),
    RenderKind("json", "CLAUDE_MIRROR_JSON", frozenset({"cat"}),
               _ext_match((".json", ".jsonl", ".ndjson")),
               "core.jsonrender:JsonStreamer"),
    RenderKind("yaml", "CLAUDE_MIRROR_YAML", frozenset({"cat", "head", "tail"}),
               _ext_match((".yml", ".yaml")), "core.yamlrender:YamlStreamer"),
    RenderKind("code", "CLAUDE_MIRROR_CODE", frozenset({"cat", "head", "tail"}),
               _lexer_match, "core.coderender:CodeStreamer",
               tailarg_readers=frozenset({"sed", "grep", "egrep", "fgrep"}),
               streamer_takes_value=True),
)


def _match_reader(cmd, kind):
    """The one detection skeleton — the token-matching core, additionally naming
    WHICH word matched and the reader command that owns it. If `cmd` is a single
    simple command whose body streams a matching file's raw contents — an
    allowlisted reader with a matching file argument, or a bare `< file.ext` stdin
    redirect — return (kind.match's truthy value, file_word, reader); else
    (None, None, None). `reader` is the invoking command basename ('' for a bare
    `< file`). Conservative: any pipe, output redirect, chain (; && ||), or command
    substitution disqualifies, because then the streamed bytes are filtered/
    derived, not the document itself. Runs on the command's `_effective` read, so a
    trailing `| head`/`| tail` (truncation) still renders and a multi-statement
    block keys off its LAST statement's file."""
    cmd = _effective(cmd)
    try:
        toks = shlex.split(cmd, posix=False)
    except ValueError:
        return None, None, None
    if not toks:
        return None, None, None
    # Any shell plumbing means the output is no longer the file verbatim.
    if any(t in _PLUMBING for t in toks):
        return None, None, None
    if "$(" in cmd:
        return None, None, None
    def _match(word):
        return kind.match(word.strip("'\"").lower())
    # `< file.ext` (with or without a leading command)
    if "<" in toks:
        i = toks.index("<")
        if i + 1 < len(toks):
            v = _match(toks[i + 1])
            if v:
                reader = "" if toks[0] == "<" else os.path.basename(toks[0].strip("'\""))
                return v, toks[i + 1].strip("'\""), reader
    head = os.path.basename(toks[0].strip("'\""))
    if head in kind.readers:
        for w in toks[1:]:
            v = _match(w)
            if v:
                return v, w.strip("'\""), head
        return None, None, None
    if head in kind.tailarg_readers and len(toks) > 1:
        w = toks[-1]                        # the FILE is the trailing arg
        v = _match(w)
        if v:
            return v, w.strip("'\""), head
    return None, None, None


def _detect_source(cmd, kind):
    """kind.match's truthy value when `cmd` streams a matching file's raw contents,
    else None — the render-kind detector stream.py's _detect_render iterates. Thin
    over _match_reader (which additionally names the matched file + reader; only the
    Read-one-liner path, code_read_target, needs those)."""
    return _match_reader(cmd, kind)[0]


def is_md(path):
    """True when `path`'s extension is a markdown one (the same set the streaming
    md_source() reader-allowlist uses). Lets the file-op click-to-view blocks
    pretty-render a .md Read/Write instead of plain-text/lexer highlighting."""
    return (path or "").lower().endswith(_MD_EXT)


# Thin per-kind wrappers over the registry (the historical public names).
_BY_NAME = {k.name: k for k in RENDER_KINDS}


def md_source(cmd):
    """True when `cmd` streams a markdown file's raw contents (see _detect_source)."""
    return bool(_BY_NAME["md"].detect(cmd))


def json_source(cmd):
    """True when `cmd` streams a whole .json file's raw contents — `cat file.json`
    or a bare `< file.json` (head/tail would truncate; see _detect_source)."""
    return bool(_BY_NAME["json"].detect(cmd))


def yaml_source(cmd):
    """True when `cmd` streams a .yml/.yaml file's raw contents (see _detect_source)."""
    return bool(_BY_NAME["yaml"].detect(cmd))


def code_source(cmd):
    """If `cmd` streams a source file the mirror can syntax-highlight, return the
    pygments LEXER NAME (e.g. 'python'); else None (see _detect_source)."""
    return _BY_NAME["code"].detect(cmd)


def code_read_target(cmd):
    """(lexer, file_path, reader) for a command whose stdout is a source file the
    mirror can syntax-highlight — a sed/grep/cat/head/tail of a .py/.kt/.java/…
    file (the `code` render kind; see code_source / _match_reader) — else
    (None, None, None). `reader` is the invoking command basename (the dim tag on
    the Read one-liner). The seam behind rendering such a command as a collapsed
    Read op instead of a streamed foreground block."""
    return _match_reader(cmd, _BY_NAME["code"])


def read_command(cmd):
    """(lexer, file_path, reader) when `cmd` should render as a collapsed Read
    one-liner instead of a streamed foreground block (a code-reading command —
    code_read_target), else (None, None, None). Gated by CLAUDE_MIRROR_CMD_READ
    (default on; '0' falls back to live streaming). The SINGLE owner of the
    decision both Bash hooks consult — claude-cmd-pre.py skips live streaming and
    claude-cmd-fmt.py renders the Read one-liner for exactly the same commands, so
    they can never disagree (a mismatch would strand a streamed header with no
    body, or double-render)."""
    if os.environ.get("CLAUDE_MIRROR_CMD_READ", "1") == "0":
        return None, None, None
    return code_read_target(cmd)


def diff_counts(tool_name, inp):
    """(added, removed) line counts for a file-mutating tool's input, matching Claude
    Code's own additions/removals: a real line-level diff for Edit/MultiEdit, the whole
    body for Write, the edited cell for NotebookEdit. (0, 0) for Read or when nothing is
    determinable — callers show a suffix only when there's a non-zero delta."""
    inp = inp or {}

    def delta(old, new):
        a, b = (old or "").splitlines(), (new or "").splitlines()
        add = rem = 0
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
            if tag in ("replace", "delete"):
                rem += i2 - i1
            if tag in ("replace", "insert"):
                add += j2 - j1
        return add, rem

    if tool_name == "Edit":
        return delta(inp.get("old_string"), inp.get("new_string"))
    if tool_name == "MultiEdit":
        add = rem = 0
        for e in inp.get("edits") or []:
            if isinstance(e, dict):
                da, dr = delta(e.get("old_string"), e.get("new_string"))
                add += da; rem += dr
        return add, rem
    if tool_name == "Write":
        return len((inp.get("content") or "").splitlines()), 0
    if tool_name == "NotebookEdit":
        n = len((inp.get("new_source") or "").splitlines())
        return (0, n) if inp.get("edit_mode") == "delete" else (n, 0)
    return 0, 0


def diff_rows(tool_name, inp, resp):
    """Diff rows for a mutation, Claude-Code-UI style: a list of (sign, lineno,
    text) tuples — sign ' ' (context, numbered in the NEW file), '-' (removal,
    numbered in the OLD file), '+' (addition, numbered in the new file), or '@'
    (a separator row between non-adjacent hunks; lineno None). The raw material
    for file_fmt's click-to-view diff block. Prefers the result's
    structuredPatch (real file line numbers, context included, exactly what
    Claude Code itself computed); falls back to a difflib unified diff over the
    input's old/new strings when the patch is absent (then numbers are
    snippet-relative). NotebookEdit has no old text in the payload, so its cell
    shows as all-additions (or all-removals for a delete), unnumbered. [] when
    nothing is determinable."""
    inp = inp or {}

    def walk(hunks):
        """hunks: [(old_start, new_start, [signed lines])] -> numbered rows."""
        rows = []
        for hi, (old, new, lines) in enumerate(hunks):
            if hi:
                rows.append(("@", None, "⋮"))
            for l in lines:
                sign, body = (l[:1] or " "), l[1:]
                if sign == "\\":
                    # The diff library's literal "\ No newline at end of file"
                    # marker — metadata, not a file line. Numbering it as
                    # context shifted every later lineno in the hunk by one.
                    # Skipped outright (like `git diff --stat`-style summaries;
                    # the mirror's diff shows content, not byte-level trivia).
                    continue
                if sign == "+":
                    rows.append(("+", new, body)); new += 1
                elif sign == "-":
                    rows.append(("-", old, body)); old += 1
                else:
                    rows.append((" ", new, body)); old += 1; new += 1
        return rows

    sp = resp.get("structuredPatch") if isinstance(resp, dict) else None
    if isinstance(sp, list) and sp and all(
            isinstance(h, dict) and isinstance(h.get("lines"), list) for h in sp):
        return walk([(int(h.get("oldStart") or 1), int(h.get("newStart") or 1),
                      [str(l) for l in h["lines"]]) for h in sp])

    def uni(old, new):
        hunks = []
        for l in difflib.unified_diff((old or "").splitlines(),
                                      (new or "").splitlines(), n=3, lineterm=""):
            m = re.match(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", l)
            if m:
                hunks.append((int(m.group(1)), int(m.group(2)), []))
            elif hunks and not l.startswith(("---", "+++")):
                hunks[-1][2].append(l)
        return hunks

    if tool_name == "Edit":
        return walk(uni(inp.get("old_string"), inp.get("new_string")))
    if tool_name == "MultiEdit":
        hunks = []
        for e in inp.get("edits") or []:
            if isinstance(e, dict):
                hunks.extend(uni(e.get("old_string"), e.get("new_string")))
        return walk(hunks)
    if tool_name == "NotebookEdit":
        sign = "-" if inp.get("edit_mode") == "delete" else "+"
        return [(sign, None, l)
                for l in (inp.get("new_source") or "").splitlines()]
    return []


def read_extent(file_info, inp=None):
    """Compact 'start-end/total' describing how much of a file a Read actually returned,
    or '' when it read the WHOLE file (or the extent can't be determined) — so a plain
    Read(name) means the entire file and any range is a signal that it did NOT.

    file_info is the result's file dict (Claude Code records startLine / numLines /
    totalLines on the Read result); inp is the tool input, a fallback (offset/limit) for
    when the result isn't in hand yet. Note a bare Read caps at 2000 lines, so a big file
    shows e.g. '1-2000/5000' — partial even though nothing was passed."""
    if isinstance(file_info, dict) and file_info.get("numLines") is not None:
        start = int(file_info.get("startLine") or 1)
        total = int(file_info.get("totalLines") or 0)
        end = start + int(file_info.get("numLines") or 0) - 1
        if start <= 1 and (total == 0 or end >= total):
            return ""                          # read the whole file
        return f"{start}-{end}/{total}" if total else f"{start}-{end}"
    inp = inp or {}
    off, lim = inp.get("offset"), inp.get("limit")
    if off or lim:
        s = int(off or 1)
        return f"{s}-{s + int(lim) - 1}" if lim else f"{s}+"
    return ""


def edit_range(structured_patch):
    """Compact line range(s) a mutation touched, from the result's structuredPatch hunks
    (each carries newStart / newLines, the affected span in the resulting file) — e.g.
    '445-462' or '445-462,501-503'. '' when there's no patch (a brand-new Write, whose
    +N count already conveys its size) or it can't be read. Caps at 3 shown ranges,
    appending '+k' for the rest, so a big MultiEdit stays short."""
    if not isinstance(structured_patch, list) or not structured_patch:
        return ""
    parts = []
    for h in structured_patch:
        if not isinstance(h, dict) or h.get("newStart") is None:
            continue
        start = int(h.get("newStart"))
        end = start + max(int(h.get("newLines") or 0), 1) - 1
        parts.append(str(start) if end <= start else f"{start}-{end}")
    if not parts:
        return ""
    if len(parts) > 3:
        return ",".join(parts[:3]) + f",+{len(parts) - 3}"
    return ",".join(parts)


# File-op verbs + colours, shared by claude-file-fmt.py (main session) and
# claude-substream.py (agents) — verbs mirror Claude Code's own UI.
FILE_LABEL = {"Read": "Read", "Edit": "Update", "MultiEdit": "Update",
              "Write": "Write", "NotebookEdit": "Update"}
FILE_RGB   = {"Read": BLUE, "Update": YELLOW, "Write": GREEN}


