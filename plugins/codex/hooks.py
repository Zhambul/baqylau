"""Codex's hook gateway: one pushed delivery → raw events (no reply channel).

Runs INSIDE the daemon (`HarnessHookGateway`). Registration stays the launch
wrapper's act (`plugins/codex/command.py`): evidence arriving before the
wrapper has registered simply waits in the interpreter's backlog.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping

from contracts.harness import (
    HarnessHookGateway,
    RawEvent,
    RawEventSourceContext,
    terminal_window_raw_event,
)
from domain.ids import ActorId, RawEventId, SessionId

HARNESS = "codex"

# The env subset only the hook process can see. One owner for this fact — the
# thin client ships exactly these keys and the gateway reads exactly these keys.
ENVIRONMENT_KEYS = ("KITTY_WINDOW_ID",)


class CodexHookGateway(HarnessHookGateway):
    def raw_events(
        self, payload: bytes, environment: Mapping[str, str]
    ) -> tuple[tuple[RawEvent, ...], bytes]:
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
        raw_events = [
            RawEvent(
                raw_event_id=RawEventId(
                    f"codex:hook:{session_id}:{hook_name}:{native_event_id}"
                ),
                harness=HARNESS,
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
        ]
        terminal_window_id = environment.get("KITTY_WINDOW_ID")
        if terminal_window_id:
            raw_events.append(terminal_window_raw_event(
                RawEventSourceContext(
                    session_id=session_id,
                    lead_actor_id=lead_actor_id,
                    actor_id=lead_actor_id,
                    parent_actor_id=None,
                    source_reference="",
                ),
                HARNESS,
                terminal_window_id,
            ))
        return tuple(raw_events), b""
