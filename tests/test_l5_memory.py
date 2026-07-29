# L5 — the memory-wiki feature: the plugins/claude_code/memory.py vocabulary
# (is_memory / record merge+escalation / vault link resolution), the note
# markdown→HTML renderer (dashboard/notehtml.py), and the end-to-end capture
# through the real claude-file-fmt.py hook + the substream renderer.
#
# The memory root is hardcoded ~/wiki/01; the ONE seam is BAQYLAU_MEMORY_ROOT,
# which these tests point at a per-test tmp vault so nothing touches the real one.
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import payloads as P
from conftest import wait_until
from core import state as ST
from dashboard import notehtml as NH
from dashboard.read import mirror as RM
from plugins.claude_code import memory as MEM
from plugins.claude_code import substream_render as SR


# ---------------------------------------------------------------- is_memory

def test_is_memory_root_and_boundaries(tmp_path, monkeypatch):
    vault = tmp_path / "wiki" / "01"
    vault.mkdir(parents=True)
    monkeypatch.setenv("BAQYLAU_MEMORY_ROOT", str(vault))
    assert MEM.is_memory(str(vault / "providers" / "tiger" / "note.md"))
    assert MEM.is_memory(str(vault / "index.md"))
    assert not MEM.is_memory(str(tmp_path / "elsewhere" / "note.md"))
    assert not MEM.is_memory(str(vault))          # the bare root is not a note
    assert not MEM.is_memory("")
    # a sibling dir sharing the prefix string must NOT count (real path boundary)
    assert not MEM.is_memory(str(tmp_path / "wiki" / "01x" / "note.md"))


def test_in_scope_gates_to_the_project(tmp_path, monkeypatch):
    proj = tmp_path / "code" / "01" / "aggregator-adapters"
    proj.mkdir(parents=True)
    monkeypatch.setenv("BAQYLAU_MEMORY_PROJECT", str(proj))
    assert MEM.in_scope(str(proj))
    assert MEM.in_scope(str(proj / "adapters-api" / "src"))
    assert MEM.in_scope(str(proj / ".claude" / "worktrees" / "wt1"))  # a worktree is in scope
    assert not MEM.in_scope(str(tmp_path / "code" / "01" / "aggregator-services"))
    assert not MEM.in_scope(str(tmp_path / "elsewhere"))
    # a sibling sharing the prefix string must NOT count
    assert not MEM.in_scope(str(tmp_path / "code" / "01" / "aggregator-adapters-x"))


def test_rel_is_vault_relative_and_refuses_the_outside(tmp_path, monkeypatch):
    vault = tmp_path / "wiki" / "01"
    vault.mkdir(parents=True)
    monkeypatch.setenv("BAQYLAU_MEMORY_ROOT", str(vault))
    assert MEM.rel(str(vault / "providers" / "egt" / "egt.md")) == "providers/egt/egt.md"
    assert MEM.rel(str(vault / "index.md")) == "index.md"
    # not a note under the root → "" (the tree files it by basename instead)
    assert MEM.rel(str(tmp_path / "elsewhere" / "n.md")) == ""
    assert MEM.rel(str(vault)) == ""
    assert MEM.rel("") == ""


# ------------------------------------------------------------------ record

def test_record_merges_and_escalates_verb(tmp_path, monkeypatch):
    vault = tmp_path / "wiki" / "01"
    vault.mkdir(parents=True)
    monkeypatch.setenv("BAQYLAU_MEMORY_ROOT", str(vault))
    log = str(tmp_path / "claude-mirror-x.log")
    ST.kv_set(log, "boot", 1)                     # create the state DB (record is parked-guarded)
    note = str(vault / "providers" / "tiger" / "note.md")

    assert MEM.record(log, note, "Read", agent=None)          # recall
    assert MEM.record(log, note, "Write", agent="note-writer")  # escalates Read → Write
    MEM.record(log, note, "Read", agent="someone-else")       # must NOT downgrade

    files = (ST.kv_get(log, "memory") or {}).get("files")
    assert isinstance(files, list) and len(files) == 1
    rec = files[0]
    assert rec["path"] == note and rec["name"] == "note.md"
    assert rec["verb"] == "Write"                 # highest rank wins
    assert rec["agent"] == "note-writer"          # the escalating op's agent
    assert rec["count"] == 3                       # every touch counts

    # a second distinct note lands as its own row
    MEM.record(log, str(vault / "index.md"), "Update", agent=None)
    files = (ST.kv_get(log, "memory") or {}).get("files")
    assert {f["name"] for f in files} == {"note.md", "index.md"}


def test_record_ignores_non_memory_and_parked(tmp_path, monkeypatch):
    vault = tmp_path / "wiki" / "01"
    vault.mkdir(parents=True)
    monkeypatch.setenv("BAQYLAU_MEMORY_ROOT", str(vault))
    log = str(tmp_path / "claude-mirror-y.log")
    ST.kv_set(log, "boot", 1)
    # not under the root → no-op
    assert MEM.record(log, str(tmp_path / "code" / "x.py"), "Write") is None
    assert ST.kv_get(log, "memory") is None
    # parked (no DB) → no-op, and never CREATES the DB
    gone = str(tmp_path / "claude-mirror-gone.log")
    assert MEM.record(gone, str(vault / "n.md"), "Write") is None
    assert not os.path.exists(gone + ".state.db")


# ------------------------------------------------------- resolve / backlinks

def _vault_with_links(tmp_path, monkeypatch):
    vault = tmp_path / "wiki" / "01"
    (vault / "providers" / "tiger" / "concepts").mkdir(parents=True)
    (vault / "platform" / "concepts").mkdir(parents=True)
    note = vault / "providers" / "tiger" / "concepts" / "launch-oauth.md"
    note.write_text("# Launch\n\nSee [[traffic-proxy]] and [[missing-note]].\n")
    (vault / "platform" / "concepts" / "traffic-proxy.md").write_text(
        "---\ntitle: Traffic proxy\ntags: [net]\n---\n# Traffic proxy\n\n"
        "## Affects\n[[launch-oauth]]\n")
    monkeypatch.setenv("BAQYLAU_MEMORY_ROOT", str(vault))
    MEM._INDEX.clear()                            # drop any TTL-cached index from another test
    return vault, note


def test_resolve_and_backlinks(tmp_path, monkeypatch):
    vault, note = _vault_with_links(tmp_path, monkeypatch)
    assert MEM.resolve("traffic-proxy") == str(vault / "platform" / "concepts" / "traffic-proxy.md")
    assert MEM.resolve("launch-oauth") == str(note)
    assert MEM.resolve("missing-note") is None    # dangling link resolves to nothing
    # launch-oauth links to traffic-proxy, so traffic-proxy has it as a backlink
    assert "launch-oauth" in MEM.backlinks(str(vault / "platform" / "concepts" / "traffic-proxy.md"))
    assert "traffic-proxy" in MEM.backlinks(str(note))


def test_read_note_parses_frontmatter_and_guards_traversal(tmp_path, monkeypatch):
    vault, note = _vault_with_links(tmp_path, monkeypatch)
    fm, body = MEM.read_note(str(vault / "platform" / "concepts" / "traffic-proxy.md"))
    assert fm.get("title") == "Traffic proxy" and fm.get("tags") == "[net]"
    assert "# Traffic proxy" in body and "## Affects" in body
    # a path OUTSIDE the root is refused (path-traversal guard)
    assert MEM.read_note("/etc/passwd") == (None, None)
    assert MEM.read_note(str(tmp_path / "outside.md")) == (None, None)


# ------------------------------------------------------------- memory_tree

def _touch(vault, rel, verb="Read", **extra):
    """One `memory` kv record for a note at `rel` under `vault`."""
    rec = {"path": str(vault / rel), "name": os.path.basename(rel),
           "verb": verb, "agent": None, "count": 1, "ts": 1.0}
    rec.update(extra)
    return rec


def _dirnames(node):
    return [d["name"] for d in node["dirs"]]


def _labels(node):
    return [n["label"] for n in node["notes"]]


def _find(node, name):
    return next(d for d in node["dirs"] if d["name"] == name)


def _vault(tmp_path, monkeypatch):
    vault = tmp_path / "wiki" / "01"
    vault.mkdir(parents=True)
    monkeypatch.setenv("BAQYLAU_MEMORY_ROOT", str(vault))
    return vault


def test_memory_tree_keeps_sibling_folders_as_their_own_rows(tmp_path, monkeypatch):
    """The question the tab exists to answer is "did we work on platform, or on
    providers — and WHICH providers", so a folder with siblings always keeps its
    row, however few notes hang off it."""
    vault = _vault(tmp_path, monkeypatch)
    tree = RM.memory_tree([
        _touch(vault, "providers/egt/egt.md", "Write"),
        _touch(vault, "providers/hacksaw/hacksaw.md"),
        _touch(vault, "providers/quadcode/quadcode.md", "Update"),
        _touch(vault, "tooling/tooling.md"),
    ])
    assert _dirnames(tree) == ["providers", "tooling"]      # alphabetical, folders first
    prov = _find(tree, "providers")
    assert _dirnames(prov) == ["egt", "hacksaw", "quadcode"]
    assert _labels(_find(prov, "hacksaw")) == ["hacksaw.md"]


def test_memory_tree_compresses_a_linear_chain_into_one_row(tmp_path, monkeypatch):
    """A folder that is not a FORK earns no row of its own: `platform` holding
    only `concepts` is one `platform/concepts` row, and the five-level slack
    path collapses to one instead of spending four indents on one note."""
    vault = _vault(tmp_path, monkeypatch)
    tree = RM.memory_tree([
        _touch(vault, "platform/concepts/architecture.md", "Update"),
        _touch(vault, "platform/concepts/networking.md"),
        _touch(vault, "slack/channels/vegas-adapters/threads/egt-acl.md"),
    ])
    assert _dirnames(tree) == ["platform/concepts",
                               "slack/channels/vegas-adapters/threads"]
    plat = _find(tree, "platform/concepts")
    assert plat["path"] == "platform/concepts"              # the collapse key is the DEEPEST dir
    assert _labels(plat) == ["architecture.md", "networking.md"]
    assert _labels(_find(tree, "slack/channels/vegas-adapters/threads")) == ["egt-acl.md"]


def test_memory_tree_folds_a_lone_leaf_folder_into_note_labels(tmp_path, monkeypatch):
    """`providers/egt` has notes of its OWN (the folder note) plus a lone
    `concepts/`, so the chain rule can't apply — the leaf folds into the note
    LABELS instead, which is the same "no row for a non-fork" rule paid in a
    path prefix."""
    vault = _vault(tmp_path, monkeypatch)
    tree = RM.memory_tree([
        _touch(vault, "providers/egt/egt.md", "Update"),
        _touch(vault, "providers/egt/concepts/wildcard-acl.md", "Write"),
        _touch(vault, "providers/hacksaw/hacksaw.md"),
    ])
    egt = _find(_find(tree, "providers"), "egt")
    assert _dirnames(egt) == []
    assert _labels(egt) == ["concepts/wildcard-acl.md", "egt.md"]
    # a leaf folder with a SIBLING folder still keeps its row (a real fork)
    tree = RM.memory_tree([
        _touch(vault, "providers/egt/egt.md"),
        _touch(vault, "providers/egt/concepts/a.md"),
        _touch(vault, "providers/egt/threads/b.md"),
    ])
    egt = _find(tree, "providers/egt")
    assert _dirnames(egt) == ["concepts", "threads"] and _labels(egt) == ["egt.md"]


def test_memory_tree_root_is_never_compressed(tmp_path, monkeypatch):
    """Folding the root would leave the scope rows with no header at all — and a
    vault-root note (index.md) hangs on the root itself."""
    vault = _vault(tmp_path, monkeypatch)
    tree = RM.memory_tree([_touch(vault, "platform/concepts/architecture.md")])
    assert tree["name"] == "" and _dirnames(tree) == ["platform/concepts"]
    tree = RM.memory_tree([
        _touch(vault, "index.md"),
        _touch(vault, "platform/concepts/architecture.md"),
    ])
    assert _labels(tree) == ["index.md"] and _dirnames(tree) == ["platform/concepts"]


def test_memory_tree_rolls_up_counts_and_writes(tmp_path, monkeypatch):
    """Every row carries its SUBTREE's totals, so a collapsed folder still says
    how much is under it. `writes` counts the notes we CHANGED — Write (created)
    and Update (revised) alike; a Read is recall, not work on the note."""
    vault = _vault(tmp_path, monkeypatch)
    tree = RM.memory_tree([
        _touch(vault, "providers/egt/egt.md", "Write"),
        _touch(vault, "providers/egt/concepts/a.md", "Update"),
        _touch(vault, "providers/hacksaw/hacksaw.md", "Read", count=3),
        _touch(vault, "index.md"),
    ])
    assert (tree["count"], tree["writes"]) == (4, 2)
    prov = _find(tree, "providers")
    assert (prov["count"], prov["writes"]) == (3, 2)
    assert (_find(prov, "egt")["count"], _find(prov, "egt")["writes"]) == (2, 2)
    assert (_find(prov, "hacksaw")["count"], _find(prov, "hacksaw")["writes"]) == (1, 0)


def test_memory_tree_keeps_the_record_fields_the_rows_render(tmp_path, monkeypatch):
    vault = _vault(tmp_path, monkeypatch)
    tree = RM.memory_tree([
        _touch(vault, "tooling/concepts/esql.md", "Write", agent="note-writer", count=2)])
    note = _find(tree, "tooling/concepts")["notes"][0]
    assert note["verb"] == "Write" and note["agent"] == "note-writer"
    assert note["count"] == 2 and note["label"] == "esql.md"
    assert note["path"] == str(vault / "tooling" / "concepts" / "esql.md")


def test_memory_tree_tolerates_junk_records(tmp_path, monkeypatch):
    """The kv is durable across park, so it can hold a row written under an
    older root. It keeps its basename at the top level rather than vanishing."""
    vault = _vault(tmp_path, monkeypatch)
    tree = RM.memory_tree([
        {"path": "/somewhere/else/stray.md", "name": "stray.md", "verb": "Read"},
        "not-a-dict", None,
        _touch(vault, "platform/concepts/a.md"),
    ])
    assert _labels(tree) == ["stray.md"] and tree["count"] == 2
    assert RM.memory_tree([])["count"] == 0
    assert RM.memory_tree(None)["dirs"] == []


# --------------------------------------------------------------- notehtml

def test_note_html_linkifies_wikilinks_and_marks_dead():
    html = NH.note_html(
        "See [[real-note]] and [[missing|the alias]] here.",
        resolve=lambda stem: "/p/real-note.md" if stem == "real-note" else None)
    assert 'data-note="real-note"' in html
    assert ">real-note<" in html                  # bare stem is the label
    assert 'data-note="missing"' in html
    assert ">the alias<" in html                  # the |alias is the label
    assert "wl dead" in html                       # the unresolvable link is marked dead


def test_note_html_escapes_and_survives_underscores():
    # a stem with underscores must not be chewed by markdown emphasis, and raw
    # HTML in the body must be escaped (escape-first, never raw to the page)
    html = NH.note_html("[[cloud_shared_config]] <script>x</script>",
                        resolve=lambda stem: "/x")
    assert 'data-note="cloud_shared_config"' in html
    assert "<script>" not in html and "&lt;script&gt;" in html


def test_frontmatter_rows_escape():
    rows = NH.frontmatter_rows({"title": "a <b>", "tags": "[x]"})
    assert ("title", "a &lt;b&gt;") in rows
    assert ("tags", "[x]") in rows


# ------------------------------------------------ end-to-end: the real hook

def _mem_env(test_env, vault, project):
    # BAQYLAU_MEMORY_PROJECT is the scope seam — the session's cwd is s.cwd, so an
    # in-scope test points it at s.cwd; an out-of-scope test points it elsewhere.
    return dict(test_env, BAQYLAU_MEMORY_ROOT=str(vault),
                BAQYLAU_MEMORY_PROJECT=str(project))


def test_file_fmt_marks_and_records_a_memory_op(run_hook, test_env, session, tmp_path):
    """The real claude-file-fmt.py hook: a Write under the vault, from an IN-SCOPE
    session, paints the ❖ marker AND stashes the note into the `memory` kv."""
    s = session.make()
    vault = tmp_path / "wiki" / "01"
    (vault / "platform" / "concepts").mkdir(parents=True)
    note = str(vault / "platform" / "concepts" / "arch.md")
    run_hook("claude-file-fmt.py",
             P.post_file(s, tool="Write", path=note, tid="toolu_mem1"),
             env=_mem_env(test_env, vault, s.cwd))
    wait_until(lambda: (ST.kv_get(s.log, "memory") or {}).get("files"),
               desc="memory kv populated")
    files = (ST.kv_get(s.log, "memory") or {}).get("files")
    assert files and files[0]["path"] == note and files[0]["verb"] == "Write"
    assert files[0]["agent"] is None                  # main agent
    assert MEM.MARK in s.ops_text()                   # ❖ in the mirror one-liner
    # the emitted line op carries the web-filter mem tag
    assert any(op.get("mem") for op in s.ops() if op.get("t") == "line")


def test_file_fmt_scope_gate_off_project(run_hook, test_env, session, tmp_path):
    """The SAME wiki write from a session OUTSIDE the enabled project is a plain
    file op — no ❖ marker, no `memory` kv (the feature is scoped)."""
    s = session.make()
    vault = tmp_path / "wiki" / "01"
    (vault / "platform").mkdir(parents=True)
    note = str(vault / "platform" / "arch.md")
    other_project = str(tmp_path / "some" / "other" / "project")
    run_hook("claude-file-fmt.py",
             P.post_file(s, tool="Write", path=note, tid="toolu_oos1"),
             env=_mem_env(test_env, vault, other_project))
    wait_until(lambda: s.ops(), desc="op emitted")
    assert MEM.MARK not in s.ops_text()
    assert not any(op.get("mem") for op in s.ops())
    assert ST.kv_get(s.log, "memory") is None


def test_file_fmt_leaves_non_memory_ops_untouched(run_hook, test_env, session, tmp_path):
    s = session.make()
    vault = tmp_path / "wiki" / "01"
    vault.mkdir(parents=True)
    run_hook("claude-file-fmt.py",
             P.post_file(s, tool="Edit", path=os.path.join(s.cwd, "app.py"),
                         tid="toolu_code1"),
             env=_mem_env(test_env, vault, s.cwd))
    wait_until(lambda: s.ops(), desc="op emitted")
    assert MEM.MARK not in s.ops_text()
    assert not any(op.get("mem") for op in s.ops())
    assert ST.kv_get(s.log, "memory") is None


# ---------------------------------------------- subagent capture (substream)

def test_substream_render_file_records_under_the_subagent(tmp_path, monkeypatch):
    """A subagent's memory write lands in the SAME kv, stamped with its name —
    the team-wide capture the main-agent-only mirror can't provide."""
    vault = tmp_path / "wiki" / "01"
    (vault / "providers").mkdir(parents=True)
    monkeypatch.setenv("BAQYLAU_MEMORY_ROOT", str(vault))
    # the substream tailer's cwd is the session dir; in this in-process test that
    # is the pytest cwd, so point the scope seam at it (in_scope() over getcwd())
    monkeypatch.setenv("BAQYLAU_MEMORY_PROJECT", os.getcwd())
    log = str(tmp_path / "claude-mirror-sub.log")
    ST.kv_set(log, "boot", 1)
    r = SR.Renderer(
        log=log, agent="note-writer", label="note-writer", rgb=(1, 2, 3),
        sub_fg=False, op_tag=lambda: "", ctx_tag=lambda: "",
        take_subfg=lambda tid: None,
        spawn_fg_tailer=lambda tid, rec, cmd="": None,
        spawn_tailer=lambda kind, taskid, cmd="", group=None: None)
    note = str(vault / "providers" / "note.md")
    r.render_file("Write", {"file_path": note, "content": "hi"},
                  result={}, tid="tsub1")
    files = (ST.kv_get(log, "memory") or {}).get("files")
    assert files and files[0]["path"] == note
    assert files[0]["verb"] == "Write" and files[0]["agent"] == "note-writer"
