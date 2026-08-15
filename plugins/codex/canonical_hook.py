"""Codex hook intake into the canonical event transaction."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from contracts.harness import HarnessHook, HookIntake, RawEvent, RecognizedSession
from domain.ids import ActorId, RawEventId, SessionId


def codex_process_id(starting_process_id: int | None = None) -> int:
    """Return the exact Codex ancestor; never substitute a different process."""
    process_id = starting_process_id or os.getppid()
    for _ in range(12):
        if process_id <= 1:
            break
        completed = subprocess.run(
            ["ps", "-o", "ppid=,comm=", "-p", str(process_id)],
            capture_output=True,
            text=True,
            timeout=2,
        )
        fields = completed.stdout.strip().split(None, 1)
        if len(fields) != 2:
            break
        parent_process_id, command = fields
        if os.path.basename(command.strip()) == "codex":
            return process_id
        process_id = int(parent_process_id)
    raise RuntimeError("Codex hook has no Codex process ancestor")


class CodexHook(HarnessHook):
    def receive(self, payload: bytes) -> HookIntake:
        document = json.loads(payload)
        if not isinstance(document, dict):
            raise ValueError("Codex hook payload must be an object")
        session_id = SessionId(str(document["session_id"]))
        lead_actor_id = ActorId(f"{session_id}:lead")
        native_actor_id = document.get("agent_id")
        actor_id = ActorId(str(native_actor_id)) if native_actor_id else lead_actor_id
        source_reference = str(document.get("transcript_path") or "")
        if not source_reference:
            raise ValueError("Codex hook payload has no rollout path")
        hook_name = str(document.get("hook_event_name") or "hook")
        native_process_id = codex_process_id() if hook_name == "SessionStart" else None
        native_event_id_value = document.get("hook_event_id") or document.get("uuid")
        native_event_id = str(native_event_id_value or hashlib.sha256(payload).hexdigest())
        return HookIntake(
            session=RecognizedSession(
                session_id=session_id,
                lead_actor_id=lead_actor_id,
                native_session_id=str(session_id),
                source_reference=source_reference,
                working_directory=document.get("cwd") or None,
                native_process_id=native_process_id,
            ),
            raw_events=(
                RawEvent(
                    raw_event_id=RawEventId(
                        f"codex:hook:{session_id}:{hook_name}:{native_event_id}"
                    ),
                    harness="codex",
                    source_type="hook",
                    source_name=hook_name,
                    source_position=native_event_id,
                    session_id=session_id,
                    actor_id=actor_id,
                    parent_actor_id=lead_actor_id if native_actor_id else None,
                    observed_at=time.time(),
                    encoding="json",
                    payload=payload,
                ),
            ),
        )


hook = CodexHook()


def record_hook(payload: bytes) -> dict:
    document = json.loads(payload)
    if not isinstance(document, dict):
        raise ValueError("Codex hook payload must be an object")

    from app.bootstrap import build_default_application

    application = build_default_application()
    output = application.hooks.receive("codex", payload)
    if output:
        import sys

        sys.stdout.buffer.write(output)
    return document


def main() -> None:
    record_hook(sys.stdin.buffer.read())


if __name__ == "__main__":
    main()
