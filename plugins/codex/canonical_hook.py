"""Codex hook entry: parse stdin, record the raw event, exit.

A hook is a RECORDER: it appends evidence and nothing else. Registration is the
launch wrapper's act (`plugins/codex/command.py`), so evidence arriving before
the wrapper has registered simply waits in the interpreter's backlog.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from contracts.harness import RawEvent
from domain.ids import ActorId, RawEventId, SessionId


def hook_raw_event(payload: bytes) -> RawEvent:
    document = json.loads(payload)
    if not isinstance(document, dict):
        raise ValueError("Codex hook payload must be an object")
    session_id = SessionId(str(document["session_id"]))
    lead_actor_id = ActorId(f"{session_id}:lead")
    native_actor_id = document.get("agent_id")
    actor_id = ActorId(str(native_actor_id)) if native_actor_id else lead_actor_id
    if not str(document.get("transcript_path") or ""):
        raise ValueError("Codex hook payload has no rollout path")
    hook_name = str(document.get("hook_event_name") or "hook")
    native_event_id_value = document.get("hook_event_id") or document.get("uuid")
    native_event_id = str(native_event_id_value or hashlib.sha256(payload).hexdigest())
    return RawEvent(
        raw_event_id=RawEventId(f"codex:hook:{session_id}:{hook_name}:{native_event_id}"),
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
        source_identity=f"codex:hook:{session_id}",
    )


def record_hook(payload: bytes) -> None:
    raw_event = hook_raw_event(payload)

    from app.data import data_directory
    from app.host import ApplicationHost
    from runtime.recorder import RawEventRecorder

    RawEventRecorder(os.path.join(data_directory(), "events.db")).record((raw_event,))
    ApplicationHost().ensure_running()


def main() -> None:
    payload = sys.stdin.buffer.read()
    try:
        record_hook(payload)
    except Exception:
        # A hook must never block or fail its harness; the audit row is the trace.
        try:
            from core import audit

            audit.error("", "codex hook (record)", {"payload_bytes": len(payload)})
        except Exception:
            pass


if __name__ == "__main__":
    main()
