# L1b — the single per-event dispatcher (claude-hook.py -> plugins.claude_code.
# dispatch). Pins that routing every event through ONE entry reproduces exactly
# what the old per-concern settings.json wiring produced: the right subsystem
# side-effects, each subsystem's own audit row under its ENTRY filename (never
# collapsed to claude-hook.py), the universal subscriber row on every event, the
# PreToolUse(Bash) updatedInput stdout contract, and the never-fail invariant.
import json
import os

import oracle
import payloads as P

HOOK = "claude-hook.py"


def handlers(env, sid):
    """The set of handler names that wrote a hook_events row for this session."""
    return {r[2] for r in oracle.hook_events(env, sid)}


# --------------------------------------------------------- never-fail contract

def test_garbage_stdin_exits_zero(run_hook):
    run_hook(HOOK, raw_stdin="this is { not json")


def test_unknown_event_only_subscriber(run_hook, test_env, session):
    s = session.make()
    run_hook(HOOK, P.base(s, "PermissionRequest"))
    # An event with no functional handler still gets its universal subscriber row.
    assert handlers(test_env, s.sid) == {"subscriber"}
    assert not oracle.errors(test_env, s.sid)


def test_precompact_paints_busy(run_hook, test_env, session):
    s = session.make()
    run_hook(HOOK, P.base(s, "PreCompact"))
    # Compaction has no tool/reply signal of its own — the tab dispatch paints the
    # busy magenta (working) so the tab doesn't sit stale through it.
    assert any(t[0] == "working" for t in oracle.transitions(test_env, s.sid))
    assert not oracle.errors(test_env, s.sid)


# ------------------------------------------------------- routing == old wiring

def test_posttool_bash_routes_to_cmd_fmt(run_hook, test_env, session):
    s = session.make()
    run_hook(HOOK, P.post_bash(s, "echo hi", stdout="hi\n"))
    assert "echo hi" in s.ops_text()
    assert s.counters().get("commands") == 1
    # cmd-fmt's decision row keeps its entry-filename identity, alongside subscriber.
    assert handlers(test_env, s.sid) == {"claude-cmd-fmt.py", "subscriber"}


def test_posttool_file_routes_to_file_fmt(run_hook, test_env, session):
    s = session.make()
    run_hook(HOOK, P.post_file(s, tool="Edit"))
    assert s.counters().get("tool:Edit") == 1
    assert handlers(test_env, s.sid) == {"claude-file-fmt.py", "subscriber"}


def test_posttool_read_does_not_route_to_cmd_fmt(run_hook, test_env, session):
    s = session.make()
    run_hook(HOOK, P.post_file(s, tool="Read"))
    # Read is a file tool, not Bash — cmd-fmt must not fire (disjoint matchers).
    assert "claude-cmd-fmt.py" not in handlers(test_env, s.sid)
    assert "claude-file-fmt.py" in handlers(test_env, s.sid)


def test_stop_routes_to_stop_fmt(run_hook, test_env, session):
    s = session.make()
    run_hook(HOOK, P.base(s, "Stop"))
    assert "claude-stop-fmt.py" in handlers(test_env, s.sid)
    assert "subscriber" in handlers(test_env, s.sid)


def test_posttool_records_tab_transition(run_hook, test_env, session):
    s = session.make()
    run_hook(HOOK, P.post_bash(s, "echo hi"))
    # The tab dispatch ran in-process too (posttool -> working), recorded as a
    # tab_transitions row keyed to this session.
    assert any(t[0] == "posttool" for t in oracle.transitions(test_env, s.sid))


# --------------------------------------------------- stdout rewrite contract

def test_pretool_bash_emits_updated_input(run_hook, test_env, session):
    s = session.make()
    p = run_hook(HOOK, P.pre_bash(s, "echo hi"))
    out = json.loads(p.stdout)
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert hso["permissionDecision"] == "allow"
    assert "tee -a" in hso["updatedInput"]["command"]
    # Both the tab dispatch and cmd-pre ran; cmd-pre owns the stdout.
    assert "claude-cmd-pre.py" in handlers(test_env, s.sid)


# --------------------------------------------------- _plan registry: pinned order
# The routing registry (_ROUTES) must reproduce the old if/elif ladder exactly:
# same subsystems, same ORDER (tab dispatch before formatters; SessionEnd's
# stop-fold before split-close), same matcher gating, same empty plan for
# unknown events.

def _names(ev, tool=""):
    from plugins.claude_code import dispatch
    return [name for name, _fn in dispatch._plan(ev, tool, {"tool_name": tool})]


def test_plan_sequences_pinned():
    tab = "claude-tab-status.py"
    ask = "claude-ask-fmt.py"
    assert _names("SessionStart") == [tab, "claude-split.py"]
    assert _names("UserPromptSubmit") == [tab, ask]   # ask: turn-boundary clear
    assert _names("PreToolUse", "Bash") == [tab, "claude-cmd-pre.py"]
    assert _names("PreToolUse", "Task") == [tab, "claude-subagent-fmt.py"]
    assert _names("PreToolUse", "Agent") == [tab, "claude-subagent-fmt.py"]
    assert _names("PreToolUse", "AskUserQuestion") == [tab, ask]
    assert _names("PreToolUse", "ExitPlanMode") == [tab, ask]
    assert _names("PreToolUse", "Read") == [tab]
    for ev in ("PostToolUse", "PostToolUseFailure"):  # failure pairing
        assert _names(ev, "Bash") == [tab, "claude-cmd-fmt.py"]
        for t in ("Read", "Edit", "Write", "MultiEdit", "NotebookEdit"):
            assert _names(ev, t) == [tab, "claude-file-fmt.py"]
        assert _names(ev, "Monitor") == [tab, "claude-monitor-fmt.py"]
        assert _names(ev, "AskUserQuestion") == [tab, ask]
        assert _names(ev, "ExitPlanMode") == [tab, ask]
        # the task-list kv snapshot: a status flip fires no dedicated hook —
        # the tool's own PostToolUse(+Failure) is its only refresh signal
        assert _names(ev, "TaskCreate") == [tab, "claude-task-fmt.py"]
        assert _names(ev, "TaskUpdate") == [tab, "claude-task-fmt.py"]
        assert _names(ev, "WebFetch") == [tab]
        assert _names(ev, "Readx") == [tab]  # fullmatch, not prefix
    assert _names("Notification") == [tab]
    assert _names("Stop") == [tab, "claude-stop-fmt.py", ask]
    # StopFailure = Stop's steps + the rate-limit migration, ordered LAST
    # (stop_fmt's recovery and the tab dispatch see the session before the
    # migrator can close its tab — docs/relimit.md)
    assert _names("StopFailure") == [tab, "claude-stop-fmt.py", ask,
                                     "claude-relimit.py"]
    # SessionEnd: the stop-fold step is ORDERED before split-close.
    assert _names("SessionEnd") == [tab, "claude-stop-fmt.py", "claude-split.py"]
    assert _names("SubagentStart") == ["claude-subagent-fmt.py"]
    assert _names("SubagentStop") == ["claude-subagent-fmt.py"]
    for ev in ("TaskCreated", "TaskCompleted"):
        assert _names(ev) == ["claude-task-fmt.py"]
    assert _names("PreCompact") == [tab]
    # Unknown/other events: empty plan (subscriber-only, recorded by route()).
    for ev in ("PermissionRequest", "Setup", ""):
        assert _names(ev) == []


# ---------------------------------------------------- agent_id main-session guard

def test_posttool_bash_agent_id_ignored_but_audited(run_hook, test_env, session):
    s = session.make()
    run_hook(HOOK, P.post_bash(s, "echo hi", agent_id="agent-x"))
    # cmd-fmt still SKIPS agent_id events (the substream owns subagent rendering),
    # so no command block / counter — but the universal subscriber still records it.
    assert not s.counters().get("commands")
    assert "subscriber" in handlers(test_env, s.sid)


# ------------------------------------------------------ lazy handler imports
# The formatter stack (~50ms of imports, measured) is paid only by events that
# actually route to a formatter — a tab-only event (UserPromptSubmit, most
# tools' Pre/PostToolUse) must import none of it. The handler import happens
# INSIDE the step thunk (under hookkit.run), so a broken module is a per-step
# audited swallow, not a dead dispatcher.

_LAZY_PROG = """
import sys
sys.argv = ["lazy-import-test"]
import plugins.claude_code.dispatch as D
HEAVY = {"plugins.claude_code." + m for m in
         ("cmd_pre", "cmd_fmt", "file_fmt", "monitor_fmt", "stop_fmt",
          "task_fmt", "split", "subagent_fmt", "accounting", "msgs", "tools",
          "model")}
loaded = HEAVY & set(sys.modules)
assert not loaded, "at import time: %s" % loaded
D.adopt.on_event = lambda d: None            # pin the probe to routing only
D.tabstatus.dispatch = lambda *a, **k: None
D.route({"hook_event_name": "UserPromptSubmit", "session_id": "s-lazy"})
loaded = HEAVY & set(sys.modules)
assert not loaded, "tab-only event imported: %s" % loaded
# A matched event imports exactly its handler (agent_id: the main-session
# guard makes cmd_fmt a no-op — the import is what this asserts).
D.route({"hook_event_name": "PostToolUse", "tool_name": "Bash",
         "session_id": "s-lazy", "agent_id": "agent-x",
         "tool_input": {}, "tool_response": {}})
assert "plugins.claude_code.cmd_fmt" in sys.modules
assert "plugins.claude_code.file_fmt" not in sys.modules
print("OK")
"""


def test_dispatch_lazy_handler_imports(test_env):
    import subprocess
    import sys as _sys
    from conftest import REPO
    r = subprocess.run([_sys.executable, "-c", _LAZY_PROG], cwd=REPO,
                       env=dict(test_env), capture_output=True, text=True,
                       timeout=30)
    assert r.returncode == 0 and "OK" in r.stdout, (
        "dispatch imported eagerly:\n%s%s" % (r.stdout, r.stderr))


# --------------------------------------------- the AskUserQuestion state stash

# the web dashboard's ask card (docs/dashboard.md, *Web ask*): PreToolUse
# stashes the pending questions in the state DB kv, the answer's PostToolUse
# clears it — and because every DECLINE path (Esc, "Chat about this",
# empty-"Type something" Enter) fires NO closing hook at all (measured
# 2026-07-18), the turn boundaries (Stop, UserPromptSubmit) clear it too.

ASK_QS = [{"question": "Which fruit?", "header": "Fruit",
           "options": [{"label": "Apple", "description": "crisp"},
                       {"label": "Banana", "description": "soft"}],
           "multiSelect": False}]


def _pending(s):
    rows = s.query_state("SELECT val FROM kv WHERE key='ask-pending'")
    return json.loads(rows[0][0]) if rows else None


def _seed_state_db(run_hook, s):
    # a HOSTED session's state DB exists from SessionStart on — any producer
    # write creates it; ask_fmt itself deliberately never does (see below)
    run_hook(HOOK, P.post_bash(s, "echo seed"))


def test_ask_pretool_stashes_pending(run_hook, test_env, session):
    s = session.make()
    _seed_state_db(run_hook, s)
    run_hook(HOOK, P.pre_ask(s, ASK_QS))
    pend = _pending(s)
    assert pend["tool_use_id"] == "toolu_ask1"
    assert pend["questions"] == ASK_QS
    assert "claude-ask-fmt.py" in handlers(test_env, s.sid)
    assert any(a == "ask-pending" and '"write"' in c
               for _p, a, c in oracle.state_files(test_env, s.sid))
    assert not oracle.errors(test_env, s.sid)


def test_ask_posttool_clears_answered(run_hook, test_env, session):
    s = session.make()
    _seed_state_db(run_hook, s)
    run_hook(HOOK, P.pre_ask(s, ASK_QS))
    run_hook(HOOK, P.post_ask(s, ASK_QS, {"Which fruit?": "Banana"}))
    assert _pending(s) is None
    assert any(a == "ask-pending" and "answered" in c
               for _p, a, c in oracle.state_files(test_env, s.sid))
    assert not oracle.errors(test_env, s.sid)


def test_ask_turn_boundaries_clear_the_decline(run_hook, test_env, session):
    # Esc / "Chat about this" fire NO hook — the Stop at turn end (or the next
    # UserPromptSubmit) is what drops the stale stash
    s = session.make()
    _seed_state_db(run_hook, s)
    run_hook(HOOK, P.pre_ask(s, ASK_QS))
    assert _pending(s) is not None
    run_hook(HOOK, P.stop(s))
    assert _pending(s) is None
    assert any(a == "ask-pending" and "turn ended" in c
               for _p, a, c in oracle.state_files(test_env, s.sid))
    # and UserPromptSubmit, independently
    run_hook(HOOK, P.pre_ask(s, ASK_QS, tid="toolu_ask2"))
    assert _pending(s) is not None
    run_hook(HOOK, P.user_prompt(s, "never mind"))
    assert _pending(s) is None


def test_ask_stop_without_pending_writes_nothing(run_hook, test_env, session):
    # Stop fires every turn — an empty clear must not spam state_files rows
    s = session.make()
    _seed_state_db(run_hook, s)
    run_hook(HOOK, P.stop(s))
    assert not any(a == "ask-pending"
                   for _p, a, c in oracle.state_files(test_env, s.sid))


def test_ask_subagent_is_ignored(run_hook, test_env, session):
    # an agent_id-carrying ask belongs to a subagent's inner dialog — never
    # the main session's card (CLAUDE.md main-session-only invariant)
    s = session.make()
    _seed_state_db(run_hook, s)
    run_hook(HOOK, P.pre_ask(s, ASK_QS, agent_id="agent-x"))
    assert _pending(s) is None
    assert not oracle.errors(test_env, s.sid)


def test_ask_unhosted_session_creates_no_ghost_db(run_hook, test_env, session):
    # a headless/daemon session has NO state DB — the stash must not create
    # one (its file-existence is the session-alive signal watchers poll)
    s = session.make()
    run_hook(HOOK, P.pre_ask(s, ASK_QS))
    run_hook(HOOK, P.stop(s))
    assert not os.path.exists(s.state_db)
    assert not oracle.errors(test_env, s.sid)


ASK_PLAN = {"plan": "# Plan\n1. do it", "planFilePath": "/tmp/p.md"}


def _plan_pending(s):
    rows = s.query_state("SELECT val FROM kv WHERE key='plan-pending'")
    return json.loads(rows[0][0]) if rows else None


def test_plan_pretool_stashes_pending(run_hook, test_env, session):
    s = session.make()
    _seed_state_db(run_hook, s)
    run_hook(HOOK, P.base(s, "PreToolUse", tool_name="ExitPlanMode",
                          tool_use_id="toolu_plan1", tool_input=ASK_PLAN))
    pend = _plan_pending(s)
    assert pend == {"tool_use_id": "toolu_plan1", "plan": "# Plan\n1. do it",
                    "planFilePath": "/tmp/p.md"}
    assert any(a == "plan-pending" and '"write"' in c
               for _p, a, c in oracle.state_files(test_env, s.sid))
    assert not oracle.errors(test_env, s.sid)


def test_plan_posttool_clears_only_its_key(run_hook, test_env, session):
    # the tool-scoped clear: an ExitPlanMode approval must not drop a pending
    # ask stash (and vice versa) — only the turn boundaries clear both
    s = session.make()
    _seed_state_db(run_hook, s)
    run_hook(HOOK, P.pre_ask(s, ASK_QS))
    run_hook(HOOK, P.base(s, "PreToolUse", tool_name="ExitPlanMode",
                          tool_use_id="toolu_plan1", tool_input=ASK_PLAN))
    run_hook(HOOK, P.base(s, "PostToolUse", tool_name="ExitPlanMode",
                          tool_use_id="toolu_plan1", tool_input=ASK_PLAN,
                          tool_response=ASK_PLAN))
    assert _plan_pending(s) is None
    assert _pending(s) is not None          # the ask stash survived
    run_hook(HOOK, P.stop(s))               # boundary clears the rest
    assert _pending(s) is None


# ------------------------------------------------- sessions-row path refresh

def test_relocated_transcript_refreshes_sessions_row(run_hook, test_env, session):
    """Claude Code RELOCATES the transcript when the session's cwd moves to
    another project dir (worktree entry — measured 2026-07-18): every later
    payload carries the new path, and the dispatcher's A.session_paths must
    fold it into the sessions row — the dashboard's title/ctx-probe/git chips
    and web rename all read that row, and the stale start-time path broke them
    all on a dead file (session e7192407)."""
    s = session.make()
    run_hook(HOOK, P.session_start(s))
    new_cwd = os.path.join(s.cwd, ".claude", "worktrees", "wt")
    new_t = s.transcript.replace(".jsonl", "-moved.jsonl")
    run_hook(HOOK, P.base(s, "PostToolBatch", cwd=new_cwd, transcript_path=new_t))
    row = oracle.q(test_env, "SELECT cwd, project_slug, transcript_path"
                   " FROM sessions WHERE session_id=?", (s.sid,))
    assert row == [(new_cwd, "wt", new_t)]
    # The relocation moment itself is audited: ONE session-paths row, old -> new.
    moves = [r for r in oracle.state_files(test_env, s.sid)
             if r[1] == "session-paths"]
    assert len(moves) == 1 and moves[0][0] == new_t
    c = json.loads(moves[0][2])
    assert (c["cwd"], c["transcript_path"]) == (new_cwd, new_t)
    assert (c["cwd_old"], c["transcript_path_old"]) == (s.cwd, s.transcript)
    # An unchanged later event is a no-op — no second row.
    run_hook(HOOK, P.base(s, "PostToolBatch", cwd=new_cwd, transcript_path=new_t))
    assert len([r for r in oracle.state_files(test_env, s.sid)
                if r[1] == "session-paths"]) == 1
    assert not oracle.errors(test_env, s.sid)


def test_agent_events_never_refresh_sessions_row(run_hook, test_env, session):
    """A subagent's inner events carry the MAIN transcript path but the AGENT'S
    OWN cwd (worktree isolation) — folding those in would flap the session row
    between the lead's cwd and each agent's, so agent_id events are skipped
    (the main-session-only invariant)."""
    s = session.make()
    run_hook(HOOK, P.session_start(s))
    run_hook(HOOK, P.base(s, "PostToolBatch", cwd="/agents/own/worktree",
                          agent_id="agent-1"))
    row = oracle.q(test_env, "SELECT cwd, transcript_path FROM sessions"
                   " WHERE session_id=?", (s.sid,))
    assert row == [(s.cwd, s.transcript)]
    assert not [r for r in oracle.state_files(test_env, s.sid)
                if r[1] == "session-paths"]
    assert not oracle.errors(test_env, s.sid)
