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
    run_hook(HOOK, P.base(s, "PreCompact"))
    # An event with no functional handler still gets its universal subscriber row.
    assert handlers(test_env, s.sid) == {"subscriber"}
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


# ---------------------------------------------------- agent_id main-session guard

def test_posttool_bash_agent_id_ignored_but_audited(run_hook, test_env, session):
    s = session.make()
    run_hook(HOOK, P.post_bash(s, "echo hi", agent_id="agent-x"))
    # cmd-fmt still SKIPS agent_id events (the substream owns subagent rendering),
    # so no command block / counter — but the universal subscriber still records it.
    assert not s.counters().get("commands")
    assert "subscriber" in handlers(test_env, s.sid)
