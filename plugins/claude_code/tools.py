# plugins/claude_code/tools.py — interpreting Claude Code's OWN tool payloads.
#
# Everything here reads the shapes of Claude Code's built-in tools (Bash
# command strings, Edit/Write/MultiEdit/NotebookEdit inputs, Read results,
# structuredPatch hunks) — plugin knowledge, not core. The colour values the
# FILE_RGB verbs map to come from core.ops' semantic colour table.
import difflib, os, re, shlex
from collections import namedtuple

from core import streamfmt as SF
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


def _clean_stmt(stmt):
    """One statement with its trailing TRUNCATION pipes peeled (`… | head -40`,
    `| tail`) — they only shorten the output, so the base read still renders. A
    NON-truncation pipe (`| awk`, `| grep`) is deliberately LEFT so the per-detector
    `|` guard rejects it: that output is transformed, not the file."""
    prev = None
    while prev != stmt:                             # peel nested `| head | tail`
        prev, stmt = stmt, _TRUNC_PIPE.sub("", stmt)
    return stmt.strip()


def _effective(cmd):
    """Reduce a command to the single read that determines the mirror's rendering.

    A multi-statement command (`grep … a.py↵echo …↵sed … b.py`, or `; && ||`
    -separated) streams every statement's output in order; the LAST statement's
    file is what a single lexer is keyed on (earlier statements/banners get that
    lexer too — imperfect but chosen). And a trailing truncation pipe
    (`… | head -40`, `| tail`) only shortens that output, so it's stripped and the
    base read still colours. A NON-truncation pipe (`| awk`, `| grep`) is left in
    place so the per-detector `|` guard rejects it — that output is transformed,
    not the file. Returns the cleaned statement.

    This is the DECISION statement: whether a command collapses to a Read one-liner
    at all is judged here and nowhere else. The FILE LIST is gathered more widely
    (_match_all below) — a distinction that matters, because broadening the decision
    to "any statement is a read" would start collapsing `cat foo.py; ls`, hiding
    ls's real output behind a `Read(foo.py)` line."""
    parts = _statements(cmd)
    return _clean_stmt(parts[-1] if parts else cmd)


def _follow_cd(stmts, cwd, tilde=False):
    """Statically track the working directory across a command's LEADING
    statements, so a relative redirect target in the final statement resolves
    against the directory the command actually writes in (`cd build && make >
    log` → build/log, not ./log — resolving against the hook cwd tailed a path
    that never existed, and the mirror painted "output not found").

    Returns (cwd, known). known=False = some `cd` couldn't be resolved
    statically — dynamic target (`cd "$DIR"`, `cd -`, `~` unless `tilde`),
    subshell-scoped or backgrounded (`(cd x)`, `cd x & …`), flags, or a
    quote-mangled statement split — and the caller must then REFUSE a relative
    target (tee fallback) rather than guess: tailing a wrong-but-existing file
    replays its whole contents into the mirror as command output. A later
    ABSOLUTE `cd` restores certainty; a relative `cd` on an unknown base stays
    unknown.

    `tilde` is the ONE declared difference between this function's two callers,
    and it exists because their COST of being wrong differs. `cd ~/x` is
    perfectly deterministic (unlike `cd "$DIR"`) — but parse_redirect refuses it
    (tilde=False, pinned by test_parse_redirect_untrackable_cd_bails_to_tee)
    because a wrong-but-existing redirect target replays a whole file into the
    mirror as command output, so there the conservative bail is worth losing a
    resolvable form. The memory plane (memcmd.statement_cwds) passes tilde=True:
    a wrong path there merely fails its own isfile() check and records nothing,
    and EVERY vault read in the wild is spelled `cd ~/wiki/01 && …`, so refusing
    the form would refuse the feature. Only `~` / `~/…` expand; `~user` stays
    dynamic (expanduser leaves an unknown user untouched, which would then be
    read as a relative path)."""
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
        if tilde and (t == "~" or t.startswith("~/")):
            t = os.path.expanduser(t)   # deterministic — see the `tilde` note above
        if not t or t.startswith("-") or t.startswith("~") \
                or any(c in t for c in "$`*?["):
            known = False               # flags / `cd -` / expansion: dynamic
        elif os.path.isabs(t):
            cwd, known = t, True
        elif known:
            cwd = os.path.normpath(os.path.join(cwd, t))
    return cwd, known


"""The LIVE-FG TEE WRAPPER, and its inverse.

claude-cmd-pre.py rewrites a foreground command (via `updatedInput`) so its
output ALSO tees into a side file the tailer follows — docs/streaming.md. The
consequence is easy to forget and expensive to rediscover: from that moment on
the command string EVERY later consumer sees is the WRAPPED one. PostToolUse's
payload carries it, so a Post-side reader that wants to know what the command
DID must undo the wrapper first.

That cost real capture. The memory Bash plane (memcmd.py) analysed the payload
command directly and found 2 of 10 vault reads on a replay of the session it was
written for: `{ cd ~/wiki/01 && cat …` tokenises with `{` as the first word, so
the static cd tracking below refuses the statement and every relative path under
it goes unresolved. The two that DID record were the commands cmd_pre had
declined to rewrite.

Hence a PAIR, here, with one owner: tee_wrap builds it, unwrap_tee reverses it.
unwrap_tee is exact rather than heuristic — it matches this wrapper and nothing
else (a command that merely happens to start with `{` is returned untouched), so
it can be applied unconditionally by any reader."""

_TEE_TAIL = re.compile(
    r"\n\n\} > >\(tee -a (?P<q>\S+)\) 2> >\(tee -a (?P=q) >&2\)\s*\Z")


def tee_wrap(cmd, src):
    """Wrap `cmd` so its stdout/stderr ALSO tee into `src` (the live-tail file).
    The blank line before "}" is load-bearing: a command ENDING in a
    line-continuation backslash consumes the first newline, which used to weld the
    closing "}" onto the last line — a syntax error for a command that ran fine
    unwrapped. The extra newline gives it one to eat."""
    q = shlex.quote(src)
    return "{ " + cmd + "\n\n} > >(tee -a " + q + ") 2> >(tee -a " + q + " >&2)"


def unwrap_tee(cmd):
    """`cmd` with the live-fg tee wrapper removed — the command the model actually
    asked for. Returns `cmd` unchanged when it isn't wrapped (which includes every
    PreToolUse payload, so a caller may apply it blind)."""
    if not (cmd or "").startswith("{ "):
        return cmd
    m = _TEE_TAIL.search(cmd)
    return cmd[2:m.start()] if m else cmd


def statement_cwds(cmd, cwd, tilde=False):
    """`cmd`'s statements paired with the directory each RUNS in:
    [(statement, cwd_or_None), …] in order, None where a `cd` along the way could
    not be tracked statically (see _follow_cd — the caller must then refuse to
    resolve that statement's relative paths).

    parse_redirect only ever needed the LAST statement's cwd; a consumer that
    asks "which files does this command touch" needs every statement's, because
    a read can sit anywhere in the chain (`cd ~/wiki/01 && cat a.md && cat b.md`
    — keying off the last statement lost a.md, and off the hook cwd lost both).
    Shell-shape knowledge, so it lives here with _statements/_follow_cd rather
    than being re-derived by each consumer."""
    stmts = _statements(cmd)
    out = []
    for i, st in enumerate(stmts):
        base, known = _follow_cd(stmts[:i], cwd, tilde=tilde)
        out.append((st, base if known else None))
    return out


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

_MD_EXT = SF.FILE_MD_EXT
_PLUMBING = ("|", ";", "&&", "||", "&", ">", ">>", "&>")
# A trailing STDERR redirect (`2>/dev/null`, `2>&1`, `2>>log`). Deliberately not in
# _PLUMBING: it doesn't touch stdout, so the streamed bytes are still the file
# verbatim and such a command stays render-eligible. It only has to be recognised so
# it can't be mistaken for the trailing FILE argument of a sed/grep (see
# _match_reader). shlex keeps it as ONE token, which is why a bare `>` check misses it.
_STDERR_REDIR = re.compile(r"^\d*>{1,2}(?:&\d+|\S+)$")

# The two reader sets the registry rows below are built from. WHOLE readers emit
# the file verbatim (the file may be ANY argument); FRAGMENT readers emit matching
# /selected lines and put a SCRIPT/PATTERN argument first, so only their TRAILING
# argument can be the file. "" is the reader name _match_reader reports for a bare
# `< file` stdin redirect (no command owns it) — a read-plane set carries it to
# admit that form.
_WHOLE_READERS = frozenset({"cat", "head", "tail"})
_FRAG_READERS = frozenset({"sed", "grep", "egrep", "fgrep"})


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

    A kind declares TWO planes of readers, because a reader's output decides how
    the mirror should present it:
      * the STREAM plane (`readers`/`tailarg_readers`, consulted by `detect` —
        stream.py's `_detect_render`): the command is teed and its content
        pretty-rendered LIVE, line by line, as a `▶ foreground` block.
      * the READ plane (`read_readers`/`read_tailarg_readers`, consulted by
        `read_match` — `read_command`, which both Bash hooks gate on): the
        command is NOT streamed at all; it collapses to a click-to-expand Read
        one-liner whose whole output is rendered ONCE, buffered, in the view
        stash (docs/click-to-view.md).
    The planes are per-reader, not per-kind: `cat CLAUDE.md` streams live as a
    document, while `sed -n 120,400p CLAUDE.md` collapses to `Read(CLAUDE.md)`.

    name            render-kind tag ("md"/"json"/"yaml"/"code") — stream.py's
                    RENDER_KIND (code suffixes its lexer: "code:python"), and
                    the read plane's ReadSpec.kind (which body builder renders
                    the stash — cmd_fmt._READ_BODY).
    env             the CLAUDE_MIRROR_* gate stream.py checks (default-on). The
                    read plane honours it too: a kind whose rendering is off
                    falls back to streaming rather than collapsing.
    readers         commands whose stdout is the file verbatim when the file is
                    ANY argument (cat/head/tail — grep/rg emit fragments, not a
                    document, so they never appear here).
    tailarg_readers commands whose FILE is the TRAILING arg only (sed/grep put a
                    SCRIPT/PATTERN arg first) — so `grep 'foo.py' x.txt` can't
                    masquerade as python and a recursive `grep -r pat src/` (dir
                    last, no extension) correctly opts out. Only the code kind
                    STREAMS through these: a sed/grep of a .md/.yml emits
                    fragments too, but colouring fragments in place is fine,
                    reflowing them as a document is not — which is exactly why
                    md sends its fragment readers down the READ plane instead
                    (a buffered slice, rendered whole, like a native Read of a
                    .md with an offset).
    read_*_readers  the same two shapes for the READ plane (empty = this kind
                    never collapses to a Read one-liner). Unlike the stream
                    plane, the `< file` redirect form is admitted only when ""
                    is in `read_readers`, so a kind can take `sed x.md` without
                    also taking `< x.md` (a whole document — it streams).
    match           word -> truthy detection value (True, or the lexer name) —
                    called with each candidate word quote-stripped and lowered.
    streamer        "module:Class" of the core content streamer stream.py
                    instantiates for this kind, and streamer_takes_value says
                    whether the detection value (the lexer) is its ctor arg.
    """
    def __init__(self, name, env, readers, match, streamer,
                 tailarg_readers=(), streamer_takes_value=False,
                 read_readers=frozenset(), read_tailarg_readers=frozenset()):
        self.name, self.env, self.match = name, env, match
        self.readers, self.tailarg_readers = readers, tailarg_readers
        self.read_readers = read_readers
        self.read_tailarg_readers = read_tailarg_readers
        self.streamer, self.streamer_takes_value = streamer, streamer_takes_value

    def detect(self, cmd):
        return _detect_source(cmd, self)

    def read_match(self, cmd):
        """(detection value, files, reader) when `cmd` is a READ-plane read of this
        kind's files — else (None, (), None). Same skeleton as `detect`, the read
        reader sets, plus the reader-admission filter that makes the `< file` form
        opt-in (see read_readers above).

        `files` is a TUPLE — every file the command reads, across all its statements
        (_match_all), with the DECISION still made by the last statement alone
        (_match_reader). The one-liner names files[0] and counts the rest.

        The detection VALUE is the files' CONSENSUS, or None when they disagree:
        `cat a.py b.js` reads two languages and one block can carry one lexer, so
        rather than highlight b.js as python the body falls back to unhighlighted
        (cmd_fmt._read_body_code paints raw for a None lexer). md's value is always
        True, so it can never disagree with itself."""
        admitted = frozenset(self.read_readers) | frozenset(self.read_tailarg_readers)
        if not admitted:
            return None, (), None
        matches, reader = _match_reader(cmd, self.match, self.read_readers,
                                       self.read_tailarg_readers)
        if not matches or (reader or "") not in admitted:
            return None, (), None
        every = _match_all(cmd, self.match, self.read_readers,
                           self.read_tailarg_readers) or matches
        values = {v for v, _w in every}
        return (values.pop() if len(values) == 1 else None,
                tuple(w for _v, w in every), reader)


# Priority-ordered: stream.py picks the FIRST gated-on kind that detects. Per-kind
# quirks, preserved from the four original detectors:
#   md    — cat/head/tail all qualify (a truncated document still reflows fine).
#   json  — `cat` ONLY: JSON can only be pretty-printed whole (a partial document
#           is invalid), so head/tail would truncate it into garbage.
#   yaml  — coloured in place (not reparsed), so head/tail of a .yml is fine too.
#   code  — coloured in place like YAML; extension picks the lexer (the detection
#           value); sed/grep stream a file too, via the trailing-arg rule above.
# The READ plane (read_* sets) is where a reader collapses to a Read one-liner
# instead: EVERY code reader (a source file's contents are a file slice however
# you spell the read), and md's FRAGMENT readers only (a `sed`/`grep` of a .md is
# a slice — rendered whole in the stash; `cat`/`head`/`tail` of one is a document
# and keeps streaming live). json/yaml stay stream-only.
RENDER_KINDS = (
    RenderKind("md", "CLAUDE_MIRROR_MD", _WHOLE_READERS,
               _ext_match(_MD_EXT), "core.mdrender:MarkdownStreamer",
               read_tailarg_readers=_FRAG_READERS),
    RenderKind("json", "CLAUDE_MIRROR_JSON", frozenset({"cat"}),
               _ext_match((".json", ".jsonl", ".ndjson")),
               "core.jsonrender:JsonStreamer"),
    RenderKind("yaml", "CLAUDE_MIRROR_YAML", _WHOLE_READERS,
               _ext_match((".yml", ".yaml")), "core.yamlrender:YamlStreamer"),
    RenderKind("code", "CLAUDE_MIRROR_CODE", _WHOLE_READERS,
               _lexer_match, "core.coderender:CodeStreamer",
               tailarg_readers=_FRAG_READERS, streamer_takes_value=True,
               read_readers=_WHOLE_READERS | {""},
               read_tailarg_readers=_FRAG_READERS),
)

# What read_command returns as its first element: the winning kind's NAME plus its
# detection value (the pygments lexer for code, True for md). Compares equal to the
# plain (kind, value) tuple, so callers/tests may spell it either way.
ReadSpec = namedtuple("ReadSpec", "kind value")


def _match_stmt(stmt, match, readers, tailarg_readers):
    """The one detection skeleton — the token-matching core, over ONE already-cleaned
    statement. Returns (matches, reader): `matches` is a tuple of (detection value,
    file_word) pairs IN COMMAND ORDER, empty when this statement is not such a read;
    `reader` is the invoking command basename ('' for a bare `< file`, None when
    nothing matched).

    A read is an allowlisted reader with a matching file argument, or a bare
    `< file.ext` stdin redirect. Conservative: any pipe, output redirect, chain
    (; && ||), or command substitution disqualifies, because then the streamed bytes
    are filtered/derived, not the document itself.

    SEVERAL files is the normal case for a WHOLE reader — `cat app.py utils.py` reads
    two, and returning only the first silently lost the rest (the mirror named
    app.py, and `cat a.py b.js` highlighted b.js as python). A tailarg reader names
    exactly one by definition. Callers that need a single answer take matches[0].

    Takes the matcher + reader sets rather than a RenderKind: BOTH of a kind's
    planes run this same skeleton, each with its own sets (RenderKind.detect /
    RenderKind.read_match)."""
    try:
        toks = shlex.split(stmt, posix=False)
    except ValueError:
        return (), None
    if not toks:
        return (), None
    # Any shell plumbing means the output is no longer the file verbatim.
    if any(t in _PLUMBING for t in toks):
        return (), None
    if "$(" in stmt:
        return (), None
    def _match(word):
        w = word.strip("'\"")
        # A FLAG is never the file, however it ends. `grep -ril pat ~/wiki/01/
        # --include=*.md` matched `--include=*.md` as a markdown file and the
        # mirror painted the whole recursive search as `Read(--include=*.md)` —
        # a one-liner naming a file that does not exist, hiding a search behind
        # a read. Checked here (not per-kind) because it is true of every kind's
        # matcher: an argument starting with `-` is option syntax.
        if w.startswith("-"):
            return None
        return match(w.lower())
    # `< file.ext` (with or without a leading command)
    if "<" in toks:
        i = toks.index("<")
        if i + 1 < len(toks):
            v = _match(toks[i + 1])
            if v:
                reader = "" if toks[0] == "<" else os.path.basename(toks[0].strip("'\""))
                return ((v, toks[i + 1].strip("'\"")),), reader
    head = os.path.basename(toks[0].strip("'\""))
    if head in readers:
        # EVERY matching argument: a whole reader emits each file's contents in
        # turn, so each one was genuinely read.
        found = tuple((v, w.strip("'\"")) for v, w in
                      ((_match(w), w) for w in toks[1:]) if v)
        return (found, head) if found else ((), None)
    if head in tailarg_readers and len(toks) > 1:
        # The FILE is the trailing ARGUMENT — and a redirect is not an argument,
        # it is shell syntax, so it cannot be allowed to occupy that slot.
        # `sed -n 1,80p note.md 2>/dev/null` (the idiom for reading a file that
        # may not exist) silently rendered as a streamed block instead of a
        # `Read(note.md)` one-liner, because `2>/dev/null` was the last token.
        # Stripping them does NOT weaken the anti-masquerade guard the tailarg
        # rule exists for: `grep 'foo.py' x.txt 2>/dev/null` still resolves to
        # x.txt, never to the PATTERN. Only stderr forms are stripped — a stdout
        # redirect is already refused outright by the _PLUMBING check above,
        # since then the output isn't reaching the pane at all.
        args = list(toks)
        while len(args) > 1 and _STDERR_REDIR.match(args[-1]):
            args.pop()
        if len(args) > 1:
            w = args[-1]
            v = _match(w)
            if v:
                return ((v, w.strip("'\"")),), head
    return (), None


def _match_reader(cmd, match, readers, tailarg_readers):
    """_match_stmt over the command's DECISION statement (`_effective`) — a trailing
    `| head`/`| tail` still renders, and a multi-statement block keys off its LAST
    statement. Returns _match_stmt's (matches, reader)."""
    return _match_stmt(_effective(cmd), match, readers, tailarg_readers)


def _match_all(cmd, match, readers, tailarg_readers):
    """Every file `cmd` reads with these reader sets, across ALL its statements —
    deduped, in command order. `sed -n 1,20p a.md; sed -n 1,20p b.md` reads two
    notes and the decision statement names only b.md.

    Deliberately does NOT widen the collapse DECISION (see _effective): this is
    called only after the decision statement has already matched, purely to complete
    the file list. So `cat foo.py; ls` still streams (its last statement is not a
    read) — collapsing it would hide ls's output behind a `Read(foo.py)` line."""
    out = []
    for stmt in _statements(cmd):
        matches, _reader = _match_stmt(_clean_stmt(stmt), match, readers,
                                       tailarg_readers)
        for v, w in matches:
            if not any(w == prev for _pv, prev in out):
                out.append((v, w))
    return tuple(out)


def _detect_source(cmd, kind):
    """kind.match's truthy value when `cmd` streams a matching file's raw contents,
    else None — the render-kind detector stream.py's _detect_render iterates. Thin
    over _match_reader (which additionally names the matched files + reader; only
    the Read-one-liner plane, RenderKind.read_match, needs those). The FIRST match's
    value: a live content stream renders one way for the whole command, and its
    first file is what picked that way before several could be named."""
    matches, _reader = _match_reader(cmd, kind.match, kind.readers,
                                     kind.tailarg_readers)
    return matches[0][0] if matches else None


def is_md(path):
    """True when `path`'s extension is a markdown one (the same set the streaming
    md_source() reader-allowlist uses). Lets the file-op click-to-view blocks
    pretty-render a .md Read/Write instead of plain-text/lexer highlighting."""
    return SF.file_is_md(path)


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


def read_command(cmd):
    """(ReadSpec, files, reader) when `cmd` should render as a collapsed Read
    one-liner instead of a streamed foreground block — a file-READING command: a
    sed/grep/cat/head/tail (or a bare `< file`) of a source file the mirror can
    syntax-highlight, or a sed/grep SLICE of a markdown file (the READ plane of
    RENDER_KINDS, in the registry's priority order) — else (None, (), None).
    `reader` is the invoking command basename (the dim tag on the Read one-liner);
    the ReadSpec names WHICH render kind matched and carries its detection value
    (the lexer, for code), which is what lets the expansion pick a renderer per
    kind (cmd_fmt._READ_BODY) instead of assuming a lexer.

    `files` is EVERY file the command reads, in command order — one block, but it
    names all of them (RenderKind.read_match). What can never be recovered is which
    part of the OUTPUT belongs to which file: `cat a.py b.py` emits one undelimited
    stream, so the expansion shows the whole thing under a header naming both. The
    rejected alternatives were rewriting the command to interleave delimiters (the
    tee wrapper is ADDITIVE; that would change the command's semantics — exit codes,
    quoting, stderr ordering) and re-reading each file from disk (it lies exactly
    when it matters: `head -20` and `sed -n 1,80p` truncate, and the file may have
    changed since). docs/mirror-pane.md.

    Gated by CLAUDE_MIRROR_CMD_READ (default on; '0' falls back to live
    streaming), and per kind by that kind's own CLAUDE_MIRROR_* gate — with its
    rendering off there is nothing to collapse INTO, so the command streams.

    The SINGLE owner of the decision both Bash hooks consult — claude-cmd-pre.py
    skips live streaming and claude-cmd-fmt.py renders the Read one-liner for
    exactly the same commands, so they can never disagree (a mismatch would strand
    a streamed header with no body, or double-render)."""
    if os.environ.get("CLAUDE_MIRROR_CMD_READ", "1") == "0":
        return None, (), None
    for kind in RENDER_KINDS:
        if os.environ.get(kind.env, "1") == "0":
            continue
        v, files, reader = kind.read_match(cmd)
        if files:
            # `v` may be None (the files' lexers disagree) — `files` is the match,
            # not the value, so the truth test is on the files.
            return ReadSpec(kind.name, v), files, reader
    return None, (), None


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

    sp = resp.get("structuredPatch") if isinstance(resp, dict) else None
    if isinstance(sp, list) and sp and all(
            isinstance(h, dict) and isinstance(h.get("lines"), list) for h in sp):
        return SF.file_diff_rows([
            (int(h.get("oldStart") or 1), int(h.get("newStart") or 1),
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
        return SF.file_diff_rows(uni(inp.get("old_string"), inp.get("new_string")))
    if tool_name == "MultiEdit":
        hunks = []
        for e in inp.get("edits") or []:
            if isinstance(e, dict):
                hunks.extend(uni(e.get("old_string"), e.get("new_string")))
        return SF.file_diff_rows(hunks)
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
