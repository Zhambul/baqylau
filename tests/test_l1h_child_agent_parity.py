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
#   3. Stamp the ops `sub:<agent-id>` (or `team:`) so the same scope/classify
#      machinery finds them, and mark the HOST's own scaffolding `chrome=1`.
#   4. Add a `_<tool>_ops()` builder below returning that adapter's op list for
#      the same sequence, and add it to ADAPTERS. Nothing else in this file
#      changes — if the new adapter is a real child-agent stream, it passes.
import json
import os
import sys

from conftest import REPO

if REPO not in sys.path:
    sys.path.insert(0, REPO)

from core import agentblocks as AB
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


ADAPTERS = (("claude", _claude_ops, "Explore"), ("codex", _codex_ops, "Hooke"))


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


def _norm(ops, label):
    """One adapter's ops as comparable tuples, identity normalised away."""
    groups, out = {}, []
    for op in ops:
        t = op.get("t")
        if t in ("rule", "blank"):
            out.append((t, "", "", ""))
            continue
        g = op.get("g")
        if g and g not in groups:
            groups[g] = "g%d" % len(groups)          # topology, not the ids
        note = (op.get("note") or "").replace('"%s"' % label, '"<label>"')
        stamps = ",".join(k for k in ("web", "bubbled", "chrome", "mem")
                          if op.get(k)) + ("|lk" if op.get("lk") else "")
        head = _head(op, label) if t == "label" else ""
        out.append((t, groups.get(g, ""), stamps, head + ("|" + note if note else "")))
    return out


def _both(tmp_path, monkeypatch):
    return [(name, fn(tmp_path, monkeypatch), label)
            for name, fn, label in ADAPTERS]


# ------------------------------------------------------------------------- the pins

def test_both_adapters_paint_the_same_block_sequence(tmp_path, monkeypatch):
    """Op-by-op: same kinds, same block markers, same web/bubbled/chrome/lk
    stamps, same notes (modulo the child's name), same copy-group topology."""
    (n1, a, l1), (n2, b, l2) = _both(tmp_path, monkeypatch)
    na, nb = _norm(a, l1), _norm(b, l2)
    assert na == nb, "%s and %s paint different streams:\n%s\n%s" % (
        n1, n2, "\n".join(map(str, na)), "\n".join(map(str, nb)))


def test_both_adapters_carry_the_child_identity_on_every_header(tmp_path, monkeypatch):
    """…and each is stamped with its OWN identity: every block header names the
    child (`who`) and no header leaks the other adapter's vocabulary. This is the
    half _norm deliberately erases, so it is pinned separately."""
    for name, ops, label in _both(tmp_path, monkeypatch):
        heads = [op for op in ops
                 if op.get("t") == "label" and op.get("g")]
        assert heads, name
        assert all(op.get("who") == label for op in heads), \
            "%s: every block header must carry the child's name" % name


def test_both_adapters_classify_the_same(tmp_path, monkeypatch):
    """The DERIVED layer the view modes count: identical activity-class
    sequences. A block that classifies differently folds differently, which is
    the whole `Ran 1 codex run` bug in one assertion."""
    (n1, a, _), (n2, b, _) = _both(tmp_path, monkeypatch)
    acts = [[AC.classify(op) for op in ops if op.get("t") in ("label", "line")]
            for ops in (a, b)]
    assert acts[0] == acts[1], "%s vs %s: %r != %r" % (n1, n2, acts[0], acts[1])


def test_both_adapters_keep_and_drop_the_same_in_both_views(tmp_path, monkeypatch):
    """op_items agrees in BOTH views: the session view (scope=None — only the two
    `web` endpoints survive the src stamp) and the child's own agent scope (its
    prose dropped in favour of conversation bubbles, everything else kept)."""
    got = []
    for name, ops, _label in _both(tmp_path, monkeypatch):
        aid = "a-" + name
        stamped = [dict(op, src="sub:" + aid) for op in ops]
        lead = OH.op_items(stamped, "sid")
        scoped = OH.op_items(stamped, "sid", scope={"sub:" + aid})
        got.append((name, [it.get("act") for it in lead],
                    [it.get("act") for it in scoped],
                    [bool(it.get("note")) for it in lead]))
    assert got[0][1:] == got[1][1:], \
        "%s and %s disagree on keep/drop: %r vs %r" % (got[0][0], got[1][0],
                                                       got[0][1:], got[1][1:])
    assert got[0][1], "the session view must keep the child's launch/result cards"


def test_both_adapters_reach_the_quiet_register_alike(tmp_path, monkeypatch):
    """…and inside the scope the same blocks go quiet (`⏺ <command>` instead of a
    coloured pill) — cmd_note is colour-gated, so this is what catches a block
    painted in the wrong palette."""
    got = []
    for _name, ops, _label in _both(tmp_path, monkeypatch):
        quiet = [AC.cmd_note(AC.as_lead(op)) is not None for op in ops
                 if op.get("t") == "label"]
        got.append(quiet)
    assert got[0] == got[1], "quiet-register eligibility differs: %r" % (got,)
    assert any(got[0]), "a child's command blocks must reach the quiet register"


def test_the_parity_sequence_covers_every_shared_block_kind(tmp_path, monkeypatch):
    """A guard on the guard: if a block kind is added to core/agentblocks and the
    sequence above never drives it, the parity test silently stops covering it."""
    _n, ops, label = _both(tmp_path, monkeypatch)[0]
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


def test_the_codex_adapter_needs_no_dashboard_import():
    """The dependency direction: this TEST may import dashboard modules (tests sit
    above every tier), but the adapters may not — a plugin reaching the web
    presenter would invert the rule the whole layout rests on."""
    for mod in ("plugins/codex/stream.py", "plugins/claude_code/substream_render.py"):
        src = open(os.path.join(REPO, mod), encoding="utf-8").read()
        for bad in ("import dashboard", "from dashboard"):
            assert bad not in src, "%s reaches the web presenter" % mod
