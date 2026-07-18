# L2 — end-to-end session flows.
#
# Each flow drives an ordered hook sequence with real side effects (tee'd .out
# files with real write-holders, transcripts, subagent JSONL + meta.json) and
# ends with the audit oracle: `anomalies` must be clean, streams must have
# ended with the right end_reason, slots must be released. These are the
# regression net for the cancel-recovery / hand-off / park-restore machinery.
import json
import os
import sqlite3
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
    product treats 'no process holds the file open for write' as job-done.
    Long-lived by default — every test ENDS it explicitly (terminate()), and
    the reaper sweeps leftovers. A 30s lifetime silently expired mid-test on
    slow runners (the scaled wait ceilings alone can exceed it), ending the
    stream writer-gone before the test wrote its next line — a flake that got
    WORSE, not better, as wait ceilings grew."""
    def _start(path, seconds=600):
        # >> (append) not > : truncating would race lines the test already
        # wrote into the file; append still registers as a write-holder.
        p = subprocess.Popen(["/bin/sh", "-c",
                              "exec sleep %s >> %s" % (seconds, path)],
                             start_new_session=True)
        reaper.append(p)
        wait_until(lambda: os.path.exists(path), desc="writer file appears")
        return p
    return _start


@pytest.fixture
def task_dir(test_env):
    """A tasks/ dir matching claude-stream.py's task-output glob — where Claude
    Code drops tasks/<id>.output for bg jobs and monitors. Built inside the
    per-test sandbox via CLAUDE_TASKS_GLOB_ROOT (docs/testing.md); the shipped
    default is the real macOS /private/tmp/claude-* location."""
    d = os.path.join(test_env["CLAUDE_TASKS_GLOB_ROOT"],
                     "e2e-%s" % uuid.uuid4().hex[:8], "tasks")
    os.makedirs(d)
    return d


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
    run_hook("claude-cmd-pre.py", P.pre_bash(s, "echo f1"))
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
    assert os.path.exists(s.parked_db) and not os.path.exists(s.state_db)
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


# ------------------------------------------------------------------ F2-md

# The amber SGR core/render.BANNER emits for a heading — present only if the body
# was markdown-RENDERED, never in raw `cat` output. The discriminator for this test.
_BANNER_SGR = "38;2;229;192;123"


def test_f2md_markdown_file_is_pretty_rendered(
        run_hook, test_env, session, writer):
    """`cat notes.md` (CT.md_source -> CLAUDE_STREAM_MD) streams the file through
    core/mdrender: headings become bold-amber banners, **bold** becomes SGR — not
    the raw `#`/`**` characters."""
    s = session.make()
    run_hook("claude-cmd-pre.py", P.pre_bash(s, "cat notes.md"))
    rec = fg_live_record(s)
    w = writer(rec["src"])                       # the running command holds the tee open
    # A complete block (heading + terminating blank) flushes live; likewise the paragraph.
    with open(rec["src"], "a") as f:
        f.write("# Bold Heading\n\nThis is **strong** text.\n\n")
    wait_until(lambda: "Bold Heading" in s.ops_text(), desc="heading text in mirror")
    wait_until(lambda: _BANNER_SGR in s.ops_text(),
               desc="heading rendered as a banner (markdown mode active)")
    assert "\x1b[1m" in s.ops_text(), "bold SGR from **strong**"
    # The raw markdown metacharacters should NOT survive as literal text.
    assert "# Bold Heading" not in s.ops_text(), "heading shown raw, not rendered"

    run_hook("claude-cmd-fmt.py", P.post_bash(s, "cat notes.md", duration_ms=200))
    w.terminate()
    wait_until(lambda: "sentinel" in end_reasons(test_env, s.sid),
               desc="fg stream ends on the done hand-off")
    assert not s.live("fg"), "fg slot not released"
    oracle.assert_clean(test_env, s.sid)


def test_f2sniff_fenced_output_renders_as_markdown(
        run_hook, test_env, session, writer):
    """A fg command with NO render extension (cat report.txt) whose OUTPUT contains
    a fenced code block is auto-detected as markdown (CLAUDE_MIRROR_MD_SNIFF): the
    fence highlights by language even though the filename gave no hint."""
    s = session.make()
    run_hook("claude-cmd-pre.py", P.pre_bash(s, "cat report.txt"))
    rec = fg_live_record(s)
    w = writer(rec["src"])
    with open(rec["src"], "a") as f:
        f.write("# Report\n\nSome **prose**.\n\n```json\n{\"replicas\": 3}\n```\n\n")
    # The heading became an amber banner and the json fence is colour-highlighted.
    wait_until(lambda: "38;2;229;192;123" in s.ops_text(),
               desc="fenced output auto-rendered as markdown")
    wait_until(lambda: '"replicas"' in s.ops_text(), desc="json fence content shown")
    assert "# Report" not in s.ops_text(), "heading shown raw, not rendered"
    run_hook("claude-cmd-fmt.py", P.post_bash(s, "cat report.txt", duration_ms=150))
    w.terminate()
    wait_until(lambda: "sentinel" in end_reasons(test_env, s.sid),
               desc="fg stream ends")
    assert not s.live("fg"), "fg slot not released"
    oracle.assert_clean(test_env, s.sid)


def test_f2sniff_plain_output_stays_verbatim(
        run_hook, test_env, session, writer):
    """A fg command whose output has NO fence streams verbatim, live, line by line
    (the sniff must never delay or swallow ordinary output)."""
    s = session.make()
    run_hook("claude-cmd-pre.py", P.pre_bash(s, "make build"))
    rec = fg_live_record(s)
    w = writer(rec["src"])
    with open(rec["src"], "a") as f:
        f.write("compiling module A\n")
    wait_until(lambda: "compiling module A" in s.ops_text(), desc="plain line live")
    with open(rec["src"], "a") as f:
        f.write("# not a heading, just a log\n")
    wait_until(lambda: "# not a heading" in s.ops_text(),
               desc="a stray # is NOT treated as markdown (no fence)")
    run_hook("claude-cmd-fmt.py", P.post_bash(s, "make build", duration_ms=150))
    w.terminate()
    wait_until(lambda: "sentinel" in end_reasons(test_env, s.sid), desc="fg ends")
    oracle.assert_clean(test_env, s.sid)


def test_f2json_json_file_is_pretty_printed(
        run_hook, test_env, session, writer):
    """`cat data.json` (CT.json_source -> CLAUDE_STREAM_JSON) buffers the file and,
    at completion, pretty-prints + colours it (no background panel)."""
    s = session.make()
    run_hook("claude-cmd-pre.py", P.pre_bash(s, "cat data.json"))
    rec = fg_live_record(s)
    w = writer(rec["src"])
    with open(rec["src"], "a") as f:
        f.write('{"name":"adapter","count":3,"enabled":true}\n')
    # JSON renders only at close, so drive PostToolUse to finish the stream.
    run_hook("claude-cmd-fmt.py", P.post_bash(s, "cat data.json", duration_ms=120))
    w.terminate()
    wait_until(lambda: "sentinel" in end_reasons(test_env, s.sid),
               desc="fg stream ends on the done hand-off")
    # All three keys survive the pretty-print, and the keys are coloured blue
    # (COL func) — proving the json-render path engaged (a raw cat wouldn't colour).
    txt = s.ops_text()
    for key in ('"name"', '"count"', '"enabled"'):
        assert key in txt, key
    assert "38;2;97;175;239" in txt, "JSON keys not coloured (json-render didn't run)"
    assert not s.live("fg"), "fg slot not released"
    oracle.assert_clean(test_env, s.sid)


# The magenta SGR render.pick assigns pygments Keyword tokens — present only if
# the body went through coderender, never in verbatim output.
_KEYWORD_SGR = "38;2;198;120;221"


def test_f2code_source_file_is_syntax_highlighted(
        run_hook, test_env, session, writer):
    """`sed -n '1,3p' Foo.kt` colours the body with the kotlin lexer. Detection
    runs in the TAILER, from the raw command passed via CLAUDE_STREAM_CMD
    (hookkit.stream_env) — launch sites pass the command, never the decision."""
    s = session.make()
    run_hook("claude-cmd-pre.py", P.pre_bash(s, "sed -n '1,3p' Foo.kt"))
    rec = fg_live_record(s)
    w = writer(rec["src"])
    with open(rec["src"], "a") as f:
        f.write("fun main() {\n    val x = 3\n}\n")
    # Code renders only at close (partial colouring is unreliable) — finish it.
    run_hook("claude-cmd-fmt.py", P.post_bash(s, "sed -n '1,3p' Foo.kt",
                                              duration_ms=100))
    w.terminate()
    wait_until(lambda: "sentinel" in end_reasons(test_env, s.sid),
               desc="fg stream ends on the done hand-off")
    txt = s.ops_text()
    assert "fun" in txt and "val" in txt, "kotlin body missing from mirror"
    assert _KEYWORD_SGR in txt, "keywords not coloured (code-render didn't run)"
    assert not s.live("fg"), "fg slot not released"
    oracle.assert_clean(test_env, s.sid)


def test_f2code_subagent_fg_is_syntax_highlighted(run_hook, test_env, session):
    """A SUBAGENT's `cat Foo.kt` colours too: the substream's fg tailer gets the
    same content-render detection as the main session, because every launch site
    now passes the raw command (the regression here was a subagent's source read
    streaming uncoloured — the launch sites each hand-assembled the tailer env
    and the subagent one silently missed the render key)."""
    s = session.make()
    agent = "agent-" + uuid.uuid4().hex[:8]
    s.write_subagent_jsonl(agent, [])
    run_hook("claude-subagent-fmt.py", P.subagent_start(s, agent_id=agent),
             argv=("start",))
    # The subagent's PreToolUse tees the command and leaves the subfg:<tid>
    # hand-off; the substream consumes it at the transcript's tool_use.
    run_hook("claude-cmd-pre.py",
             P.pre_bash(s, "cat Foo.kt", tid="tu_kt", agent_id=agent))
    src = s.log + ".subfg.tu_kt.out"
    with open(src, "a") as f:
        f.write("fun main() {\n    val x = 3\n}\n")
    s.write_subagent_jsonl(agent, [
        {"type": "assistant", "message": {
            "id": "smsg_kt", "model": "claude-opus-4-8", "role": "assistant",
            "content": [{"type": "tool_use", "id": "tu_kt", "name": "Bash",
                         "input": {"command": "cat Foo.kt"}}],
            "usage": {"input_tokens": 5, "output_tokens": 3,
                      "cache_creation_input_tokens": 0,
                      "cache_read_input_tokens": 0}}},
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu_kt",
             "content": "fun main() {\n    val x = 3\n}"}]}},
    ])
    wait_until(lambda: "cat Foo.kt" in s.ops_text(),
               desc="subagent fg command rendered")
    wait_until(lambda: _KEYWORD_SGR in s.ops_text(),
               desc="subagent fg output kotlin-coloured (render key reached "
                    "the substream-spawned tailer)")
    run_hook("claude-subagent-fmt.py", P.subagent_stop(s, agent_id=agent),
             argv=("stop",))
    wait_until(lambda: "stop-sentinel" in end_reasons(test_env, s.sid),
               desc="substream finalises on the stop sentinel")
    wait_until(lambda: not s.live(), desc="all slots released")
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


def test_f3b_fg_waits_for_late_redirect_target(run_hook, test_env, session,
                                                writer):
    """A foreground command that creates its own redirect target LATE
    (`sleep 45; cmd > out`, a retry loop) is still running — the fg tailer must
    wait on command LIVENESS (the PostToolUse outcome hand-off), not the flat
    FIND_S deadline. The old bounded wait painted "output not found" and released
    the fg slot at ~12s, so bg-recheck cleared the tab off blue while the command
    ran on and the late output never streamed (audit tell: an fg
    'output-file-not-found' stream whose PostToolUse fired seconds later)."""
    s = session.make()
    env = dict(test_env)
    env["CLAUDE_STREAM_FIND_S"] = "0.3"   # any bounded-wait regression trips fast
    target = os.path.join(test_env["CLAUDE_MIRROR_TMPDIR"],
                          "late-%s.out" % uuid.uuid4().hex[:8])
    cmd = "sleep 45; adapters mr > %s 2>&1" % target
    run_hook("claude-cmd-pre.py", P.pre_bash(s, cmd), env=env)
    rec = fg_live_record(s)
    assert rec["src"] == target and not rec["own"], "tailing the command's own redirect"

    # Deliberate blind sleep: this asserts event ABSENCE ("output not found" never
    # painted; slot never released) well past FIND_S — an absence can't be polled.
    time.sleep(1.5)
    assert "output not found" not in s.ops_text(), \
        "fg tailer gave up on the late redirect target"
    assert s.live("fg"), "fg slot released while the command still runs"
    assert not any(end_reasons(test_env, s.sid)), "fg stream ended early"

    # The command finally creates its redirect target (a real write-holder, so
    # writer-liveness reads the command as still running).
    w = writer(target)
    with open(target, "a") as f:
        f.write("late line\n")
    wait_until(lambda: "late line" in s.ops_text(), desc="late-created output streams")

    run_hook("claude-cmd-fmt.py", P.post_bash(s, cmd, duration_ms=45000), env=env)
    w.terminate()
    wait_until(lambda: "sentinel" in end_reasons(test_env, s.sid),
               desc="fg stream ends on the done hand-off")
    assert not s.live("fg"), "fg slot not released"
    oracle.assert_clean(test_env, s.sid)


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


def test_f4c_bg_tailer_exits_on_park_without_recreating_db(
        run_hook, test_env, session, writer):
    """A background job silent across SessionEnd: parking the state DB is the
    bg tailer's exit signal (same probe the substream/codex tailers poll). The
    check runs BEFORE the pump, so output landing after the park is never
    emitted — a post-park emit recreated a fresh empty DB at the live path
    (whose absence is the session-alive signal), turning the next resume into
    reuse-live-db with the real history stranded in the park."""
    s = session.make()
    out = os.path.join(s.cwd, "bg.log")
    w = writer(out)                # long-lived; terminated below
    run_hook("claude-cmd-fmt.py",
             P.post_bash(s, "quiet_job > %s" % out, run_in_background=True,
                         background_task_id="bg-" + uuid.uuid4().hex[:8]))
    with open(out, "a") as f:
        f.write("before park\n")
    wait_until(lambda: "before park" in s.ops_text(), desc="bg output streams")

    run_hook("claude-split.py", P.session_end(s), argv=("close",))  # parks it
    assert os.path.exists(s.parked_db) and not os.path.exists(s.state_db)
    with open(out, "a") as f:
        f.write("after park\n")    # the losing interleaving: job prints post-park

    wait_until(lambda: streams_all_ended(test_env, s.sid),
               desc="bg tailer exits once the DB is parked")
    assert any("state-db-parked" in (r or "")
               for r in end_reasons(test_env, s.sid))
    assert not os.path.exists(s.state_db), \
        "post-park emit recreated the live state DB"
    conn = sqlite3.connect("file:%s?mode=ro" % s.parked_db, uri=True, timeout=5)
    try:
        parked_ops = "\n".join(r[0] for r in conn.execute("SELECT op FROM ops"))
    finally:
        conn.close()
    assert "after park" not in parked_ops, "post-park output polluted the park"
    w.terminate()
    oracle.assert_clean(test_env, s.sid,
                        allow=("slot claims without a matching release",))


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
    # Producer-source stamping (core/ops.py "src"): every substream-rendered op
    # carries this agent's stamp — the web mirror drops them — while the launch
    # header the hook process emitted stays unstamped (main-session activity).
    ops = s.ops()
    stamped = [op for op in ops if op.get("src")]
    assert stamped and all(op["src"] == "sub:" + agent for op in stamped)
    assert any("scanning the tree now" in str(op) for op in stamped), \
        "the substream's ops must carry the agent's src stamp"
    hdr = next(op for op in ops
               if op.get("t") == "label" and "hunt the bug" in op.get("s", ""))
    assert "src" not in hdr, "the launch header is the lead's own op"

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

def mon_proc_latched(env, sid):
    """The tailer's audited monitor-pid latch (stream.mon_proc_found): only
    after it may the test end its monitor stand-in and still expect the
    process-exit end reason. Ending the stand-in on its own timer raced the
    tailer's startup — find_proc cannot identify a process that exited before
    the tailer ever ran `ps` (spawn takes seconds on a loaded runner), and the
    stream then ends idle-fallback instead."""
    return any(a == "proc-found" for _, a, _ in oracle.state_files(env, sid))


def test_f7_monitor_lifecycle(run_hook, test_env, session, task_dir, reaper):
    """Monitor: header + tailer on tasks/<id>.output, completion detected by
    the monitored PROCESS exiting (find_proc on CLAUDE_MONITOR_CMD)."""
    s = session.make()
    token = "monsig%s" % uuid.uuid4().hex[:8]
    # two statements so sh can't exec-optimize itself away — find_proc needs
    # the full command string (incl. the token) visible in a live process argv;
    # long-lived: the TEST ends it, after the tailer verifiably latched it
    cmd = "sleep 300; true #%s" % token
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
    wait_until(lambda: mon_proc_latched(test_env, s.sid),
               desc="tailer latches the monitor process")
    proc.terminate()
    proc.wait()          # reap — a zombie still reads as alive to pid_alive
    wait_until(lambda: any("monitor-process-exited" in (r or "")
                           for r in end_reasons(test_env, s.sid)),
               timeout=15, desc="monitor ends when its process exits")
    assert not s.live("monitor"), "monitor slot not released"
    oracle.assert_clean(test_env, s.sid)


def test_f7b_monitor_waits_for_lazy_output_file(run_hook, test_env, session,
                                                task_dir, reaper):
    """Claude Code creates tasks/<id>.output LAZILY on the monitor's first
    output byte — a quiet monitor has NO file for minutes. The tailer must keep
    waiting while the monitor PROCESS is alive (slot held, tab stays blue); the
    old bounded wait painted "output not found" and released the slot at 12s."""
    s = session.make()
    env = dict(test_env)
    env["CLAUDE_STREAM_FIND_S"] = "0.3"   # any bounded-wait regression trips fast
    token = "monsig%s" % uuid.uuid4().hex[:8]
    # long-lived: the TEST ends it, after the tailer verifiably latched it
    cmd = "sleep 300; true #%s" % token
    proc = subprocess.Popen(["/bin/sh", "-c", cmd], start_new_session=True)
    reaper.append(proc)
    taskid = "mon-" + uuid.uuid4().hex[:8]
    run_hook("claude-monitor-fmt.py",
             P.post_monitor(s, description="quiet monitor", command=cmd,
                            task_id=taskid), env=env)
    # Deliberate blind sleep: this asserts event ABSENCE ("output not found"
    # never painted), which cannot be polled — well past FIND_S by design.
    time.sleep(1.5)
    assert "output not found" not in s.ops_text(), \
        "tailer gave up on the lazily-created output file"
    assert s.live("monitor"), "monitor slot released while the monitor runs"
    assert not any(end_reasons(test_env, s.sid)), "monitor stream ended early"
    out = os.path.join(task_dir, taskid + ".output")   # first output, minutes in
    with open(out, "w") as f:
        f.write("late event\n")
    wait_until(lambda: "late event" in s.ops_text(),
               desc="late-created output streams")
    wait_until(lambda: mon_proc_latched(test_env, s.sid),
               desc="tailer latches the monitor process")
    proc.terminate()
    proc.wait()          # reap — a zombie still reads as alive to pid_alive
    wait_until(lambda: any("monitor-process-exited" in (r or "")
                           for r in end_reasons(test_env, s.sid)),
               timeout=15, desc="monitor ends when its process exits")
    assert not s.live("monitor"), "monitor slot not released"
    oracle.assert_clean(test_env, s.sid)


def test_f7c_monitor_dies_before_any_output(run_hook, test_env, session,
                                            task_dir, reaper):
    """Monitor process exits without ever writing output: the block closes
    ("monitor ended · no output") and the slot releases so the tab can clear —
    the process's death, not a timeout, is the "nothing to show" signal."""
    s = session.make()
    token = "monsig%s" % uuid.uuid4().hex[:8]
    # long-lived: the TEST ends it, after the tailer verifiably latched it
    cmd = "sleep 300; true #%s" % token
    proc = subprocess.Popen(["/bin/sh", "-c", cmd], start_new_session=True)
    reaper.append(proc)
    run_hook("claude-monitor-fmt.py",
             P.post_monitor(s, description="silent monitor", command=cmd,
                            task_id="mon-" + uuid.uuid4().hex[:8]))
    wait_until(lambda: mon_proc_latched(test_env, s.sid),
               desc="tailer latches the monitor process")
    proc.terminate()
    proc.wait()          # reap — a zombie still reads as alive to pid_alive
    wait_until(lambda: any("monitor-exited-silent" in (r or "")
                           for r in end_reasons(test_env, s.sid)),
               timeout=15, desc="silent monitor closes on process exit")
    assert "monitor ended · no output" in s.ops_text()
    assert not s.live("monitor"), "monitor slot not released"
    oracle.assert_clean(test_env, s.sid)


def test_f7d_monitor_process_never_found(run_hook, test_env, session, task_dir):
    """No output file AND no identifiable monitor process (nothing to key
    liveness on): fall back to the bounded give-up rather than wedge the slot
    (and the blue tab) forever."""
    s = session.make()
    env = dict(test_env)
    env["CLAUDE_STREAM_PROCFIND_S"] = "0.4"
    cmd = "true #no-such-proc-%s" % uuid.uuid4().hex[:8]   # matches no live argv
    run_hook("claude-monitor-fmt.py",
             P.post_monitor(s, description="ghost monitor", command=cmd,
                            task_id="mon-" + uuid.uuid4().hex[:8]), env=env)
    wait_until(lambda: any("monitor process never found" in (r or "")
                           for r in end_reasons(test_env, s.sid)),
               timeout=15, desc="ghost monitor gives up on the bounded wait")
    assert "output not found" in s.ops_text()
    assert not s.live("monitor"), "monitor slot not released"
    oracle.assert_clean(test_env, s.sid)


# --------------------------------------------------------------------- F8

def test_f8_task_rows(run_hook, test_env, session, seed):
    s = session.make()
    # hosted-session precondition (task_fmt never creates the state DB itself)
    seed.py("from core import state as ST; ST.kv_set(%r, 'seeded', 1)" % s.log)
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


def test_f9f_async_launch_ack_does_not_end_streamer(run_hook, test_env, session):
    """An ASYNC (background) agent's Task resolves IMMEDIATELY in the parent
    transcript with a synthetic "Async agent launched successfully" tool_result
    (is_error absent) — meaning launched, NOT finished. f9d's parent-result
    recovery must NOT treat that ack as a resolution (doing so ended the streamer
    ~2s in with 0 lines, so the agent's whole transcript never reached the
    mirror). The streamer keeps tailing and exits only on the real SubagentStop."""
    s = session.make()
    agent = "agent-" + uuid.uuid4().hex[:8]
    tid = "toolu_" + uuid.uuid4().hex[:12]
    s.write_meta(agent, toolUseId=tid)
    s.write_subagent_jsonl(agent, [])
    run_hook("claude-subagent-fmt.py", P.subagent_start(s, agent_id=agent),
             argv=("start",))
    s.write_subagent_jsonl(agent, SUB_EVENTS[:3])
    wait_until(lambda: "scanning the tree now" in s.ops_text(), desc="running")
    # The async launch ack lands in the PARENT transcript right away — is_error
    # absent, text "launched successfully". It must NOT end the streamer.
    s.add_line({"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tid, "content": [
            {"type": "text", "text": "Async agent launched successfully. "
             "agentId: " + agent}]}]}})
    # Give the parent scan (test-shortened CLAUDE_STREAM_PARENT_SCAN_S=0.3s
    # throttle) several chances to (wrongly, if regressed) fire. Deliberate
    # blind sleep: this asserts event ABSENCE, which cannot be polled.
    time.sleep(1.0)
    assert not any("parent-task-resolved" in (r or "")
                   for r in end_reasons(test_env, s.sid)), \
        "launch ack must not end the streamer"
    assert s.live(), "slot still held while the async agent runs"
    # The rest of the transcript streams, then the real SubagentStop finalises.
    s.write_subagent_jsonl(agent, SUB_EVENTS)
    run_hook("claude-subagent-fmt.py", P.subagent_stop(s, agent_id=agent),
             argv=("stop",))
    wait_until(lambda: "stop-sentinel" in end_reasons(test_env, s.sid),
               desc="substream finalises on the real stop sentinel")
    wait_until(lambda: not s.live(), desc="slot released")
    oracle.assert_clean(test_env, s.sid)


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
    # The watcher spawns BEFORE the paint, but no longer exits turn-over on a
    # green/idle/empty row it sees before ANY mid-turn paint landed this run
    # (the premature-turn-over race: a failed/lagging THINKING paint left the
    # previous turn's green in the row and the watcher's first tick killed it,
    # leaving a later cancel with no recovery) — so one dispatch suffices even
    # at test poll speed.
    wait_until(watcher_alive, desc="a live interrupt-watch on the magenta tab")
    # The interrupt must land AFTER the watcher's transcript-size snapshot, and
    # that snapshot immediately follows its A.stream_start registration — so
    # the observable "snapshot taken" fact is an un-ended interrupt-watch
    # stream row (not a blind sleep). An earlier watcher that already exited
    # turn-over leaves an ENDED row, hence the ended_at IS NULL filter.
    wait_until(lambda: any(k == "interrupt-watch" and ended is None
                           for k, _, ended, _ in oracle.streams(test_env, s.sid)),
               desc="interrupt-watch registered its stream (snapshot taken)")
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
    assert os.path.exists(s.parked_db)
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


# ------------------------------------------------------------------- F10b

def test_f10b_daemon_origin_start_skips_pane(run_hook, test_env, session,
                                             fake_kitten):
    """A SessionStart with NO KITTY_WINDOW_ID and no claude_session-tagged pane
    (the agents view spawns `claude daemon run`, whose hooks carry a scrubbed
    env) must not touch the terminal: the old focused-tab fallback closed the
    focused session's mirror as "stale" and split an empty one in its place."""
    victim = session.make()                  # an innocent session, focused tab
    run_hook("claude-split.py", P.session_start(victim), argv=("open",))
    assert any(w["user_vars"].get("claude_mirror") == victim.sid
               for w in fake_kitten.windows())
    fake_kitten.clear()

    daemon = session.make()
    env = dict(test_env)
    env.pop("KITTY_WINDOW_ID", None)         # daemon-origin: no kitty env
    run_hook("claude-split.py", P.session_start(daemon), argv=("open",), env=env)

    assert fake_kitten.calls("launch") == [], "daemon session opened a pane"
    assert fake_kitten.calls("close-window") == [], "daemon session swept a pane"
    assert any(w["user_vars"].get("claude_mirror") == victim.sid
               for w in fake_kitten.windows()), "victim's mirror was hijacked"
    assert not os.path.exists(daemon.state_db), "daemon session grew a state DB"
    rows = oracle.q(test_env, "SELECT action, detail FROM pane_events "
                              "WHERE session_id=?", (daemon.sid,))
    assert rows == [("open", "skipped: no host pane (daemon/headless session)")]
    run_hook("claude-split.py", P.session_end(victim), argv=("close",))
    wait_until(lambda: streams_all_ended(test_env, victim.sid), desc="all ended")
    oracle.assert_clean(test_env, victim.sid)
    oracle.assert_clean(test_env, daemon.sid)


# ------------------------------------------------------------------- F10c

def test_f10c_daemon_origin_resume_reopens_anchored(run_hook, test_env, session,
                                                    fake_kitten):
    """Re-entering a chat from the agents view fires a source=resume
    SessionStart from the daemon (scrubbed env). The session's pane is still
    tagged claude_session=<sid>, so the mirror reopens anchored to IT
    (--next-to id:<win>), not to whichever window happens to be focused."""
    s = session.make()
    run_hook("claude-split.py", P.session_start(s), argv=("open",))
    wins = [w for w in fake_kitten.windows()   # simulate the mirror being gone
            if not ({"claude_mirror", "claude_scorebar"} & set(w["user_vars"]))]
    with open(os.path.join(fake_kitten.root, "kitten-windows.json"), "w") as f:
        json.dump(wins, f)
    fake_kitten.clear()

    env = dict(test_env)
    env.pop("KITTY_WINDOW_ID", None)         # daemon-origin resume: no kitty env
    run_hook("claude-split.py", P.session_start(s, source="resume"),
             argv=("open",), env=env)

    assert any(w["user_vars"].get("claude_mirror") == s.sid
               for w in fake_kitten.windows()), "mirror not reopened on resume"
    mirror_launch = next(c for c in fake_kitten.calls("launch")
                         if f"claude_mirror={s.sid}" in c)
    i = mirror_launch.index("--next-to")
    assert mirror_launch[i + 1] == "id:" + fake_kitten.window_id, \
        "mirror not anchored to the session's own pane"
    run_hook("claude-split.py", P.session_end(s), argv=("close",))
    wait_until(lambda: streams_all_ended(test_env, s.sid), desc="all ended")
    oracle.assert_clean(test_env, s.sid)


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
    assert os.path.exists(s.parked_db)
    wait_until(lambda: streams_all_ended(test_env, s.sid),
               desc="substream exits once the DB is parked")
    assert any("parked" in (r or "") for r in end_reasons(test_env, s.sid))
    # The exiting substream's cleanup is parked-gated: once the session is over
    # it makes no state-DB writes and spawns no bg-recheck (see f11b) — so no
    # recheck can race the SessionEnd tab clear below, and no recheck
    # transition row exists at all (the stream row above closes AFTER on_exit,
    # so by now cleanup has already run — this is not a timing window).
    assert not [r for r in oracle.transitions(test_env, s.sid)
                if r[0] == "bg-recheck"], \
        "post-park substream cleanup fired a bg-recheck"
    run_hook(TAB, P.session_end(s), argv=("clear",))
    assert oracle.tab_state(test_env, fake_kitten.window_id) is None
    oracle.assert_clean(test_env, s.sid,
                        allow=("SubagentStart without SubagentStop",
                               "slot claims without a matching release"))


def test_f11b_substream_parked_exit_makes_no_state_writes(run_hook, test_env,
                                                          session):
    """The parked exit's "no writes past this point" covers cleanup() too: its
    release_id / agent_set / pid_del all connect to the state DB, so an
    UNGATED on_exit either recreated a fresh empty DB at the live path (no
    cached connection — the session-alive-signal hazard) or, through this
    streamer's cached connection, deleted the slot rows out of the parked
    snapshot. Gate check: after the parked exit the live path stays absent and
    the parked snapshot still holds the agent's sub.id + sub.pid live rows
    exactly as parked."""
    s = session.make()
    agent = "agent-" + uuid.uuid4().hex[:8]
    s.write_subagent_jsonl(agent, [])
    run_hook("claude-subagent-fmt.py", P.subagent_start(s, agent_id=agent),
             argv=("start",))
    s.write_subagent_jsonl(agent, SUB_EVENTS[:3])
    wait_until(lambda: "scanning the tree now" in s.ops_text(), desc="live")

    run_hook("claude-split.py", P.session_end(s), argv=("close",))  # parks it
    assert os.path.exists(s.parked_db) and not os.path.exists(s.state_db)
    wait_until(lambda: streams_all_ended(test_env, s.sid),
               desc="substream exits once the DB is parked")
    assert any("state-db-parked" in (r or "")
               for r in end_reasons(test_env, s.sid))

    # No recreation at the live path (the no-cached-conn failure class) ...
    assert not os.path.exists(s.state_db), \
        "post-park cleanup recreated the live state DB"
    # ... and no pollution of the parked snapshot (the cached-conn class): the
    # slot rows the SubagentStart hook + streamer claimed are still there,
    # untouched by release_id/pid_del.
    conn = sqlite3.connect("file:%s?mode=ro" % s.parked_db, uri=True, timeout=5)
    try:
        live = conn.execute("SELECT kind, key FROM live").fetchall()
    finally:
        conn.close()
    assert ("sub.id", agent) in live, \
        "post-park release_id deleted the slot row from the parked snapshot"
    assert ("sub.pid", agent) in live, \
        "post-park pid_del deleted the pid row from the parked snapshot"
    # And no bg-recheck was spawned for a session that no longer exists.
    assert not [r for r in oracle.transitions(test_env, s.sid)
                if r[0] == "bg-recheck"]
    oracle.assert_clean(test_env, s.sid,
                        allow=("SubagentStart without SubagentStop",
                               "slot claims without a matching release"))


# -------------------------------------------------------------------- F12

def test_f12_fg_tailer_crash_still_tears_down(test_env, session, monkeypatch):
    """A crash in the fg tailer's main() (renderer exception, signal) must not
    leak its teardown: cleanup() is stream_lifecycle's on_exit now, not
    main()'s last statement — before that, a crash after open_tailer left the
    tee .out until the 7-day sweep, the fg-live record until the next Bash
    PreToolUse noticed the dead pid, and never ran the stale-red recheck.
    Runs the streamer IN-PROCESS (the only way to raise from a chosen phase)
    with the run identity seeded via the product APIs the launch hooks use."""
    import sys as _sys

    from core import slots as claude_slots
    from core import state as S
    from plugins.claude_code import stream as ST

    # The in-process product code reads the hermetic env at call time.
    for k in list(os.environ):
        if k.startswith(("KITTY_", "CLAUDE_")) and k not in test_env:
            monkeypatch.delenv(k)
    for k, v in test_env.items():
        monkeypatch.setenv(k, v)

    s = session.make()
    src = os.path.join(s.cwd, "tee.out")            # our own tee target (OWN=1)
    with open(src, "w") as f:
        f.write("some output\n")
    done = s.log + ".t1.done"                       # session-keyed sentinel token

    # Seed exactly what claude-cmd-pre.py seeds before spawning the tailer:
    # the fg-live hand-off (pid = the tailer's — here: this process) + the slot.
    S.hand_put(s.log, "fg-live",
               {"tid": "tu_crash", "src": src, "done": done, "pid": os.getpid()})
    slot, marker = claude_slots.claim("fg", s.log)
    claude_slots.set_owner(marker, os.getpid())
    monkeypatch.setenv("CLAUDE_STREAM_SRC", src)
    monkeypatch.setenv("CLAUDE_STREAM_OWN", "1")
    monkeypatch.setenv("CLAUDE_STREAM_DONE", done)
    monkeypatch.setattr(_sys, "argv",
                        ["claude-stream.py", "fg", "tu_crash", s.log, str(slot)])

    # entry()/_init mutate module globals — register restores so this in-process
    # run can't leak identity into other tests on the same worker.
    for name in ("KIND", "TASKID", "LOG", "SIG", "OUTER", "SLOT", "_MARKER",
                 "SLOT_RGB", "OUTER_RGB", "SRC", "OWN", "DONE", "SKIP_EXISTING",
                 "POS0", "GROUP", "CMD", "RKIND", "RVALUE", "RENDER_KIND",
                 "MD", "SNIFF"):
        monkeypatch.setattr(ST, name, getattr(ST, name))
    monkeypatch.setattr(ST, "_CLEANED", {"done": False, "path": None})
    monkeypatch.setattr(ST, "_LSOF_STATE", dict(ST._LSOF_STATE))

    def boom(run, tail):                            # crash right after open_tailer
        raise RuntimeError("boom mid-stream")
    monkeypatch.setattr(ST, "make_pump", boom)

    ST.entry()                                      # must swallow the crash

    assert not os.path.exists(src), "crash leaked the tee .out file"
    assert fg_live_record(s) is None, "crash leaked the fg-live record"
    assert s.live("fg") == [], "crash leaked the fg slot row"
    releases = [r for r in oracle.slots(test_env, s.sid)
                if r[0] == "fg" and r[3] == "release"]
    assert len(releases) == 1, "slot must be released exactly once: %r" % releases
    assert any(r[2] == "main" for r in oracle.errors(test_env, s.sid)), \
        "the crash itself must be audited"
    assert [r[1] for r in oracle.streams(test_env, s.sid)] == ["crash"]
