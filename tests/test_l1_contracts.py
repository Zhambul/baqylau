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

def test_task_fmt_created_and_completed(run_hook, test_env, session):
    s = session.make()
    run_hook("claude-task-fmt.py", P.task_created(s, "7", "Ship it"))
    run_hook("claude-task-fmt.py", P.task_completed(s, "7", "Ship it"))
    text = s.ops_text()
    assert "Ship it" in text
    assert "7" in text


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
