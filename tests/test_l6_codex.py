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
import time
import uuid
from datetime import datetime, timezone

import pytest

import oracle
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
            seed.py("import claude_state as S; S.connect(%r)" % self.s.log)
            self.jobs = os.path.join(test_env["TMPDIR"], "codex-companion",
                                     slug_for(self.s.cwd), "jobs")
            os.makedirs(self.jobs, exist_ok=True)

        def start_watcher(self):
            p = subprocess.Popen(
                [sys.executable, os.path.join(REPO, "claude-codex-watch.py"),
                 self.s.log, self.s.cwd, self.s.sid],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
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

        def add_rollout(self, originator="codex_exec", cwd=None, events=()):
            u = str(uuid.uuid4())
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
    w = codex.start_watcher()
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
    w = codex.start_watcher()
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
    w = codex.start_watcher()
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
    # RO_GRACE (8s) must elapse before a rollout with no companion is adopted
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
