"""Run Codex with exact process-to-rollout lifecycle observation."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from typing import Literal

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dataclasses import replace

from contracts.harness import Session
from contracts.terminal import SessionPaneRequest
from app import pane_preferences, pending_session
from plugins.codex.canonical import process_event, rollout_session

ROLLOUT_POLL_SECONDS = 0.05


def process_rollout(process_id: int) -> Session | None:
    """Return the one root rollout held open by the exact Codex process."""
    completed = subprocess.run(
        ["lsof", "-a", "-p", str(process_id), "-Fn"],
        capture_output=True,
        text=True,
        timeout=2,
        check=True,
    )
    sessions = []
    for line in completed.stdout.splitlines():
        if not line.startswith("n") or not line.endswith(".jsonl"):
            continue
        session = rollout_session(line[1:])
        if session is not None:
            sessions.append(session)
    unique_sessions = {session.session_id: session for session in sessions}
    if len(unique_sessions) > 1:
        raise RuntimeError(f"Codex process {process_id} owns multiple root rollouts")
    return next(iter(unique_sessions.values()), None)


def native_codex_process(launcher_process_id: int) -> int | None:
    children = subprocess.run(
        ["pgrep", "-P", str(launcher_process_id)],
        capture_output=True,
        text=True,
        timeout=2,
    ).stdout.split()
    process_ids = [launcher_process_id, *(int(value) for value in children)]
    completed = subprocess.run(
        ["ps", "-o", "pid=,comm=", "-p", ",".join(str(value) for value in process_ids)],
        capture_output=True,
        text=True,
        timeout=2,
        check=True,
    )
    matches = []
    for line in completed.stdout.splitlines():
        fields = line.strip().split(None, 1)
        if len(fields) == 2 and os.path.basename(fields[1]) == "codex":
            matches.append(int(fields[0]))
    if len(matches) > 1:
        raise RuntimeError(f"Codex launcher {launcher_process_id} has multiple Codex processes")
    return matches[0] if matches else None


def record_process(
    context,
    session: Session,
    state: Literal["started", "finished"],
) -> None:
    if context.sessions.find(session.session_id) is None:
        context.sessions.register("codex", session)
    context.recorder.record((process_event(session, state),))
    context.host.ensure_running()


class LaunchContext:
    """The wrapper's own working set — no application graph is built here."""

    def __init__(self) -> None:
        import os as operating_system

        from app.data import data_directory
        from app.host import ApplicationHost
        from app.session_terminal import ApplicationTerminal
        from runtime.recorder import RawEventRecorder
        from runtime.sessions import SessionRegistry

        database_path = operating_system.path.join(data_directory(), "events.db")
        self.sessions = SessionRegistry(database_path)
        self.recorder = RawEventRecorder(database_path)
        self.terminal = ApplicationTerminal()
        self.host = ApplicationHost()


def run(arguments: list[str]) -> int:
    executable = shutil.which("codex")
    if executable is None:
        raise RuntimeError("codex executable is not on PATH")
    context = LaunchContext()
    process = subprocess.Popen([executable, *arguments])
    previous_interrupt_handler = signal.signal(signal.SIGINT, signal.SIG_IGN)
    session = None
    pending_session_id = None
    try:
        native_process_id = None
        while process.poll() is None and native_process_id is None:
            native_process_id = native_codex_process(process.pid)
            if native_process_id is None:
                time.sleep(ROLLOUT_POLL_SECONDS)
        anchor_window_id = context.terminal.current_window()
        if native_process_id is not None and anchor_window_id is not None:
            pending_session_id = pending_session.identity(native_process_id)
            opened = context.terminal.open_pending_session_panes(
                SessionPaneRequest(
                    pending_session_id,
                    anchor_window_id,
                    pane_preferences.width_percent(os.getcwd()),
                )
            )
            if not opened.succeeded:
                raise RuntimeError(opened.reason or "pending Codex panes failed to open")
        while process.poll() is None and session is None:
            if native_process_id is None:
                break
            session = process_rollout(native_process_id)
            if session is None:
                time.sleep(ROLLOUT_POLL_SECONDS)
        if session is not None:
            session = replace(session, native_process_id=native_process_id)
            record_process(context, session, "started")
            if pending_session_id is not None:
                adopted = context.terminal.adopt_pending_session_panes(
                    pending_session_id,
                    session.session_id,
                )
                if not adopted.succeeded:
                    raise RuntimeError(adopted.reason or "pending Codex panes failed to adopt")
        return_code = process.wait()
    finally:
        signal.signal(signal.SIGINT, previous_interrupt_handler)
    if session is not None:
        record_process(context, session, "finished")
    elif pending_session_id is not None:
        context.terminal.close_session_panes(pending_session_id)
    if pending_session_id is not None:
        pending_session.clear(pending_session_id)
    return return_code


def main() -> None:
    raise SystemExit(run(sys.argv[1:]))


if __name__ == "__main__":
    main()
