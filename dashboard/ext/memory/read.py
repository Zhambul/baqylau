# dashboard/ext/memory/read.py — the Memory tab's read model + route handlers.
#
# The vault folder tree the tab renders, the note-viewer payload, and the
# extension's scope/badge/route callables (wired by the descriptor in
# __init__.py). Everything memory-specific the dashboard serves lives HERE —
# the core read model and http tier only see the dashboard.ext registry root.
# The vocabulary (root path, scope gate, kv shape, vault readers) stays with
# its owner, plugins/claude_code/memory.py (the DASHBOARD_PLUGIN_REACHES row).
import os

from core import sessionapi as API
from dashboard import ext
from dashboard.ext.memory import notehtml
from plugins.claude_code import memory as MEM


def scope(cwd):
    """True when a session working in `cwd` (already canonical — the read model
    canonicalizes once) gets the Memory tab at all — the feature is deliberately
    scoped to one project (MEM.in_scope), though the wiki it reads is shared
    across code/01."""
    return MEM.in_scope(cwd)


def badge(sid, agent):
    """The Memory tab badge's number — distinct wiki notes touched plus vault
    searches run, team-wide (`agent` unused: BADGE_SCOPED=False, the tab is
    session-wide). The framework applies the off-scope -> 0 gate
    (read/session.badge_count's scope weave), so both its readers — the overview
    payload and the SSE badge channel — cannot disagree about it."""
    return API.memory_count(sid)


def _mem_node(path, name):
    """One folder row of the memory tree. `dirs` is a dict while the tree is
    being built and a sorted LIST once _mem_rollup has run (what the client
    iterates); `notes` are the records filed directly on this row."""
    return {"name": name, "path": path, "dirs": {}, "notes": []}


def _mem_compress(node, top=False):
    """Fold the two shapes of structural noise out of one node, bottom-up. The
    vault is five levels deep but mostly LINEAR, so a literal tree spends four
    rows of indent on `slack › channels › vegas-adapters › threads` to show one
    note. Two rules, both about the same thing — a folder that is not a FORK in
    the road doesn't earn a row of its own:

      * a chain (no notes of its own + exactly one sub-folder) collapses into
        one row carrying the joined path — `platform/concepts`, and the slack
        chain above;
      * a lone leaf sub-folder under a row that DOES have notes of its own
        (so the chain rule can't apply, `providers/egt` = `egt.md` + a
        `concepts/`) folds into that row's NOTE labels as a path prefix —
        `concepts/wildcard-egress-acl.md`.

    A folder with SIBLINGS always keeps its row: `providers` holds egt +
    hacksaw + quadcode, and which provider we worked on is exactly the question
    the tree exists to answer. The ROOT is never compressed (`top`) — folding it
    would leave the scope rows with no header at all, and its own top-level
    notes (index.md) would swap places with a folder."""
    for sub in list(node["dirs"].values()):
        _mem_compress(sub)
    if top:
        return
    while len(node["dirs"]) == 1 and not node["notes"]:
        (sub,) = node["dirs"].values()
        node["name"] = node["name"] + "/" + sub["name"]
        node["path"] = sub["path"]
        node["notes"] = sub["notes"]
        node["dirs"] = sub["dirs"]
    if len(node["dirs"]) == 1 and node["notes"]:
        (sub,) = node["dirs"].values()
        if not sub["dirs"]:
            for note in sub["notes"]:
                note["label"] = sub["name"] + "/" + note["label"]
            node["notes"].extend(sub["notes"])
            node["dirs"] = {}


def _mem_rollup(node):
    """Sum each row's subtree (`count` notes, `writes` of them created/revised)
    and freeze the child order: folders before notes, each alphabetical. NOT by
    recency or by count — the tree repaints live as the session touches notes,
    and a row that jumps while you are reading it is worse than a stale one."""
    count, writes = len(node["notes"]), 0
    for note in node["notes"]:
        if note.get("verb") in ("Write", "Update"):
            writes += 1
    for sub in node["dirs"].values():
        _mem_rollup(sub)
        count += sub["count"]
        writes += sub["writes"]
    node["count"], node["writes"] = count, writes
    node["dirs"] = sorted(node["dirs"].values(), key=lambda d: d["name"].lower())
    node["notes"].sort(key=lambda n: n["label"].lower())


def memory_tree(records):
    """The session's touched memory notes as the VAULT's own folder tree, the
    Memory tab's read model (docs/dashboard.md *Memory tab*). `records` is the
    flat `memory` kv list (sessionapi.memory) — a list of note names answers
    "what did we touch" but not "did we work on platform, or on providers (and
    WHICH), or on tooling", which is what the vault's structure already encodes.

    Returns the root node: {name, path, dirs:[node…], notes:[record+label…],
    count, writes}. Every node carries its subtree's rollup, so a collapsed
    folder still says how much is under it; each note keeps its record fields
    (verb/agent/count/path — the row renders the same chips the flat list did)
    plus `label`, its name relative to the row it hangs on. A record whose path
    is not under the vault root (a stale kv row from another root) keeps its
    basename at the top level rather than being dropped."""
    root = _mem_node("", "")
    for rec in records or []:
        if not isinstance(rec, dict):
            continue
        parts = [p for p in MEM.rel(rec.get("path") or "").split("/") if p]
        if not parts:
            parts = [rec.get("name") or "?"]
        node = root
        for seg in parts[:-1]:
            sub = node["dirs"].get(seg)
            if sub is None:
                sub = node["dirs"][seg] = _mem_node(
                    (node["path"] + "/" + seg).lstrip("/"), seg)
            node = sub
        note = dict(rec)
        note["label"] = parts[-1]
        node["notes"].append(note)
    _mem_compress(root, top=True)
    _mem_rollup(root)
    return root


def note_payload(path, stem):
    """A memory-wiki note rendered for the Memory-tab viewer, by absolute `path`
    (a tab row) OR bare `stem` (a followed [[wikilink]]). Resolves the stem
    through the vault index, reads the note (path-traversal-guarded to the memory
    root by MEM.read_note), and renders the body with clickable/backlink-aware
    HTML. Returns {name, path, frontmatter:[[k,v]…], html, backlinks:[stem…],
    missing:bool}; missing=True (empty html) for a dangling stem / a path outside
    the root / an unreadable note — the client shows a 'note not found' card."""
    p = path or (MEM.resolve(stem) or "")
    fm, body = MEM.read_note(p) if p else (None, None)
    if body is None:
        return {"name": stem or os.path.basename(path or "") or "?", "path": "",
                "frontmatter": [], "html": "", "backlinks": [], "missing": True}
    name = os.path.basename(p)
    if name.endswith(".md"):
        name = name[:-3]
    return {"name": name, "path": p,
            "frontmatter": notehtml.frontmatter_rows(fm),
            "html": notehtml.note_html(body, resolve=MEM.resolve),
            "backlinks": MEM.backlinks(p), "missing": False}


# --- the tab's GET routes (ext.session_gets — same wire as before the move) ---

def get_memory(sid, url):
    """Everything the Memory tab shows: the notes this session touched — BOTH the
    flat newest-touch-first list and the same records grouped into the vault's
    folder tree, which is what the tab renders — plus the vault SEARCHES it ran
    (the expandable query/results cards above the tree, docs/dashboard.md *Memory
    searches*). Notes and searches are independent halves of one kv: a search
    names notes it never opened, and a note is often read with no search behind
    it, so neither is derivable from the other."""
    mem = API.memory(sid)
    return {"memory": mem, "tree": memory_tree(mem),
            "searches": search_cards(API.memory_searches(sid))}


def search_cards(searches):
    """The search records as the tab's cards: each hit gains `viewable` — whether
    the note behind it still exists and can be opened in the note viewer — so the
    client renders a plain row rather than a dead link for a hit whose note was
    since renamed or deleted (qmd's index outlives the file). Everything else is
    passed through as recorded; the SERVER decides linkability because it is the
    side that knows the filesystem."""
    out = []
    for s in searches or []:
        if not isinstance(s, dict):
            continue
        card = dict(s)
        hits = []
        for h in s.get("hits") or []:
            if not isinstance(h, dict):
                continue
            hit = dict(h)
            path = hit.get("path") or ""
            hit["viewable"] = bool(path) and MEM.is_memory(path) \
                and os.path.isfile(path)
            hits.append(hit)
        card["hits"] = hits
        out.append(card)
    return out


def get_note(sid, url):
    return note_payload(ext.qstr(url, "path"), ext.qstr(url, "stem"))
