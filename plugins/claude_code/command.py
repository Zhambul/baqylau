"""Run Claude Code with launch-time session registration.

The wrapper is the ONE registrar for Claude Code sessions: it chooses the
session identity up front (``--session-id``), registers the session before the
harness can fire a single hook, and records the process-exit observation that
survives even a killed CLI. Hooks only append evidence.
"""

from __future__ import annotations

import glob
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from typing import Literal

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from contracts.harness import RawEvent, Session
from domain.ids import ActorId, RawEventId, SessionId
from plugins.claude_code import account

TRANSCRIPT_POLL_SECONDS = 0.05
RESUME_FLAGS = {"--resume", "-r", "--continue", "-c"}


def _transcript_roots() -> tuple[str, ...]:
    roots = []
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    if configured:
        roots.append(configured)
    roots.append(os.path.expanduser("~/.claude"))
    for record in account.registry():
        directory = account.config_dir_for(record["slug"])
        if directory:
            roots.append(directory)
    seen = []
    for root in roots:
        real = os.path.realpath(root)
        if real not in seen:
            seen.append(real)
    return tuple(seen)


def _find_transcript(session_id: str | None, started_at: float) -> str | None:
    """The transcript for a forced session id, or the newest one born after launch."""
    candidates = []
    for root in _transcript_roots():
        if session_id is not None:
            pattern = os.path.join(root, "projects", "*", f"{session_id}.jsonl")
        else:
            pattern = os.path.join(root, "projects", "*", "*.jsonl")
        for path in glob.glob(pattern):
            try:
                modified_at = os.path.getmtime(path)
            except FileNotFoundError:
                continue
            if session_id is not None or modified_at >= started_at:
                candidates.append((modified_at, os.path.realpath(path)))
    if not candidates:
        return None
    return max(candidates)[1]


def _session_for(path: str, native_process_id: int) -> Session:
    native_session_id = os.path.basename(path)[: -len(".jsonl")]
    session_id = SessionId(native_session_id)
    return Session(
        session_id=session_id,
        lead_actor_id=ActorId(f"{session_id}:lead"),
        native_session_id=native_session_id,
        source_reference=path,
        working_directory=os.getcwd(),
        native_process_id=native_process_id,
    )


def process_raw_event(session: Session, state: Literal["started", "finished"]) -> RawEvent:
    process_id = session.native_process_id
    document = {"process_id": process_id, "state": state}
    return RawEvent(
        raw_event_id=RawEventId(
            f"claude_code:process:{session.session_id}:{process_id}:{state}"
        ),
        harness="claude_code",
        source_type="process",
        source_name=f"process:{process_id}",
        source_position=state,
        session_id=session.session_id,
        actor_id=session.lead_actor_id,
        parent_actor_id=None,
        observed_at=time.time(),
        encoding="json",
        payload=json.dumps(document, separators=(",", ":"), sort_keys=True).encode("utf-8"),
        source_identity=f"claude_code:process:{session.session_id}:{process_id}",
    )


def run(arguments: list[str]) -> int:
    from app.data import data_directory
    from app.host import ApplicationHost
    from runtime.recorder import RawEventRecorder
    from runtime.sessions import SessionRegistry

    command = account.DEFAULT_COMMAND
    if arguments and not arguments[0].startswith("-"):
        command, arguments = arguments[0], arguments[1:]
    forced_session_id: str | None = None
    if not RESUME_FLAGS.intersection(arguments) and "--session-id" not in arguments:
        forced_session_id = str(uuid.uuid4())
        arguments = [*arguments, "--session-id", forced_session_id]

    database_path = os.path.join(data_directory(), "events.db")
    sessions = SessionRegistry(database_path)
    recorder = RawEventRecorder(database_path)
    started_at = time.time()
    process = subprocess.Popen(account.launch_argv(arguments, command))
    previous_interrupt_handler = signal.signal(signal.SIGINT, signal.SIG_IGN)
    session = None
    try:
        while process.poll() is None and session is None:
            path = _find_transcript(forced_session_id, started_at)
            if path is None:
                time.sleep(TRANSCRIPT_POLL_SECONDS)
                continue
            session = _session_for(path, process.pid)
            if sessions.find(session.session_id) is None:
                sessions.register("claude_code", session)
            ApplicationHost().ensure_running()
        return_code = process.wait()
    finally:
        signal.signal(signal.SIGINT, previous_interrupt_handler)
        if session is not None:
            recorder.record((process_raw_event(session, "finished"),))
    return return_code


def main() -> None:
    raise SystemExit(run(sys.argv[1:]))


if __name__ == "__main__":
    main()
