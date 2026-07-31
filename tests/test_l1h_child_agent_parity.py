# L1h — the CHILD-AGENT PARITY contract.
#
# Every host runs children (Claude Code spawns subagents and teammates, codex
# spawns its own), and each streams the same anatomy: a launch card, tool calls,
# shell commands, file ops, messages, a result, a footer. core/agentblocks.py owns
# the BLOCKS and the stamps on them; this file pins that the two ADAPTERS which
# drive it actually produce the same stream — the drift that made a codex-native
# subagent render as raw JavaScript under a `Ran 1 codex run` summary while a
# Claude subagent rendered as cards.
#
# The guard is structural, not textual: one synthetic sequence is driven through
# BOTH adapters — plugins/claude_code/substream_render.Renderer over fake
# transcript records, and plugins/codex/stream.Renderer in the SUBAGENT register
# over fake rollout records — and compared after normalising away the things that
# are ALLOWED to differ (identity: who/colour/tags/src/label, and the wording
# inside a block). What must match is everything a downstream stage reads: the op
# kinds, the block MARKERS, the web/bubbled/chrome/lk stamps, the note wording
# modulo the child's name, the copy-group topology, and then the DERIVED layer —
# the activity classes, what the web keeps and drops in both views, and which
# blocks the quiet register covers.
#
# HOW A THIRD ADAPTER (opencode, …) PASSES THIS:
#   1. Build a `core.agentblocks.AgentStream` for the child (label/rgb/register,
#      a model tag callable, a duration callable) and paint EVERY block through
#      it. Do not hand-roll a chip: the stamps are the contract, and they are
#      exactly what a hand-rolled copy forgets.
#   2. Emit the launch card ONCE when the child's own work begins, and hold the
#      final message so it lands as `result` rather than `message`.
#   2b. Declare each TASK (`AgentStream.task`, core/childtask.py) before its
#      endpoints paint — the identity that keeps two tasks of one child two
#      results, and (where the host names its turns) what lets the web place a
#      completion before the answer of the turn it belongs to. Named in _norm's
#      stamps below, so an adapter that skips it fails the columns.
#   3. Stamp the ops `<its register>:<agent-id>` so the same scope/classify
#      machinery finds them, and mark the HOST's own scaffolding `chrome=1`.
#   4. Add a `_<tool>_ops()` builder below returning that adapter's op list for
#      the same sequence, and add it to ADAPTERS. Nothing else in this file
#      changes — if the new adapter is a real child-agent stream, it passes.
#
# …and `_third_host_ops` below IS that instruction followed, as a test: a host
# this repo does not have, with a register row and a palette of its own and no
# plugin package at all, driven through the same sequence. It exists to prove
# the claim the whole abstraction rests on — that a new host needs ZERO
# presenter changes — by being the case nothing was written for. It fails the
# moment the presenter re-learns a host's vocabulary: the palette it paints in
# is in no table this build ships, and its register is added at RUN TIME.
import json
import os
import sys

from conftest import REPO

if REPO not in sys.path:
    sys.path.insert(0, REPO)

from core import agentblocks as AB
from core import childtask as CT
from core import ops as O
from core import render as R
from core import slots as SL
from core import state as S
from core import streamfmt as SF
from dashboard.opshtml import actclass as AC
from dashboard.opshtml import ops as OH
from plugins.claude_code import substream_render as SR
from plugins.codex import stream as ST

# The one sequence both adapters are driven over. Each step is a BLOCK the two
# hosts both produce; the payloads differ (a Claude tool call is a ToolSearch, a
# codex one a web__run) because the point is the SHAPE, not the vocabulary.
BRIEF = "Get the weather in Bali"
TOOL_REQ = 'query: {"q": "bali"}'
TOOL_OUT = "27C, scattered clouds"
CMD_OK, CMD_OUT = "echo hi", "hi"
CMD_BAD, CMD_ERR = "false", "boom"
MSG = "Checking the live forecast now."
RESULT = "Bali: 27C, scattered clouds."
FOOT_EXTRA = " · 22k in · 656 out · cache 82%"


def _ops(log):
    _last, rows = S.ops_after(log, 0)
    return rows


# --------------------------------------------------------------- the two adapters

def _claude_ops(tmp_path, monkeypatch):
    """The Claude adapter: a subagent transcript through substream_render."""
    log = str(tmp_path / "claude-mirror-parity-claude.log")
    r = SR.Renderer(
        log=log, agent="a1b2", label="Explore", rgb=SL.SUB_PALETTE[0],
        sub_fg=False, op_tag=lambda: "opus-5·high", ctx_tag=lambda: "",
        take_subfg=lambda tid: None,
        spawn_fg_tailer=lambda tid, rec, cmd="": None,
        spawn_tailer=lambda kind, taskid, cmd="", group=None: None,
        agent_dur=lambda: "42.0s")
    # the TASK its endpoints belong to — what plugins/claude_code/substream.
    # declare_task() does for a real streamer (one generation == one task)
    r.blocks.task(CT.key("a1b2", "tool_use_1@0"))
    r.render_prompt(BRIEF)                                   # ⇢ launch card
    r.on_tool_use({"name": "ToolSearch", "id": "t1",
                   "input": {"query": '{"q": "bali"}'}})     # · tool + request
    r.on_tool_result({"tool_use_id": "t1", "content": TOOL_OUT})
    r.on_tool_use({"name": "Bash", "id": "t2",
                   "input": {"command": CMD_OK}})            # ▶ command (ok)
    r.on_tool_result({"tool_use_id": "t2", "content": CMD_OUT})
    r.on_tool_use({"name": "Bash", "id": "t3",
                   "input": {"command": CMD_BAD}})           # ▶ command (failed)
    r.on_tool_result({"tool_use_id": "t3", "content": CMD_ERR, "is_error": True})
    r.on_tool_use({"name": "Edit", "id": "t4",               # file one-liner
                   "input": {"file_path": str(tmp_path / "app.py"),
                             "old_string": "a", "new_string": "b\nc"}})
    r.on_tool_result({"tool_use_id": "t4", "content": "ok"}, {})
    r.render_message(MSG)                                    # ✎ message …
    r.render_message(RESULT)                                 # … flushed by the next
    r.flush_msg(is_result=True)                              # ⇠ result card
    O.emit(log, *r.blocks.footer("ended", "42.0s", FOOT_EXTRA))
    return _ops(log)


def _codex_ops(tmp_path, monkeypatch):
    """The codex adapter: a subagent ROLLOUT through stream.Renderer."""
    monkeypatch.setenv("CLAUDE_CODEX_SUBAGENT", "1")   # what watch.spawn exports
    log = str(tmp_path / "claude-mirror-parity-codex.log")
    fork_iso, fork_epoch = "2026-07-30T12:19:59.556Z", 1785413999
    roll = tmp_path / "rollout-2026-07-30T12-19-59-child.jsonl"
    roll.write_text("".join(json.dumps(r) + "\n" for r in (
        {"type": "session_meta", "timestamp": fork_iso,
         "payload": {"thread_source": "subagent", "timestamp": fork_iso,
                     "source": {"subagent": {"thread_spawn": {}}}}},
        {"type": "response_item", "payload": {
            "type": "message", "role": "user",
            "content": [{"type": "input_text", "text": BRIEF}]}},
        {"type": "event_msg", "payload": {"type": "task_started",
                                          "started_at": fork_epoch}},
    )), encoding="utf-8")
    ST._init(["claude-codex-stream.py", log,
              ",".join(str(x) for x in SL.SUB_PALETTE[0]), str(roll), "-",
              "Hooke"])
    assert ST.REGISTER == ST.REG_SUBAGENT, "the env flag must select the register"
    rd = ST.Renderer()
    rd.ro_tag = "gpt-5.6-terra · low"
    rd.fork_epoch = fork_epoch
    rd.sub_open = False
    feed = rd.feed_rollout
    # the bootstrap gate flip — the launch card, off the brief in the prefix
    feed({"kind": "task_started", "at": fork_epoch, "ts": None})
    feed({"kind": "tool", "name": "web__run", "args": '{"q": "bali"}',
          "call_id": "c1"})                                  # · tool + request
    feed({"kind": "exec_result", "exit": None, "output": TOOL_OUT,
          "call_id": "c1", "ts": None})
    feed({"kind": "exec", "cmd": CMD_OK, "call_id": "c2", "ts": None})
    feed({"kind": "exec_result", "exit": "0", "output": CMD_OUT,
          "call_id": "c2", "ts": None})
    feed({"kind": "exec", "cmd": CMD_BAD, "call_id": "c3", "ts": None})
    feed({"kind": "exec_result", "exit": "1", "output": CMD_ERR,
          "call_id": "c3", "ts": None})
    feed({"kind": "patch", "success": True,                  # file one-liner
          "files": [{"path": str(tmp_path / "app.py"), "change": "update",
                     "added": 2, "removed": 1}]})
    feed({"kind": "message", "text": MSG})                   # ✎ message …
    feed({"kind": "message", "text": RESULT})                # … flushed by the next
    feed({"kind": "task_complete", "at": fork_epoch + 42, "ts": None})
    O.emit(log, *rd.blocks.footer("ended", "42.0s", FOOT_EXTRA))
    return _ops(log)


# --------------------------------------------------- the THIRD host (synthetic)

# A host this build has never heard of: its own register name, its own `src`
# prefix, its own display word, and a palette that is in NO table core ships.
REG_THIRD = "opencode"
THIRD_PALETTE = [(11, 99, 200), (13, 101, 202)]
THIRD_ROW = {"src": "oc", "act": O.ACT_AGENT, "word": SF.host_word("Opencode"),
             "palette": THIRD_PALETTE}


def _third_host_ops(tmp_path, monkeypatch):
    """THE THIRD-HOST PROOF: a ~25-line adapter, no plugin package, no presenter
    change, no registry edit that outlives this function.

    Everything host-specific it owns is the register ROW (added here at run time,
    which is the point — the presenter cannot have been written for it) and the
    calls it makes. The op STAMPS, the block shapes, the notes, the classes and
    the scope behaviour all come from core/agentblocks, and every assertion in
    this file then runs on it unchanged."""
    monkeypatch.setitem(AB.REGISTERS, REG_THIRD, THIRD_ROW)
    log = str(tmp_path / "claude-mirror-parity-third.log")
    blocks = AB.AgentStream(label="Kepler", rgb=THIRD_PALETTE[0],
                            register=REG_THIRD,
                            tags=lambda: "oc-1·high", agent_dur=lambda: "42.0s")
    # …and it declares itself the way every child does: the ambient producer
    # stamp, `<its prefix>:<agent id>` (core/ops.set_src). Restored after, since
    # the stamp is process state.
    monkeypatch.setattr(O, "_SRC", AB.src_stamp(REG_THIRD) + "k1", raising=False)
    monkeypatch.setattr(O, "_SRC_INIT", True, raising=False)
    # …and the TASK its two endpoint cards are about (step 2b above). No parent
    # turn: a host that cannot name its turns simply passes none.
    blocks.task(CT.key("k1", "job-7"))
    g = O.new_group(log)
    O.emit(log, *blocks.launch(BRIEF, g))                      # ⇢ launch card
    g = O.new_group(log)
    O.emit(log, *blocks.tool_open("web.search", TOOL_REQ, g))  # · tool + request
    O.emit(log, *blocks.tool_close(g, TOOL_OUT))
    g = O.new_group(log)
    O.emit(log, *blocks.cmd_open(CMD_OK, g))                   # ▶ command (ok)
    O.emit(log, *blocks.cmd_close(g, CMD_OUT))
    g = O.new_group(log)
    O.emit(log, *blocks.cmd_open(CMD_BAD, g))                  # ▶ command (failed)
    O.emit(log, *blocks.cmd_close(g, CMD_ERR, failed=True))
    O.emit(log, *blocks.file_line("Update", "app.py", O.YELLOW,  # file one-liner
                                  added=2, removed=1))
    O.emit(log, *blocks.message(MSG, O.new_group(log)))        # ✎ message
    O.emit(log, *blocks.result(RESULT, O.new_group(log)))      # ⇠ result card
    O.emit(log, *blocks.footer("ended", "42.0s", FOOT_EXTRA))
    return _ops(log)


# (name, builder, the child's label, the REGISTER it is painted in)
ADAPTERS = (("claude", _claude_ops, "Explore", AB.REG_AGENT),
            ("codex", _codex_ops, "Hooke", AB.REG_AGENT),
            ("opencode", _third_host_ops, "Kepler", REG_THIRD))


# ------------------------------------------------------------------ normalisation

# What a block header is allowed to differ in: the child's NAME (identity) and the
# wording after the marker (one host's tool is `ToolSearch`, the other's
# `web__run`). What must match is the MARKER the block opens with — the closed
# vocabulary every downstream stage reads.
def _head(op, label):
    text = R.strip_ansi(op.get("s") or "")
    text = AC.lead_head(text)                 # cut the tags, as agent scope does
    for mark in (SF.MARK_PROMPT, SF.MARK_RESULT, SF.MARK_MESSAGE):
        if text.startswith(mark[0]):
            return "%s %s" % mark
    if text.startswith(AB.FAIL_MARK):
        # `■ failed` vs `■ failed (exit 1)` — a codex result carries an exit code
        # and a Claude tool_result does not; the MARK is the shared part
        return AB.FAIL_MARK
    for mark in (AB.TOOL_GLYPH, AB.CMD_GLYPH, AB.BG_GLYPH):
        if text.startswith(mark + " "):
            # the tool's NAME is per-host; a command's kind word is not
            return mark if mark == AB.TOOL_GLYPH else text
    if text.startswith("■ " + label):
        return "■ <label> ended"              # the run footer
    return text


def _norm(ops, label, word=SF.AGENT_WORD):
    """One adapter's ops as comparable tuples, identity normalised away.

    `word` is the REGISTER's naming template (core/agentblocks.REGISTERS' `word`
    row) — `Agent "%s"`, `Codex "%s"`, a third host's own. It is identity, like
    the child's name: WHICH word the web calls a child is the one thing a
    register selects, so the comparison erases it and the sentence around it —
    the verb, the duration, the `·` — is what must match."""
    groups, out = {}, []
    for op in ops:
        t = op.get("t")
        if t in ("rule", "blank"):
            out.append((t, "", "", ""))
            continue
        g = op.get("g")
        if g and g not in groups:
            groups[g] = "g%d" % len(groups)          # topology, not the ids
        note = (op.get("note") or "").replace(word % label, "<child>")
        stamps = ",".join(k for k in ("web", "bubbled", "chrome", "mem")
                          if op.get(k)) + ("|lk" if op.get("lk") else "")
        # …and which ENDPOINT of a child task this op is (core/childtask.py). The
        # task's ID is identity (one host's turn id, another's tool_use id) and is
        # erased like the child's name; the STEP is the contract — an adapter that
        # declares no task stamps neither endpoint, and its launch/result cards
        # then order differently in the web feed from every other host's.
        ct = CT.of(op)
        if ct:
            stamps += "|task:" + ct["step"]
        head = _head(op, label) if t == "label" else ""
        out.append((t, groups.get(g, ""), stamps, head + ("|" + note if note else "")))
    return out


def _both(tmp_path, monkeypatch):
    """Every adapter's (name, ops, label, register word). The builder runs FIRST
    and the word is read after, so a register a builder adds at run time (the
    third host) is in the table by the time its word is asked for."""
    out = []
    for name, fn, label, reg in ADAPTERS:
        ops = fn(tmp_path, monkeypatch)
        out.append((name, ops, label, AB.register_word(reg)))
    return out


# ------------------------------------------------------------------------- the pins

def test_both_adapters_paint_the_same_block_sequence(tmp_path, monkeypatch):
    """Op-by-op: same kinds, same block markers, same web/bubbled/chrome/lk
    stamps, same notes (modulo the child's name), same copy-group topology.

    Every adapter is compared against the FIRST, so adding one adds a column
    rather than a case."""
    (n1, a, l1, w1), *rest = _both(tmp_path, monkeypatch)
    na = _norm(a, l1, w1)
    for n2, b, l2, w2 in rest:
        nb = _norm(b, l2, w2)
        assert na == nb, "%s and %s paint different streams:\n%s\n%s" % (
            n1, n2, "\n".join(map(str, na)), "\n".join(map(str, nb)))


def test_both_adapters_carry_the_child_identity_on_every_header(tmp_path, monkeypatch):
    """…and each is stamped with its OWN identity: every block header names the
    child (`who`) and no header leaks the other adapter's vocabulary. This is the
    half _norm deliberately erases, so it is pinned separately."""
    for name, ops, label, _w in _both(tmp_path, monkeypatch):
        heads = [op for op in ops
                 if op.get("t") == "label" and op.get("g")]
        assert heads, name
        assert all(op.get("who") == label for op in heads), \
            "%s: every block header must carry the child's name" % name


def test_both_adapters_classify_the_same(tmp_path, monkeypatch):
    """The DERIVED layer the view modes count: identical activity-class
    sequences. A block that classifies differently folds differently, which is
    the whole `Ran 1 codex run` bug in one assertion."""
    def _acts(ops):
        return [AC.classify(op) for op in ops
                if op.get("t") in ("label", "line")]

    (n1, a, _l1, _w1), *rest = _both(tmp_path, monkeypatch)
    want = _acts(a)
    for n2, b, _l, _w in rest:
        assert _acts(b) == want, "%s vs %s: %r != %r" % (n1, n2, _acts(b), want)


def test_both_adapters_keep_and_drop_the_same_in_both_views(tmp_path, monkeypatch):
    """op_items agrees in BOTH views: the session view (scope=None — only the two
    `web` endpoints survive the src stamp) and the child's own agent scope (its
    prose dropped in favour of conversation bubbles, everything else kept).

    Each adapter is stamped with its OWN register's prefix, which is the half
    that matters for a third host: the scope is the agent ID, so a prefix this
    build has never seen is filtered identically to `sub:`."""
    got = []
    for name, ops, _label, _w in _both(tmp_path, monkeypatch):
        aid = "a-" + name
        pre = {"opencode": THIRD_ROW["src"]}.get(name, "sub")
        stamped = [dict(op, src=pre + ":" + aid) for op in ops]
        lead = OH.op_items(stamped, "sid")
        scoped = OH.op_items(stamped, "sid", scope=aid)
        got.append((name, [it.get("act") for it in lead],
                    [it.get("act") for it in scoped],
                    [bool(it.get("note")) for it in lead]))
    for row in got[1:]:
        assert got[0][1:] == row[1:], \
            "%s and %s disagree on keep/drop: %r vs %r" % (got[0][0], row[0],
                                                           got[0][1:], row[1:])
    assert got[0][1], "the session view must keep the child's launch/result cards"


def test_both_adapters_reach_the_quiet_register_alike(tmp_path, monkeypatch):
    """…and inside the scope the same blocks go quiet (`⏺ <command>` instead of a
    coloured pill) — cmd_note is colour-gated, so this is what catches a block
    painted in the wrong palette."""
    got = []
    for _name, ops, _label, _w in _both(tmp_path, monkeypatch):
        quiet = [AC.cmd_note(AC.as_lead(op)) is not None for op in ops
                 if op.get("t") == "label"]
        got.append(quiet)
    assert all(q == got[0] for q in got), \
        "quiet-register eligibility differs: %r" % (got,)
    assert any(got[0]), "a child's command blocks must reach the quiet register"


def test_the_parity_sequence_covers_every_shared_block_kind(tmp_path, monkeypatch):
    """A guard on the guard: if a block kind is added to core/agentblocks and the
    sequence above never drives it, the parity test silently stops covering it."""
    _n, ops, label, _w = _both(tmp_path, monkeypatch)[0]
    heads = {_head(op, label) for op in ops if op.get("t") == "label"}
    for mark in ("%s %s" % SF.MARK_PROMPT, "%s %s" % SF.MARK_RESULT,
                 "%s %s" % SF.MARK_MESSAGE, AB.TOOL_GLYPH, "■ <label> ended"):
        assert mark in heads, "the parity sequence no longer drives %r" % mark
    assert any(h.startswith(AB.CMD_GLYPH) for h in heads), "no command block"
    # …and the FAILURE mark, which is a BODY op (a block's outcome, not its head)
    bodies = [R.strip_ansi(op.get("s") or "") for op in ops
              if op.get("t") == "gut"]
    assert any(b.startswith(AB.FAIL_MARK) for b in bodies), "no failed block"
    # …and the file one-liner, likewise a body op in both adapters
    assert any(b.startswith("Update(") for b in bodies), "no file op"


def test_a_third_host_classifies_scopes_and_collapses_with_no_presenter_change(
        tmp_path, monkeypatch):
    """THE PROOF, stated rather than implied by the columns above: everything the
    web does with a host it has never heard of, spelled out.

    The adapter's palette is in NO table this build ships and its register row is
    added at run time, so every answer here has to come from the op — the
    producer's `act`, its `src`, its `bubbled`/`web` stamps — and not from a
    table of known hosts. Each assertion below was a REAL failure mode before P6:
    a `None` class folds a whole session into "ran N agents", an unmatched scope
    renders a BLANK mirror, an un-recoloured header keeps the terminal's pill and
    hides its file ops from every summary."""
    ops = _third_host_ops(tmp_path, monkeypatch)
    stamp = AB.src_stamp(REG_THIRD) + "k1"
    assert all(op.get("src") == stamp for op in ops if op.get("t") != "rule"), \
        "the adapter must declare its producer source"

    # 1. CLASSIFICATION — off the producer's stamp, in the shared vocabulary
    acts = [AC.classify(op)[0] for op in ops if op.get("t") == "label"]
    assert O.ACT_AGENT in acts          # its launch/result cards: its row's act
    assert O.ACT_TOOL in acts and O.ACT_BASH in acts
    # every header names a class except the run FOOTER, which closes a block it
    # does not name (the one deliberate None)
    assert acts.count(None) == 1
    # …and the file one-liner, a `gut` that IS a whole block
    files = [op for op in ops if op.get("act") in
             (O.ACT_READ, O.ACT_EDIT, O.ACT_WRITE)]
    assert len(files) == 1 and files[0]["act"] == O.ACT_EDIT
    assert (files[0].get("add"), files[0].get("rem")) == (2, 1)

    # 2. SCOPE — the id alone, so an unknown PREFIX cannot blank the mirror
    assert OH.in_scope(ops[0], "k1") and not OH.in_scope(ops[0], "other")
    scoped = OH.op_items(ops, "sid", scope="k1")
    assert scoped, "a third host's agent scope must not render blank"

    # 3. COLLAPSE — prose dropped in scope (its conversation is bubbled), the two
    #    endpoint cards kept in the LEAD's view, activity kept in both
    txt = " ".join(it.get("html") or "" for it in scoped)
    assert "message" not in txt and BRIEF not in txt      # prose gone in scope
    # activity stays — matched on the command WORD, since the block's `code` op
    # is syntax-highlighted and the two words are not adjacent in the HTML
    assert "echo" in txt
    # …and its file op arrived as a `line` carrying its own class, which is what
    # makes it visible to a view-mode summary (a `gut` names none)
    assert [it["t"] for it in scoped if it.get("act") == O.ACT_EDIT] == ["line"]
    lead = OH.op_items(ops, "sid")
    # …the two `web` endpoint CARDS (each a header + the body behind its click)
    # and nothing between them
    assert [it.get("act") for it in lead] == [O.ACT_AGENT, None,
                                              O.ACT_AGENT, None], \
        "the lead's mirror shows a child's two ENDPOINTS and nothing between"
    assert [bool(it.get("note")) for it in lead] == [True, False, True, False]
    # …named in ITS OWN register's word, which is a row in the table and not a
    # branch anywhere
    import html as _html
    txt2 = _html.unescape(" ".join(it["html"] for it in lead))
    assert 'Opencode "Kepler" launched' in txt2
    assert 'Opencode "Kepler" finished · 42.0s' in txt2

    # 4. as_lead RECOLOURS its command header — off the stamp, since its palette
    #    is one no table here has ever seen
    cmd = next(op for op in ops if op.get("act") == O.ACT_BASH)
    assert tuple(cmd["c"]) == tuple(THIRD_PALETTE[0])
    assert tuple(AC.as_lead(cmd)["c"]) == tuple(O.SLATE)
    assert AC.cmd_note(AC.as_lead(cmd)) is not None        # …so it goes quiet


def test_the_codex_adapter_needs_no_dashboard_import():
    """The dependency direction: this TEST may import dashboard modules (tests sit
    above every tier), but the adapters may not — a plugin reaching the web
    presenter would invert the rule the whole layout rests on."""
    for mod in ("plugins/codex/stream.py", "plugins/claude_code/substream_render.py"):
        src = open(os.path.join(REPO, mod), encoding="utf-8").read()
        for bad in ("import dashboard", "from dashboard"):
            assert bad not in src, "%s reaches the web presenter" % mod
