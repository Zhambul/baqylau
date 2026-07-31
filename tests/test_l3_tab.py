# L3 — the tab-state machine, end to end against the fake kitten.
#
# claude-tab-status.py is the file the terminal-abstraction refactor touches
# first; these tests pin the whole contract: state -> exact set-tab-color argv,
# the tab-DB persistence rules (only on rc==0), the dedup, and every dispatch's
# decision logic (pretool/posttool/notify/stop/bg-recheck/agent-start).
import os

import pytest

import oracle
import payloads as P
from colors import clear_argv, tab_color_argv

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


def test_raw_socket_paint_persists_and_skips_subprocess(run_hook, test_env,
                                                        session, fake_kitten,
                                                        fake_rc_socket):
    """With a LIVE rc socket the paint travels as a raw @kitty-cmd frame —
    the kitten recorder must see nothing — and ok:true persists the row
    exactly like rc==0 did."""
    s = session.make()
    run_hook(TAB, P.base(s, ""), argv=("idle",))
    frames = fake_rc_socket.commands("set-tab-color")
    assert frames and frames[-1]["payload"]["match"] == \
        "window_id:%s" % fake_kitten.window_id
    assert frames[-1]["no_response"] is False
    assert fake_kitten.calls("set-tab-color") == []
    assert oracle.tab_state(test_env, fake_kitten.window_id) == "idle"


def test_raw_socket_ok_false_not_persisted_and_retried(run_hook, test_env,
                                                       session, fake_kitten,
                                                       fake_rc_socket):
    """ok:false over the raw socket is rc!=0: the row must stay unchanged so
    the next same-state event retries (the stranded-colour contract, now on
    the raw path)."""
    s = session.make()
    fake_rc_socket.response = {"ok": False, "error": "no matching tabs"}
    run_hook(TAB, P.base(s, ""), argv=("idle",))
    assert oracle.tab_state(test_env, fake_kitten.window_id) is None
    assert fake_kitten.calls("set-tab-color") == []   # no subprocess retry
    fake_rc_socket.response = {"ok": True}
    run_hook(TAB, P.base(s, ""), argv=("idle",))      # NOT deduped away
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
    must never clear the main's own executing blue. fg may — to WORKING."""
    s = session.make()
    run_hook(TAB, P.base(s, ""), argv=("executing",))
    run_hook(TAB, {}, argv=("bg-recheck", s.log, "sub"))
    assert oracle.tab_state(test_env, fake_kitten.window_id) == "executing"
    run_hook(TAB, {}, argv=("bg-recheck", s.log, "fg"))
    assert oracle.tab_state(test_env, fake_kitten.window_id) == "working"


def test_bg_recheck_fg_never_paints_your_turn(run_hook, test_env, session,
                                              fake_kitten):
    """THE regression guard. A finished fg stream is MID-TURN evidence and says
    only "this command's writer is gone" — never "the turn is over". Reading it
    as a cancel painted GREEN over live turns whenever the command had in fact
    been DENIED before it ran (session 674d78d1, 2026-07-31): no PostToolUse, an
    orphaned tailer, writer-gone in 2s, "your turn" on a busy session. A real
    cancel is covered by interrupt-watch's transcript record instead, so nothing
    needs green here (docs/tab-colors.md *A Bash call that never ran*)."""
    s = session.make()
    for start in ("executing", "awaiting-bg"):
        run_hook(TAB, P.base(s, ""), argv=(start,))
        run_hook(TAB, {}, argv=("bg-recheck", s.log, "fg"))
        assert oracle.tab_state(test_env, fake_kitten.window_id) == "working", start
    # …while a BACKGROUND job finishing still does mean it: the tab only reaches
    # awaiting-bg because `stop` put it there, i.e. the turn had already ended.
    run_hook(TAB, P.base(s, ""), argv=("awaiting-bg",))
    run_hook(TAB, {}, argv=("bg-recheck", s.log, "monitor"))
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


# ------------------------------------------- interrupt-watch turn-over gate

# The REAL record Claude Code appends on a cancel (measured) — a `type:"user"`
# message whose content is the marker. Seeding a looser shape is what let the
# quoted-marker false positive hide: the watcher's scan was a bare substring
# match, so ANY line carrying the bytes passed the test suite too.
INTERRUPT_REC = ('{"type":"user","message":{"role":"user","content":'
                 '[{"type":"text","text":"[Request interrupted by user]"}]}}')
# Growth that merely QUOTES the marker, byte-faithful to the live 2e9b57e4
# transcript: a nested_memory attachment injecting a worktree CLAUDE.md that
# DOCUMENTS the marker, trailed by the metadata records Claude Code writes with
# it. The trailer matters — none of them is a `"type":"user"` record, so the
# queued-prompt guard does NOT catch this, which is why the tab really flipped.
QUOTED_MARKER = (
    '{"type":"attachment","attachment":{"type":"nested_memory",'
    '"path":"/p/CLAUDE.md","content":{"content":"the transcript\'s '
    '`[Request interrupted by user]` line for plain replies"}}}\n'
    '{"type":"last-prompt"}\n{"type":"ai-title"}\n'
    '{"type":"mode"}\n{"type":"permission-mode"}'
)
# The same class from a tool_result (a grep hit / file read / audit paste). Kept
# out of QUOTED_MARKER because a tool_result IS a `"type":"user"` record, so the
# queued guard masks it in the drive harness — the predicate test covers it.
QUOTED_IN_TOOL_RESULT = (
    '{"type":"user","message":{"role":"user","content":[{"type":"tool_result",'
    '"tool_use_id":"t1","content":"grep hit: [Request interrupted by user]"}]}}'
)


def _resolved(got):
    """A dispatch handler's result as a bare STATE. Handlers return the literal
    state to paint, `(state, reason)` when they have something to say about WHY
    (the reason rides the one tab_transitions row main() writes), or None. The
    reason used to be a module GLOBAL these tests never saw; it is a return
    value now, so unwrap here rather than at every assertion."""
    return got[0] if isinstance(got, tuple) else got


def _drive_interruptwatch(monkeypatch, tmp_path, states, interrupt_at=None,
                          queued=False, quoted_at=None):
    """Run run_interruptwatch in-process, fully sequenced (no timing races):
    tab_get returns states[i] on its i-th call (last value repeats), and the
    synthetic interrupt line is appended to the transcript during tab_get call
    number `interrupt_at` (None = never; queued=True also appends the
    immediately-delivered queued user-prompt record right after it, the way
    Claude Code does when a message was queued at interrupt time).
    `quoted_at` appends transcript growth that only QUOTES the marker — which
    must never be read as a cancel. Returns
    (resolved_state, end_reason, [audited transition reasons])."""
    import sys as _sys
    from conftest import REPO
    if REPO not in _sys.path:
        _sys.path.insert(0, REPO)
    import plugins.claude_code.tabstatus as T

    transcript = tmp_path / "sess-x.jsonl"
    transcript.write_text('{"type":"user"}\n')
    ended, reasons = {}, []

    class _A:
        def stream_start(self, *a, **k):
            return 7

        def stream_end(self, sid, reason):
            ended["reason"] = reason

        def transition(self, sid, win, dispatch, prev, new, applied, reason):
            reasons.append(reason)

    tick = {"n": -1}

    def fake_tab_get(win):
        tick["n"] += 1
        if quoted_at is not None and tick["n"] == quoted_at:
            with open(transcript, "a", encoding="utf-8") as f:
                f.write(QUOTED_MARKER + "\n")
        if interrupt_at is not None and tick["n"] == interrupt_at:
            with open(transcript, "a", encoding="utf-8") as f:
                f.write(INTERRUPT_REC + "\n")
                if queued:
                    f.write('{"type":"user","message":{"content":"queued"}}\n')
        return states[min(tick["n"], len(states) - 1)]

    monkeypatch.setattr(T, "WIN", "9")
    monkeypatch.setattr(T, "A", _A())
    monkeypatch.setattr(T, "watcher_del", lambda *a, **k: None)
    monkeypatch.setattr(T, "tab_get", fake_tab_get)
    monkeypatch.setattr(T, "WATCH_POLL_S", 0.001)
    return (_resolved(T.run_interruptwatch(str(transcript))),
            ended.get("reason"), reasons)


def test_dispatch_handlers_take_args_and_return_their_reason():
    """Two properties the DISPATCHES table only pretends to have unless pinned.

    (1) Uniform signature. Every entry is called the same way — `handler(args)`
    — where `args` is the dispatch's argv words, parsed ONCE by entry(). Five
    handlers used to re-read `sys.argv[2]`/`[3]`/`[4]` from inside their bodies,
    so the table's apparent zero-arg contract was a lie and those five could not
    be exercised without patching a process-global.

    (2) The reason is a RETURN VALUE. Why the resolved state was chosen belongs
    to the one tab_transitions row main() writes; as the module global `REASON`
    it was an out-of-band channel that four handlers wrote and dispatch() — the
    in-process entry, called on hook events — did NOT save/restore alongside the
    payload it does, so a second dispatch in one process could attribute a stale
    reason to a fresh transition."""
    import inspect
    import sys as _sys
    from conftest import REPO
    if REPO not in _sys.path:
        _sys.path.insert(0, REPO)
    import plugins.claude_code.tabstatus as T

    assert not hasattr(T, "REASON"), "the reason is a return value, not a global"
    for name, fn in T.DISPATCHES.items():
        sig = inspect.signature(fn)
        assert list(sig.parameters) == ["args"], (name, sig)
        assert sig.parameters["args"].default == (), name
    # resolve() normalises both shapes, so a handler with nothing to say stays a
    # one-liner and main() always gets a pair
    assert T.resolve("idle") == ("idle", "")
    T.DISPATCHES["__probe_bare"] = lambda args=(): "working"
    T.DISPATCHES["__probe_pair"] = lambda args=(): ("working", "because")
    T.DISPATCHES["__probe_none"] = lambda args=(): None
    try:
        assert T.resolve("__probe_bare") == ("working", "")
        assert T.resolve("__probe_pair") == ("working", "because")
        assert T.resolve("__probe_none") == (None, "")
    finally:
        for k in ("__probe_bare", "__probe_pair", "__probe_none"):
            T.DISPATCHES.pop(k, None)


def test_interruptwatch_survives_stale_preturn_green(monkeypatch, tmp_path):
    """The premature-turn-over race: the watcher is spawned BEFORE d_thinking's
    paint, and a failed/lagging paint leaves the PREVIOUS turn's green in the
    tab row — the watcher must keep watching (not exit turn-over) until it has
    seen a mid-turn state this run, so a cancel later in the turn still flips
    green. Ticks 0-1 show the stale green; the paint lands at tick 2; the
    cancel arrives at tick 3 on magenta."""
    state, reason, reasons = _drive_interruptwatch(
        monkeypatch, tmp_path,
        states=["awaiting-response", "awaiting-response", "thinking", "working"],
        interrupt_at=3)
    assert state == "awaiting-response", \
        "watcher exited on the stale pre-turn green (premature turn-over)"
    assert reason == "interrupt-detected-flipped-green"
    assert sum("stale pre-turn row" in r for r in reasons) == 1  # audited once


def test_interruptwatch_still_exits_on_genuine_turn_over(monkeypatch, tmp_path):
    """Once a mid-turn state HAS been seen, green means the turn resolved on
    its own — the watcher must still exit turn-over (the gate must not turn it
    into a whole-session watcher)."""
    state, reason, _ = _drive_interruptwatch(
        monkeypatch, tmp_path,
        states=["thinking", "working", "awaiting-response"])
    assert state is None
    assert reason == "turn-over"


def test_interruptwatch_recheck_defers_when_turn_resolved(monkeypatch, tmp_path):
    """The post-interrupt re-check is unchanged: interrupt seen but the tab is
    already green (the turn resolved concurrently) -> do nothing."""
    state, reason, _ = _drive_interruptwatch(
        monkeypatch, tmp_path,
        states=["thinking", "working", "awaiting-response"],
        interrupt_at=1)
    assert state is None
    assert reason == "interrupt-seen-but-turn-already-over"


def test_interruptwatch_queued_prompt_keeps_watching(monkeypatch, tmp_path):
    """An interrupt with a QUEUED message is not a cancel-to-idle: Claude Code
    delivers the queued prompt immediately and a new turn starts thinking, so
    flipping green painted "done" over a live think (stuck green until the
    first tool event — reported live). The user-prompt record right after the
    interrupt line is the tell: the watcher must NOT flip, and must KEEP
    watching the delivered turn until it genuinely ends."""
    state, reason, reasons = _drive_interruptwatch(
        monkeypatch, tmp_path,
        states=["thinking", "thinking", "working", "awaiting-response"],
        interrupt_at=1, queued=True)
    assert state is None, "flipped green over the queued prompt's turn"
    assert reason == "turn-over"          # kept watching to the genuine end
    assert any("queued prompt delivered" in r for r in reasons)


def test_interruptwatch_ignores_a_merely_QUOTED_marker(monkeypatch, tmp_path):
    """The false-green bug (session 2e9b57e4, three times in one session): the
    watcher scanned raw transcript bytes for the marker, so growth that only
    QUOTED it read as a cancel and the tab flipped green MID-TURN. The live
    trigger was a nested_memory attachment injecting a worktree CLAUDE.md that
    documents the marker; a tool_result carrying a grep/file read of it is the
    same class. Neither may flip the tab: only a real cancel record counts."""
    state, reason, _ = _drive_interruptwatch(
        monkeypatch, tmp_path,
        states=["thinking", "working", "working", "working", "awaiting-response"],
        quoted_at=1)
    assert state is None, "a quoted marker flipped the tab green mid-turn"
    assert reason == "turn-over"        # ran on to the turn's genuine end


def test_interruptwatch_still_flips_after_a_quoted_marker(monkeypatch, tmp_path):
    """The quoted-marker growth must not DESENSITISE the watcher either: a real
    cancel arriving later in the same turn still flips green (the consumed-to-
    the-last-complete-line cursor keeps advancing past the noise)."""
    state, reason, _ = _drive_interruptwatch(
        monkeypatch, tmp_path,
        states=["thinking", "working", "working", "working"],
        quoted_at=1, interrupt_at=3)
    assert state == "awaiting-response"
    assert reason == "interrupt-detected-flipped-green"


def test_is_interrupt_line_shapes():
    """The predicate itself, over the shapes measured in real transcripts."""
    import sys as _sys
    from conftest import REPO
    if REPO not in _sys.path:
        _sys.path.insert(0, REPO)
    from plugins.claude_code.tabstatus import is_interrupt_line as ok

    # real cancels: bare-string content, text-block content, and the tool-call
    # variant (which the old closing-bracket scan never even detected)
    assert ok(b'{"type":"user","message":{"role":"user","content":'
              b'"[Request interrupted by user]"}}')
    assert ok(INTERRUPT_REC.encode())
    assert ok(b'{"type":"user","message":{"role":"user","content":[{"type":'
              b'"text","text":"[Request interrupted by user for tool use]"}]}}')
    # quotes, not cancels
    for line in QUOTED_MARKER.split("\n") + [QUOTED_IN_TOOL_RESULT]:
        assert not ok(line.encode()), line[:60]
    assert not ok(b'{"type":"assistant","message":{"content":[{"type":"text",'
                  b'"text":"I saw [Request interrupted by user] in the log"}]}}')
    # unrelated / undecidable lines
    assert not ok(b'{"type":"user","message":{"content":"hello"}}')
    assert not ok(b'{"type":"user","message":{"content":"[Request interr')  # torn


def _drive_escaperecheck(monkeypatch, tmp_path, states, grow_at=None,
                         baseline=None, grow_lines=None):
    """Run d_escape_recheck in-process, sequenced like _drive_interruptwatch:
    tab_get returns states[i] per call (call 0 is the pre-loop start read;
    last value repeats), and the transcript grows during tab_get call number
    `grow_at` — by a user-prompt record unless `grow_lines` overrides the
    appended text. Returns (resolved_state, [audited transition reasons])."""
    import sys as _sys
    from conftest import REPO
    if REPO not in _sys.path:
        _sys.path.insert(0, REPO)
    import plugins.claude_code.tabstatus as T

    transcript = tmp_path / "sess-esc.jsonl"
    transcript.write_text('{"type":"user"}\n')
    reasons = []

    class _A:
        def transition(self, sid, win, dispatch, prev, new, applied, reason):
            reasons.append(reason)

    tick = {"n": -1}

    def fake_tab_get(win):
        tick["n"] += 1
        if grow_at is not None and tick["n"] == grow_at:
            with open(transcript, "a", encoding="utf-8") as f:
                f.write(grow_lines if grow_lines is not None else
                        '{"type":"user","message":{"content":"new prompt"}}\n')
        return states[min(tick["n"], len(states) - 1)]

    # the dispatch WORDS, passed as a value — entry() parses sys.argv[2:] once
    # and hands them down, so a handler never reads argv and this driver no
    # longer has to fake a process-wide argv to exercise one function
    args = ("/tmp/claude-mirror-sess-esc.log", str(transcript))
    if baseline is not None:
        args += (str(baseline),)
    monkeypatch.setattr(T, "WIN", "9")
    monkeypatch.setattr(T, "A", _A())
    monkeypatch.setattr(T, "tab_get", fake_tab_get)
    monkeypatch.setattr(T, "WATCH_POLL_S", 0.001)
    monkeypatch.setattr(T, "ESCAPE_GRACE_S", 0.02)   # keep the full-grace path fast
    return _resolved(T.d_escape_recheck(args)), reasons


def test_escaperecheck_flips_dead_magenta(monkeypatch, tmp_path):
    """The mid-thinking cancel gap, closed for a WEB interrupt: Esc into a
    thinking tab, then total silence (no state movement, no transcript bytes)
    -> the turn is dead, flip green."""
    state, _ = _drive_escaperecheck(monkeypatch, tmp_path, states=["thinking"])
    assert state == "awaiting-response"


def test_escaperecheck_bails_on_new_prompt(monkeypatch, tmp_path):
    """The re-prompt race: a NEW message within the grace repaints the same
    magenta invisibly (dedup), so the state poll alone can't see it — the
    transcript's new user record must bail the flip, or green paints over a
    live think (the exact false-positive the banned idle-timeout had)."""
    state, reasons = _drive_escaperecheck(
        monkeypatch, tmp_path, states=["thinking"], grow_at=2)
    assert state is None, "flipped green over a freshly-submitted turn"
    assert any("user record appeared" in r for r in reasons)


def test_escaperecheck_bails_on_press_time_growth(monkeypatch, tmp_path):
    """The press-time baseline: a prompt landing in the spawn-latency gap
    (before the recheck's own first stat) is still seen — the dashboard
    passes the size it measured BEFORE sending the Escape."""
    state, reasons = _drive_escaperecheck(
        monkeypatch, tmp_path, states=["thinking"], baseline=0)
    assert state is None
    assert any("user record appeared" in r for r in reasons)


def test_escaperecheck_ignores_metadata_growth(monkeypatch, tmp_path):
    """The cancel-edit gesture appends pure metadata (ai-title, last-prompt)
    right after killing the turn — a raw-growth bail false-positived on the
    gesture's own records and left the tab magenta (observed live). Metadata
    growth must NOT bail; the dead magenta still flips."""
    state, _ = _drive_escaperecheck(
        monkeypatch, tmp_path, states=["thinking"], grow_at=2,
        grow_lines=('{"type":"ai-title","aiTitle":"t"}\n'
                    '{"type":"last-prompt","lastPrompt":"p"}\n'))
    assert state == "awaiting-response", \
        "metadata append blocked the mid-thinking cancel recovery"


def test_escaperecheck_bails_on_state_movement_and_non_magenta(monkeypatch,
                                                              tmp_path):
    """Any state movement means a real signal handled it; and a non-magenta
    start belongs to the other recoveries (writer-liveness on blue, dialog
    outcomes on red) — both must leave the tab alone."""
    state, reasons = _drive_escaperecheck(
        monkeypatch, tmp_path, states=["thinking", "awaiting-response"])
    assert state is None
    assert any("state moved on" in r for r in reasons)
    state, reasons = _drive_escaperecheck(
        monkeypatch, tmp_path, states=["executing"])
    assert state is None
    assert any("not on magenta" in r for r in reasons)


# ------------------------------------------------- frontend substitutability

def _spawn_watcher_with(monkeypatch, fe):
    """Drive tabstatus._spawn_watcher in-process with frontend `fe`, capturing
    the Popen call (argv + env) instead of spawning a real watcher."""
    import sys as _sys
    from conftest import REPO
    if REPO not in _sys.path:
        _sys.path.insert(0, REPO)
    import plugins.claude_code.tabstatus as T

    spawned = {}

    class _Proc:
        pid = 4242

    def fake_popen(argv, **kw):
        spawned["argv"] = list(argv)
        spawned["env"] = kw.get("env")
        return _Proc()

    import core.spawn as CS
    monkeypatch.setattr(CS.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(T, "watcher_set", lambda *a, **k: None)
    monkeypatch.setattr(T, "WIN", "9")
    monkeypatch.setattr(T, "FE", fe)
    T._spawn_watcher("bgwatch", ["bg-watch", "/tmp/x.log"])
    return spawned


def test_spawn_watcher_survives_stub_frontend(monkeypatch):
    """Building the watcher env must go through the Frontend contract
    (export_env), not kitty-only attrs like .listen — a frontend whose
    available() is True but has no .listen would AttributeError, and the
    swallow in _spawn_watcher would hide it: the watcher silently never
    spawns. Pin that the stub path spawns cleanly with no kitty vars."""
    from frontends.base import Frontend
    monkeypatch.delenv("KITTY_LISTEN_ON", raising=False)
    spawned = _spawn_watcher_with(monkeypatch, Frontend())
    assert spawned.get("argv"), "watcher was not spawned under the stub frontend"
    assert spawned["argv"][-2:] == ["bg-watch", "/tmp/x.log"]
    # The stub exports nothing — the child env must not grow a kitty var.
    assert "KITTY_LISTEN_ON" not in spawned["env"]


def test_spawn_watcher_kitty_exports_socket(monkeypatch):
    """Under kitty, export_env() must land the resolved socket in the child
    env (the detached watcher is re-parented, so it can't re-resolve)."""
    from frontends.kitty import KittyFrontend
    monkeypatch.setenv("KITTY_LISTEN_ON", "stale-value")
    fe = KittyFrontend(listen="unix:/tmp/fe-test.sock", kitten="/bin/true")
    spawned = _spawn_watcher_with(monkeypatch, fe)
    assert spawned["env"]["KITTY_LISTEN_ON"] == "unix:/tmp/fe-test.sock"
    assert spawned["env"]["KITTY_WINDOW_ID"] == "9"


# --------------------------------------------- watcher spawn is audit-covered
# _spawn_watcher used a raw Popen inside `except: pass`: a failed spawn left
# NO rows at all — indistinguishable from "watcher never requested", the exact
# non-firing-invisible class the recovery watchers exist for. It now routes
# through core.spawn.spawn_detached (A.spawn on success, A.error on failure).

def _audit_rows(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_AUDIT", "1")
    monkeypatch.setenv("CLAUDE_AUDIT_DIR", str(tmp_path / "audit"))

    def rows(sql):
        import sqlite3
        db = str(tmp_path / "audit" / "audit.db")
        if not os.path.exists(db):
            return []
        conn = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=5)
        try:
            return conn.execute(sql).fetchall()
        finally:
            conn.close()
    return rows


def test_spawn_watcher_success_writes_spawn_row(monkeypatch, tmp_path):
    from frontends.base import Frontend
    rows = _audit_rows(tmp_path, monkeypatch)
    spawned = _spawn_watcher_with(monkeypatch, Frontend())
    assert spawned.get("argv"), "watcher was not spawned"
    got = rows("SELECT child_pid, purpose FROM spawns")
    assert got == [(4242, "watcher:bg-watch")], got


def test_spawn_watcher_failure_writes_error_row(monkeypatch, tmp_path):
    """Popen raising (fork failure, exec problem) must land an errors row —
    and still not raise into the hook."""
    import core.spawn as CS
    import plugins.claude_code.tabstatus as T
    from frontends.base import Frontend
    rows = _audit_rows(tmp_path, monkeypatch)

    def boom(*a, **k):
        raise OSError("fork failed")

    monkeypatch.setattr(CS.subprocess, "Popen", boom)
    monkeypatch.setattr(T, "watcher_set", lambda *a, **k: None)
    monkeypatch.setattr(T, "WIN", "9")
    monkeypatch.setattr(T, "FE", Frontend())
    T._spawn_watcher("bgwatch", ["bg-watch", "/tmp/x.log"])   # must not raise
    funcs = [r[0] for r in rows("SELECT func FROM errors")]
    assert any(f.startswith("spawn claude-tab-status.py") for f in funcs), funcs
    assert rows("SELECT id FROM spawns") == []


def test_tab_db_readers_bind_values(monkeypatch, tmp_path):
    """tab_get/watcher_pid must use bound parameters, not string-interpolated
    SQL — a value with a quote character used to break the query (and was an
    injection surface). Round-trip through the real DB with hostile keys."""
    from core import tabs
    monkeypatch.setattr(tabs, "TABDB", str(tmp_path / "tab.db"))
    win = "9'; DROP TABLE tab;--"
    tabs.tab_set(win, "executing")
    assert tabs.tab_get(win) == "executing"
    kind = "bg'watch"
    tabs.watcher_set(kind, win, 4242)
    assert tabs.watcher_pid(kind, win) == 4242
    tabs.watcher_del(kind, win)
    assert tabs.watcher_pid(kind, win) is None
    tabs.tab_clear(win)
    assert tabs.tab_get(win) == ""
