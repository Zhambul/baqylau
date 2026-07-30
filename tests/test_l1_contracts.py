# L1 — per-handler hook contracts.
#
# Pins, for every hook entry point: the never-fail invariant (rc 0 on garbage),
# audit-before-swallow, the agent_id main-session guard, the Failure-event
# path, and each handler's happy-path effect on the state/audit DBs.
import json

import pytest

import oracle
import payloads as P
from conftest import wait_until

# Every hook entry point exactly as Claude Code invokes it: (script, argv).
ALL_HANDLERS = [
    ("claude-cmd-pre.py", ()),
    ("claude-cmd-fmt.py", ()),
    ("claude-file-fmt.py", ()),
    ("claude-monitor-fmt.py", ()),
    ("claude-subagent-fmt.py", ("push",)),
    ("claude-subagent-fmt.py", ("start",)),
    ("claude-subagent-fmt.py", ("stop",)),
    ("claude-task-fmt.py", ()),
    ("claude-skill-fmt.py", ()),
    ("claude-stop-fmt.py", ()),
    ("claude-split.py", ("open",)),
    ("claude-split.py", ("close",)),
    ("claude-tab-status.py", ("idle",)),
    ("claude-tab-status.py", ("thinking",)),
    ("claude-tab-status.py", ("pretool",)),
    ("claude-tab-status.py", ("posttool",)),
    ("claude-tab-status.py", ("notify",)),
    ("claude-tab-status.py", ("stop",)),
    ("claude-tab-status.py", ("clear",)),
]

# The handlers built on hookkit.read_payload — these must leave an audit
# errors row ("payload parse") when stdin is not JSON.
HOOKLIB_HANDLERS = [h for h in ALL_HANDLERS
                    if h[0] not in ("claude-tab-status.py", "claude-split.py")]


# ------------------------------------------------------- never-fail contract

@pytest.mark.parametrize("script,argv", ALL_HANDLERS,
                         ids=["%s:%s" % (s, "-".join(a) or "_") for s, a in ALL_HANDLERS])
def test_garbage_stdin_exits_zero(run_hook, script, argv):
    run_hook(script, raw_stdin="this is { not json", argv=argv)


@pytest.mark.parametrize("script,argv", ALL_HANDLERS,
                         ids=["%s:%s" % (s, "-".join(a) or "_") for s, a in ALL_HANDLERS])
def test_empty_payload_exits_zero(run_hook, script, argv):
    run_hook(script, payload={}, argv=argv)


@pytest.mark.parametrize("script,argv", HOOKLIB_HANDLERS,
                         ids=["%s:%s" % (s, "-".join(a) or "_") for s, a in HOOKLIB_HANDLERS])
def test_garbage_stdin_is_audited_before_swallow(run_hook, test_env, script, argv):
    run_hook(script, raw_stdin="this is { not json", argv=argv)
    errs = oracle.errors(test_env)
    assert any("payload parse" in (e[2] or "") for e in errs), \
        "no 'payload parse' errors row after garbage stdin: %s" % errs


# ------------------------------------------------------------- claude-cmd-pre

def test_cmd_pre_rewrites_via_updated_input(run_hook, test_env, session):
    s = session.make()
    p = run_hook("claude-cmd-pre.py", P.pre_bash(s, "echo hello"))
    out = json.loads(p.stdout)
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert hso["permissionDecision"] == "allow"
    assert "echo hello" in hso["updatedInput"]["command"]
    assert "tee -a" in hso["updatedInput"]["command"]
    # visible effects: fg slot claimed, header ops emitted, spawn audited
    assert s.live("fg"), "no live fg slot row"
    assert "▶ foreground" in s.ops_text()
    assert any("live fg stream" in d for d in oracle.decisions(test_env, s.sid))
    assert any(p[2].startswith("stream:fg") for p in oracle.spawns(test_env, s.sid))


def test_cmd_pre_own_redirect_skips_rewrite(run_hook, test_env, session):
    s = session.make()
    p = run_hook("claude-cmd-pre.py", P.pre_bash(s, "ls -la > files.txt"))
    assert p.stdout.strip() == "", "redirecting command must not be rewritten"
    assert any("own redirect" in d for d in oracle.decisions(test_env, s.sid))
    assert s.live("fg"), "redirect path still claims the fg slot"


@pytest.mark.parametrize("payload_kw,reason", [
    (dict(run_in_background=True), "background command"),
])
def test_cmd_pre_ignores(run_hook, test_env, session, payload_kw, reason):
    s = session.make()
    p = run_hook("claude-cmd-pre.py", P.pre_bash(s, "echo hi", **payload_kw))
    assert p.stdout.strip() == ""
    assert not s.live("fg")
    assert any(("ignored: " + reason) in d for d in oracle.decisions(test_env, s.sid))


def test_cmd_pre_subagent_fg_rewrites(run_hook, test_env, session):
    # A subagent's foreground command IS now rewritten to tee (so claude-substream.py
    # can live-tail it) — but cmd-pre leaves the header + tailer to the substream: it
    # claims NO fg slot and emits NO header, only the tee rewrite + a "subfg:<tid>"
    # hand-off marker.
    s = session.make()
    p = run_hook("claude-cmd-pre.py", P.pre_bash(s, "echo hi", agent_id="agent-x"))
    hso = json.loads(p.stdout)["hookSpecificOutput"]
    assert hso["permissionDecision"] == "allow"
    assert "tee -a" in hso["updatedInput"]["command"]
    assert not s.live("fg"), "subagent fg must NOT claim the main-session fg slot"
    assert "▶ foreground" not in s.ops_text(), "substream owns the header, not cmd-pre"
    assert any("subagent live fg" in d for d in oracle.decisions(test_env, s.sid))
    assert any(a == "write" and ":subfg:" in path
               for (path, a, _c) in oracle.state_files(test_env, s.sid))


def test_cmd_pre_tee_wrap_shape_unit():
    # Pin the exact wrap shape — the blank line before "}" is load-bearing
    # (a trailing "\" in the command eats one newline; see cmd_pre._tee_wrap).
    from plugins.claude_code import cmd_pre
    w = cmd_pre._tee_wrap("echo hi", "/tmp/x y.out")
    assert w == ("{ echo hi\n\n} > >(tee -a '/tmp/x y.out')"
                 " 2> >(tee -a '/tmp/x y.out' >&2)")


@pytest.mark.parametrize("agent_id", [None, "agent-x"])
def test_cmd_pre_wrap_and_envelope_identical_both_paths(run_hook, test_env,
                                                        session, agent_id):
    # Main-session and subagent fg rewrites must produce the SAME tee-wrap
    # string shape and the SAME updatedInput JSON envelope (both come from the
    # shared _tee_wrap/_emit_updated_input helpers).
    s = session.make()
    kw = dict(agent_id=agent_id) if agent_id else {}
    p = run_hook("claude-cmd-pre.py", P.pre_bash(s, "echo hello", **kw))
    out = json.loads(p.stdout)
    hso = out["hookSpecificOutput"]
    assert set(out) == {"hookSpecificOutput"}
    assert set(hso) == {"hookEventName", "permissionDecision", "updatedInput"}
    assert hso["hookEventName"] == "PreToolUse"
    assert hso["permissionDecision"] == "allow"
    cmd = hso["updatedInput"]["command"]
    assert cmd.startswith("{ echo hello\n\n} > >(tee -a "), \
        "blank line before '}' is load-bearing"
    assert cmd.endswith(" >&2)")
    assert cmd.count("tee -a") == 2
    # the tee target is a session-keyed sidecar (.out for main, .subfg.<tid>.out sub)
    assert (".subfg." in cmd) == bool(agent_id)
    assert ".out" in cmd


@pytest.mark.parametrize("agent_id", [None, "agent-x"])
def test_cmd_pre_redirect_branch_identical_both_paths(run_hook, test_env,
                                                      session, agent_id):
    # Both fg paths go through the shared _prepare_tee orchestration: a command
    # that already redirects its own stdout is NOT rewritten (no updatedInput,
    # its own target is tailed instead) on the main path AND the subagent path.
    s = session.make()
    kw = dict(agent_id=agent_id) if agent_id else {}
    p = run_hook("claude-cmd-pre.py", P.pre_bash(s, "ls -la > files.txt", **kw))
    assert p.stdout.strip() == "", "redirecting command must not be rewritten"
    assert any("own redirect" in d for d in oracle.decisions(test_env, s.sid))
    if agent_id:
        assert not s.live("fg"), "subagent fg must NOT claim the fg slot"
        assert any(a == "write" and ":subfg:" in path
                   for (path, a, _c) in oracle.state_files(test_env, s.sid))
    else:
        assert s.live("fg"), "redirect path still claims the fg slot"


@pytest.mark.parametrize("agent_id", [None, "agent-x"])
def test_cmd_pre_midcommand_redirect_tees_both_paths(run_hook, test_env,
                                                     session, agent_id):
    # Statement-scoped parse_redirect on EVERY launch path: a command whose last
    # redirect is mid-command (the visible output goes to stdout after it — the
    # repro_summary.txt shape) must be tee-rewritten, not redirect-tailed, on
    # the main fg path AND the subagent fg path (cmd-fmt's bg path shares the
    # same tokenizer; its None fallback is Claude Code's own task output file).
    s = session.make()
    kw = dict(agent_id=agent_id) if agent_id else {}
    cmd = "cd sub && x >> sum.txt; sort sum.txt"
    p = run_hook("claude-cmd-pre.py", P.pre_bash(s, cmd, **kw))
    hso = json.loads(p.stdout)["hookSpecificOutput"]
    assert "tee -a" in hso["updatedInput"]["command"], \
        "mid-command redirect must fall back to the tee"
    assert any("rewrote command (tee)" in d
               for d in oracle.decisions(test_env, s.sid))


def test_cmd_pre_subagent_fg_optout(run_hook, test_env, session):
    s = session.make()
    env = dict(test_env, CLAUDE_MIRROR_LIVE_FG_SUB="0")
    p = run_hook("claude-cmd-pre.py", P.pre_bash(s, "echo hi", agent_id="agent-x"), env=env)
    assert p.stdout.strip() == ""
    assert any("CLAUDE_MIRROR_LIVE_FG_SUB=0" in d for d in oracle.decisions(test_env, s.sid))


def test_cmd_pre_escape_hatch(run_hook, test_env, session):
    s = session.make()
    env = dict(test_env, CLAUDE_MIRROR_LIVE_FG="0")
    p = run_hook("claude-cmd-pre.py", P.pre_bash(s, "echo hi"), env=env)
    assert p.stdout.strip() == ""
    assert not s.live("fg")


def test_cmd_pre_stale_fg_live_record_is_cleared(run_hook, test_env, session):
    """A cancelled command's fg-live record (dead tailer pid) must not wedge
    the next command out of live streaming."""
    s = session.make()
    run_hook("claude-cmd-pre.py", P.pre_bash(s, "echo one", tid="t1"))
    # kill the first tailer -> its pid in the record is now dead
    pid = s.live("fg")[0][2]
    import os, signal
    try:
        os.killpg(int(pid), signal.SIGKILL)
    except OSError:
        pass
    wait_until(lambda: not _alive(pid), desc="first fg tailer death")
    p = run_hook("claude-cmd-pre.py", P.pre_bash(s, "echo two", tid="t2"))
    assert "echo two" in json.loads(p.stdout)["hookSpecificOutput"]["updatedInput"]["command"]
    assert any(r[1] == "remove-stale" for r in oracle.state_files(test_env, s.sid))


def test_cmd_pre_fg_live_hand_put_failure_retried_and_audited(run_hook, test_env,
                                                              session):
    """A failed fg-live hand_put loses the OUTCOME hand-off (the tailer is
    already spawned; cmd-fmt can't hand it the finish chip) — it must be
    retried once and both attempts audited WITH the tool_use_id context.
    A directory squatting on the state-DB path makes every connect fail."""
    import os
    s = session.make()
    os.makedirs(s.state_db)
    run_hook("claude-cmd-pre.py", P.pre_bash(s, "echo hi", tid="t-retry"))
    rows = [r for r in oracle.errors(test_env, s.sid)
            if "fg-live record" in (r[2] or "")]
    # one 'retrying' row + one final row = the retry actually ran, audited
    assert [r[2] for r in rows] == ["write fg-live record (retrying)",
                                    "write fg-live record"], rows
    assert all("t-retry" in (r[3] or "") for r in rows), rows


def _alive(pid):
    import os
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


# ------------------------------------------------------------- claude-cmd-fmt

def test_cmd_fmt_renders_finished_block(run_hook, test_env, session):
    s = session.make()
    run_hook("claude-cmd-fmt.py", P.post_bash(s, "echo hi", stdout="hi\n"))
    text = s.ops_text()
    assert "echo hi" in text
    assert s.counters().get("commands") == 1
    assert not s.counters().get("failed")


def test_cmd_fmt_failure_event(run_hook, test_env, session):
    s = session.make()
    run_hook("claude-cmd-fmt.py",
             P.post_bash(s, "false", failure=True, error="exit status 1"))
    assert s.counters().get("commands") == 1
    assert s.counters().get("failed") == 1


def test_cmd_fmt_ignores_agent_events(run_hook, test_env, session):
    s = session.make()
    run_hook("claude-cmd-fmt.py", P.post_bash(s, "echo hi", agent_id="agent-x"))
    assert not s.ops(), "subagent command must be rendered by the substream only"
    assert not s.counters().get("commands")


# ------------------------------------------------------------ claude-file-fmt

def test_file_fmt_counts_diff_and_tool(run_hook, test_env, session):
    s = session.make()
    run_hook("claude-file-fmt.py", P.post_file(s, tool="Edit"))
    c = s.counters()
    assert c.get("tool:Edit") == 1
    assert c.get("added", 0) >= 1 and c.get("removed", 0) >= 1
    assert s.query_state("SELECT path FROM files"), "file set not updated"
    assert s.ops(), "no mirror line for the file op"


def test_file_fmt_ignores_agent_events(run_hook, test_env, session):
    s = session.make()
    run_hook("claude-file-fmt.py", P.post_file(s, tool="Edit", agent_id="agent-x"))
    assert not s.ops()
    assert not s.counters().get("tool:Edit")


def test_file_fmt_failure_does_not_inflate_diff(run_hook, test_env, session):
    s = session.make()
    run_hook("claude-file-fmt.py", P.post_file(s, tool="Edit", failure=True))
    c = s.counters()
    assert not c.get("added") and not c.get("removed"), \
        "a failed file op must not count +/- lines"


def test_file_fmt_scratchpad_gets_icon(run_hook, test_env, session):
    # A file op in the session scratchpad shows the ✎ icon (streamfmt.file_display)
    # so scratch-file churn is distinguishable from project edits; the audit
    # decision carries the [scratch] tag.
    from core.render import strip_ansi
    s = session.make()
    path = "/tmp/claude-503/-proj-slug/%s/scratchpad/notes.md" % s.sid
    run_hook("claude-file-fmt.py", P.post_file(s, tool="Write", path=path))
    assert "(✎ notes.md)" in strip_ansi(s.ops_text())
    assert any("[scratch]" in d for d in oracle.decisions(test_env, s.sid))


def test_file_fmt_outside_project_shows_location(run_hook, test_env, session):
    # A file op OUTSIDE the session cwd shows a dim abbreviated dir prefix —
    # a bare basename hid where the op landed. In-project ops stay bare.
    from core.render import strip_ansi
    s = session.make()
    run_hook("claude-file-fmt.py", P.post_file(s, tool="Edit", path="/etc/hosts",
                                               tid="toolu_out"))
    assert "(/etc/hosts)" in strip_ansi(s.ops_text())
    assert any("[out]" in d for d in oracle.decisions(test_env, s.sid))
    run_hook("claude-file-fmt.py", P.post_file(s, tool="Edit", tid="toolu_in"))
    assert "(example.py)" in strip_ansi(s.ops_text()), \
        "in-project op must stay a bare basename"


# --------------------------------------------------------- claude-monitor-fmt

def test_monitor_fmt_renders_and_spawns(run_hook, test_env, session):
    s = session.make()
    run_hook("claude-monitor-fmt.py", P.post_monitor(s))
    assert "monitor" in s.ops_text()
    assert s.live("monitor"), "no monitor slot claimed"
    assert any(p[2].startswith("stream:") for p in oracle.spawns(test_env, s.sid))


def test_monitor_fmt_renders_subagent_monitors_too(run_hook, test_env, session):
    """Deliberate exception to the agent_id guard (see plugins/claude_code/hookkit.py note)."""
    s = session.make()
    run_hook("claude-monitor-fmt.py", P.post_monitor(s, agent_id="agent-x"))
    assert "monitor" in s.ops_text()


def test_monitor_fmt_failure_closes_block_inline(run_hook, test_env, session):
    s = session.make()
    run_hook("claude-monitor-fmt.py",
             P.post_monitor(s, failure=True, error="boom"))
    assert "monitor" in s.ops_text()
    assert not oracle.spawns(test_env, s.sid), \
        "failed monitor (no taskId) must not spawn a tailer"


# -------------------------------------------------------- claude-subagent-fmt

def test_subagent_push_queues_description(run_hook, test_env, session):
    s = session.make()
    run_hook("claude-subagent-fmt.py", P.pre_task(s, "hunt the bug"), argv=("push",))
    rows = s.query_state("SELECT text FROM queue")
    assert rows and "hunt the bug" in rows[0][0]


def test_subagent_start_claims_slot_and_spawns_substream(run_hook, test_env, session):
    s = session.make()
    s.write_subagent_jsonl("agent-0001", [])
    run_hook("claude-subagent-fmt.py", P.subagent_start(s), argv=("start",))
    assert s.agents(), "no agents row after SubagentStart"
    assert any(p[2].startswith("stream:") for p in oracle.spawns(test_env, s.sid))
    assert s.ops(), "no header block for the subagent"


def test_subagent_duplicate_start_is_guarded(run_hook, test_env, session):
    """SubagentStart can fire twice for background agents (CLAUDE.md
    invariant) — the second must not claim a second slot or repeat the header."""
    s = session.make()
    s.write_subagent_jsonl("agent-0001", [])
    run_hook("claude-subagent-fmt.py", P.subagent_start(s), argv=("start",))
    ops_before = len(s.ops())
    slots_before = len(s.live())
    run_hook("claude-subagent-fmt.py", P.subagent_start(s), argv=("start",))
    assert len(s.live()) == slots_before, "duplicate start claimed another slot"
    assert len(s.ops()) == ops_before, "duplicate start repeated the header"


def test_subagent_stop_signals_streamer(run_hook, test_env, session):
    """The stop hook's job is the done signal; `done` itself is transient
    (reset once the streamer finalises), so pin the audited decision."""
    s = session.make()
    s.write_subagent_jsonl("agent-0001", [])
    run_hook("claude-subagent-fmt.py", P.subagent_start(s), argv=("start",))
    run_hook("claude-subagent-fmt.py", P.subagent_stop(s), argv=("stop",))
    stops = [d for d in oracle.decisions(test_env, s.sid, "claude-subagent-fmt.py")
             if d.startswith("stop:")]
    assert stops, "no audited stop decision"
    # duplicate stop tolerated
    run_hook("claude-subagent-fmt.py", P.subagent_stop(s), argv=("stop",))


# ------------------------------------------------------------ claude-task-fmt

def _seed_task_dir(test_env, s, tasks):
    """Claude Code's on-disk task dir — <config>/tasks/session-<first uuid
    segment>/<id>.json, the format task_fmt.tasks_dir() reads. Re-stated here
    to pin it (like the Session path arithmetic)."""
    import os
    d = os.path.join(test_env["CLAUDE_CONFIG_DIR"], "tasks",
                     "session-" + s.sid.split("-")[0])
    os.makedirs(d, exist_ok=True)
    for t in tasks:
        with open(os.path.join(d, "%s.json" % t["id"]), "w", encoding="utf-8") as f:
            json.dump(t, f)
    return d


def _tasks_kv(s):
    rows = s.query_state("SELECT val FROM kv WHERE key='tasks'")
    return json.loads(rows[0][0]) if rows else None


_TASK = {"id": "1", "subject": "Ship it", "description": "ship the thing",
         "activeForm": "Shipping it", "status": "pending",
         "blocks": [], "blockedBy": []}


def test_task_fmt_created_and_completed(run_hook, test_env, session, seed):
    s = session.make()
    # a hosted session's state DB exists from SessionStart on (any product
    # write creates it); task_fmt itself deliberately never does
    seed.py("from core import state as ST; ST.kv_set(%r, 'seeded', 1)" % s.log)
    run_hook("claude-task-fmt.py", P.task_created(s, "7", "Ship it"))
    run_hook("claude-task-fmt.py", P.task_completed(s, "7", "Ship it"))
    text = s.ops_text()
    assert "Ship it" in text
    assert "7" in text


def test_task_fmt_snapshots_tasks_kv(run_hook, test_env, session, seed):
    # the web tasks card's source (docs/dashboard.md, *Web tasks*): every
    # task-touching hook re-reads the on-disk dir into the `tasks` kv
    s = session.make()
    seed.py("from core import state as ST; ST.kv_set(%r, 'seeded', 1)" % s.log)
    done = dict(_TASK, id="2", subject="Done thing", status="completed")
    _seed_task_dir(test_env, s, [done, _TASK])
    run_hook("claude-task-fmt.py", P.post_task_update(s, "1", "in_progress"))
    got = _tasks_kv(s)
    # id-sorted, full records, both statuses
    assert [t["id"] for t in got["tasks"]] == ["1", "2"]
    assert got["tasks"][0]["subject"] == "Ship it"
    assert got["tasks"][1]["status"] == "completed"
    assert any(a == "tasks" and '"write"' in c
               for _p, a, c in oracle.state_files(test_env, s.sid))
    # a PostToolUse snapshot paints NO mirror line (TaskCreated/Completed own those)
    assert "Ship it" not in s.ops_text()
    assert not oracle.errors(test_env, s.sid)
    # the dedicated events snapshot too — after the dir changed
    import os
    d = _seed_task_dir(test_env, s, [dict(_TASK, status="completed")])
    os.remove(os.path.join(d, "2.json"))
    run_hook("claude-task-fmt.py", P.task_completed(s, "1", "Ship it"))
    got = _tasks_kv(s)
    assert [t["id"] for t in got["tasks"]] == ["1"]
    assert got["tasks"][0]["status"] == "completed"


def test_task_fmt_resolves_drifted_task_dir(run_hook, test_env, session, seed):
    """After a --resume Claude Code keys the on-disk task dir by a FRESH
    internal id, not the sid (measured 2026-07-30, session 6e58ae19: the
    resumed process wrote session-275b8fdf while the snapshot kept re-reading
    the dead sid dir) — the snapshot must follow the dir that holds the
    event's task, pin it, and let a matching sid dir out-rank a stale pin."""
    import os
    s = session.make()
    seed.py("from core import state as ST; ST.kv_set(%r, 'seeded', 1)" % s.log)
    # the drifted dir holds the event's task; the sid dir doesn't exist
    drift = os.path.join(test_env["CLAUDE_CONFIG_DIR"], "tasks",
                         "session-deadbeef")
    os.makedirs(drift, exist_ok=True)
    with open(os.path.join(drift, "1.json"), "w", encoding="utf-8") as f:
        json.dump(dict(_TASK, subject="Drifted"), f)
    run_hook("claude-task-fmt.py", P.task_created(s, "1", "Drifted"))
    assert [t["subject"] for t in _tasks_kv(s)["tasks"]] == ["Drifted"]
    # the resolution was pinned + audited
    assert any(a == "tasks-dir" and '"pin"' in c
               for _p, a, c in oracle.state_files(test_env, s.sid))
    # a later status flip (id-only probe) stays on the pinned dir
    with open(os.path.join(drift, "1.json"), "w", encoding="utf-8") as f:
        json.dump(dict(_TASK, subject="Drifted", status="completed"), f)
    run_hook("claude-task-fmt.py", P.post_task_update(s, "1", "completed"))
    assert _tasks_kv(s)["tasks"][0]["status"] == "completed"
    # a FRESH task landing back in the SID dir out-ranks the pin — and un-pins
    _seed_task_dir(test_env, s, [dict(_TASK, id="2", subject="Fresh list")])
    run_hook("claude-task-fmt.py", P.task_created(s, "2", "Fresh list"))
    assert [t["subject"] for t in _tasks_kv(s)["tasks"]] == ["Fresh list"]
    assert any(a == "tasks-dir" and '"unpin"' in c
               for _p, a, c in oracle.state_files(test_env, s.sid))
    assert not oracle.errors(test_env, s.sid)


def test_task_fmt_id_only_flip_follows_the_freshest_dir(run_hook, test_env,
                                                        session, seed):
    """A TaskUpdate status flip probes by taskId ALONE (tool_input carries no
    subject), and tiny integer ids exist in EVERY list — so a stale sid dir
    that still holds <id>.json 'matches' just as well as the live drifted dir.
    Candidate ORDER cannot break that tie (the 6e58ae19 re-regression: the
    dead sid dir kept winning and re-stashing the dead list); the FRESHEST
    matched record must win, because the hook fires as the direct consequence
    of a write moments ago."""
    import os
    import time as _t
    s = session.make()
    seed.py("from core import state as ST; ST.kv_set(%r, 'seeded', 1)" % s.log)
    # the sid dir holds a STALE 1.json (the dead pre-resume list)…
    sid_dir = _seed_task_dir(test_env, s, [_TASK])
    old = _t.time() - 3600
    os.utime(os.path.join(sid_dir, "1.json"), (old, old))
    # …while the drifted dir holds a FRESH 1.json (the live list)
    drift = os.path.join(test_env["CLAUDE_CONFIG_DIR"], "tasks",
                         "session-feedf00d")
    os.makedirs(drift, exist_ok=True)
    with open(os.path.join(drift, "1.json"), "w", encoding="utf-8") as f:
        json.dump(dict(_TASK, subject="New life", status="in_progress"), f)
    run_hook("claude-task-fmt.py", P.post_task_update(s, "1", "in_progress"))
    got = _tasks_kv(s)
    assert [t["subject"] for t in got["tasks"]] == ["New life"]
    assert any(a == "tasks-dir" and '"pin"' in c
               for _p, a, c in oracle.state_files(test_env, s.sid))
    assert not oracle.errors(test_env, s.sid)


def test_task_fmt_agent_and_unhosted_guards(run_hook, test_env, session):
    import os
    # a subagent's inner task event never touches the main session's kv …
    s = session.make()
    _seed_task_dir(test_env, s, [_TASK])
    run_hook("claude-task-fmt.py",
             P.post_task_update(s, "1", agent_id="agent-0001"))
    # … and an UNHOSTED session (no state DB) must not have one created for it
    # (the ghost-DB rule — the DB's existence is the session-alive signal)
    run_hook("claude-task-fmt.py", P.post_task_update(s, "1"))
    run_hook("claude-task-fmt.py", P.task_created(s, "1", "Ship it"))
    assert not os.path.exists(s.state_db)
    assert not oracle.errors(test_env, s.sid)


# ------------------------------------------------------------ claude-stop-fmt

def test_stop_does_not_fold_otel_authoritative(run_hook, test_env, session):
    # A plain Stop no longer folds the transcript — cost is OTEL-authoritative
    # (the OTLP receiver updates the scoreboard live). The fold survives only as a
    # SessionEnd fallback (see test_session_end_fallback_folds_when_no_otel).
    s = session.make()
    s.add_assistant("msg_001", usage={"input_tokens": 100, "output_tokens": 50,
                                      "cache_creation_input_tokens": 0,
                                      "cache_read_input_tokens": 0})
    run_hook("claude-stop-fmt.py", P.stop(s))
    assert not s.counters().get("tokens"), "Stop should no longer fold (OTEL owns cost)"


def test_session_end_fallback_folds_when_no_otel(run_hook, test_env, session):
    # SessionEnd with no OTEL data (otel_seen absent) DOES fold, as the resilience
    # fallback so a telemetry-off session isn't $0.
    s = session.make()
    s.add_assistant("msg_001", usage={"input_tokens": 100, "output_tokens": 50,
                                      "cache_creation_input_tokens": 0,
                                      "cache_read_input_tokens": 0})
    run_hook("claude-stop-fmt.py", P.session_end(s))
    assert s.counters().get("tokens", 0) > 0, "SessionEnd fallback did not fold"


def test_stop_fmt_ignores_agent_stops(run_hook, test_env, session):
    s = session.make()
    s.add_assistant("msg_001")
    d = P.stop(s)
    d["agent_id"] = "agent-x"
    run_hook("claude-stop-fmt.py", d)
    assert not s.counters().get("tokens"), "agent Stop must not fold the main transcript"


# ---------------------------------------------------------- claude-tab-status

def test_tab_status_noops_without_kitty_env(run_hook, test_env, session):
    """No KITTY_WINDOW_ID / KITTY_LISTEN_ON -> exit 0, no tab DB row."""
    s = session.make()
    run_hook("claude-tab-status.py", P.user_prompt(s), argv=("thinking",))
    assert oracle.tab_state(test_env, "") is None


def test_tab_status_paints_and_records(run_hook, test_env, session, fake_kitten):
    s = session.make()
    run_hook("claude-tab-status.py", P.user_prompt(s), argv=("thinking",),
             env=test_env)
    calls = fake_kitten.calls("set-tab-color")
    assert calls, "no set-tab-color call recorded by the fake kitten"
    assert oracle.tab_state(test_env, fake_kitten.window_id) is not None


# ------------------------------------------------------------ core/paths (unit)

def test_paths_accessors():
    """core/paths is the ONE owner of the mirror-log path format — pin the
    accessors every other module must go through instead of re-encoding the
    format by hand (state_db suffix, verbatim-key log path)."""
    import sys
    from conftest import REPO
    if REPO not in sys.path:
        sys.path.insert(0, REPO)
    from core import paths as CP

    log = CP.mirror_log("abc-123")
    assert log == CP.PREFIX + "abc-123.log"
    # state_db: the .state.db suffix lives here and only here.
    assert CP.state_db(log) == log + ".state.db"
    assert CP.sid_from_log(CP.state_db(log)) == "abc-123"
    # mirror_log sanitizes its input...
    assert CP.mirror_log("a/b c") == CP.PREFIX + "a-b-c.log"
    # ...log_for_key deliberately does NOT: an already-formed key (recovered
    # from a path/URL) must round-trip verbatim.
    assert CP.log_for_key("a-b-c") == CP.PREFIX + "a-b-c.log"
    assert CP.log_for_key("a/b c") == CP.PREFIX + "a/b c.log"
    # And the two agree on any already-sanitized key.
    key = CP.sanitize_sid("weird sid!*")
    assert CP.log_for_key(key) == CP.mirror_log("weird sid!*")


def test_paths_root_is_repo_root():
    """core/paths.ROOT / core/paths.BIN are the ONE owners of repo-root and
    entry-directory derivation (every module that spawns an ENTRY script by
    filename joins against BIN). Pin that ROOT resolves to the actual repo root
    and that the known entry scripts live in BIN."""
    import os
    import sys
    from conftest import REPO
    if REPO not in sys.path:
        sys.path.insert(0, REPO)
    from core import paths as CP

    assert os.path.isabs(CP.ROOT)
    assert os.path.samefile(CP.ROOT, REPO)
    assert os.path.samefile(CP.BIN, os.path.join(REPO, "bin"))
    for shim in ("claude-hook.py", "claude-stream.py", "claude-tab-status.py",
                 "claude-mirror.py", "claude-codex-launch.py",
                 "claude-otlp-launch.py"):
        assert os.path.isfile(os.path.join(CP.BIN, shim)), shim


# --- the plugin PROVIDER surface ------------------------------------------------
# frontends/ has had a declared interface (frontends/base.Frontend) and a contract
# test since it existed. plugins/ is the same problem — a registry of optional
# duck-typed functions reached by name — and had neither: a provider whose name
# was misspelled, or whose signature drifted from the fan-out calling it, was
# never an error. It was simply never found, and the feature degraded silently to
# "no plugin answered". `plugins.PROVIDERS` declares the surface; these two pin it
# against reality in both directions.

def test_every_plugin_fanout_reaches_a_declared_provider():
    """Every provider name the fan-outs reach for is DECLARED in PROVIDERS.

    The direction that actually breaks: a fan-out is written (or renamed) with a
    name no plugin defines. `getattr(p, name, None)` finds nothing on every
    plugin, the fan-out returns its default, and the feature is quietly off with
    no error, no audit row and no failing test. Routing every lookup through
    `plugins.provider()` turns that into a KeyError at the call — but only if the
    table is complete, which is what this checks by parsing the fan-outs."""
    import ast
    import inspect
    import plugins

    src = inspect.getsource(plugins)
    tree = ast.parse(src)
    reached = set()
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        fn = n.func
        # the fan-out primitives take the name as their first argument (_named
        # is the single-plugin router — host-scoped slash_commands rides it;
        # _first_owner the sid-keyed one that routes to the session's owner) …
        if isinstance(fn, ast.Name) and fn.id in ("_first", "_first_path",
                                                  "_first_owner",
                                                  "_concat_unique", "_named"):
            a = n.args[0]
            if isinstance(a, ast.Constant):
                reached.add(a.value)
        # … and the hand-rolled loops go through provider(p, "<name>")
        if isinstance(fn, ast.Name) and fn.id == "provider" and len(n.args) > 1:
            a = n.args[1]
            if isinstance(a, ast.Constant):
                reached.add(a.value)
    assert reached, "no fan-outs found — the parse broke, not the contract"
    undeclared = sorted(reached - set(plugins.PROVIDERS))
    assert not undeclared, "fan-outs reach undeclared providers: %s" % undeclared
    # and nothing may reach a plugin attribute by a bare getattr any more: that
    # is the door the table exists to close
    assert "getattr(p," not in src, "a fan-out bypassing plugins.provider()"
    # every declared provider is actually used by some fan-out — a row nothing
    # calls is a promise to plugin authors that nothing keeps
    unused = sorted(set(plugins.PROVIDERS) - reached)
    assert not unused, "PROVIDERS rows no fan-out calls: %s" % unused


def test_plugin_providers_match_the_declared_arity():
    """Every provider a plugin actually defines accepts the arity its fan-out
    calls it with.

    A signature that drifts is the second silent failure: the name resolves, the
    call raises TypeError, and because most fan-outs are read-side (dashboards,
    the scorebar) it surfaces as a broken card rather than a pointed error.
    `min_args` is the smallest count any fan-out passes positionally; a provider
    may accept more only with defaults (`context(path, main=False)`), and *args
    accepts anything."""
    import inspect
    import plugins

    seen = {}
    for p in plugins.all_plugins():
        for name, min_args in plugins.PROVIDERS.items():
            fn = getattr(p, name, None)
            if fn is None:
                continue
            seen.setdefault(name, []).append(p.__name__)
            sig = inspect.signature(fn)
            params = list(sig.parameters.values())
            if any(q.kind is inspect.Parameter.VAR_POSITIONAL for q in params):
                continue
            positional = [q for q in params
                          if q.kind in (inspect.Parameter.POSITIONAL_ONLY,
                                        inspect.Parameter.POSITIONAL_OR_KEYWORD)]
            required = sum(1 for q in positional
                           if q.default is inspect.Parameter.empty)
            where = "%s.%s%s" % (p.__name__, name, sig)
            assert len(positional) >= min_args, \
                "%s takes fewer than the %d args its fan-out passes" % (where, min_args)
            assert required <= min_args, \
                "%s requires more than the %d args its fan-out passes" % (where, min_args)
    # the host implements nearly all of them; a table row NO plugin implements is
    # dead weight that reads as supported
    orphans = sorted(set(plugins.PROVIDERS) - set(seen))
    assert not orphans, "declared providers no plugin implements: %s" % orphans


# --- the path-keyed OWNERSHIP gate (owns / owns_by / _first_path) -----------------
# PROVIDERS says what a plugin may be asked and with what arity; it cannot say
# WHICH FILES a plugin may be asked about, and first-plugin-wins is therefore
# first-PARSER-wins. That is not hypothetical: every Claude transcript reader here
# is bounded and fails open by design — `prompt_count` returns its cap for any file
# over PROMPT_SCAN_B without reading a byte of it — so `plugins.prompts()` answered
# 8 human prompts for a 429KB CODEX ROLLOUT. The size of another tool's file decided
# a Claude-shaped answer, and the ⊜ compact gate believed it.
#
# So the path-keyed fan-outs go through `_first_path`, which skips a plugin whose
# declared `owns(path)` says no. This is the corpus that pins both halves: a real
# Claude transcript still answers exactly as before, and a rollout answers with the
# empty defaults instead of the host's.

def _claude_transcript(tmp_path, sid="ac9f0f2e-0000-4000-8000-000000000001"):
    """A file at Claude Code's OWN on-disk layout — `<…>/projects/<cwd-hash>/
    <sid>.jsonl` — carrying a summary record and one real human prompt. The
    layout is the fixture: `owns` answers from it, deliberately, because a
    bounded parser cannot answer from content it never reads."""
    import os
    d = tmp_path / "projects" / "-Users-me-code-proj"
    d.mkdir(parents=True, exist_ok=True)
    p = d / (sid + ".jsonl")
    recs = [{"type": "summary", "summary": "My session"},
            {"type": "user", "message": {"role": "user",
                                         "content": "count the widgets"}}]
    p.write_text("".join(json.dumps(r) + "\n" for r in recs), encoding="utf-8")
    return os.path.join(str(d), sid + ".jsonl")


def _codex_rollout(tmp_path):
    """A codex rollout at ITS layout (`sessions/<Y>/<M>/<D>/rollout-….jsonl`),
    deliberately bigger than transcript.PROMPT_SCAN_B — the measured shape of
    the bug. Its records carry none of the byte prefilters the Claude probes
    look for, so every wrong answer this file can produce comes from a fast
    path that never read it."""
    import os
    from plugins.claude_code import transcript
    d = tmp_path / ".codex" / "sessions" / "2026" / "07" / "29"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "rollout-2026-07-29T10-00-00-0f0f0f0f.jsonl"
    line = json.dumps({"type": "response_item",
                       "payload": {"type": "message", "role": "human",
                                   "text": "x" * 400}}) + "\n"
    n = transcript.PROMPT_SCAN_B // len(line) + 8      # comfortably over the cap
    p.write_text(line * n, encoding="utf-8")
    assert os.path.getsize(str(p)) > transcript.PROMPT_SCAN_B
    return str(p)


def _agent_sidecar(tpath, agent_id="0001"):
    """One of an owned transcript's per-agent sidecars, at the layout
    transcript.agent_paths derives — `<sid>/subagents/agent-<id>.jsonl`."""
    import os

    from plugins.claude_code import transcript
    jsonl, _meta = transcript.agent_paths(tpath, agent_id)
    os.makedirs(os.path.dirname(jsonl), exist_ok=True)
    with open(jsonl, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "user",
                             "message": {"role": "user", "content": "go"}}) + "\n")
    return jsonl


def test_ownership_covers_the_agent_sidecars_too(tmp_path):
    """An AGENT transcript is ours as well, and the gate has to say so: the
    dashboard's per-agent ctx bars call plugins.context() with the streams
    keystone `src_path`, i.e. `…/<sid>/subagents/agent-<id>.jsonl`
    (dashboard/read/session.agents_ctx, main=False — an agent transcript is its
    sidechain turns). An `owns` that recognised only the SESSION layout would
    have silently blanked every agent's bar. Renaming one, though, is
    meaningless — which is why `renameable` stays the narrower predicate."""
    import plugins

    from plugins.claude_code import transcript

    agent = _agent_sidecar(_claude_transcript(tmp_path))
    assert transcript._agent_transcript(agent) is True   # claimed by LAYOUT
    assert plugins.owns_by(agent) == "claude_code"
    assert plugins._owns(plugins.claude_code, agent) is True
    assert plugins.renameable(agent) is False
    assert plugins.set_session_title(agent, "nope") is None


def test_ownership_falls_back_to_the_first_record(tmp_path):
    """A transcript that is OURS but not where we expect — a relocated config
    dir, a copy, most of the suite's own fixtures — is still ours: the bounded
    head read finds a record only Claude Code writes. The fallback exists
    because "not at the layout" must not mean "not mine"; it cannot claim a
    codex rollout, whose records are its own top-level vocabulary
    (session_meta/response_item/…), which is exactly the collision the
    top-level-`type`-only rule avoids."""
    import plugins

    stray = tmp_path / "stray.jsonl"
    stray.write_text(json.dumps({"type": "user",
                                 "message": {"role": "user", "content": "hi"}})
                     + "\n", encoding="utf-8")
    assert plugins.owns_by(str(stray)) == "claude_code"
    assert plugins.prompts(str(stray)) == 1

    theirs = tmp_path / "theirs.jsonl"
    theirs.write_text(json.dumps({"type": "session_meta",
                                  "payload": {"id": "0f0f"}}) + "\n",
                      encoding="utf-8")
    assert plugins.owns_by(str(theirs)) is None
    assert plugins.prompts(str(theirs)) is None


def test_owns_by_names_one_owner_at_most(tmp_path):
    """`owns_by` returns the owning plugin's short name, None when nobody
    claims the file — and NO path is claimed by two plugins. The last part is
    the invariant that keeps first-plugin-wins meaningful now that a second tool
    declares ownership: claude_code owns its transcripts, codex owns its rollouts,
    and no path is claimed by both."""
    import plugins

    tpath, rollout = _claude_transcript(tmp_path), _codex_rollout(tmp_path)
    assert plugins.owns_by(tpath) == "claude_code"
    assert plugins.owns_by(rollout) == "codex"
    assert plugins.owns_by(str(tmp_path / "projects" / "h" / "gone.jsonl")) is None
    assert plugins.owns_by("") is None
    for path in (tpath, rollout):
        claimers = []
        for p in plugins.all_plugins():
            fn = plugins.provider(p, "owns")
            if fn is not None and fn(path):
                claimers.append(p.__name__)
        assert len(claimers) <= 1, "two plugins claim %s: %s" % (path, claimers)


def test_path_keyed_fanouts_still_answer_for_an_owned_transcript(tmp_path):
    """The no-regression half: for a real Claude transcript the gate changes
    nothing — every path-keyed fan-out answers exactly what its provider says."""
    import plugins

    tpath = _claude_transcript(tmp_path)
    assert plugins.session_title(tpath) == "My session"
    assert plugins.title_and_rename(tpath) == ("My session", "")
    assert plugins.renameable(tpath)
    assert plugins.prompts(tpath) == 1
    # read by the same owned path, with nothing to report — a real answer, not
    # a refusal (both probes return None on a transcript with no usage/goal)
    assert plugins.context(tpath) is None
    assert plugins.goal(tpath) is None


def test_a_rollout_is_owned_by_codex_not_the_claude_parser(tmp_path):
    """The bug this exists for: `plugins.prompts(<codex rollout>)` was 8 — the
    Claude parser answering for a file that is not its own.

    The fix is ownership, and now that CODEX declares it, the fan-out routes a
    rollout to codex and SKIPS the Claude parser. codex may still fail open on a
    big rollout (its compact gate does, exactly as the Claude one does for a big
    transcript — the count only ever argues for DISABLING a button), but that is
    codex's honest answer about codex's file: the leak was the WRONG plugin
    answering, and it no longer does. `transcript.prompt_count` called DIRECTLY
    still fails open — the point is that the fan-out no longer calls it here."""
    import plugins
    from plugins import claude_code
    from plugins.claude_code import transcript

    rollout = _codex_rollout(tmp_path)
    assert transcript.prompt_count(rollout) == transcript.PROMPT_CAP, \
        "the Claude parser's fail-open fast path is the premise of this test"
    # the Claude parser is skipped; codex owns and answers
    assert plugins.owns_by(rollout) == "codex"
    assert plugins._owns(claude_code, rollout) is False
    assert plugins.prompts(rollout) == transcript.PROMPT_CAP  # codex's own fail-open
    assert plugins.renameable(rollout) is True
    # nothing to report from this rollout's CONTENT (no title/usage/goal in it)
    assert plugins.session_title(rollout) == ""
    assert plugins.title_and_rename(rollout) == ("", "")
    assert plugins.context(rollout) is None
    assert plugins.goal(rollout) is None                     # codex has no goal provider


def test_set_session_title_never_writes_a_claude_record_to_a_rollout(tmp_path):
    """The one path-keyed fan-out that WRITES: a rollout must never grow a Claude
    `agent-name` record. The Claude owner takes one; codex owns the rollout and
    writes its OWN name store (its `threads.title` index, absent in this fixture,
    so its write returns False) — never the rollout FILE, so it stays byte-stable
    either way."""
    import os

    import plugins

    tpath, rollout = _claude_transcript(tmp_path), _codex_rollout(tmp_path)
    before = os.path.getsize(rollout)
    assert plugins.set_session_title(tpath, "Renamed by the web") is True
    assert plugins.session_title(tpath) == "Renamed by the web"
    # codex owns the rollout: it writes threads.title (no index here -> False),
    # NOT the rollout file — so the rollout never gains a Claude agent-name record
    assert plugins.set_session_title(rollout, "Renamed by the web") is False
    assert os.path.getsize(rollout) == before


def test_a_plugin_without_owns_is_asked_exactly_as_before(tmp_path, monkeypatch):
    """Ownership is OPT-IN: the gate skips a plugin only when that plugin
    DECLARES `owns`. A plugin that never said which files are its own keeps
    being asked about everything, which is what made this change a no-op for
    otel — and what stops a future plugin going silent by omission.

    `otel` is the example now that codex declares `owns` (its rollout ownership);
    otel is a cross-cutting subsystem, not an agent tool, and has no files of its
    own, so it is the plugin still asked about everything."""
    import plugins
    from plugins import claude_code, otel
    from plugins.claude_code import transcript

    rollout = _codex_rollout(tmp_path)
    assert plugins._owns(otel, rollout) is True              # declares no owns
    assert plugins._owns(claude_code, rollout) is False
    monkeypatch.delattr(claude_code, "owns")
    assert plugins.prompts(rollout) == transcript.PROMPT_CAP, \
        "without an `owns` the host is asked again — the pre-gate behaviour"


# --- the HOST control surface (plugins.host) --------------------------------------
# frontends/ has a declared Frontend base + a contract test; PROVIDERS has the
# same for the READ fan-outs. The HOST control interface (plugins.host.HostControl)
# is the WRITE-side equivalent — one class, inert defaults, and caps DERIVED from
# which gestures a subclass overrode (never an authored {name: bool}). These pin
# that derivation in both directions, exactly as the two PROVIDERS tests above do.

def test_host_caps_derive_from_declared_gestures_both_directions():
    """Every declared GESTURE is a method on HostControl, and caps() reports
    exactly the declared gestures — no cap without a method, no method without a
    cap. The inert base overrides nothing, so every cap is False (the derivation,
    not a hand-written table)."""
    from plugins.host import GESTURES, HostControl
    for g in GESTURES:
        assert callable(getattr(HostControl, g, None)), "gesture %r missing" % g
    base_caps = HostControl().caps()
    assert set(base_caps) == set(GESTURES)
    assert base_caps == {g: False for g in GESTURES}


def test_host_caps_are_derived_not_authored():
    """A subclass's caps follow WHICH gestures it overrode — the one-source-of-
    truth rule. Overriding one flips exactly that bit; overriding none stays
    all-False even though it IS a subclass (identity of the function object is
    what caps() reads, so a shared/looped method would wrongly read False)."""
    from plugins.host import GESTURES, HostControl

    class OneGesture(HostControl):
        def interrupt(self, fe, win, ctx):
            return self._ack()

    caps = OneGesture().caps()
    assert caps["interrupt"] is True
    assert all(v is False for k, v in caps.items() if k != "interrupt")

    class Empty(HostControl):
        pass

    assert Empty().caps() == {g: False for g in GESTURES}


def test_claude_code_host_drives_every_gesture():
    """The host tool's caps read all-True — which is what keeps the dashboard's
    _caps_guard a no-op for a Claude session (byte-identical control plane)."""
    import plugins
    from plugins.host import GESTURES

    h = plugins.host_named("claude_code")
    assert h is not None and h.name == "claude_code" and h.launchable is True
    assert h.label
    assert h.caps() == {g: True for g in GESTURES}


def test_hosts_and_host_of(tmp_path):
    """hosts() enumerates the launchable tools for the new-session picker (both
    claude_code and codex now); host_of resolves a path to its owning host via
    owns_by — claude_code for a transcript, codex for a rollout, None for an
    empty/unknown path."""
    import plugins
    from plugins.host import GESTURES

    hs = {h["name"]: h for h in plugins.hosts()}
    assert "claude_code" in hs and "codex" in hs
    assert hs["claude_code"]["launchable"] is True and hs["claude_code"]["label"]
    assert hs["codex"]["launchable"] is True and hs["codex"]["label"]

    tpath, rollout = _claude_transcript(tmp_path), _codex_rollout(tmp_path)
    assert plugins.host_of(tpath).name == "claude_code"
    assert plugins.host_of(rollout).name == "codex"
    assert plugins.host_of("") is None
    assert plugins.host_named("nope") is None
    assert plugins.host_caps("claude_code")["interrupt"] is True
    # codex drives its SUPPORTED gestures: interrupt/compact/rename/ask/plan and
    # model/effort True (model/effort via codex's INTERACTIVE /model picker), the
    # rest inert (no rewind/migrate) — derived from the overridden methods, not an
    # authored dict. `send` reads True since P2: it is not caps-GATED (the
    # composer is always reachable), but it is now a real gesture with a real
    # body, and the derivation would be lying if a host with a send body read
    # False.
    assert plugins.host_caps("codex") == {
        "interrupt": True, "send": True, "rename": True, "rewind": False,
        "migrate": False, "compact": True, "model": True, "effort": True,
        "ask": True, "plan": True}
    assert set(plugins.host_caps("codex")) == set(GESTURES)
    assert plugins.host_caps("nope") == {}


def test_a_third_tool_degrades_cleanly_not_codex_shaped(tmp_path, monkeypatch):
    """The abstraction isn't codex-shaped: a FAKE host that owns a path but
    leaves every gesture inert reads all-caps-False, and session_caps hands the
    dashboard that host's REAL NAME + the restricted map — so the client greys
    every button and _caps_guard 409s it. This is the copilot/opencode path
    proven before either exists. (`host` used to blank to "" for a non-default
    owner; P1 serves the owner's own name — see session_caps.)"""
    import plugins
    from dashboard.read import session as rsession
    from plugins.host import GESTURES, HostControl

    class FakeHost(HostControl):
        name = "faketool"
        label = "Fake Tool"

    monkeypatch.setattr(plugins, "owns_by", lambda p: "faketool" if p else None)
    monkeypatch.setattr(plugins, "host_caps",
                        lambda name: FakeHost().caps() if name == "faketool" else {})
    host, caps = rsession.session_caps("/some/rollout.jsonl")
    assert host == "faketool"                     # the REAL owner, not a sentinel
    assert caps == {g: False for g in GESTURES}   # every gesture unsupported


def test_session_caps_defaults_open_for_an_empty_path():
    """The load-bearing edge case: an EMPTY transcript_path (a daemon-origin
    scrubbed-env session, or a row written before the .jsonl exists) is a
    legitimate Claude session and must NOT fail closed — session_caps defaults
    it to the Claude host with FULL caps, so its control plane stays live."""
    from dashboard.read import session as rsession
    from plugins.host import GESTURES

    host, caps = rsession.session_caps("")
    assert host == "claude_code"
    assert caps == {g: True for g in GESTURES}


def test_session_caps_full_for_an_owned_claude_transcript(tmp_path):
    """A path claude_code genuinely owns resolves to the Claude host + full
    caps (the no-regression half — session_caps changes nothing for a real
    Claude session)."""
    from dashboard.read import session as rsession
    from plugins.host import GESTURES

    host, caps = rsession.session_caps(_claude_transcript(tmp_path))
    assert host == "claude_code"
    assert caps == {g: True for g in GESTURES}


def test_caps_guard_passes_full_caps_and_409s_a_missing_cap(monkeypatch):
    """http/base._caps_guard: proceed (False, no response, no row) when the
    owning host HAS the cap — the claude_code no-op that keeps the control plane
    byte-identical — and 409 + a `web-*` ok:False row when it does NOT. Driven
    against a minimal fake handler so it needs no HTTP socket."""
    import plugins
    from dashboard.http import base
    from plugins.host import GESTURES

    rows = []
    monkeypatch.setattr(base.A, "state_file",
                        lambda log, path, action, content: rows.append((action, content)))

    class Fake:
        _caps_guard = base._Base._caps_guard

        def __init__(self):
            self.responses = []

        @staticmethod
        def _audit_target(sid):
            return {"transcript_path": "/x.jsonl"}, "log-key", "/tmp/x.state.db"

        def _json(self, obj, code=200):
            self.responses.append((code, obj))

    f = Fake()
    # a claude_code-owned session has every cap True -> the guard is a no-op
    monkeypatch.setattr(plugins, "owns_by", lambda p: "claude_code")
    assert f._caps_guard("sid", "interrupt", "web-interrupt") is False
    assert f.responses == [] and rows == []

    # a session owned by a tool whose host leaves interrupt inert -> 409 + row
    monkeypatch.setattr(plugins, "owns_by", lambda p: "faketool")
    monkeypatch.setattr(plugins, "host_caps",
                        lambda name: {g: False for g in GESTURES})
    assert f._caps_guard("sid", "interrupt", "web-interrupt") is True
    assert f.responses[-1][0] == 409
    assert f.responses[-1][1]["cap"] == "interrupt"
    action, content = rows[-1]
    assert action == "web-interrupt"
    assert content["ok"] is False and content["cap"] == "interrupt"


# --- the dashboard -> plugin BOUNDARY ---------------------------------------------
# `plugins.PROVIDERS` says WHAT a plugin may be asked; this says WHO may ask it
# directly. frontends/ has had both halves for a long time (the `Frontend` base
# class plus test_no_caller_outside_frontends_uses_kitty_internals); plugins had
# only the first, so the layering rule CLAUDE.md and docs/styleguide.md both
# state — the dashboard imports core, the plugins REGISTRY ROOT, and frontends —
# had drifted at seven sites with nothing to notice, and the styleguide's own
# ownership table sanctioned five of them in passing. Two statements that
# disagree are worse than either: a reader cannot tell a deliberate reach from
# a fresh one.
#
# So the exceptions are a LIST, with a reason each, and a new reach fails here
# until it is either routed through a provider or written down.
DASHBOARD_PLUGIN_REACHES = {
    # The web PRESENTER of Claude Code's own tool payloads. op_items renders ops
    # a claude_code formatter produced, so the file-op verb table and the
    # payload shapes are the same single-owner facts the terminal mirror reads
    # (docs/styleguide.md rows for tools.FILE_LABEL / the file-op payload
    # shapes). Routing them through a provider would mean a fan-out per lookup
    # inside a per-op render loop.
    # …and the same argument for the two other producer vocabularies it reads
    # BACK off a painted op: the task line's ✚/✓ glyphs (task_fmt) and team
    # mail's ●/◉ + colours (msgs.event_ops). A paint op carries no "this is a
    # task/mail row" field, so the classifier recovers the class from the glyph —
    # which makes the glyph a shared fact needing ONE owner, and the owner is the
    # producer. A provider fan-out cannot serve it: this is a comparison inside a
    # per-op loop, and the ONLY alternative is respelling the glyphs here, which
    # is exactly the drift that had `◉ read · …` classified as a MONITOR.
    # (P6 removed this file's `tools` reach: the file-op VERB set and the act
    # each names come from core/streamfmt.FILE_ACTS now — beside the shape that
    # paints them, and the same table for every host. The two glyph reaches stay:
    # mail's ●/◉ and a task row's ✚/✓ are their producers' vocabulary and the
    # presenter is their one reader.)
    "dashboard/opshtml/actclass.py": {"plugins.claude_code.msgs",
                                      "plugins.claude_code.task_fmt"},
    "dashboard/opshtml/tools.py": {"plugins.claude_code.tools"},
    # op_html strips Claude Code's injected <system-reminder> blocks out of a
    # subagent's brief/result body, for ops written before producers did it
    # themselves — parked ops cannot be re-stamped. The strip's owner is
    # transcript.py (it is a fact about Claude Code's transcript TEXT), and calling
    # it is strictly better than a second regex here.
    "dashboard/opshtml/ops.py": {"plugins.claude_code.transcript"},
    # The memory EXTENSION (dashboard/ext/memory) IS a claude_code feature end
    # to end: the vault root, the project scope gate and the note/backlink
    # readers all belong to plugins/claude_code/memory.py — an extension
    # reaching its OWN plugin vocabulary module is the sanctioned shape
    # (docs/styleguide.md *Layering*); the generic read model no longer touches
    # it (read/session applies every extension's gate through ext.badge_rows).
    "dashboard/ext/memory/read.py": {"plugins.claude_code.memory"},
    # (P1) dashboard/read/session.py is GONE from this list. It reached
    # `plugins.claude_code.model` (short_model / model_default_effort, applied to
    # every host's model ids) and `plugins.claude_code.accounting` (cost_usd,
    # applied to whatever any plugin's agent_usage returned) — both now answered
    # by the OWNING host: HostControl.model_short/model_default_effort resolved
    # through plugins.host_of on the row's own transcript, and `cost` folded into
    # the agent_usage provider's return.
    # (P2) dashboard/http/post/interrupt.py is GONE from this list. It reached
    # `plugins.claude_code.transcript` for the queue-drain check and the
    # take-back flag — both inside the interrupt BODY, which moved into
    # ClaudeCodeHost.interrupt with the rest of the gesture. The endpoint now
    # calls host.interrupt(...) and knows neither.
}


def test_no_dashboard_module_reaches_a_plugin_internal_off_the_allowlist():
    """Every `from plugins.<tool> import ...` inside dashboard/ is on the
    allowlist above, with the module it reaches.

    The rule is that the dashboard talks to plugins through the registry root
    (`import plugins` + the PROVIDERS fan-outs) — that is what lets a second
    host tool answer the same question. Each exception here is a fact that is
    genuinely Claude-Code-specific AND sits on a per-op render path or is a
    single-owner table the styleguide already assigns; the point of the list is
    that a new one has to be argued rather than typed."""
    import ast
    import os

    from conftest import REPO

    offenders = []
    dash = os.path.join(REPO, "dashboard")
    for root, dirs, files in os.walk(dash):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in sorted(files):
            if not f.endswith(".py"):
                continue
            path = os.path.join(root, f)
            rel = os.path.relpath(path, REPO)
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), path)
            allowed = DASHBOARD_PLUGIN_REACHES.get(rel, set())
            for n in ast.walk(tree):
                mod = None
                if isinstance(n, ast.ImportFrom) and (n.module or "").startswith("plugins."):
                    mod = n.module
                elif isinstance(n, ast.Import):
                    for a in n.names:
                        if a.name.startswith("plugins."):
                            mod = a.name
                if mod is None:
                    continue
                # `from plugins.claude_code import tools as T` names the package
                # in `module` and the module in `names` — normalise both spellings
                if isinstance(n, ast.ImportFrom):
                    for a in n.names:
                        full = mod if mod.count(".") > 1 else mod + "." + a.name
                        if full not in allowed and mod not in allowed:
                            offenders.append("%s:%d: %s" % (rel, n.lineno, full))
                elif mod not in allowed:
                    offenders.append("%s:%d: %s" % (rel, n.lineno, mod))
    assert offenders == [], (
        "dashboard modules reaching plugin internals off the allowlist "
        "(route it through plugins.PROVIDERS, or add it to "
        "DASHBOARD_PLUGIN_REACHES with a reason):\n" + "\n".join(offenders))


def test_the_dashboard_plugin_allowlist_has_no_stale_rows():
    """A row nobody needs any more is the other half of the drift: it keeps
    saying a coupling exists after it has been routed through a provider, so
    the next reader re-learns a shape the repo no longer has."""
    import os

    from conftest import REPO

    stale = [rel for rel in DASHBOARD_PLUGIN_REACHES
             if not os.path.isfile(os.path.join(REPO, rel))]
    assert stale == [], "allowlist rows for files that no longer exist: %s" % stale
    unused = []
    for rel, mods in DASHBOARD_PLUGIN_REACHES.items():
        with open(os.path.join(REPO, rel), encoding="utf-8") as fh:
            src = fh.read()
        for mod in sorted(mods):
            leaf = mod.rsplit(".", 1)[1]
            if ("import " + leaf) not in src and mod not in src:
                unused.append((rel, mod))
    assert unused == [], "allowlist rows nothing reaches any more: %s" % unused


# --- the dashboard EXTENSION registry (dashboard/ext) -------------------------
# The abstraction's whole point is that adding an extension edits NO core
# dashboard file — which is only true while core files talk to dashboard.ext
# (the registry root) and never to dashboard.ext.<name>. Same two-way
# enforcement style as DASHBOARD_PLUGIN_REACHES above.

def test_no_module_outside_ext_imports_an_extension_package():
    """Only dashboard/ext/ itself may import dashboard.ext.<name>. Everything
    else — read/, http/, notify/, opshtml/ — goes through the registry root's
    fan-outs (ext.badge_rows / ext.session_gets / …), which is what keeps a new
    extension a new package plus one all_ext() line."""
    import ast
    import os

    from conftest import REPO

    offenders = []
    dash = os.path.join(REPO, "dashboard")
    for root, dirs, files in os.walk(dash):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in sorted(files):
            if not f.endswith(".py"):
                continue
            path = os.path.join(root, f)
            rel = os.path.relpath(path, REPO)
            if rel.startswith("dashboard/ext" + os.sep) \
                    or rel == "dashboard/ext/__init__.py":
                continue
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), path)
            for n in ast.walk(tree):
                mods = []
                if isinstance(n, ast.ImportFrom):
                    mod = n.module or ""
                    if mod.startswith("dashboard.ext."):
                        mods = [mod]
                    elif mod == "dashboard.ext":
                        mods = ["dashboard.ext." + a.name for a in n.names]
                elif isinstance(n, ast.Import):
                    mods = [a.name for a in n.names
                            if a.name.startswith("dashboard.ext.")]
                offenders += ["%s:%d: %s" % (rel, n.lineno, m) for m in mods]
    assert offenders == [], (
        "core dashboard files reaching an extension package (go through the "
        "dashboard.ext registry root):\n" + "\n".join(offenders))


def test_every_extension_conforms_to_the_declared_surface():
    """Each registered extension carries the required constants with the right
    types, and every capability it provides is a callable SURFACE declares —
    the Python half of the descriptor contract (the JS half is pinned by the
    jsdom extension test)."""
    from dashboard import ext

    for e in ext.all_ext():
        name = e.__name__
        assert isinstance(e.NAME, str) and e.NAME, name
        assert isinstance(e.LABEL, str) and e.LABEL, name
        assert isinstance(e.TAB_AFTER, str), name
        assert isinstance(e.BADGE_SCOPED, bool), name
        assert isinstance(e.PRODUCER, str), name
        for cap in ext.SURFACE:
            if cap[0].isupper():
                continue
            fn = ext.provider(e, cap)
            if fn is None:
                continue
            if cap.endswith(("_get", "_post")):
                assert isinstance(fn, dict), (name, cap)
                assert all(callable(v) for v in fn.values()), (name, cap)
            else:
                assert callable(fn), (name, cap)


def test_ext_route_tables_build_and_collide_loudly():
    """The four merged route tables build (a duplicate verb across extensions
    is a ValueError at the first request, not a silent last-wins), no extension
    route shadows a built-in, and provider() refuses an undeclared capability."""
    import pytest

    from dashboard import ext
    from dashboard.http.get import _GetMixin
    from dashboard.http.post import _PostMixin

    sgets, fgets = ext.session_gets(), ext.fixed_gets()
    sposts, fposts = ext.session_posts(), ext.fixed_posts()
    assert set(sgets) >= {"memory", "note"}          # the memory tab's routes
    assert not set(sgets) & set(_GetMixin._SESSION_GET)
    assert not set(fgets) & set(_GetMixin._FIXED_GET)
    assert not set(sposts) & set(_PostMixin._SESSION_POST)
    assert not set(fposts) & set(_PostMixin._FIXED_POST)
    with pytest.raises(KeyError):
        ext.provider(ext.all_ext()[0], "no_such_capability")


def test_ext_badges_ride_the_badges_table():
    """Every extension badge surfaces as a BADGES row (field <name>_count,
    event <name>) — which is exactly what feeds BOTH the overview payload and
    the derived SSE badge channel, so an extension cannot get one without the
    other."""
    from dashboard import ext
    from dashboard.read import session as rsession

    rows = {b.event: b for b in rsession.BADGES}
    for r in ext.badge_rows():
        assert r.name in rows, r.name
        assert rows[r.name].field == r.name + "_count"
        assert rows[r.name].scoped == r.scoped
