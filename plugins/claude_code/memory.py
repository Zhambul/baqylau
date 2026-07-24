# plugins/claude_code/memory.py — the MEMORY-WIKI vocabulary owner.
#
# In the code/01 workflow a session's durable knowledge lives in an Obsidian-style
# markdown wiki at ~/wiki/01 (notes with YAML frontmatter, cross-linked with bare
# [[wikilinks]]). A Read/Write/Edit whose path falls under that root is a MEMORY op
# — recall (Read), persist (Write), or revise (Update/Edit). This module is the ONE
# owner of that fact (docs/styleguide.md single-owner table): the root path, the
# is_memory() test, the mirror MARK, the per-session `memory` kv snapshot the web
# dashboard's Memory tab reads (write half — record()), and the read-side vault
# helpers the dashboard's note viewer follows links with (resolve/backlinks/
# read_note). Import-safe: no I/O at import.
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
MARK = "\u2756"                     # ❖ — the distinct memory marker baked into the mirror one-liner

_DEFAULT_ROOT = "~/wiki/01"

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

# Vault index cache: {root: (built_at, name2path, backlinks)} — resolve()/
# backlinks() scan every note once, TTL-refreshed (the dashboard is read-only and
# multi-threaded; the scan is cheap for a few-hundred-note vault).
_INDEX = {}
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

def record(log, path, verb, agent=None):
    """Merge one touched note into the session's `memory` kv (the Memory tab's
    source). The kv is {"files": [{path, name, verb, agent, count, ts}, …]} keyed
    by path: a repeat touch bumps count/ts and ESCALATES verb by _VERB_RANK (Write
    beats Update beats Read), stamping the escalating op's agent. `agent` is the
    subagent name (None = main agent). Guarded by ST.parked (never CREATE the DB —
    its file-existence is the session-alive signal, same rule as task_fmt), and the
    read-modify-write runs in one BEGIN IMMEDIATE so the main hook and the substream
    tailer can't clobber each other. Returns an audit-decision fragment.

    NOT a memory op / parked / DB gone → returns None (caller skips the audit note).
    """
    if not is_memory(path) or ST.parked(log):
        return None
    conn = ST.connect(log)
    if conn is None:
        return None
    name = os.path.basename(path.rstrip("/")) or path
    now = time.time()
    try:
        with ST.immediate(conn):
            row = conn.execute("SELECT val FROM kv WHERE key=?", (KEY,)).fetchone()
            try:
                stash = json.loads(row[0]) if row else None
            except Exception:
                stash = None
            files = stash.get("files") if isinstance(stash, dict) else None
            if not isinstance(files, list):
                files = []
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
            conn.execute("INSERT INTO kv(key, val) VALUES(?, ?) "
                         "ON CONFLICT(key) DO UPDATE SET val = excluded.val",
                         (KEY, json.dumps({"files": files}, ensure_ascii=False)))
    except Exception:
        A.error(log, "memory.record", {"path": path, "verb": verb, "agent": agent})
        return None
    A.state_file(log, ST.db_path(log), KEY,
                 {"action": "write", "verb": verb, "path": path,
                  "agent": agent or "main", "notes": len(files)})
    who = agent or "main"
    return "%s %s [mem:%s]" % (verb.lower(), name, who)


# --- read side: vault link resolution (the note viewer follows these) ---------------

def _scan():
    """Walk the vault once → (name2path, backlinks). name2path maps a note stem to
    its absolute path (first wins on a stem collision — the wiki's convention is
    globally-unique stems). backlinks maps a stem to the sorted list of note stems
    whose text links to it via [[stem]]. Skips the .obsidian/.git dirs. Missing
    root → empty maps."""
    base = root()
    name2path, links = {}, {}
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in (".obsidian", ".git")]
        for fn in filenames:
            if not fn.endswith(".md"):
                continue
            stem = fn[:-3]
            fp = os.path.join(dirpath, fn)
            name2path.setdefault(stem, fp)
            try:
                with open(fp, encoding="utf-8", errors="replace") as f:
                    body = f.read(_READ_CAP)
            except OSError:
                continue
            for m in _LINK_RE.finditer(body):
                target = m.group(1).strip()
                links.setdefault(target, set()).add(stem)
    backlinks = {k: sorted(v) for k, v in links.items()}
    return name2path, backlinks


def _index():
    """TTL-cached _scan() keyed on the current root."""
    base = root()
    ent = _INDEX.get(base)
    now = time.time()
    if ent and now - ent[0] < _INDEX_TTL_S:
        return ent[1], ent[2]
    name2path, backlinks = _scan()
    _INDEX[base] = (now, name2path, backlinks)
    return name2path, backlinks


def resolve(stem):
    """A bare [[wikilink]] stem → the note's absolute path, or None when the vault
    has no such note (a dangling link — the wiki keeps those on purpose)."""
    if not stem:
        return None
    name2path, _ = _index()
    return name2path.get(stem.strip())


def backlinks(path):
    """The note stems whose text links to the note at `path` (its `## Affects` /
    incoming references), sorted. Empty when nothing links in."""
    if not path:
        return []
    stem = os.path.basename(path.rstrip("/"))
    if stem.endswith(".md"):
        stem = stem[:-3]
    _, bl = _index()
    return bl.get(stem, [])


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
