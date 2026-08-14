# plugins/claude_code/memcmd.py — the BASH plane of the memory feature.
#
# memory.py owns the memory-wiki FACT (the root, the kv, the vault readers) and was
# wired only to Claude Code's Read/Write/Edit TOOLS. That covered the smaller half
# of real recall. Measured on one in-scope session (d8dc5a67, 2026-07-30): ten wiki
# notes read, three vault searches run, and NOT ONE reached the `memory` kv — every
# single touch was a shell command:
#
#   cd ~/wiki/01 && cat platform/concepts/observability.md 2>/dev/null | head -60
#   cd ~/wiki/01 && cat platform/concepts/rscheck-healthcheck.md platform/… x3
#   cd ~/wiki/01 && find . -name "cloud-manifest-port-started-state.md" -exec cat {} \;
#   qmd search "manifest started healthcheck"
#
# This module is the ONE owner of "which vault notes does this shell command read,
# and what did qmd answer" (docs/styleguide.md single-owner table). It parses; it
# never writes — the kv writers stay memory.record/record_search, and the two Bash
# formatters reach it through the one fileobs Obs row (its cmd_match/cmd_record
# plane), exactly as file ops reach memory.record through the same row.
#
# WHY a token scan rather than tools.read_command. That function answers a
# different question — "should the mirror COLLAPSE this command into a Read
# one-liner", which is deliberately narrow: one file, from the LAST statement, from
# an allowlisted reader, no plumbing. Every command above fails it for a reason
# that is correct there and wrong here (three files at once; the read sits in a
# non-final statement; `find` is not a reader; a relative path under an untracked
# `cd`). So the read-plane verdict is not reusable and this plane walks EVERY
# statement and EVERY token, asking only: is this token a real file under the
# vault. The proof that a token is a note is the FILESYSTEM (is_memory + isfile),
# not a grammar — which is also what keeps it from false-positiving on flags and
# patterns (`--include=*.md` is not a file).
#
# Import-safe: no I/O at import.
import os
import re
import shlex

from plugins.claude_code import memory as MEM
from plugins.claude_code import shell

# Commands whose presence in a statement means it READS file contents. Checked
# across the whole statement, not just its head, because the reader is routinely
# not the first word: `find . -name x.md -exec cat {} \;`, `xargs cat`, `… | head`.
# A statement with no reader at all (`ls ~/wiki/01`, `rm x.md`, `git add x.md`)
# records nothing — the feature is scoped to READS (a Bash WRITE into the vault is
# deliberately out of scope for now; docs/dashboard.md *Memory searches*).
# Deliberately WIDER than tools._WHOLE_READERS/_FRAG_READERS: those sets are about
# what the mirror can RE-RENDER (bat/glow/less are excluded there because they
# style their own output), but a note read through `glow` is just as much a recall.
_READERS = frozenset({
    "cat", "head", "tail", "sed", "grep", "egrep", "fgrep", "rg", "ag", "ack",
    "awk", "nl", "bat", "glow", "mdcat", "less", "more", "view", "xxd", "strings",
})

# Shell metacharacters that make a token a PATTERN, not a path (`*.md`, `$NOTE`).
_DYNAMIC = "$`*?["

# `qmd` subcommands, split by what they MEAN for memory. The SEARCH ones ask the
# vault a question and get ranked passages back (a search card); the READ ones name
# a document and print it (an ordinary note read, recorded in `files` like a `cat`).
# Anything else (`status`, `update`, `collection …`, `mcp`) is index maintenance,
# not recall, and is ignored.
QMD_SEARCH_SUBS = ("query", "search", "vsearch")
QMD_READ_SUBS = ("get", "multi-get")

# One result block of qmd's output:
#
#   qmd://wiki01/platform/concepts/rscheck-healthcheck.md:13 #000b85
#   Title: rscheck — what actually answers `/getstatus:81` on a cloud service
#   Context: 01 platform memory wiki: …
#   Score:  86%
#
#   @@ -12,4 @@ (11 before, 54 after)
#   <the passage>
#
# The header is the anchor (a `qmd get` header has no :line and two spaces before
# the #docid — both optional here). Matched as a WHOLE line so a `qmd://` URL
# quoted inside a passage can't open a phantom hit.
_HIT_RE = re.compile(r"^qmd://([^/\s]+)/(\S+?)(?::(\d+))?\s+#([0-9a-fA-F]+)$")
_HUNK_RE = re.compile(r"^@@ .*@@")
# The expansion preamble a `qmd query` prints before searching: the LLM-rewritten
# lex:/vec:/hyde: lines, drawn as a box tree. Worth keeping — it is what the search
# actually ASKED, as opposed to what was typed. Only the TYPED lines: the tree's
# first row is the original query verbatim, which the record already holds.
_EXP_RE = re.compile(r"^[├└─│]+[─\s]*((?:lex|vec|hyde|intent):\s*.+?)\s*$")
_FIELDS = (("Title:", "title"), ("Score:", "score"))


def _toks(stmt):
    """`stmt` tokenised with quotes SURVIVING (posix=False — the same choice
    tools.py makes, for the same reason: a quoted argument must stay
    distinguishable from shell syntax). () on a mis-quoted statement, which the
    callers then skip."""
    try:
        return shlex.split(stmt, posix=False)
    except ValueError:
        return ()


def _bare(tok):
    """A token with its surrounding quotes removed."""
    tok = tok.strip()
    if len(tok) >= 2 and tok[0] in ("'", '"') and tok[-1] == tok[0]:
        return tok[1:-1]
    return tok.strip("'\"")


def _has_reader(toks):
    """Does this statement run something that prints file contents? (see _READERS)"""
    return any(os.path.basename(_bare(t)) in _READERS for t in toks)


def _note_at(tok, at, inside):
    """One token → the absolute path of the vault note it names, or None.

    Two shapes, in order:
      * a PATH (absolute, `~`-rooted, or relative to `at`, the directory this
        statement runs in) that is a real file under the vault root;
      * a bare `<stem>.md` BASENAME resolved through the vault's own name index
        (`find . -name "x.md" -exec cat` names the note without its path) — but
        ONLY when the statement runs inside the vault, so a `README.md` in some
        repo can't be answered with a same-named note.
    A flag, a glob/expansion, and anything without a note extension are rejected
    before the filesystem is touched."""
    w = _bare(tok)
    if not w or w.startswith("-") or any(c in w for c in _DYNAMIC):
        return None
    if not w.lower().endswith(MEM.NOTE_EXT):
        return None
    p = os.path.abspath(os.path.join(at, os.path.expanduser(w)))
    if MEM.is_memory(p) and os.path.isfile(p):
        return p
    if inside and "/" not in w:
        r = MEM.resolve(os.path.splitext(w)[0])
        if r and os.path.isfile(r):
            return r
    return None


def _qmd_note(arg):
    """A `qmd get`/`multi-get` argument → the absolute note path, or None. qmd
    addresses a document by its COLLECTION-relative path (`platform/concepts/
    deploy.md`) or its full `qmd://<collection>/<path>` URL, with an optional
    `:from[:count]` line window — none of which is relative to the shell's cwd,
    which is why these don't go through _note_at."""
    w = _bare(arg)
    if not w or w.startswith("-") or any(c in w for c in _DYNAMIC):
        return None
    if w.startswith("qmd://"):
        w = w[6:].split("/", 1)[-1]          # drop the collection segment
    w = re.sub(r":\d+(?::\d+)?$", "", w)     # drop the :from[:count] window
    if not w.lower().endswith(MEM.NOTE_EXT):
        return None
    p = os.path.join(MEM.root(), w)
    if os.path.isfile(p):
        return os.path.abspath(p)
    return MEM.resolve(os.path.splitext(os.path.basename(w))[0])


def _qmd_call(toks):
    """A `qmd` statement's tokens → ("search", sub, query) | ("read", sub, paths)
    | None. The query is every non-flag argument after the subcommand, joined —
    qmd takes it as one string, and quoting it is optional."""
    if not toks or os.path.basename(_bare(toks[0])) != "qmd":
        return None
    sub = _bare(toks[1]) if len(toks) > 1 else ""
    args = [t for t in toks[2:] if not _bare(t).startswith("-")]
    if sub in QMD_SEARCH_SUBS:
        # Stop at the first shell operator: `qmd search "x" 2>/dev/null | head -40`
        # must not fold `head` and `-40` into the query.
        words = []
        for t in args:
            b = _bare(t)
            if not b or b in ("|", ";", "&", ">", ">>") or ">" in b or "<" in b:
                break
            words.append(b)
        return ("search", sub, " ".join(words).strip())
    if sub in QMD_READ_SUBS:
        return ("read", sub, tuple(p for p in (_qmd_note(a) for a in args) if p))
    return None


def plan(cmd, cwd=None):
    """What this shell command does to memory: (note_paths, searches).

    note_paths — absolute vault notes it READS, deduped, in first-appearance
    order (every statement, every token — see the module header).
    searches    — [(kind, sub, query), …] vault questions it asks.

    `cwd` is the SESSION cwd (the hook payload's); "" / None means this process's
    cwd (the substream tailer inherits the session dir). Each statement resolves
    its relative paths against its OWN directory (tools.statement_cwds with
    tilde=True — `cd ~/wiki/01 && cat platform/…md` is the universal spelling and
    resolving it against the hook cwd found nothing). A statement whose relative
    paths can't be placed (an untrackable `cd`) contributes no NOTES — but is
    still asked for a SEARCH, which is cwd-independent: qmd addresses the vault
    by collection, not by where the shell happens to stand.

    The command is UNWRAPPED first (tools.unwrap_tee): at PostToolUse — where
    the recording half runs — the payload carries cmd_pre's tee rewrite, and
    `{ cd ~/wiki/01 && …` defeats the cd tracking outright. Measured on the
    session this plane was written for: without the unwrap, 2 of 10 reads and 0
    of 2 searches survive.

    Pure: reads the filesystem to CONFIRM a path, writes nothing."""
    base = cwd or os.getcwd()
    found, searches = [], []
    for stmt, at in shell.statement_directories(
        shell.original_command(cmd or ""),
        base,
    ):
        toks = _toks(stmt)
        if not toks:
            continue
        call = _qmd_call(toks)
        if call and call[0] == "search":
            if call[2]:
                searches.append(("qmd", call[1], call[2]))
            continue
        paths = list(call[2]) if call else []
        if not call:
            if at is None or not _has_reader(toks):
                continue
            inside = MEM.is_memory(os.path.join(at, "x"))
            paths = [p for p in (_note_at(t, at, inside) for t in toks) if p]
        for p in paths:
            if p not in found:
                found.append(p)
    return tuple(found), tuple(searches)


def touches(cmd, cwd=None):
    """True when this command reads a vault note or searches the vault — the
    fileobs `cmd_match` plane, which is what bakes the ❖ MARK into the mirror
    block's header. Deliberately the same predicate the recording side uses, so
    a marked block always has a record behind it and vice versa."""
    notes, searches = plan(cmd, cwd)
    return bool(notes or searches)


# --- qmd output → the search card's answer -----------------------------------------

def qmd_hits(output):
    """Parse qmd's stdout into (hits, expanded).

    hits — [{path, rel, name, line, title, score, snippet}, …] in qmd's own
    ranked order. `path` is the absolute note ('' when the vault no longer has it
    — the card then shows the row without a link rather than dropping the answer),
    `rel` its vault-relative path, `name` its stem.
    expanded — the lex:/vec:/hyde: lines a `qmd query` prints before searching
    ([] for `search`/`vsearch`, which don't expand).

    Tolerant by construction: the output is frequently TRUNCATED (`| head -40` is
    the idiomatic way to run these), so a half-written final block is kept with
    whatever fields arrived, and anything unrecognised is skipped."""
    hits, expanded = [], []
    cur, snip = None, None

    def close():
        if cur is None:
            return
        if snip is not None:
            text = "\n".join(snip).strip("\n")
            if len(text) > MEM.SNIPPET_CAP:
                text = text[:MEM.SNIPPET_CAP] + "\n…"
            cur["snippet"] = text
        hits.append(cur)

    for raw in (output or "").splitlines():
        ln = raw.strip()
        m = _HIT_RE.match(ln)
        if m:
            close()
            rel = m.group(2)
            cur = {"path": _hit_path(rel), "rel": rel,
                   "name": os.path.splitext(os.path.basename(rel))[0],
                   "line": int(m.group(3)) if m.group(3) else None,
                   "title": "", "score": "", "snippet": ""}
            snip = None
            continue
        if cur is None:
            # Still in the expansion preamble (before the first result).
            em = _EXP_RE.match(raw.rstrip())
            if em and em.group(1):
                expanded.append(em.group(1))
            continue
        if snip is None:
            for prefix, key in _FIELDS:
                if ln.startswith(prefix):
                    cur[key] = ln[len(prefix):].strip()
                    break
            if _HUNK_RE.match(ln):
                snip = []                    # the passage starts on the next line
            continue
        snip.append(raw)
    close()
    return hits, expanded


def _hit_path(rel):
    """A hit's collection-relative path → the absolute note, '' when unresolvable.
    Falls back to the vault name index for a collection whose root is not the
    memory root (qmd may index more than this feature's vault)."""
    p = os.path.join(MEM.root(), rel)
    if os.path.isfile(p):
        return os.path.abspath(p)
    return MEM.resolve(os.path.splitext(os.path.basename(rel))[0]) or ""


# --- the fileobs cmd_record plane --------------------------------------------------

def record(log, cmd, cwd=None, output="", agent=None):
    """Snapshot everything this command did to memory into the session's `memory`
    kv, and return the audit-decision fragments (empty when it touched nothing).
    Called AFTER the block's own emit, like the file plane — memory.record* are
    parked-guarded and never create the state DB.

    A note READ goes into `files` as a Read (verb precedence in memory.record
    keeps a later Write/Update winning). A SEARCH goes into `searches` with its
    parsed answer — but the hits are attached only when the command ran EXACTLY
    ONE search: qmd's output carries no marker saying which of two concatenated
    searches a result block belongs to, and splitting it on a guess would file
    one query's answer under another. The query itself is always recorded, so a
    multi-search command still shows both cards, without hits."""
    notes, searches = plan(cmd, cwd)
    frags = []
    for p in notes:
        n = MEM.record(log, p, "Read", agent=agent)
        if n:
            frags.append(n)
    hits, expanded = ((), ())
    if len(searches) == 1:
        hits, expanded = qmd_hits(output)
    for kind, sub, query in searches:
        n = MEM.record_search(log, kind, sub, query, hits, cmd=cmd,
                              expanded=expanded, agent=agent)
        if n:
            frags.append(n)
    return frags
