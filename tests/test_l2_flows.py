# L2 — end-to-end session flows.
#
# Each flow drives an ordered hook sequence with real side effects (tee'd .out
# files with real write-holders, transcripts, subagent JSONL + meta.json) and
# ends with the audit oracle: `anomalies` must be clean, streams must have
# ended with the right end_reason, slots must be released. These are the
# regression net for the cancel-recovery / hand-off / park-restore machinery.
import json
import os
import shutil
import signal
import subprocess
import time
import uuid

import pytest

import oracle
import payloads as P
from conftest import wait_until

TAB = "claude-tab-status.py"


# ------------------------------------------------------------------ helpers

@pytest.fixture
def writer(reaper):
    """A real write-holder on a file (what has_writer/lsof detects): the
    product treats 'no process holds the file open for write' as job-done."""
    def _start(path, seconds=30):
        # >> (append) not > : truncating would race lines the test already
        # wrote into the file; append still registers as a write-holder.
        p = subprocess.Popen(["/bin/sh", "-c",
                              "exec sleep %s >> %s" % (seconds, path)],
                             start_new_session=True)
        reaper.append(p)
        wait_until(lambda: os.path.exists(path), desc="writer file appears")
        return p
    return _start


needs_private_tmp = pytest.mark.skipif(
    not os.path.isdir("/private/tmp"),
    reason="claude-stream.py find_file globs /private/tmp/claude-*/ — the "
           "empirical macOS location of Claude Code's tasks/<id>.output; on "
           "Linux there is no /private (a known product gap: bg-task/monitor "
           "output discovery is macOS-only today)")


@pytest.fixture
def task_dir():
    """A tasks/ dir matching claude-stream.py's /private/tmp/claude-* glob —
    where Claude Code drops tasks/<id>.output for bg jobs and monitors."""
    root = "/private/tmp/claude-e2e-%s" % uuid.uuid4().hex[:8]
    d = os.path.join(root, "t", "tasks")
    os.makedirs(d)
    yield d
    shutil.rmtree(root, ignore_errors=True)


def fg_live_record(s):
    rows = s.query_state("SELECT val FROM handoffs WHERE key='fg-live'")
    return json.loads(rows[0][0]) if rows else None


def streams_all_ended(env, sid, exclude=()):
    """All audited streams closed. `exclude` skips kinds that legitimately
    outlive the moment (the codex watcher runs until SessionEnd parks the DB)."""
    rows = [r for r in oracle.streams(env, sid) if r[0] not in exclude]
    return rows and all(r[2] is not None for r in rows)


def end_reasons(env, sid):
    return [r[1] for r in oracle.streams(env, sid)]


# --------------------------------------------------------------------- F1

def test_f1_minimal_session(run_hook, test_env, session, fake_kitten):
    """SessionStart -> prompt -> bash -> Stop -> SessionEnd: tab arc
    idle->thinking->blue->green->cleared, scoreboard counted, history parked."""
    s = session.make()
    s.add_user()
    run_hook("claude-split.py", P.session_start(s), argv=("open",))
    run_hook(TAB, P.session_start(s), argv=("idle",))
    run_hook(TAB, P.user_prompt(s), argv=("thinking",))
    assert oracle.tab_state(test_env, fake_kitten.window_id) == "thinking"

    run_hook(TAB, P.pre_bash(s, "echo f1"), argv=("pretool",))
    assert oracle.tab_state(test_env, fake_kitten.window_id) == "executing"
    p = run_hook("claude-cmd-pre.py", P.pre_bash(s, "echo f1"))
    src = fg_live_record(s)["src"]
    with open(src, "a") as f:
        f.write("f1 output\n")
    s.add_assistant("msg_f1")
    run_hook("claude-cmd-fmt.py", P.post_bash(s, "echo f1", stdout="f1 output\n"))
    run_hook(TAB, P.post_bash(s, "echo f1"), argv=("posttool",))

    run_hook("claude-stop-fmt.py", P.stop(s))
    run_hook(TAB, P.stop(s), argv=("stop",))
    wait_until(lambda: oracle.tab_state(test_env, fake_kitten.window_id)
               == "awaiting-response", desc="tab green after stop")

    wait_until(lambda: streams_all_ended(test_env, s.sid,
                                         exclude=("codex-watcher",)),
               desc="streams ended (codex watcher lives until SessionEnd)")
    assert "f1 output" in s.ops_text()
    # Commands are counted live; token/cost is OTEL-authoritative now, so the cmd
    # hook no longer folds it — tokens stays 0 until the OTLP receiver (absent here)
    # or the SessionEnd fallback below books it.
    assert s.counters().get("commands") == 1

    # SessionEnd: with no OTEL data (hermetic), the stop-fmt fallback folds the
    # transcript so the session isn't $0. In a real session the dispatcher runs this
    # before claude-split.py parks the DB.
    run_hook("claude-stop-fmt.py", P.session_end(s))
    assert s.counters().get("tokens", 0) > 0, "SessionEnd fallback did not fold cost"
    run_hook(TAB, P.session_end(s), argv=("clear",))
    run_hook("claude-split.py", P.session_end(s), argv=("close",))
    assert os.path.exists(s.state_db + ".keep") and not os.path.exists(s.state_db)
    wait_until(lambda: streams_all_ended(test_env, s.sid),
               desc="codex watcher exits once the DB is parked")
    oracle.assert_clean(test_env, s.sid)


# --------------------------------------------------------------------- F2

def test_f2_fg_lifecycle_streams_live_and_takes_real_outcome(
        run_hook, test_env, session, writer):
    """The tee rewrite streams output as it lands; PostToolUse hands the real
    chip to the tailer via the done: hand-off (end_reason=sentinel)."""
    s = session.make()
    run_hook("claude-cmd-pre.py", P.pre_bash(s, "echo working && sleep 1"))
    rec = fg_live_record(s)
    w = writer(rec["src"])                       # the running command
    with open(rec["src"], "a") as f:
        f.write("line one\n")
    wait_until(lambda: "line one" in s.ops_text(), desc="live line in mirror")
    with open(rec["src"], "a") as f:
        f.write("line two\n")
    wait_until(lambda: "line two" in s.ops_text(), desc="second live line")

    run_hook("claude-cmd-fmt.py",
             P.post_bash(s, "echo working && sleep 1", duration_ms=1500))
    w.terminate()
    wait_until(lambda: "sentinel" in end_reasons(test_env, s.sid),
               desc="fg stream ends on the done hand-off")
    assert "■ finished" in s.ops_text()
    assert not s.live("fg"), "fg slot not released"
    assert fg_live_record(s) is None, "fg-live record not consumed"
    oracle.assert_clean(test_env, s.sid)


# --------------------------------------------------------------------- F3

def test_f3_failed_command_chip(run_hook, test_env, session):
    s = session.make()
    run_hook("claude-cmd-pre.py", P.pre_bash(s, "false"))
    run_hook("claude-cmd-fmt.py",
             P.post_bash(s, "false", failure=True, error="Exit code 1\nboom"))
    wait_until(lambda: "■ failed (exit 1)" in s.ops_text(),
               desc="failed chip lands (via the fg tailer hand-off)")
    assert s.counters().get("failed") == 1
    wait_until(lambda: streams_all_ended(test_env, s.sid), desc="tailer exits")
    oracle.assert_clean(test_env, s.sid, allow=("failed tools",))


# --------------------------------------------------------------------- F4

def test_f4a_background_command_lifecycle(run_hook, test_env, session,
                                          fake_kitten, writer):
    """Genuine run_in_background: header + tailer on the redirect target,
    writer-gone completion, bg-recheck flips the stale blue to green."""
    s = session.make()
    out = os.path.join(s.cwd, "bg.log")
    cmd = "build_stuff > %s" % out
    w = writer(out)                # long-lived; the test terminates it below
    run_hook("claude-cmd-fmt.py",
             P.post_bash(s, cmd, run_in_background=True,
                         background_task_id="bg-" + uuid.uuid4().hex[:8]))
    assert "▷ background" in s.ops_text()
    with open(out, "a") as f:
        f.write("bg line\n")
    wait_until(lambda: "bg line" in s.ops_text(), desc="bg output streams")

    run_hook(TAB, P.stop(s), argv=("stop",))     # live bg row -> blue
    assert oracle.tab_state(test_env, fake_kitten.window_id) == "awaiting-bg"

    w.terminate()
    wait_until(lambda: "writer-gone" in end_reasons(test_env, s.sid),
               desc="bg stream ends when the writer exits")
    wait_until(lambda: oracle.tab_state(test_env, fake_kitten.window_id)
               == "awaiting-response", desc="bg-recheck flips blue to green")
    assert not s.live("bg"), "bg slot not released"
    # the stop dispatch also spawned a bg-watch; it exits state-moved-on a
    # poll tick after the green lands — let its stream row close first
    wait_until(lambda: streams_all_ended(test_env, s.sid),
               desc="bg-watch stream row closed")
    oracle.assert_clean(test_env, s.sid)


@needs_private_tmp
def test_f4b_ctrl_b_conversion(run_hook, test_env, session, task_dir, writer):
    """Ctrl+B mid-command: fg tailer bows out silently (converted-ctrl-b),
    a bg tailer takes over from Claude Code's own tasks/<id>.output file."""
    s = session.make()
    run_hook("claude-cmd-pre.py", P.pre_bash(s, "long_job"))
    rec = fg_live_record(s)
    wtee = writer(rec["src"])
    with open(rec["src"], "a") as f:
        f.write("before ctrl+b\n")
    wait_until(lambda: "before ctrl+b" in s.ops_text(), desc="fg tee streams")

    taskid = "bgt-" + uuid.uuid4().hex[:8]
    out = os.path.join(task_dir, taskid + ".output")
    wbg = writer(out)              # long-lived; terminated below
    run_hook("claude-cmd-fmt.py",
             P.post_bash(s, "long_job", background_task_id=taskid,
                         backgrounded_by_user=True, duration_ms=800))
    wtee.terminate()
    wait_until(lambda: "converted-ctrl-b" in end_reasons(test_env, s.sid),
               desc="fg tailer bows out on the converted sentinel")
    assert "backgrounded (ctrl+b)" in s.ops_text()

    with open(out, "a") as f:
        f.write("after ctrl+b\n")
    wait_until(lambda: "after ctrl+b" in s.ops_text(),
               desc="bg tailer continues the block")
    wbg.terminate()
    wait_until(lambda: streams_all_ended(test_env, s.sid), desc="all ended")
    oracle.assert_clean(test_env, s.sid)


# --------------------------------------------------------------------- F5

SUB_EVENTS = [
    {"type": "user", "message": {"role": "user", "content": "find the bug"}},
    {"type": "assistant", "message": {
        "id": "smsg_1", "model": "claude-opus-4-8", "role": "assistant",
        "content": [{"type": "text", "text": "scanning the tree now"}],
        "usage": {"input_tokens": 40, "output_tokens": 12,
                  "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}},
    {"type": "assistant", "message": {
        "id": "smsg_2", "model": "claude-opus-4-8", "role": "assistant",
        "content": [{"type": "tool_use", "id": "tu_1", "name": "Bash",
                     "input": {"command": "grep -r bug ."}}],
        "usage": {"input_tokens": 50, "output_tokens": 9,
                  "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}},
    {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "tu_1",
         "content": "src/x.py: bug here"}]}},
]


def test_f5_subagent_lifecycle(run_hook, test_env, session):
    """push -> start -> transcript streams in order -> stop: desc queue,
    slot + agents row, substream rendering, done flag, stream end."""
    s = session.make()
    agent = "agent-" + uuid.uuid4().hex[:8]
    run_hook("claude-subagent-fmt.py", P.pre_task(s, "hunt the bug"),
             argv=("push",))
    s.write_subagent_jsonl(agent, [])
    run_hook("claude-subagent-fmt.py", P.subagent_start(s, agent_id=agent),
             argv=("start",))
    assert "hunt the bug" in s.ops_text(), "queued desc not on the header"

    s.write_subagent_jsonl(agent, SUB_EVENTS)
    wait_until(lambda: "scanning the tree now" in s.ops_text(),
               desc="subagent message rendered")
    wait_until(lambda: "grep -r bug ." in s.ops_text(),
               desc="subagent tool_use rendered")

    run_hook("claude-subagent-fmt.py", P.subagent_stop(s, agent_id=agent),
             argv=("stop",))
    # `done` is a transient signal (reset after the streamer finalises) — the
    # durable evidence is the stream's end_reason.
    wait_until(lambda: "stop-sentinel" in end_reasons(test_env, s.sid),
               desc="substream finalises on the stop sentinel")
    wait_until(lambda: not s.live(), desc="subagent slot released")
    oracle.assert_clean(test_env, s.sid)


def test_f5b_duplicate_start_and_stop_mid_flow(run_hook, test_env, session):
    """Background agents really do fire SubagentStart/Stop twice (CLAUDE.md);
    the flow must survive without duplicate blocks or leaked slots."""
    s = session.make()
    agent = "agent-" + uuid.uuid4().hex[:8]
    s.write_subagent_jsonl(agent, [])
    run_hook("claude-subagent-fmt.py", P.subagent_start(s, agent_id=agent),
             argv=("start",))
    run_hook("claude-subagent-fmt.py", P.subagent_start(s, agent_id=agent),
             argv=("start",))
    # through the tool_use event — a buffered text message only flushes on the
    # NEXT renderable event (the substream's message coalescing)
    s.write_subagent_jsonl(agent, SUB_EVENTS[:3])
    wait_until(lambda: "scanning the tree now" in s.ops_text(), desc="rendered")
    assert s.ops_text().count("scanning the tree now") == 1, \
        "duplicate start double-rendered the transcript"
    run_hook("claude-subagent-fmt.py", P.subagent_stop(s, agent_id=agent),
             argv=("stop",))
    run_hook("claude-subagent-fmt.py", P.subagent_stop(s, agent_id=agent),
             argv=("stop",))
    wait_until(lambda: streams_all_ended(test_env, s.sid), desc="ended")
    assert not s.live()
    oracle.assert_clean(test_env, s.sid,
                        allow=("duplicate SubagentStart",))   # the flagged real dup


# --------------------------------------------------------------------- F6

def test_f6_teammate_rendering(run_hook, test_env, session):
    """A teammate (meta.json taskKind=in_process_teammate) renders with the
    teammate header and its <teammate-message> traffic is unwrapped."""
    s = session.make()
    agent = "mate-" + uuid.uuid4().hex[:8]
    s.write_meta(agent, taskKind="in_process_teammate")
    s.write_subagent_jsonl(agent, [])
    run_hook("claude-subagent-fmt.py",
             P.subagent_start(s, agent_id=agent, agent_type="worker"),
             argv=("start",))
    assert "teammate" in s.ops_text()
    s.write_subagent_jsonl(agent, [
        {"type": "user", "message": {"role": "user", "content":
         '<teammate-message teammate_id="lead" summary="go">'
         'ping from the lead</teammate-message>'}},
    ])
    wait_until(lambda: "ping from the lead" in s.ops_text(),
               desc="teammate message unwrapped and rendered")
    run_hook("claude-subagent-fmt.py",
             P.subagent_stop(s, agent_id=agent, agent_type="worker"),
             argv=("stop",))
    wait_until(lambda: streams_all_ended(test_env, s.sid), desc="ended")
    oracle.assert_clean(test_env, s.sid)


# --------------------------------------------------------------------- F7

@needs_private_tmp
def test_f7_monitor_lifecycle(run_hook, test_env, session, task_dir, reaper):
    """Monitor: header + tailer on tasks/<id>.output, completion detected by
    the monitored PROCESS exiting (find_proc on CLAUDE_MONITOR_CMD)."""
    s = session.make()
    token = "monsig%s" % uuid.uuid4().hex[:8]
    # two statements so sh can't exec-optimize itself away — find_proc needs
    # the full command string (incl. the token) visible in a live process argv
    cmd = "sleep 1.5; true #%s" % token
    proc = subprocess.Popen(["/bin/sh", "-c", cmd], start_new_session=True)
    reaper.append(proc)
    taskid = "mon-" + uuid.uuid4().hex[:8]
    out = os.path.join(task_dir, taskid + ".output")
    with open(out, "w") as f:
        f.write("monitor event 1\n")
    run_hook("claude-monitor-fmt.py",
             P.post_monitor(s, description="watching", command=cmd,
                            task_id=taskid))
    assert "◉ monitor" in s.ops_text()
    wait_until(lambda: "monitor event 1" in s.ops_text(),
               desc="monitor output streams")
    proc.wait()
    wait_until(lambda: any("monitor-process-exited" in (r or "")
                           for r in end_reasons(test_env, s.sid)),
               timeout=15, desc="monitor ends when its process exits")
    assert not s.live("monitor"), "monitor slot not released"
    oracle.assert_clean(test_env, s.sid)


# --------------------------------------------------------------------- F8

def test_f8_task_rows(run_hook, test_env, session):
    s = session.make()
    run_hook("claude-task-fmt.py", P.task_created(s, "3", "Refactor the seams"))
    run_hook("claude-task-fmt.py", P.task_completed(s, "3", "Refactor the seams"))
    text = s.ops_text()
    assert text.count("Refactor the seams") == 2
    oracle.assert_clean(test_env, s.sid)


# --------------------------------------------------------------------- F9

def test_f9a_cancelled_fg_command_self_heals(run_hook, test_env, session):
    """Cancel fires NO hook: the fg tailer must notice the writer never
    lived, exit writer-gone, release the slot and reclaim its record."""
    s = session.make()
    run_hook("claude-cmd-pre.py", P.pre_bash(s, "sleep 999"))
    assert fg_live_record(s) is not None
    wait_until(lambda: "writer-gone" in end_reasons(test_env, s.sid),
               desc="tailer notices there is no writer")
    wait_until(lambda: not s.live("fg"), desc="fg slot released")
    wait_until(lambda: fg_live_record(s) is None,
               desc="tailer reclaims its own fg-live record")
    oracle.assert_clean(test_env, s.sid)


def test_f9b_cancelled_subagent_stops_via_meta(run_hook, test_env, session):
    """SubagentStop never fires on cancel — stoppedByUser in meta.json is the
    recovery signal the substream polls."""
    s = session.make()
    agent = "agent-" + uuid.uuid4().hex[:8]
    s.write_subagent_jsonl(agent, [])
    run_hook("claude-subagent-fmt.py", P.subagent_start(s, agent_id=agent),
             argv=("start",))
    s.write_subagent_jsonl(agent, SUB_EVENTS[:3])
    wait_until(lambda: "scanning the tree now" in s.ops_text(), desc="running")
    s.write_meta(agent, stoppedByUser=True)
    wait_until(lambda: any("stoppedByUser" in (r or "")
                           for r in end_reasons(test_env, s.sid)),
               desc="substream exits on stoppedByUser")
    wait_until(lambda: not s.live(), desc="slot released")
    oracle.assert_clean(test_env, s.sid,
                        allow=("SubagentStart without SubagentStop",))  # the cancel


def test_f9d_rejected_subagent_stops_via_parent_result(run_hook, test_env,
                                                       session):
    """A REJECTED Task fires no SubagentStop AND leaves meta.json without
    stoppedByUser — neither f5's stop-sentinel nor f9b's meta signal ever comes.
    The parent transcript's tool_result for the Task's toolUseId is the recovery
    signal, so the streamer exits (and releases its tab-blue-holding slot)
    instead of hanging until the 6h backstop."""
    s = session.make()
    agent = "agent-" + uuid.uuid4().hex[:8]
    tid = "toolu_" + uuid.uuid4().hex[:12]
    s.write_meta(agent, toolUseId=tid)          # the parent Task call's id
    s.write_subagent_jsonl(agent, [])
    run_hook("claude-subagent-fmt.py", P.subagent_start(s, agent_id=agent),
             argv=("start",))
    s.write_subagent_jsonl(agent, SUB_EVENTS[:3])
    wait_until(lambda: "scanning the tree now" in s.ops_text(), desc="running")
    # The user rejected the Task: its result lands in the PARENT transcript,
    # is_error, with NO SubagentStop and NO stoppedByUser stamp.
    s.add_line({"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tid, "is_error": True,
         "content": "The user doesn't want to proceed with this tool use."}]}})
    wait_until(lambda: any("parent-task-resolved" in (r or "")
                           for r in end_reasons(test_env, s.sid)),
               desc="substream exits on the parent Task result")
    wait_until(lambda: not s.live(), desc="slot released")
    oracle.assert_clean(test_env, s.sid,
                        allow=("SubagentStart without SubagentStop",))  # the reject


def test_f9e_api_error_subagent_stops_via_stopfailure(run_hook, test_env, session):
    """A subagent turn that dies on an API error (529) fires StopFailure carrying
    its agent_id and NO SubagentStop (nor stoppedByUser). For an async agent the
    parent tool_result is only the launch ack, so f9d's recovery can't fire either.
    claude-stop-fmt.py must hand that StopFailure to the same finaliser SubagentStop
    uses — else the streamer keeps its tab-blue-holding slot and hangs."""
    s = session.make()
    agent = "agent-" + uuid.uuid4().hex[:8]
    s.write_subagent_jsonl(agent, [])
    run_hook("claude-subagent-fmt.py", P.subagent_start(s, agent_id=agent),
             argv=("start",))
    s.write_subagent_jsonl(agent, SUB_EVENTS[:3])
    wait_until(lambda: "scanning the tree now" in s.ops_text(), desc="running")
    # The agent's final turn 529'd: StopFailure fires with the agent_id, no
    # SubagentStop, no stoppedByUser stamp.
    run_hook("claude-stop-fmt.py",
             P.base(s, "StopFailure", agent_id=agent, agent_type="Explore",
                    error="server_error",
                    last_assistant_message="API Error: 529 Overloaded."))
    wait_until(lambda: "stop-sentinel" in end_reasons(test_env, s.sid),
               desc="streamer finalises on the done flag set by stop-fmt")
    wait_until(lambda: not s.live(), desc="slot released")
    # SubagentStart-without-SubagentStop is expected (none ever fired), and the
    # StopFailure itself lands in the informational "failed tools" section (hook
    # matches %Failure%). The stuck-blue *regression* anomaly must NOT fire — the
    # StopFailure was handed to the finaliser (decision 'stopfail: …').
    oracle.assert_clean(test_env, s.sid,
                        allow=("SubagentStart without SubagentStop", "failed tools"))


def test_f9c_interrupted_reply_flips_green(run_hook, test_env, session,
                                           fake_kitten):
    """A cancelled plain reply leaves only the transcript line — the
    interrupt-watch must flip the stuck magenta to green."""
    s = session.make()
    s.add_user()

    def watcher_alive():
        rows = oracle.watchers(test_env, "interruptwatch")
        if not rows:
            return False
        try:
            os.kill(int(rows[0][2]), 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    run_hook(TAB, P.user_prompt(s), argv=("thinking",))
    assert oracle.tab_state(test_env, fake_kitten.window_id) == "thinking"
    # At test poll speed the first watcher can beat the paint and exit
    # turn-over on the still-empty tab row; re-running the dispatch re-ensures
    # one (production's 0.5s first tick never loses this race).
    run_hook(TAB, P.user_prompt(s), argv=("thinking",))
    wait_until(watcher_alive, desc="a live interrupt-watch on the magenta tab")
    time.sleep(0.3)                    # let it take its size snapshot
    s.add_interrupted()
    wait_until(lambda: oracle.tab_state(test_env, fake_kitten.window_id)
               == "awaiting-response", desc="interrupt-watch flips green")
    wait_until(lambda: streams_all_ended(test_env, s.sid),
               desc="interrupt-watch stream row closed")


# -------------------------------------------------------------------- F10

def test_f10_resume_restores_mirror_history(run_hook, test_env, session,
                                            fake_kitten):
    # fake_kitten matters: without a live listen socket claude-split.py no-ops
    # entirely ("not inside kitty"), so open/close would never park/restore
    s = session.make()
    run_hook("claude-split.py", P.session_start(s), argv=("open",))
    run_hook("claude-file-fmt.py", P.post_file(s, tool="Edit"))
    run_hook("claude-file-fmt.py", P.post_file(s, tool="Write"))
    ops_before = len(s.ops())
    assert ops_before >= 2

    run_hook("claude-split.py", P.session_end(s), argv=("close",))
    assert os.path.exists(s.state_db + ".keep")
    assert not os.path.exists(s.state_db)

    run_hook("claude-split.py", P.session_start(s, source="resume"),
             argv=("open",))
    assert os.path.exists(s.state_db), "state DB not restored on resume"
    assert len(s.ops()) == ops_before, "mirror history lost across resume"
    actions = [r[1] for r in oracle.state_files(test_env, s.sid)]
    assert "keep-history" in actions and "restore-history" in actions
    # close again so the second open's codex watcher exits before the oracle
    run_hook("claude-split.py", P.session_end(s), argv=("close",))
    wait_until(lambda: streams_all_ended(test_env, s.sid), desc="all ended")
    oracle.assert_clean(test_env, s.sid)   # incl. 'resume lost history' == 0


# -------------------------------------------------------------------- F11

def test_f11_session_end_parks_db_and_substream_exits(run_hook, test_env,
                                                      session, fake_kitten):
    """SessionEnd while an agent stream is still live: parking the DB is the
    exit signal — no orphaned tailer, stream row closed with state-db-parked."""
    s = session.make()
    agent = "agent-" + uuid.uuid4().hex[:8]
    s.write_subagent_jsonl(agent, [])
    run_hook("claude-subagent-fmt.py", P.subagent_start(s, agent_id=agent),
             argv=("start",))
    s.write_subagent_jsonl(agent, SUB_EVENTS[:3])
    wait_until(lambda: "scanning the tree now" in s.ops_text(), desc="live")

    run_hook("claude-split.py", P.session_end(s), argv=("close",))
    assert os.path.exists(s.state_db + ".keep")
    wait_until(lambda: streams_all_ended(test_env, s.sid),
               desc="substream exits once the DB is parked")
    assert any("parked" in (r or "") for r in end_reasons(test_env, s.sid))
    # The exiting substream fires a detached bg-recheck that lands a beat
    # after its stream row closes. Let it fully settle (its transition row is
    # written before its tab write, so also wait for the tab to reflect an
    # applied flip), THEN run SessionEnd's other wiring row — the tab clear —
    # so the recheck can't repaint over the cleared tab.
    def recheck_settled():
        rows = [r for r in oracle.transitions(test_env, s.sid)
                if r[0] == "bg-recheck"]
        if not rows:
            return False
        if rows[-1][3] == 1:       # applied flip -> wait for the tab to show it
            return oracle.tab_state(test_env, fake_kitten.window_id) == rows[-1][2]
        return True                 # bailed -> nothing more will be painted
    wait_until(recheck_settled, timeout=15,
               desc="the exiting substream's bg-recheck settled")
    run_hook(TAB, P.session_end(s), argv=("clear",))
    assert oracle.tab_state(test_env, fake_kitten.window_id) is None
    oracle.assert_clean(test_env, s.sid,
                        allow=("SubagentStart without SubagentStop",
                               "slot claims without a matching release"))
