# L6 — codex run discovery + streaming.
#
# Drives the real claude-codex-watch.py against synthetic companion jobs
# (<TMPDIR>/codex-companion/<slug>/jobs) and native rollouts
# (~/.codex/sessions/...), both relocated into the test env via TMPDIR/HOME.
# Pins: the workspace slug format (byte-compatible with codex's state.mjs),
# session-id attribution, the predates-session filter, the codex-tui drop,
# rollout adoption after the companion grace, and the parked-DB exit.
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone

import pytest

import oracle
import payloads as P
from conftest import REPO, wait_until


def slug_for(cwd):
    """The slug claude-codex-watch.py derives (and codex's state.mjs writes) —
    re-stated here deliberately: this format is a cross-tool contract."""
    root = cwd                                   # test cwds are not git repos
    rp = os.path.realpath(root)
    base = os.path.basename(root.rstrip("/")) or "workspace"
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", base).strip("-") or "workspace"
    return "%s-%s" % (slug, hashlib.sha256(rp.encode()).hexdigest()[:16])


@pytest.fixture
def codex(test_env, session, seed, reaper):
    """A session with a live state DB and a running codex watcher, plus
    builders for companion jobs and rollouts inside the test env."""
    class _Codex:
        def __init__(self):
            self.s = session.make()
            # the DB file's existence is the watcher's session-alive signal
            seed.py("from core import state as S; S.connect(%r)" % self.s.log)
            self.jobs = os.path.join(test_env["TMPDIR"], "codex-companion",
                                     slug_for(self.s.cwd), "jobs")
            os.makedirs(self.jobs, exist_ok=True)

        def start_watcher(self, host_pid=None):
            argv = [sys.executable, os.path.join(REPO, "bin", "claude-codex-watch.py"),
                    self.s.log, self.s.cwd, self.s.sid]
            if host_pid is not None:          # 4th arg -> STANDALONE host manager
                argv.append(str(host_pid))
            p = subprocess.Popen(
                argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, env=dict(test_env),
                start_new_session=True)
            reaper.append(p)
            return p

        def add_job(self, title="codex Fix the thing", session_id=None,
                    created=None, status="running"):
            jid = "job-" + uuid.uuid4().hex[:8]
            logfile = os.path.join(self.jobs, jid + ".log")
            open(logfile, "a").close()
            data = {"threadId": str(uuid.uuid4()), "title": title,
                    "status": status, "logFile": logfile,
                    "createdAt": (created or
                                  datetime.now(timezone.utc).isoformat()
                                  .replace("+00:00", "Z"))}
            if session_id is not None:
                data["sessionId"] = session_id
            path = os.path.join(self.jobs, jid + ".json")
            with open(path, "w") as f:
                json.dump(data, f)
            return path, logfile

        def log_event(self, logfile, head, *body):
            with open(logfile, "a") as f:
                f.write("[2026-07-06T10:00:00.000Z] %s\n" % head)
                for b in body:
                    f.write(b + "\n")

        def add_rollout(self, originator="codex_exec", cwd=None, events=(), u=None):
            u = u or str(uuid.uuid4())
            now = datetime.now()
            d = os.path.join(test_env["HOME"], ".codex", "sessions",
                             "%04d" % now.year, "%02d" % now.month,
                             "%02d" % now.day)
            os.makedirs(d, exist_ok=True)
            path = os.path.join(
                d, "rollout-%s-%s.jsonl" % (now.strftime("%Y-%m-%dT%H-%M-%S"), u))
            with open(path, "w") as f:
                f.write(json.dumps({"type": "session_meta", "payload": {
                    "cwd": cwd or self.s.cwd, "originator": originator}}) + "\n")
                for e in events:
                    f.write(json.dumps(e) + "\n")
            return path, u
    return _Codex()


def test_companion_job_discovered_streamed_and_completed(test_env, codex):
    codex.start_watcher()
    jf, logfile = codex.add_job(title="codex Fix the thing",
                                session_id=codex.s.sid)
    codex.log_event(logfile, "Running command: echo codex-hi")
    codex.log_event(logfile, "Turn started")   # a head only renders when the
    #                                            NEXT [ts] line flushes it
    wait_until(lambda: "Fix the thing" in codex.s.ops_text(),
               desc="codex block header with the job label")
    wait_until(lambda: "echo codex-hi" in codex.s.ops_text(),
               desc="companion log command chip")
    codex.log_event(logfile, "Assistant message", "all fixed now")
    codex.log_event(logfile, "Turn completed")     # flushes the message block
    wait_until(lambda: "all fixed now" in codex.s.ops_text(),
               desc="assistant message rendered")
    # a failed command surfaces the shared red exit chip (companion-side path
    # of Renderer._emit_exit_chip; the rollout side is pinned in
    # test_rollout_deepening_files_tokens_search_model)
    codex.log_event(logfile, "Command failed: false (exit 3)")
    codex.log_event(logfile, "Turn started")       # flushes the failed head
    wait_until(lambda: "■ exit 3" in codex.s.ops_text(),
               desc="companion failed-exit chip")

    with open(jf) as f:
        data = json.load(f)
    data["status"] = "completed"
    with open(jf, "w") as f:
        json.dump(data, f)
    wait_until(lambda: any(r[1] and r[0] == "codex"
                           for r in oracle.streams(test_env, codex.s.sid)),
               desc="codex stream ends on sidecar status=completed")
    assert "ended" in codex.s.ops_text()           # the ■ codex ended footer


def test_other_sessions_and_stale_jobs_skipped(test_env, codex):
    codex.start_watcher()
    # a job owned by ANOTHER session, and one predating this session
    codex.add_job(title="codex Not ours", session_id="some-other-session")
    codex.add_job(title="codex Ancient",
                  created="2020-01-01T00:00:00Z", session_id=codex.s.sid)
    _, logfile = codex.add_job(title="codex Ours", session_id=codex.s.sid)
    wait_until(lambda: "Ours" in codex.s.ops_text(), desc="our job streams")
    text = codex.s.ops_text()
    assert "Not ours" not in text, "another session's job leaked into our mirror"
    assert "Ancient" not in text, "a pre-session job was replayed"


def test_rollout_adopted_and_tui_dropped(test_env, codex):
    """A raw `codex exec` rollout in this repo is adopted (after the
    companion grace); a human codex-tui rollout never is."""
    codex.start_watcher()
    codex.add_rollout(originator="codex-tui", events=[
        {"type": "event_msg", "payload": {"type": "user_message",
                                          "message": "tui secret"}}])
    _, u = codex.add_rollout(events=[
        {"type": "event_msg", "payload": {"type": "user_message",
                                          "message": "fix the flaky test"}},
        {"type": "response_item", "payload": {
            "type": "function_call", "name": "exec_command",
            "arguments": json.dumps({"cmd": ["pytest", "-q"]})}},
    ])
    # RO_GRACE (test-shortened via CLAUDE_CODEX_RO_GRACE_S) must elapse before
    # a rollout with no companion is adopted
    wait_until(lambda: "fix the flaky test" in codex.s.ops_text(),
               timeout=20, desc="rollout adopted after the companion grace")
    wait_until(lambda: "pytest -q" in codex.s.ops_text(),
               desc="exec_command rendered as a cmd chip")
    assert "tui secret" not in codex.s.ops_text(), \
        "a human codex-tui run was adopted into the session mirror"


def test_watcher_exits_when_state_db_parked(test_env, codex):
    w = codex.start_watcher()
    wait_until(lambda: any(r[0] == "codex-watcher"
                           for r in oracle.streams(test_env, codex.s.sid)),
               desc="watcher registered its stream")
    os.replace(codex.s.state_db, codex.s.state_db + ".keep")   # SessionEnd park
    wait_until(lambda: w.poll() is not None, desc="watcher process exits")
    rows = [r for r in oracle.streams(test_env, codex.s.sid)
            if r[0] == "codex-watcher"]
    assert rows and rows[0][1] and "parked" in rows[0][1]


def test_watcher_spawned_after_park_never_resurrects_db(test_env, codex):
    """The slow-spawn race (CI's f10b timeout): the session parks BEFORE the
    watcher's first state-DB write. The watcher's lock claim must not recreate
    the DB file — file-existence is the session-alive signal, so a resurrected
    DB makes the loop's parked() probe never fire and the watcher an immortal
    orphan whose stream row never ends. It must instead exit immediately with
    the distinct audited fate."""
    os.replace(codex.s.state_db, codex.s.state_db + ".keep")   # park FIRST
    w = codex.start_watcher()
    wait_until(lambda: w.poll() is not None, desc="watcher exits without a DB")
    assert not os.path.exists(codex.s.state_db), \
        "the watcher resurrected the parked state DB"
    rows = [r for r in oracle.streams(test_env, codex.s.sid)
            if r[0] == "codex-watcher"]
    assert rows and rows[0][2] is not None, rows        # ended, not orphaned
    assert rows[0][1] == "parked-before-start (no state DB)", rows


def test_rollout_deepening_files_tokens_search_model(test_env, codex):
    """The rollout-side deepening: apply_patch file ops render + feed the
    scoreboard, token_count folds into Σ/cost at the footer (bump-agent,
    kind=codex), web searches / the model tag / a failed exit / a compaction
    all render. Event shapes verbatim from real ~/.codex/sessions rollouts."""
    w = codex.start_watcher()
    _, u = codex.add_rollout(events=[
        {"type": "turn_context", "payload": {
            "model": "gpt-5-codex",
            "collaboration_mode": {"settings": {"reasoning_effort": "medium"}}}},
        {"type": "event_msg", "payload": {"type": "task_started"}},
        {"type": "response_item", "payload": {
            "type": "web_search_call",
            "action": {"type": "search", "query": "kitty remote control docs"}}},
        {"type": "event_msg", "payload": {"type": "patch_apply_end",
            "success": True, "changes": {
                "/w/a.py": {"type": "update",
                            "unified_diff": "@@\n-old\n+new\n+more\n"},
                "/w/b.sh": {"type": "add", "content": "#!/bin/sh\necho hi\n"}}}},
        {"type": "response_item", "payload": {
            "type": "function_call_output",
            "output": "Process exited with code 2\nOutput:\nboom"}},
        {"type": "event_msg", "payload": {"type": "context_compacted"}},
        {"type": "event_msg", "payload": {"type": "token_count", "info": {
            "total_token_usage": {"input_tokens": 1000,
                                  "cached_input_tokens": 600,
                                  "output_tokens": 50,
                                  "total_tokens": 1050}}}},
        {"type": "event_msg", "payload": {"type": "task_complete"}},
    ])
    # adoption waits out the 8s companion grace; completion then follows the
    # (test-shortened) CLAUDE_CODEX_GRACE_S
    wait_until(lambda: any(r[1] and r[0] == "codex"
                           for r in oracle.streams(test_env, codex.s.sid)),
               timeout=25, desc="rollout adopted and completed")
    text = codex.s.ops_text()
    assert "⚙ gpt-5-codex · medium" in text          # turn_context model tag
    assert "kitty remote control docs" in text        # web_search_call query
    assert "Update" in text and "a.py" in text        # patch: updated file
    assert "Write" in text and "b.sh" in text         # patch: added file
    assert "■ exit 2" in text                         # failed exec output
    assert "⟳ compacted" in text                      # context_compacted
    # footer rollup: fresh in = 1000-600, out = 50, cache 600/1000
    assert "400 in" in text and "50 out" in text and "cache 60%" in text
    assert "≈ <$0.01" in text                         # gpt-5-codex priced
    # scoreboard: file ops counted like any agent's; tokens folded once
    c = codex.s.counters()
    assert c.get("added") == 4 and c.get("removed") == 1
    assert c.get("tokens") == 450
    assert c.get("tk_in") == 400 and c.get("tk_out") == 50 and c.get("tk_read") == 600
    assert c.get("tool:Edit") == 1 and c.get("tool:Write") == 1
    assert codex.s.query_state("SELECT COUNT(*) FROM files")[0][0] == 2
    # the token fold arrived attributed (bump-agent with kind=codex meta)
    rows = [r for r in oracle.state_files(test_env, codex.s.sid)
            if r[1] == "bump-agent" and '"kind": "codex"' in (r[2] or "")]
    assert rows, "codex token fold must be an attributed bump-agent row"
    # end the session (park the DB) so the watcher exits, then the full oracle
    os.replace(codex.s.state_db, codex.s.state_db + ".keep")
    wait_until(lambda: w.poll() is not None, desc="watcher exits after park")
    oracle.assert_clean(test_env, codex.s.sid)


# ---- standalone codex: its own SessionStart hook hosts the mirror -----------
# (no Claude Code session — plugins/codex/session.py + watch.py standalone mode)

def test_standalone_watcher_streams_own_tui_rollout(test_env, codex, reaper):
    """A STANDALONE watcher (host pid passed) streams THIS codex session's own
    rollout — matched by uuid == session id — and adopts it even though the
    originator is codex-tui (the human-driven TUI IS this session), with none of
    the companion grace the secondary-source path imposes on raw runs."""
    host = subprocess.Popen(["sleep", "60"])
    reaper.append(host)
    codex.start_watcher(host_pid=host.pid)
    codex.add_rollout(originator="codex-tui", u=codex.s.sid, events=[
        {"type": "event_msg", "payload": {"type": "user_message",
                                          "message": "standalone hello"}}])
    wait_until(lambda: "standalone hello" in codex.s.ops_text(),
               desc="standalone codex streams its own codex-tui rollout")
    host.terminate()


def test_standalone_watcher_ignores_foreign_rollouts(test_env, codex, reaper):
    """Standalone pins to its OWN session id: a different codex run in the same
    repo (a stray rollout with another uuid) is NOT adopted — each standalone tab
    shows exactly its own session, no cross-tab bleed."""
    host = subprocess.Popen(["sleep", "60"])
    reaper.append(host)
    codex.start_watcher(host_pid=host.pid)
    codex.add_rollout(originator="codex_exec", events=[   # random uuid != our sid
        {"type": "event_msg", "payload": {"type": "user_message",
                                          "message": "not mine"}}])
    codex.add_rollout(originator="codex-tui", u=codex.s.sid, events=[
        {"type": "event_msg", "payload": {"type": "user_message",
                                          "message": "mine"}}])
    wait_until(lambda: "mine" in codex.s.ops_text(), desc="own rollout streamed")
    assert "not mine" not in codex.s.ops_text(), \
        "a foreign codex run leaked into a standalone session's mirror"
    host.terminate()


def test_standalone_teardown_parks_db_on_host_exit(test_env, codex, reaper):
    """Codex fires no SessionEnd hook, so the standalone watcher owns teardown:
    when the codex host pid dies (here: killed), it parks the state DB (-> durable
    park, so a codex `resume` replays) and exits — the SessionEnd surrogate."""
    host = subprocess.Popen(["sleep", "60"])
    reaper.append(host)
    w = codex.start_watcher(host_pid=host.pid)
    wait_until(lambda: any(r[0] == "codex-watcher"
                           for r in oracle.streams(test_env, codex.s.sid)),
               desc="standalone watcher registered")
    host.terminate()
    host.wait()
    wait_until(lambda: os.path.exists(codex.s.parked_db),
               desc="state DB parked when codex host exits")
    assert not os.path.exists(codex.s.state_db)
    wait_until(lambda: w.poll() is not None, desc="watcher exits after teardown")
    pane = [r for r in oracle.q(
        test_env, "SELECT action, ok, detail FROM pane_events WHERE session_id=?",
        (codex.s.sid,))]
    assert any(a == "close" and "standalone" in (d or "")
               for a, _ok, d in pane), "teardown must audit the pane close"


def test_standalone_session_handler_opens_mirror(run_hook, test_env, session,
                                                 fake_kitten, reaper):
    """claude-codex-session.py — codex's native SessionStart hook — stands up the
    mirror for a standalone codex (no host mirror in the tab) and records
    standalone-open. Its spawned watcher is reaped via the audit spawns table."""
    s = session.make()
    run_hook("claude-codex-session.py", P.session_start(s))
    assert any("claude-mirror.py" in " ".join(map(str, a))
               for a in fake_kitten.calls("launch")), "no mirror pane launched"
    assert os.path.exists(s.state_db), "state DB not created by the codex host"
    assert any("standalone-open" in d
               for d in oracle.decisions(test_env, s.sid, handler="codex-session"))
    os.replace(s.state_db, s.state_db + ".keep")   # signal the spawned watcher to exit


def test_standalone_session_handler_nested_skips(run_hook, test_env, session,
                                                 fake_kitten, reaper):
    """Codex running as a Claude SUBAGENT inherits Claude's pane, so its
    SessionStart hook fires too — but the tab already carries Claude's live
    claude_mirror, whose watcher already streams the run. The handler must detect
    the nested host and open NO second mirror."""
    s = session.make()
    host = session.make()                          # the hosting Claude session
    open(host.state_db, "w").close()               # its mirror DB is live
    fake_kitten.set_ls([{"id": 1, "is_focused": True, "tabs": [
        {"id": 1, "is_focused": True, "windows": [
            {"id": int(fake_kitten.window_id), "is_focused": True,
             "user_vars": {"claude_session": "claude-host"}},
            {"id": 777, "user_vars": {"claude_mirror": host.sid}}]}]}])
    run_hook("claude-codex-session.py", P.session_start(s))
    assert not os.path.exists(s.state_db), \
        "nested codex must not stand up its own state DB"
    assert not any("claude-mirror.py" in " ".join(map(str, a))
                   for a in fake_kitten.calls("launch")), \
        "nested codex must not open a second mirror"
    assert any("nested-skip" in d
               for d in oracle.decisions(test_env, s.sid, handler="codex-session"))


# ---- audit coverage of the codex degrade paths -------------------------------

def test_rollout_malformed_lines_audited_once_with_count(test_env, codex):
    """Complete-but-unparseable rollout lines (FileTailer only surfaces
    newline-terminated lines, so these are never mid-write partials): exactly
    ONE errors row per run — the FIRST bad line, with src/offset/snippet — the
    rest only counted, the total stamped onto the stream_end reason
    (`… · malformed-lines:N`). No audit flood however broken the writer."""
    codex.start_watcher()
    path, u = codex.add_rollout(events=[
        {"type": "event_msg", "payload": {"type": "user_message",
                                          "message": "count my garbage"}}])
    wait_until(lambda: "count my garbage" in codex.s.ops_text(),
               timeout=20, desc="rollout adopted")
    with open(path, "a") as f:
        f.write("{not json\n")
        f.write("also not json\n")
        f.write(json.dumps({"type": "event_msg",
                            "payload": {"type": "task_complete"}}) + "\n")
    wait_until(lambda: any(r[0] == "codex" and r[1]
                           for r in oracle.streams(test_env, codex.s.sid)),
               timeout=25, desc="codex stream ended")
    rows = [r for r in oracle.errors(test_env, codex.s.sid)
            if r[2] == "codex rollout parse"]
    assert len(rows) == 1, "expected exactly one first-line error row: %r" % rows
    ctx = rows[0][3] or ""
    assert "{not json" in ctx and '"offset":' in ctx and path in ctx, ctx
    ends = [r[1] for r in oracle.streams(test_env, codex.s.sid)
            if r[0] == "codex" and r[1]]
    assert any("malformed-lines:2" in e for e in ends), ends


def test_claims_db_makedirs_failure_audited(monkeypatch):
    """A failed claims-dir makedirs degrades (lock_acquire surfaces the unusable
    path) but must leave an errors row naming the path first."""
    from plugins.codex import watch as W
    rec = []

    class RecA:
        def error(self, log, func, context=None):
            rec.append((func, context))

    monkeypatch.setattr(W, "A", RecA())

    def boom(*a, **k):
        raise OSError("disk says no")
    monkeypatch.setattr(W.os, "makedirs", boom)
    db = W.claims_db()
    assert db.endswith("mirror-claims.db")     # still degrades to a usable path
    assert rec and rec[0][0] == "codex claims_db makedirs", rec
    assert "codex-companion" in (rec[0][1] or {}).get("path", ""), rec
