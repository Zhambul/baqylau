# plugins/claude_code/memory.py — the MEMORY-WIKI vocabulary owner.
#
# In the code/01 workflow a session's durable knowledge lives in an Obsidian-style
# markdown wiki at ~/wiki/01 (notes with YAML frontmatter, cross-linked with bare
# [[wikilinks]]). A Read/Write/Edit whose path falls under that root is a MEMORY op
# — recall (Read), persist (Write), or revise (Update/Edit). This module is the ONE
# owner of that fact (docs/styleguide.md single-owner table): the root path, the
# is_memory() test, the mirror MARK, the per-session `memory` kv snapshot the web
# dashboard's Memory tab reads (write half — record() for a touched note,
# record_search() for a vault SEARCH), and the read-side vault helpers the
# dashboard's note viewer follows links with (resolve/backlinks/read_note).
# Import-safe: no I/O at import.
#
# A memory op does not have to be a Read/Write/Edit TOOL call. Most vault recall
# in the wild is spelled as a shell command (`cd ~/wiki/01 && cat platform/…md`,
# `find . -name x.md -exec cat {} \;`, `qmd query "…"`), and for a year none of it
# reached this kv at all — the Bash plane lives in its sibling memcmd.py, which
# calls the same two writers below. This module stays the kv/vault owner; memcmd
# owns "which notes does this shell command read, and what did qmd answer".
#
# The root is HARDCODED (~/wiki/01) behind root() — the one seam is an internal env
# override the hermetic tests point at a tmp vault (BAQYLAU_MEMORY_ROOT); it is NOT
# a user-facing knob. Everything derives the root from root(), never re-encodes it.
import json
import os
import re
import time

from core import ops as O
from core import state as ST

A = O.A    # audit trail (real module, or a no-op stub if it failed to import)

KEY = "memory"                      # the state-DB kv stash the Memory tab reads

# Bounds on the `searches` half of the kv. It is read whole on every Memory-tab
# poll, so it cannot grow with the session: SEARCH_MAX searches kept (newest
# first), HITS_MAX result rows per search (qmd's own page is ~10), SNIPPET_CAP
# chars per snippet. Past the cap the OLDEST search drops — the tab is a working
# record of this session's recall, and a query from a hundred commands ago is one
# you have already acted on.
SEARCH_MAX = 40
HITS_MAX = 12
SNIPPET_CAP = 1200
MARK = "\u2756"                     # ❖ — the distinct memory marker baked into the mirror one-liner

_DEFAULT_ROOT = "~/wiki/01"

# What a NOTE is spelled as. The vault is markdown-only (qmd's own collection
# pattern over it is `**/*.md`), so an extension is what separates a note from the
# `.obsidian/` machinery around it. is_memory() deliberately does NOT check this —
# it answers "is this path under the vault" for a file op that already named a real
# file — but the Bash plane scans arbitrary command tokens and needs the filter
# (memcmd._note_at) before it touches the filesystem.
NOTE_EXT = (".md", ".markdown")

# The feature is SCOPED to one project: the memory wiki (~/wiki/01) is shared
# across all of code/01, but a session only gets the ❖ marker / Memory tab /
# note viewer when it is working inside aggregator-adapters (the project whose
# .claude/ wires up the wiki). BAQYLAU_MEMORY_PROJECT overrides it — the
# hermetic-test seam only, not a user knob.
_DEFAULT_PROJECT = "~/code/01/aggregator-adapters"

# Verb precedence for the stored label when a note is touched more than once: a
# Write (note created) outranks an Update (revised) outranks a Read (recalled), so
# the tab shows the most consequential thing that happened to each note.
_VERB_RANK = {"Read": 1, "Update": 2, "Write": 3}

# A bare [[wikilink]] occurrence: stem is everything up to a | alias, # anchor, or
# the closing ]]. Obsidian resolves the stem by bare name across the whole vault.
_LINK_RE = re.compile(r"\[\[\s*([^\]|#]+?)\s*(?:[#|][^\]]*)?\]\]")

_READ_CAP = 256 * 1024              # bounded note read (a note is prose, not a log)

# Vault index caches, {root: (built_at, value)}, TTL-refreshed: _INDEX holds the
# cheap stem→path map resolve() needs, _LINKS the expensive stem→backlinks map
# (see the two _scan_* functions for why they are separate).
_INDEX = {}
_LINKS = {}
_INDEX_TTL_S = 30.0


def root():
    """The memory-wiki root, absolute and symlink-agnostic. Hardcoded ~/wiki/01;
    BAQYLAU_MEMORY_ROOT overrides it (the hermetic-test seam only — undocumented,
    not a user knob)."""
    return os.path.abspath(os.path.expanduser(
        os.environ.get("BAQYLAU_MEMORY_ROOT") or _DEFAULT_ROOT))


def is_memory(path):
    """True when `path` is a note UNDER the memory root (the file-op → memory-op
    test). The bare root (a directory) or anything outside returns False. This
    is the PATH test only — callers combine it with in_scope() (the project
    gate) so the feature activates only for aggregator-adapters sessions."""
    if not path:
        return False
    return os.path.abspath(path).startswith(root() + os.sep)


def rel(path):
    """`path` as a VAULT-RELATIVE, '/'-joined path ("providers/egt/egt.md"), or
    "" when it is not a note under the root. The ONE place the root prefix is
    stripped: root() owns the root, so the split off it belongs here too. The
    dashboard's Memory tab groups a session's touched notes by the vault's own
    folder structure with it (dashboard/read/mirror.memory_tree)."""
    if not is_memory(path):
        return ""
    return os.path.relpath(os.path.abspath(path), root()).replace(os.sep, "/")


def project():
    """The project the memory feature is enabled for (aggregator-adapters),
    absolute. BAQYLAU_MEMORY_PROJECT overrides it (test seam only)."""
    return os.path.abspath(os.path.expanduser(
        os.environ.get("BAQYLAU_MEMORY_PROJECT") or _DEFAULT_PROJECT))


def in_scope(cwd=None):
    """True when a session working in `cwd` (default: this process's cwd — the
    session dir a hook/tailer runs in) is inside the enabled project. The wiki
    is shared across code/01, but the feature is deliberately scoped to
    aggregator-adapters, so a wiki note touched from ANOTHER project is not a
    memory op here (and that session shows no Memory tab). A worktree under the
    project (…/.claude/worktrees/<x>) is in scope (it starts with the root)."""
    cwd = cwd or os.getcwd()
    p = project()
    ap = os.path.abspath(cwd)
    return ap == p or ap.startswith(p + os.sep)


# --- write side: the per-session `memory` kv snapshot -------------------------------
#
# The kv is ONE json object with two independent lists:
#   {"files":    [{path, name, verb, agent, count, ts}, …],     # notes touched
#    "searches": [{kind, sub, query, cmd, expanded, hits,
#                  agent, count, ts}, …]}                        # vault searches
# Both writers go through _merge below so neither can drop the other's list — a
# writer that rebuilt the whole object from its own list would silently erase
# every search the moment a note was read (the shape's one real hazard).

def _merge(log, mutate, what):
    """Read-modify-write the `memory` kv under one BEGIN IMMEDIATE, so the main
    hook and the substream tailer can't clobber each other. `mutate(stash)` edits
    the whole stash dict in place (both lists present and list-typed) and returns
    an audit-decision fragment, or None to mean "nothing recorded" — in which case
    the write still happens (harmless: the stash is unchanged) but the caller gets
    None. `what` names the operation in the audit-error row.

    Guarded by ST.parked: never CREATE the DB — its file-existence is the
    session-alive signal (same rule as task_fmt). Returns (fragment, stash) on
    success, (None, None) when parked / the DB is gone / the write raised."""
    if ST.parked(log):
        return None, None
    conn = ST.connect(log)
    if conn is None:
        return None, None
    try:
        with ST.immediate(conn):
            row = conn.execute("SELECT val FROM kv WHERE key=?", (KEY,)).fetchone()
            try:
                stash = json.loads(row[0]) if row else None
            except Exception:
                stash = None
            if not isinstance(stash, dict):
                stash = {}
            for k in ("files", "searches"):
                if not isinstance(stash.get(k), list):
                    stash[k] = []
            note = mutate(stash)
            conn.execute("INSERT INTO kv(key, val) VALUES(?, ?) "
                         "ON CONFLICT(key) DO UPDATE SET val = excluded.val",
                         (KEY, json.dumps(stash, ensure_ascii=False)))
    except Exception:
        A.error(log, "memory." + what, {"log": log})
        return None, None
    return note, stash


def record(log, path, verb, agent=None):
    """Merge one touched note into the session's `memory` kv `files` list (the
    Memory tab's source), keyed by path: a repeat touch bumps count/ts and
    ESCALATES verb by _VERB_RANK (Write beats Update beats Read), stamping the
    escalating op's agent. `agent` is the subagent name (None = main agent).
    Returns an audit-decision fragment.

    NOT a memory op / parked / DB gone → returns None (caller skips the audit note).
    """
    if not is_memory(path):
        return None
    name = os.path.basename(path.rstrip("/")) or path
    now = time.time()

    def mutate(stash):
        files = stash["files"]
        cur = next((f for f in files if isinstance(f, dict)
                    and f.get("path") == path), None)
        if cur is None:
            cur = {"path": path, "name": name, "verb": verb,
                   "agent": agent, "count": 0, "ts": now}
            files.append(cur)
        elif _VERB_RANK.get(verb, 0) >= _VERB_RANK.get(cur.get("verb"), 0):
            # Escalate the stored verb; stamp the escalating op's agent.
            cur["verb"] = verb
            cur["agent"] = agent
        cur["count"] = int(cur.get("count") or 0) + 1
        cur["ts"] = now
        return "%s %s [mem:%s]" % (verb.lower(), name, agent or "main")

    note, stash = _merge(log, mutate, "record")
    if note is None:
        return None
    A.state_file(log, ST.db_path(log), KEY,
                 {"action": "write", "verb": verb, "path": path,
                  "agent": agent or "main", "notes": len(stash["files"])})
    return note


def record_search(log, kind, sub, query, hits, cmd="", expanded=(), agent=None):
    """Merge one vault SEARCH into the session's `memory` kv `searches` list — the
    Memory tab's search cards (docs/dashboard.md *Memory searches*). A search is
    the OTHER half of recall: `qmd query "how does rscheck answer getstatus"`
    reads no single note, so record() above sees nothing, yet it is exactly the
    moment the session asked memory a question. What is worth keeping is the
    QUESTION and the ANSWER, so the record carries both: `query` (+ `expanded`,
    the LLM-expanded lex/vec/hyde lines qmd prints for a `query`) and `hits` (the
    parsed result rows — memcmd.qmd_hits).

    `kind` is the tool ("qmd"), `sub` its subcommand ("query"/"search"/"vsearch")
    — kept apart because they read differently in the card and only `query` has
    expansion. Keyed by (kind, sub, query): re-running a query bumps count/ts and
    REPLACES the hits (the freshest answer is the one that mattered), so a retried
    search doesn't fill the tab with near-duplicates. The list is newest-first and
    capped at SEARCH_MAX. Returns an audit-decision fragment, or None when parked /
    the DB is gone / there is no query to record."""
    query = (query or "").strip()
    if not query:
        return None
    now = time.time()
    rows = [dict(h) for h in (hits or [])][:HITS_MAX]
    exp = [str(e) for e in (expanded or [])]

    def mutate(stash):
        searches = stash["searches"]
        cur = next((s for s in searches if isinstance(s, dict)
                    and s.get("kind") == kind and s.get("sub") == sub
                    and s.get("query") == query), None)
        if cur is None:
            cur = {"kind": kind, "sub": sub, "query": query, "count": 0}
            searches.append(cur)
        cur.update({"cmd": cmd, "expanded": exp, "hits": rows,
                    "agent": agent, "ts": now})
        cur["count"] = int(cur.get("count") or 0) + 1
        searches.sort(key=lambda s: s.get("ts") or 0, reverse=True)
        del searches[SEARCH_MAX:]
        return "%s %s(%s) → %d hits [mem:%s]" % (
            kind, sub, cap_query(query), len(rows), agent or "main")

    note, stash = _merge(log, mutate, "record_search")
    if note is None:
        return None
    A.state_file(log, ST.db_path(log), KEY,
                 {"action": "search", "kind": kind, "sub": sub, "query": query,
                  "hits": len(rows), "agent": agent or "main",
                  "searches": len(stash["searches"])})
    return note


def cap_query(query, n=60):
    """A query shortened for a one-line audit/mirror label (the full text lives in
    the kv record and the tab's card)."""
    query = " ".join((query or "").split())
    return query if len(query) <= n else query[:n - 1] + "…"


# --- read side: vault link resolution (the note viewer follows these) ---------------
#
# TWO indexes, deliberately, because their COSTS differ by three orders of
# magnitude and they no longer have the same callers. Names are a directory walk
# (~700 dirents); backlinks must OPEN AND READ every note in the vault. They were
# one scan while both were dashboard-only (a long-lived server, one build per TTL),
# but resolve() is now on the HOOK path — memcmd resolves a `find -name x.md`
# basename inside a short-lived process that exits before any cache pays off, and
# building backlinks there would have read the whole vault on every Bash command.
# So resolve() takes the cheap one and backlinks() alone pays for the expensive one.

def _scan_names():
    """Walk the vault → {stem: absolute path} (first wins on a stem collision — the
    wiki's convention is globally-unique stems). Skips the .obsidian/.git dirs.
    Opens NOTHING. Missing root → empty map."""
    name2path = {}
    for dirpath, dirnames, filenames in os.walk(root()):
        dirnames[:] = [d for d in dirnames if d not in (".obsidian", ".git")]
        for fn in filenames:
            if fn.endswith(".md"):
                name2path.setdefault(fn[:-3], os.path.join(dirpath, fn))
    return name2path


def _scan_links():
    """Walk the vault reading each note → {stem: sorted [stems linking to it]} via
    its bare [[stem]] occurrences."""
    links = {}
    for stem, fp in _scan_names().items():
        try:
            with open(fp, encoding="utf-8", errors="replace") as f:
                body = f.read(_READ_CAP)
        except OSError:
            continue
        for m in _LINK_RE.finditer(body):
            links.setdefault(m.group(1).strip(), set()).add(stem)
    return {k: sorted(v) for k, v in links.items()}


def _cached(cache, build):
    """TTL-cached `build()` keyed on the current root (the dashboard is read-only
    and multi-threaded; a redundant concurrent build is cheaper than a lock)."""
    base = root()
    ent = cache.get(base)
    now = time.time()
    if ent and now - ent[0] < _INDEX_TTL_S:
        return ent[1]
    val = build()
    cache[base] = (now, val)
    return val


def resolve(stem):
    """A bare [[wikilink]] stem (or a note basename) → the note's absolute path, or
    None when the vault has no such note (a dangling link — the wiki keeps those on
    purpose)."""
    if not stem:
        return None
    return _cached(_INDEX, _scan_names).get(stem.strip())


def backlinks(path):
    """The note stems whose text links to the note at `path` (its `## Affects` /
    incoming references), sorted. Empty when nothing links in."""
    if not path:
        return []
    stem = os.path.basename(path.rstrip("/"))
    if stem.endswith(".md"):
        stem = stem[:-3]
    return _cached(_LINKS, _scan_links).get(stem, [])


def read_note(path):
    """A note's (frontmatter dict, body str), bounded to _READ_CAP. REFUSES any
    path outside the memory root (path-traversal guard — the dashboard passes
    user-controlled stems/paths). Returns (None, None) when refused/unreadable.
    Frontmatter is a leading '---\\n…\\n---' block parsed as flat key: value lines
    (no yaml dependency); a note without it yields ({}, whole-text)."""
    if not path or not is_memory(path):
        return None, None
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read(_READ_CAP)
    except OSError:
        return None, None
    return _split_frontmatter(text)


def _split_frontmatter(text):
    fm = {}
    body = text
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end != -1:
            block = text[4:end]
            nl = text.find("\n", end + 1)
            body = text[nl + 1:] if nl != -1 else ""
            for ln in block.split("\n"):
                if ":" in ln:
                    k, v = ln.split(":", 1)
                    k = k.strip()
                    if k:
                        fm[k] = v.strip()
    return fm, body
