# L1b — the single per-event dispatcher (claude-hook.py -> plugins.claude_code.
# dispatch). Pins that routing every event through ONE entry reproduces exactly
# what the old per-concern settings.json wiring produced: the right subsystem
# side-effects, each subsystem's own audit row under its ENTRY filename (never
# collapsed to claude-hook.py), the universal subscriber row on every event, the
# PreToolUse(Bash) updatedInput stdout contract, and the never-fail invariant.
import json

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
    assert _names("SessionStart") == [tab, "claude-split.py"]
    assert _names("UserPromptSubmit") == [tab]
    assert _names("PreToolUse", "Bash") == [tab, "claude-cmd-pre.py"]
    assert _names("PreToolUse", "Task") == [tab, "claude-subagent-fmt.py"]
    assert _names("PreToolUse", "Agent") == [tab, "claude-subagent-fmt.py"]
    assert _names("PreToolUse", "Read") == [tab]
    for ev in ("PostToolUse", "PostToolUseFailure"):  # failure pairing
        assert _names(ev, "Bash") == [tab, "claude-cmd-fmt.py"]
        for t in ("Read", "Edit", "Write", "MultiEdit", "NotebookEdit"):
            assert _names(ev, t) == [tab, "claude-file-fmt.py"]
        assert _names(ev, "Monitor") == [tab, "claude-monitor-fmt.py"]
        assert _names(ev, "WebFetch") == [tab]
        assert _names(ev, "Readx") == [tab]  # fullmatch, not prefix
    assert _names("Notification") == [tab]
    for ev in ("Stop", "StopFailure"):
        assert _names(ev) == [tab, "claude-stop-fmt.py"]
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
