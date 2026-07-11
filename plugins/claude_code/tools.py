# plugins/claude_code/tools.py — interpreting Claude Code's OWN tool payloads.
#
# Everything here reads the shapes of Claude Code's built-in tools (Bash
# command strings, Edit/Write/MultiEdit/NotebookEdit inputs, Read results,
# structuredPatch hunks) — plugin knowledge, not core. The colour values the
# FILE_RGB verbs map to come from core.ops' semantic colour table.
import difflib, os, re, shlex

from core.ops import BLUE, GREEN, YELLOW


def parse_redirect(cmd, cwd):
    """If `cmd` sends stdout to a file (… > file / &> file / 1>> file), return
    (absolute_target, append) — else None. Used by BOTH Bash hooks: claude-cmd-pre
    tails the redirect target instead of tee-ing a second copy, and claude-cmd-fmt
    points the background tailer at it (the task's own output file stays empty
    when the bytes go to the redirect). Conservative: only stdout (or &>)
    redirects, skip /dev/* and fd-dup targets (&1), give up on anything we can't
    tokenise. Last redirect wins (the effective stdout sink).

    Tokenised with posix=False so QUOTES SURVIVE: posix mode stripped them, which
    made `grep '>' file` indistinguishable from `grep > file` — the fg tailer then
    streamed the whole existing file into the mirror as "command output". A token
    starting with a quote is a literal argument, never a redirect. Heredocs bail
    entirely (their BODY lines tokenise like real redirects and last-wins picked
    those), as do `>|` clobbers and `>(…)` process substitution — None just means
    the caller falls back to its own tee side file, which is always safe."""
    try:
        toks = shlex.split(cmd, posix=False)
    except ValueError:
        return None
    if any(t.startswith("<<") for t in toks):
        return None
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
        target = os.path.join(cwd or os.getcwd(), target)
    return target, append


# Plain-text readers whose stdout is a markdown file verbatim — the mirror can
# pretty-render it. Deliberately EXCLUDES bat/glow/mdcat/less/more (they already
# style their output — re-rendering would double-format) and grep/rg/head-of-a-
# match (they emit fragments, not a document).
_MD_READERS = {"cat", "head", "tail"}
_MD_EXT = (".md", ".markdown", ".mdown", ".mkd")


def md_source(cmd):
    """True when `cmd` is a single simple command whose body streams a markdown
    file's raw contents — an allowlisted reader (cat/head/tail) with a .md/.markdown
    argument, or a bare `< file.md` stdin redirect. Conservative: any pipe, output
    redirect, chain (; && ||), or command substitution disqualifies it, because
    then the streamed bytes are filtered/derived, not the document itself."""
    try:
        toks = shlex.split(cmd, posix=False)
    except ValueError:
        return False
    if not toks:
        return False
    # Any shell plumbing means the output is no longer the file verbatim.
    if any(t in ("|", ";", "&&", "||", "&", ">", ">>", "&>") for t in toks):
        return False
    if any(c in cmd for c in "`$(") and "$(" in cmd:
        return False
    def _has_md_arg(words):
        for w in words:
            w = w.strip("'\"")
            if w.lower().endswith(_MD_EXT):
                return True
        return False
    # `< file.md` (with or without a leading command)
    if "<" in toks:
        i = toks.index("<")
        if i + 1 < len(toks):
            tgt = toks[i + 1].strip("'\"")
            if tgt.lower().endswith(_MD_EXT):
                return True
    head = os.path.basename(toks[0].strip("'\""))
    if head in _MD_READERS and _has_md_arg(toks[1:]):
        return True
    return False


# JSON can only be pretty-printed whole (a partial document is invalid), so only
# `cat` (or `< file.json`) qualifies — head/tail would truncate it. jq is excluded
# (it already pretty-prints + colours). Same plumbing guard as md_source.
_JSON_READERS = {"cat"}
_JSON_EXT = (".json", ".jsonl", ".ndjson")


def json_source(cmd):
    """True when `cmd` streams a whole .json file's raw contents — `cat file.json`
    or a bare `< file.json`. Any pipe/redirect/chain disqualifies it."""
    try:
        toks = shlex.split(cmd, posix=False)
    except ValueError:
        return False
    if not toks:
        return False
    if any(t in ("|", ";", "&&", "||", "&", ">", ">>", "&>") for t in toks):
        return False
    if "$(" in cmd:
        return False
    def _has_json_arg(words):
        return any(w.strip("'\"").lower().endswith(_JSON_EXT) for w in words)
    if "<" in toks:
        i = toks.index("<")
        if i + 1 < len(toks) and toks[i + 1].strip("'\"").lower().endswith(_JSON_EXT):
            return True
    head = os.path.basename(toks[0].strip("'\""))
    return head in _JSON_READERS and _has_json_arg(toks[1:])


# YAML is coloured in place (not reparsed), so head/tail of a .yml is fine too.
_YAML_READERS = {"cat", "head", "tail"}
_YAML_EXT = (".yml", ".yaml")


def yaml_source(cmd):
    """True when `cmd` streams a .yml/.yaml file's raw contents — an allowlisted
    reader (cat/head/tail) with a .yml/.yaml argument, or a bare `< file.yml`."""
    try:
        toks = shlex.split(cmd, posix=False)
    except ValueError:
        return False
    if not toks:
        return False
    if any(t in ("|", ";", "&&", "||", "&", ">", ">>", "&>") for t in toks):
        return False
    if "$(" in cmd:
        return False
    def _has_yaml_arg(words):
        return any(w.strip("'\"").lower().endswith(_YAML_EXT) for w in words)
    if "<" in toks:
        i = toks.index("<")
        if i + 1 < len(toks) and toks[i + 1].strip("'\"").lower().endswith(_YAML_EXT):
            return True
    head = os.path.basename(toks[0].strip("'\""))
    return head in _YAML_READERS and _has_yaml_arg(toks[1:])


# Source files coloured in place (like YAML) — the extension picks the lexer.
# cat/head/tail take the file among their args; sed/grep put a SCRIPT/PATTERN arg
# first and the FILE last, so their lexer is read from the trailing arg only — that
# way a pattern like `grep 'foo.py' x.txt` can't masquerade as python, and a
# recursive `grep -r pat src/` (dir last, no extension) correctly opts out.
_CODE_READERS = {"cat", "head", "tail"}
_CODE_TAILARG_READERS = {"sed", "grep", "egrep", "fgrep"}


def code_source(cmd):
    """If `cmd` streams a source file the mirror can syntax-highlight (cat/head/tail
    of a file whose extension is in coderender.LANGS, sed/grep of one, or a bare
    `< file.py`), return the pygments LEXER NAME (e.g. 'python'); else None. Same
    plumbing guards."""
    from core.coderender import LANGS
    try:
        toks = shlex.split(cmd, posix=False)
    except ValueError:
        return None
    if not toks:
        return None
    if any(t in ("|", ";", "&&", "||", "&", ">", ">>", "&>") for t in toks):
        return None
    if "$(" in cmd:
        return None

    def _lexer_for(words):
        for w in words:
            w = w.strip("'\"").lower()
            for ext, lexer in LANGS.items():
                if w.endswith(ext):
                    return lexer
        return None

    if "<" in toks:
        i = toks.index("<")
        if i + 1 < len(toks):
            lx = _lexer_for([toks[i + 1]])
            if lx:
                return lx
    head = os.path.basename(toks[0].strip("'\""))
    if head in _CODE_READERS:
        return _lexer_for(toks[1:])
    if head in _CODE_TAILARG_READERS and len(toks) > 1:
        return _lexer_for([toks[-1]])       # the FILE is the trailing arg
    return None


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


