# L3 — the tab-state machine, end to end against the fake kitten.
#
# claude-tab-status.py is the file the terminal-abstraction refactor touches
# first; these tests pin the whole contract: state -> exact set-tab-color argv,
# the tab-DB persistence rules (only on rc==0), the dedup, and every dispatch's
# decision logic (pretool/posttool/notify/stop/bg-recheck/agent-start).
import pytest

import oracle
import payloads as P
from colors import COLOR_TABLE, clear_argv, tab_color_argv
from conftest import wait_until

TAB = "claude-tab-status.py"
LITERAL_STATES = ["idle", "working", "executing", "awaiting-bg",
                  "awaiting-command", "awaiting-response"]


# ------------------------------------------------------------ paint contract

@pytest.mark.parametrize("state", LITERAL_STATES)
def test_literal_state_paints_exact_argv(run_hook, test_env, session,
                                         fake_kitten, state):
    s = session.make()
    run_hook(TAB, P.base(s, ""), argv=(state,))
    calls = fake_kitten.calls("set-tab-color")
    assert calls, "no set-tab-color call for state %r" % state
    assert calls[-1] == tab_color_argv(fake_kitten.listen,
                                       fake_kitten.window_id, state)
    assert oracle.tab_state(test_env, fake_kitten.window_id) == state


def test_thinking_dispatch_paints_magenta(run_hook, test_env, session,
                                          fake_kitten):
    """`thinking` is a dispatch (reads the UserPromptSubmit payload), not a
    bare literal — but must still land the magenta paint."""
    s = session.make()
    d = P.user_prompt(s)
    d["transcript_path"] = ""       # no transcript -> no interrupt-watch child
    run_hook(TAB, d, argv=("thinking",))
    assert fake_kitten.calls("set-tab-color")[-1] == tab_color_argv(
        fake_kitten.listen, fake_kitten.window_id, "thinking")
    assert oracle.tab_state(test_env, fake_kitten.window_id) == "thinking"


def test_clear_paints_none_and_drops_row(run_hook, test_env, session,
                                         fake_kitten):
    s = session.make()
    run_hook(TAB, P.base(s, ""), argv=("idle",))
    run_hook(TAB, P.session_end(s), argv=("clear",))
    assert fake_kitten.calls("set-tab-color")[-1] == clear_argv(
        fake_kitten.listen, fake_kitten.window_id)
    assert oracle.tab_state(test_env, fake_kitten.window_id) is None
    # clearing an already-cleared tab is a no-op (no extra socket call)
    n = len(fake_kitten.calls("set-tab-color"))
    run_hook(TAB, P.session_end(s), argv=("clear",))
    assert len(fake_kitten.calls("set-tab-color")) == n


def test_same_state_deduped(run_hook, test_env, session, fake_kitten):
    """Re-applying the shown colour must skip the socket round-trip."""
    s = session.make()
    run_hook(TAB, P.base(s, ""), argv=("idle",))
    n = len(fake_kitten.calls("set-tab-color"))
    run_hook(TAB, P.base(s, ""), argv=("idle",))
    assert len(fake_kitten.calls("set-tab-color")) == n
    assert any(r[3] == 0 and "already shown" in (r[4] or "")
               for r in oracle.transitions(test_env, s.sid) +
                        oracle.transitions(test_env, ""))


def test_failed_paint_not_persisted_and_retried(run_hook, test_env, session,
                                                fake_kitten):
    """rc!=0 must leave the tab row unchanged so the next same-state event
    retries (persisting a failed paint stranded colours — the fixed bug)."""
    s = session.make()
    fake_kitten.set_rc("set-tab-color", 1)
    run_hook(TAB, P.base(s, ""), argv=("idle",))
    assert oracle.tab_state(test_env, fake_kitten.window_id) is None
    fake_kitten.set_rc("set-tab-color", 0)
    run_hook(TAB, P.base(s, ""), argv=("idle",))    # NOT deduped away
    assert oracle.tab_state(test_env, fake_kitten.window_id) == "idle"


# ------------------------------------------------------------------ pretool

@pytest.mark.parametrize("tool,state", [
    ("Bash", "executing"), ("Task", "executing"), ("Agent", "executing"),
    ("AskUserQuestion", "awaiting-command"), ("ExitPlanMode", "awaiting-command"),
    ("Edit", "working"), ("Read", "working"), ("mcp__foo__bar", "working"),
])
def test_pretool_tool_mapping(run_hook, test_env, session, fake_kitten,
                              tool, state):
    s = session.make()
    d = P.base(s, "PreToolUse", tool_name=tool)
    run_hook(TAB, d, argv=("pretool",))
    assert oracle.tab_state(test_env, fake_kitten.window_id) == state


def test_pretool_posttool_ignore_agent_events(run_hook, test_env, session,
                                              fake_kitten):
    s = session.make()
    run_hook(TAB, P.base(s, ""), argv=("awaiting-response",))
    run_hook(TAB, P.base(s, "PreToolUse", tool_name="Bash", agent_id="agent-x"),
             argv=("pretool",))
    assert oracle.tab_state(test_env, fake_kitten.window_id) == "awaiting-response"
    run_hook(TAB, P.base(s, "PostToolUse", tool_name="Bash", agent_id="agent-x"),
             argv=("posttool",))
    assert oracle.tab_state(test_env, fake_kitten.window_id) == "awaiting-response"


def test_posttool_goes_working(run_hook, test_env, session, fake_kitten):
    s = session.make()
    run_hook(TAB, P.base(s, "PostToolUse", tool_name="Bash"), argv=("posttool",))
    assert oracle.tab_state(test_env, fake_kitten.window_id) == "working"


# ------------------------------------------------------------------- notify

def test_notify_permission_prompt_goes_red(run_hook, test_env, session,
                                           fake_kitten, seed):
    """Red wins even while a bg job runs — it's the one 'you're needed' cue."""
    s = session.make()
    seed.live_row(s.log, "bg", seed.live_pid())
    run_hook(TAB, P.notification(s, "Claude needs your permission to use Bash"),
             argv=("notify",))
    assert oracle.tab_state(test_env, fake_kitten.window_id) == "awaiting-command"


def test_notify_ignored_while_main_mid_turn(run_hook, test_env, session,
                                            fake_kitten):
    s = session.make()
    run_hook(TAB, P.base(s, ""), argv=("working",))
    run_hook(TAB, P.notification(s, "teammate finished a task"), argv=("notify",))
    assert oracle.tab_state(test_env, fake_kitten.window_id) == "working"


def test_notify_stays_blue_while_bg_runs(run_hook, test_env, session,
                                         fake_kitten, seed):
    s = session.make()
    run_hook(TAB, P.base(s, ""), argv=("awaiting-response",))
    seed.live_row(s.log, "bg", seed.live_pid())
    run_hook(TAB, P.notification(s, "waiting for your input"), argv=("notify",))
    assert oracle.tab_state(test_env, fake_kitten.window_id) == "awaiting-bg"


def test_notify_bg_finished_main_takes_over(run_hook, test_env, session,
                                            fake_kitten):
    """Blue tab + no live jobs -> the main is about to process the result:
    magenta (working), not green."""
    s = session.make()
    run_hook(TAB, P.base(s, ""), argv=("awaiting-bg",))
    run_hook(TAB, P.notification(s, "background task finished"), argv=("notify",))
    assert oracle.tab_state(test_env, fake_kitten.window_id) == "working"


def test_notify_plain_message_goes_green(run_hook, test_env, session,
                                         fake_kitten):
    s = session.make()
    run_hook(TAB, P.base(s, ""), argv=("idle",))
    run_hook(TAB, P.notification(s, "waiting for your input"), argv=("notify",))
    assert oracle.tab_state(test_env, fake_kitten.window_id) == "awaiting-response"


# --------------------------------------------------------------------- stop

def test_stop_goes_green_when_nothing_runs(run_hook, test_env, session,
                                           fake_kitten):
    s = session.make()
    run_hook(TAB, P.stop(s), argv=("stop",))
    assert oracle.tab_state(test_env, fake_kitten.window_id) == "awaiting-response"


def test_stop_stays_blue_while_live_row_exists(run_hook, test_env, session,
                                               fake_kitten, seed):
    s = session.make()
    seed.live_row(s.log, "bg", seed.live_pid())
    run_hook(TAB, P.stop(s), argv=("stop",))
    assert oracle.tab_state(test_env, fake_kitten.window_id) == "awaiting-bg"


def test_stop_ignores_stale_dead_pid_row(run_hook, test_env, session,
                                         fake_kitten, seed):
    """A live row whose pid is dead is stale — must not hold the tab blue."""
    s = session.make()
    seed.live_row(s.log, "bg", seed.dead_pid())
    run_hook(TAB, P.stop(s), argv=("stop",))
    assert oracle.tab_state(test_env, fake_kitten.window_id) == "awaiting-response"


def test_stop_honours_payload_running_background_tasks(run_hook, test_env,
                                                       session, fake_kitten):
    """No live rows, but the Stop payload itself reports a running background
    task (burst-scoped teammate between tasks) -> stay blue."""
    s = session.make()
    d = P.stop(s)
    d["background_tasks"] = [{"task_id": "t1", "status": "running"}]
    run_hook(TAB, d, argv=("stop",))
    assert oracle.tab_state(test_env, fake_kitten.window_id) == "awaiting-bg"


def test_stop_ignores_agent_stops(run_hook, test_env, session, fake_kitten):
    s = session.make()
    run_hook(TAB, P.base(s, ""), argv=("working",))
    d = P.stop(s)
    d["agent_id"] = "agent-x"
    run_hook(TAB, d, argv=("stop",))
    assert oracle.tab_state(test_env, fake_kitten.window_id) == "working"


# --------------------------------------------------------------- agent-start

def test_agent_start_goes_blue_but_red_wins(run_hook, test_env, session,
                                            fake_kitten):
    s = session.make()
    run_hook(TAB, P.base(s, ""), argv=("awaiting-response",))
    run_hook(TAB, {}, argv=("agent-start", s.log))
    assert oracle.tab_state(test_env, fake_kitten.window_id) == "awaiting-bg"
    run_hook(TAB, P.base(s, ""), argv=("awaiting-command",))
    run_hook(TAB, {}, argv=("agent-start", s.log))
    assert oracle.tab_state(test_env, fake_kitten.window_id) == "awaiting-command"


# ---------------------------------------------------------------- bg-recheck

def test_bg_recheck_flips_stale_blue_to_green(run_hook, test_env, session,
                                              fake_kitten):
    s = session.make()
    run_hook(TAB, P.base(s, ""), argv=("awaiting-bg",))
    run_hook(TAB, {}, argv=("bg-recheck", s.log, "bg"))
    assert oracle.tab_state(test_env, fake_kitten.window_id) == "awaiting-response"


def test_bg_recheck_sub_goes_magenta_not_green(run_hook, test_env, session,
                                               fake_kitten):
    """A finishing subagent means the main takes over -> working, not green."""
    s = session.make()
    run_hook(TAB, P.base(s, ""), argv=("awaiting-bg",))
    run_hook(TAB, {}, argv=("bg-recheck", s.log, "sub"))
    assert oracle.tab_state(test_env, fake_kitten.window_id) == "working"


def test_bg_recheck_refuses_while_job_still_running(run_hook, test_env, session,
                                                    fake_kitten, seed):
    s = session.make()
    run_hook(TAB, P.base(s, ""), argv=("awaiting-bg",))
    seed.live_row(s.log, "bg", seed.live_pid())
    run_hook(TAB, {}, argv=("bg-recheck", s.log, "bg"))
    assert oracle.tab_state(test_env, fake_kitten.window_id) == "awaiting-bg"


def test_bg_recheck_only_fg_clears_executing(run_hook, test_env, session,
                                             fake_kitten):
    """The release-before-recheck / cross-clear guard: a finishing subagent
    must never clear the main's own executing blue."""
    s = session.make()
    run_hook(TAB, P.base(s, ""), argv=("executing",))
    run_hook(TAB, {}, argv=("bg-recheck", s.log, "sub"))
    assert oracle.tab_state(test_env, fake_kitten.window_id) == "executing"
    run_hook(TAB, {}, argv=("bg-recheck", s.log, "fg"))
    assert oracle.tab_state(test_env, fake_kitten.window_id) == "awaiting-response"


def test_bg_recheck_leaves_non_bg_colours_alone(run_hook, test_env, session,
                                                fake_kitten):
    s = session.make()
    run_hook(TAB, P.base(s, ""), argv=("awaiting-response",))
    run_hook(TAB, {}, argv=("bg-recheck", s.log, "bg"))
    assert oracle.tab_state(test_env, fake_kitten.window_id) == "awaiting-response"


# -------------------------------------------------------------- ro purity

def test_probe_never_creates_state_db(run_hook, test_env, session, fake_kitten):
    """The tab tracker opens the state DB mode=ro: probing a session with no
    DB must not create one (its existence is the session-alive signal)."""
    import os
    s = session.make()
    assert not os.path.exists(s.state_db)
    run_hook(TAB, P.stop(s), argv=("stop",))
    assert not os.path.exists(s.state_db)
