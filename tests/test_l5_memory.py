# L5 — the memory-wiki feature: the plugins/claude_code/memory.py vocabulary
# (is_memory / record merge+escalation / vault link resolution), the note
# markdown→HTML renderer (dashboard/ext/memory/notehtml.py), the BASH plane
# (plugins/claude_code/memcmd.py — vault notes read through the shell, and the qmd
# searches a session ran), and the end-to-end capture through the real
# claude-file-fmt.py / claude-cmd-fmt.py hooks + the substream renderer.
#
# The memory root is hardcoded ~/wiki/01; the ONE seam is BAQYLAU_MEMORY_ROOT,
# which these tests point at a per-test tmp vault so nothing touches the real one.
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import payloads as P
from conftest import wait_until
from core import sessionapi as API
from core import state as ST
from dashboard.ext.memory import notehtml as NH
from dashboard.ext.memory import read as RM
from plugins.claude_code import memcmd as MC
from plugins.claude_code import memory as MEM
from plugins.claude_code import tools as CT
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
    _clear_index()
    return vault, note


def _clear_index():
    """Drop both TTL-cached vault indexes from another test (names + backlinks)."""
    MEM._INDEX.clear()
    MEM._LINKS.clear()


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


# ============================================================================
# THE BASH PLANE (plugins/claude_code/memcmd.py)
#
# Every command below is a VERBATIM shape from session d8dc5a67 (2026-07-30),
# which read ten vault notes and ran two searches and recorded NOT ONE of them:
# memory detection was wired only to the Read/Write/Edit tools, and this session
# did all of its recall through the shell. These tests are that session's
# regression suite.
# ============================================================================

def _vault_notes(tmp_path, monkeypatch, *rels):
    """A tmp vault holding `rels` (vault-relative note paths), pointed at by the
    BAQYLAU_MEMORY_ROOT seam. Returns (vault, {rel: abs})."""
    vault = tmp_path / "wiki" / "01"
    made = {}
    for rel in rels:
        p = vault / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# %s\n" % rel)
        made[rel] = str(p)
    vault.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("BAQYLAU_MEMORY_ROOT", str(vault))
    _clear_index()
    return vault, made


def test_plan_finds_a_note_read_behind_a_cd_and_a_truncation_pipe(tmp_path, monkeypatch):
    """`cd ~/wiki/01 && cat platform/concepts/observability.md 2>/dev/null | head -60`
    — the single most common shape, and the one that needs BOTH the per-statement
    cwd tracking and the ~ expansion (tools.statement_cwds(tilde=True))."""
    vault, notes = _vault_notes(tmp_path, monkeypatch,
                                "platform/concepts/observability.md")
    cmd = ("cd %s && cat platform/concepts/observability.md 2>/dev/null | head -60"
           % vault)
    got, searches = MC.plan(cmd, str(tmp_path / "elsewhere"))
    assert got == (notes["platform/concepts/observability.md"],)
    assert searches == ()


def test_plan_finds_every_file_of_a_multi_file_cat(tmp_path, monkeypatch):
    """`cat a.md b.md c.md` — tools.read_command names only ONE file (it is
    choosing a single lexer); this plane must record all three."""
    vault, notes = _vault_notes(
        tmp_path, monkeypatch,
        "platform/concepts/rscheck-healthcheck.md",
        "platform/concepts/rscheck-enablement-01conf-scope.md",
        "platform/concepts/cloud-boot-window-health-inc.md")
    cmd = ("cd %s && cat platform/concepts/rscheck-healthcheck.md "
           "platform/concepts/rscheck-enablement-01conf-scope.md "
           "platform/concepts/cloud-boot-window-health-inc.md" % vault)
    got, _ = MC.plan(cmd, str(tmp_path))
    assert set(got) == set(notes.values()) and len(got) == 3


def test_plan_resolves_a_find_exec_cat_by_bare_basename(tmp_path, monkeypatch):
    r"""`find . -name "x.md" -exec cat {} \;` names the note WITHOUT its path, and
    `find` is not a reader — the reader is the `-exec cat` deep in the token list.
    Resolved through the vault's own name index, and only because the statement
    runs INSIDE the vault."""
    vault, notes = _vault_notes(
        tmp_path, monkeypatch,
        "slack/channels/inc-auto-root/threads/inc-47877-pgsoft.md")
    cmd = (r'cd %s && find . -name "inc-47877-pgsoft.md" -exec cat {} \; | head -60'
           % vault)
    got, _ = MC.plan(cmd, str(tmp_path))
    assert got == (notes["slack/channels/inc-auto-root/threads/inc-47877-pgsoft.md"],)


def test_plan_refuses_a_bare_basename_from_outside_the_vault(tmp_path, monkeypatch):
    """The same note name read in a REPO is that repo's file, not the vault's — the
    bare-basename fallback is gated on the statement running inside the vault."""
    _vault_notes(tmp_path, monkeypatch, "README.md")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# repo\n")
    got, _ = MC.plan("cat README.md", str(repo))
    assert got == ()


def test_plan_ignores_a_search_pattern_and_a_flag(tmp_path, monkeypatch):
    """`grep -ril pat ~/wiki/01/ --include=*.md` reads no single note: the vault
    root is a DIRECTORY and `--include=*.md` is a flag, not a file. (It was
    previously mis-rendered as `Read(--include=*.md)` — see the tools.py fix.)"""
    vault, _ = _vault_notes(tmp_path, monkeypatch, "platform/x.md")
    got, searches = MC.plan(
        'grep -ril "6988" %s/ --include=*.md | head -30' % vault, str(tmp_path))
    assert got == () and searches == ()


def test_plan_needs_a_reader_in_the_statement(tmp_path, monkeypatch):
    """Naming a note is not reading it: the statement must run something that
    prints file contents (the feature is scoped to READS)."""
    vault, notes = _vault_notes(tmp_path, monkeypatch, "platform/x.md")
    for cmd in ("ls -l platform/x.md", "rm platform/x.md", "git add platform/x.md"):
        got, _ = MC.plan("cd %s && %s" % (vault, cmd), str(tmp_path))
        assert got == (), cmd
    got, _ = MC.plan("cd %s && sed -n 1,5p platform/x.md" % vault, str(tmp_path))
    assert got == (notes["platform/x.md"],)


def test_plan_unwraps_the_live_fg_tee_rewrite(tmp_path, monkeypatch):
    """THE load-bearing one. claude-cmd-pre.py rewrites a foreground command so it
    tees into a side file, and PostToolUse — where the recording half runs — hands
    back the WRAPPED text. `{ cd ~/wiki/01 && cat …` tokenises with `{` as the first
    word, which makes the static cd tracking refuse the statement and every relative
    path under it unresolvable. Replayed against the real session this plane was
    written for, the un-unwrapped parser found 2 of 10 reads and 0 of 2 searches —
    and the two survivors were only the commands cmd_pre had declined to rewrite."""
    vault, notes = _vault_notes(tmp_path, monkeypatch,
                                "platform/concepts/observability.md")
    raw = ("cd %s && cat platform/concepts/observability.md 2>/dev/null | head -60"
           % vault)
    wrapped = CT.tee_wrap(raw, "/tmp/claude-mirror-x.log.fg.123.456.out")
    assert CT.unwrap_tee(wrapped) == raw          # the pair round-trips exactly
    got, _ = MC.plan(wrapped, str(tmp_path))
    assert got == (notes["platform/concepts/observability.md"],)
    # …and an unwrapped command (every PreToolUse payload) is untouched, so the
    # MARK side and the RECORD side see the same thing
    assert MC.plan(raw, str(tmp_path))[0] == got
    # a command that merely starts with `{` is not this wrapper
    assert CT.unwrap_tee("{ echo hi; }") == "{ echo hi; }"


def test_plan_finds_a_qmd_search_even_under_an_untrackable_cd(tmp_path, monkeypatch):
    """A search is cwd-INDEPENDENT — qmd addresses the vault by collection, not by
    where the shell stands — so an unplaceable statement must still yield its query
    (only its relative NOTE paths are lost). The tee wrapper made every live-streamed
    command look exactly like this."""
    _vault_notes(tmp_path, monkeypatch, "platform/x.md")
    got, searches = MC.plan('cd "$D" && qmd search "healthcheck"', str(tmp_path))
    assert got == () and searches == (("qmd", "search", "healthcheck"),)


def test_plan_skips_a_statement_under_an_untrackable_cd(tmp_path, monkeypatch):
    """A `cd "$DIR"` makes the following statements' relative paths unknowable —
    skipped rather than resolved against the wrong base."""
    vault, _ = _vault_notes(tmp_path, monkeypatch, "platform/x.md")
    got, _ = MC.plan('cd "$DIR" && cat platform/x.md', str(vault))
    assert got == ()


def test_plan_reads_qmd_get_relative_to_the_VAULT_not_the_cwd(tmp_path, monkeypatch):
    """`qmd get` addresses a document by its COLLECTION-relative path (and takes a
    :from:count window / a qmd:// URL), none of which is relative to the shell."""
    vault, notes = _vault_notes(tmp_path, monkeypatch, "platform/concepts/deploy.md")
    want = (notes["platform/concepts/deploy.md"],)
    for cmd in ("qmd get platform/concepts/deploy.md",
                "qmd get platform/concepts/deploy.md:10:40",
                "qmd get qmd://wiki01/platform/concepts/deploy.md"):
        got, searches = MC.plan(cmd, str(tmp_path / "anywhere"))
        assert got == want and searches == (), cmd


def test_plan_reads_a_qmd_search_out_of_a_multi_statement_command(tmp_path, monkeypatch):
    """The verbatim shape: a grep, an echo banner, then the search — the query must
    survive the statement split AND stop at the shell operators after it."""
    vault, _ = _vault_notes(tmp_path, monkeypatch, "platform/x.md")
    cmd = ('cd %s && grep -ril "started:" . | head -20; echo "=== qmd ==="; '
           'qmd search "manifest started healthcheck" 2>/dev/null | head -40' % vault)
    got, searches = MC.plan(cmd, str(tmp_path))
    assert got == ()
    assert searches == (("qmd", "search", "manifest started healthcheck"),)


def test_plan_takes_an_unquoted_query_and_ignores_index_maintenance(tmp_path, monkeypatch):
    _vault_notes(tmp_path, monkeypatch, "platform/x.md")
    _, s = MC.plan("qmd query how does rscheck answer getstatus", str(tmp_path))
    assert s == (("qmd", "query", "how does rscheck answer getstatus"),)
    for cmd in ("qmd status", "qmd update --pull", "qmd collection list", "qmd mcp"):
        got, s = MC.plan(cmd, str(tmp_path))
        assert got == () and s == (), cmd


def test_touches_is_the_same_predicate_as_the_record(tmp_path, monkeypatch):
    """The mirror MARK and the kv record must never disagree: a marked block always
    has a record behind it, and an unmarked one never does."""
    vault, _ = _vault_notes(tmp_path, monkeypatch, "platform/x.md")
    for cmd, want in [("cd %s && cat platform/x.md" % vault, True),
                      ('qmd query "how"', True),
                      ("ls -l", False),
                      ("", False)]:
        assert MC.touches(cmd, str(tmp_path)) is want, cmd


# ------------------------------------------------------- the qmd output parser

_QMD_OUT = """Expanding query... (4.1s)
├─ how does rscheck answer getstatus
├─ lex: how rscheck returns
├─ vec: accessing the getstatus method
└─ hyde: The process of how rscheck answers involves several ste...
Searching 6 queries...
Reranking 16 chunks... (10.3s)
qmd://wiki01/platform/concepts/rscheck-healthcheck.md:13 #000b85
Title: rscheck — what answers `/getstatus:81` on a cloud service
Context: 01 platform memory wiki: distilled notes …
Score:  86%

@@ -12,4 @@ (11 before, 54 after)
Documented in the internal docs repo at `l4-lb.md`
("Healthchecks (rscheck)", [[01docs]]).

qmd://wiki01/platform/concepts/envoy-l7-lb.md:53 #01c7b8
Title: Envoy L7 LB — how a cloud adapter gets a public entrypoint
Score:  84%

@@ -52,4 @@ (51 before, 76 after)
  what filters it to live pods.
"""


def test_qmd_hits_parses_rank_title_score_and_passage(tmp_path, monkeypatch):
    _vault_notes(tmp_path, monkeypatch,
                 "platform/concepts/rscheck-healthcheck.md",
                 "platform/concepts/envoy-l7-lb.md")
    hits, expanded = MC.qmd_hits(_QMD_OUT)
    assert [h["rel"] for h in hits] == ["platform/concepts/rscheck-healthcheck.md",
                                        "platform/concepts/envoy-l7-lb.md"]
    assert hits[0]["name"] == "rscheck-healthcheck"
    assert hits[0]["line"] == 13 and hits[0]["score"] == "86%"
    assert hits[0]["title"].startswith("rscheck —")
    assert "Healthchecks (rscheck)" in hits[0]["snippet"]
    assert "Context:" not in hits[0]["snippet"]      # metadata is not the passage
    assert hits[0]["path"].endswith("platform/concepts/rscheck-healthcheck.md")
    # only the TYPED expansion lines — the tree's first row is the query verbatim
    assert expanded == ["lex: how rscheck returns",
                        "vec: accessing the getstatus method",
                        "hyde: The process of how rscheck answers involves several ste..."]


def test_qmd_hits_survives_a_truncated_tail(tmp_path, monkeypatch):
    """`| head -40` is the idiomatic way to run these, so the last block routinely
    arrives half-written — kept, with whatever fields made it."""
    _vault_notes(tmp_path, monkeypatch, "platform/concepts/rscheck-healthcheck.md")
    cut = _QMD_OUT[:_QMD_OUT.index("Score:  84%")]
    hits, _ = MC.qmd_hits(cut)
    assert len(hits) == 2
    assert hits[1]["title"].startswith("Envoy L7 LB") and hits[1]["snippet"] == ""


def test_qmd_hits_ignores_a_url_quoted_inside_a_passage(tmp_path, monkeypatch):
    _vault_notes(tmp_path, monkeypatch, "platform/x.md")
    out = ("qmd://wiki01/platform/x.md:1 #aa11bb\nScore:  50%\n\n@@ -1,4 @@ (0, 0)\n"
           "see qmd://wiki01/platform/other.md for the rest\n")
    hits, _ = MC.qmd_hits(out)
    assert len(hits) == 1 and "qmd://wiki01/platform/other.md" in hits[0]["snippet"]


def test_qmd_hits_keeps_a_hit_whose_note_is_gone(tmp_path, monkeypatch):
    """qmd's index outlives a renamed/deleted note — the ANSWER is still what the
    session was told, so the row stays (with no path, hence not clickable)."""
    _vault_notes(tmp_path, monkeypatch, "platform/x.md")
    hits, _ = MC.qmd_hits("qmd://wiki01/platform/vanished.md:3 #ff00aa\nScore:  10%\n")
    assert len(hits) == 1 and hits[0]["path"] == "" and hits[0]["name"] == "vanished"


# ------------------------------------------------------ record_search (the kv)

def test_record_search_stores_the_question_and_its_answer(tmp_path, monkeypatch):
    _vault_notes(tmp_path, monkeypatch, "platform/concepts/rscheck-healthcheck.md")
    log = str(tmp_path / "claude-mirror-s.log")
    ST.kv_set(log, "boot", 1)
    hits, expanded = MC.qmd_hits(_QMD_OUT)
    assert MEM.record_search(log, "qmd", "query", "how does rscheck answer",
                             hits, cmd="qmd query …", expanded=expanded)
    got = (ST.kv_get(log, "memory") or {}).get("searches")
    assert len(got) == 1
    rec = got[0]
    assert rec["kind"] == "qmd" and rec["sub"] == "query"
    assert rec["query"] == "how does rscheck answer" and rec["count"] == 1
    assert len(rec["hits"]) == 2 and rec["expanded"] == expanded
    assert rec["agent"] is None


def test_record_search_dedups_a_rerun_and_refreshes_its_hits(tmp_path, monkeypatch):
    _vault_notes(tmp_path, monkeypatch, "platform/x.md")
    log = str(tmp_path / "claude-mirror-s2.log")
    ST.kv_set(log, "boot", 1)
    MEM.record_search(log, "qmd", "search", "healthcheck", [{"rel": "a.md"}])
    MEM.record_search(log, "qmd", "search", "healthcheck",
                      [{"rel": "b.md"}, {"rel": "c.md"}])
    MEM.record_search(log, "qmd", "vsearch", "healthcheck", [])   # a DIFFERENT sub
    got = (ST.kv_get(log, "memory") or {}).get("searches")
    assert len(got) == 2
    rerun = next(s for s in got if s["sub"] == "search")
    assert rerun["count"] == 2
    assert [h["rel"] for h in rerun["hits"]] == ["b.md", "c.md"]   # freshest answer wins


def test_record_search_caps_the_list_dropping_the_oldest(tmp_path, monkeypatch):
    _vault_notes(tmp_path, monkeypatch, "platform/x.md")
    log = str(tmp_path / "claude-mirror-s3.log")
    ST.kv_set(log, "boot", 1)
    for i in range(MEM.SEARCH_MAX + 5):
        MEM.record_search(log, "qmd", "search", "q%03d" % i, [])
    got = (ST.kv_get(log, "memory") or {}).get("searches")
    assert len(got) == MEM.SEARCH_MAX
    assert got[0]["query"] == "q%03d" % (MEM.SEARCH_MAX + 4)       # newest first
    assert "q000" not in [s["query"] for s in got]                 # oldest dropped


def test_record_search_and_record_never_clobber_each_other(tmp_path, monkeypatch):
    """The kv's one real hazard: both halves live under one key, so a writer that
    rebuilt the object from its OWN list would erase the other's."""
    vault, notes = _vault_notes(tmp_path, monkeypatch, "platform/x.md")
    log = str(tmp_path / "claude-mirror-s4.log")
    ST.kv_set(log, "boot", 1)
    MEM.record_search(log, "qmd", "query", "q1", [])
    MEM.record(log, notes["platform/x.md"], "Read")
    MEM.record_search(log, "qmd", "query", "q2", [])
    MEM.record(log, notes["platform/x.md"], "Write")
    stash = ST.kv_get(log, "memory") or {}
    assert len(stash.get("searches") or []) == 2
    assert len(stash.get("files") or []) == 1
    assert stash["files"][0]["verb"] == "Write"


def test_record_search_needs_a_query(tmp_path, monkeypatch):
    _vault_notes(tmp_path, monkeypatch, "platform/x.md")
    log = str(tmp_path / "claude-mirror-s5.log")
    ST.kv_set(log, "boot", 1)
    assert MEM.record_search(log, "qmd", "query", "   ", []) is None
    assert ST.kv_get(log, "memory") is None


# --------------------------------------------- memcmd.record (both halves)

def test_memcmd_record_files_notes_and_searches(tmp_path, monkeypatch):
    vault, notes = _vault_notes(tmp_path, monkeypatch,
                                "platform/concepts/rscheck-healthcheck.md",
                                "platform/concepts/envoy-l7-lb.md")
    log = str(tmp_path / "claude-mirror-r.log")
    ST.kv_set(log, "boot", 1)
    frags = MC.record(log, 'cd %s && cat platform/concepts/envoy-l7-lb.md' % vault,
                      str(tmp_path), "")
    assert frags and "envoy-l7-lb.md" in frags[0]
    frags = MC.record(log, 'qmd query "how does rscheck answer"', str(tmp_path),
                      _QMD_OUT)
    assert frags and "2 hits" in frags[0]
    stash = ST.kv_get(log, "memory") or {}
    # a search HIT is not a note the session read — only the cat is in `files`
    assert [f["name"] for f in stash["files"]] == ["envoy-l7-lb.md"]
    assert len(stash["searches"][0]["hits"]) == 2


def test_memcmd_record_refuses_to_file_one_querys_answer_under_another(tmp_path, monkeypatch):
    """Two searches in one command: qmd's output says nothing about which block
    belongs to which, so both queries are recorded WITHOUT hits rather than one
    being given the other's answer."""
    _vault_notes(tmp_path, monkeypatch, "platform/concepts/rscheck-healthcheck.md")
    log = str(tmp_path / "claude-mirror-r2.log")
    ST.kv_set(log, "boot", 1)
    MC.record(log, 'qmd search "a"; qmd search "b"', str(tmp_path), _QMD_OUT)
    got = (ST.kv_get(log, "memory") or {}).get("searches")
    assert sorted(s["query"] for s in got) == ["a", "b"]
    assert all(s["hits"] == [] for s in got)


# ------------------------------------------------------- sessionapi read side

def test_sessionapi_serves_both_halves_and_counts_them(tmp_path, monkeypatch):
    from core import paths as PATHS
    vault, notes = _vault_notes(tmp_path, monkeypatch, "platform/x.md")
    sid = "sid-mem-api"
    log = PATHS.log_for_key(sid)
    monkeypatch.setattr(API, "state_db_for", lambda s: ST.db_path(log))
    ST.kv_set(log, "boot", 1)
    try:
        MEM.record(log, notes["platform/x.md"], "Read")
        MEM.record_search(log, "qmd", "query", "q1", [{"rel": "a.md"}])
        assert [n["name"] for n in API.memory(sid)] == ["x.md"]
        assert [s["query"] for s in API.memory_searches(sid)] == ["q1"]
        # the badge counts BOTH — a session that only SEARCHED must not read as 0
        assert API.memory_count(sid) == 2
    finally:
        for p in (ST.db_path(log), ST.db_path(log) + "-wal", ST.db_path(log) + "-shm"):
            if os.path.exists(p):
                os.remove(p)


def test_search_cards_mark_a_hit_openable_only_when_the_note_exists(tmp_path, monkeypatch):
    vault, notes = _vault_notes(tmp_path, monkeypatch, "platform/x.md")
    cards = RM.search_cards([{"query": "q", "hits": [
        {"rel": "platform/x.md", "path": notes["platform/x.md"]},
        {"rel": "platform/gone.md", "path": str(vault / "platform" / "gone.md")},
        {"rel": "outside.md", "path": str(tmp_path / "outside.md")},
    ]}])
    assert [h["viewable"] for h in cards[0]["hits"]] == [True, False, False]


# --------------------------------------------------- end-to-end (the real hooks)

def test_cmd_fmt_marks_and_records_a_bash_note_read(run_hook, test_env, session, tmp_path):
    """The regression this whole plane exists for: the real claude-cmd-fmt.py hook
    over `cd <vault> && cat <note>` stashes the note AND marks the mirror block."""
    s = session.make()
    vault = tmp_path / "wiki" / "01"
    (vault / "platform" / "concepts").mkdir(parents=True)
    note = vault / "platform" / "concepts" / "observability.md"
    note.write_text("# obs\n")
    cmd = "cd %s && cat platform/concepts/observability.md | head -60" % vault
    run_hook("claude-cmd-fmt.py",
             P.post_bash(s, cmd, tid="toolu_bm1", stdout="# obs\n"),
             env=_mem_env(test_env, vault, s.cwd))
    wait_until(lambda: (ST.kv_get(s.log, "memory") or {}).get("files"),
               desc="memory kv populated from a Bash read")
    files = (ST.kv_get(s.log, "memory") or {}).get("files")
    assert files[0]["path"] == str(note) and files[0]["verb"] == "Read"
    assert MEM.MARK in s.ops_text()                    # ❖ on the block header
    assert any(op.get("mem") for op in s.ops() if op.get("t") == "label")


def test_cmd_fmt_marks_and_records_a_qmd_search(run_hook, test_env, session, tmp_path):
    """A `qmd query` reads no file at all — its record is the QUESTION plus the
    ranked answer parsed out of the command's own output."""
    s = session.make()
    vault = tmp_path / "wiki" / "01"
    (vault / "platform" / "concepts").mkdir(parents=True)
    for n in ("rscheck-healthcheck.md", "envoy-l7-lb.md"):
        (vault / "platform" / "concepts" / n).write_text("# %s\n" % n)
    run_hook("claude-cmd-fmt.py",
             P.post_bash(s, 'qmd query "how does rscheck answer getstatus"',
                         tid="toolu_bq1", stdout=_QMD_OUT),
             env=_mem_env(test_env, vault, s.cwd))
    wait_until(lambda: (ST.kv_get(s.log, "memory") or {}).get("searches"),
               desc="search recorded")
    got = (ST.kv_get(s.log, "memory") or {}).get("searches")
    assert got[0]["query"] == "how does rscheck answer getstatus"
    assert got[0]["sub"] == "query" and len(got[0]["hits"]) == 2
    assert got[0]["hits"][0]["score"] == "86%"
    assert not (ST.kv_get(s.log, "memory") or {}).get("files")   # nothing opened
    assert MEM.MARK in s.ops_text()
    # the header's mem flavour is what the web words as "queried", not "recalled"
    assert [op.get("mem") for op in s.ops() if op.get("t") == "label"
            and op.get("mem")] == ["search"]


def test_cmd_fmt_marks_a_collapsed_read_one_liner(run_hook, test_env, session, tmp_path):
    """A `sed`/`grep` of a note collapses to a Read one-liner (tools.read_command),
    which is the whole block — so its ❖ and `mem` ride the LINE, like a file op."""
    s = session.make()
    vault = tmp_path / "wiki" / "01"
    (vault / "platform").mkdir(parents=True)
    note = vault / "platform" / "arch.md"
    note.write_text("# arch\n")
    run_hook("claude-cmd-fmt.py",
             P.post_bash(s, "sed -n 1,5p %s" % note, tid="toolu_bs1",
                         stdout="# arch\n"),
             env=_mem_env(test_env, vault, s.cwd))
    wait_until(lambda: (ST.kv_get(s.log, "memory") or {}).get("files"),
               desc="memory kv populated from a collapsed read")
    assert (ST.kv_get(s.log, "memory"))["files"][0]["path"] == str(note)
    assert MEM.MARK in s.ops_text()
    assert any(op.get("mem") for op in s.ops() if op.get("t") == "line")


def test_cmd_fmt_bash_scope_gate_off_project(run_hook, test_env, session, tmp_path):
    """The SAME vault read from a session outside the enabled project is a plain
    command — the Bash plane applies the same in_scope() gate as the file plane."""
    s = session.make()
    vault = tmp_path / "wiki" / "01"
    (vault / "platform").mkdir(parents=True)
    (vault / "platform" / "arch.md").write_text("# arch\n")
    run_hook("claude-cmd-fmt.py",
             P.post_bash(s, "cat %s/platform/arch.md" % vault, tid="toolu_boos"),
             env=_mem_env(test_env, vault, str(tmp_path / "other" / "project")))
    wait_until(lambda: s.ops(), desc="op emitted")
    assert MEM.MARK not in s.ops_text()
    assert not any(op.get("mem") for op in s.ops())
    assert ST.kv_get(s.log, "memory") is None


def test_cmd_fmt_leaves_an_ordinary_command_untouched(run_hook, test_env, session, tmp_path):
    s = session.make()
    vault = tmp_path / "wiki" / "01"
    vault.mkdir(parents=True)
    run_hook("claude-cmd-fmt.py",
             P.post_bash(s, "make test", tid="toolu_bplain"),
             env=_mem_env(test_env, vault, s.cwd))
    wait_until(lambda: s.ops(), desc="op emitted")
    assert MEM.MARK not in s.ops_text()
    assert not any(op.get("mem") for op in s.ops())
    assert ST.kv_get(s.log, "memory") is None


def test_substream_records_a_subagents_bash_note_read(tmp_path, monkeypatch):
    """A teammate's shell recall lands in the SAME team-wide kv, stamped with its
    name — the Bash twin of the file-plane subagent test above."""
    vault = tmp_path / "wiki" / "01"
    (vault / "providers").mkdir(parents=True)
    note = vault / "providers" / "egt.md"
    note.write_text("# egt\n")
    monkeypatch.setenv("BAQYLAU_MEMORY_ROOT", str(vault))
    monkeypatch.setenv("BAQYLAU_MEMORY_PROJECT", os.getcwd())
    _clear_index()
    log = str(tmp_path / "claude-mirror-subcmd.log")
    ST.kv_set(log, "boot", 1)
    r = SR.Renderer(
        log=log, agent="note-writer", label="note-writer", rgb=(1, 2, 3),
        sub_fg=False, op_tag=lambda: "", ctx_tag=lambda: "",
        take_subfg=lambda tid: None,
        spawn_fg_tailer=lambda tid, rec, cmd="": None,
        spawn_tailer=lambda kind, taskid, cmd="", group=None: None)
    cmd = "cat %s" % note
    r.on_tool_use({"name": "Bash", "input": {"command": cmd}, "id": "tb1"})
    r.on_tool_result({"tool_use_id": "tb1", "content": "# egt\n"})
    files = (ST.kv_get(log, "memory") or {}).get("files")
    assert files and files[0]["path"] == str(note)
    assert files[0]["verb"] == "Read" and files[0]["agent"] == "note-writer"
