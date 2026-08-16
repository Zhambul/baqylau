"""Codex's hook gateway: one pushed delivery → raw events (no reply channel).

Runs INSIDE the daemon (`HarnessHookGateway`). One raw event per delivery, the
request's flat fields stamped on the row; interpretation stays with the
interpreter's next tick.
"""

from __future__ import annotations

import hashlib
import json
import time

from harness.contract import HarnessHookGateway
from harness.models import HarnessHookRequest, HarnessHookResponse, RawEvent
from domain.ids import ActorId, RawEventId, SessionId

HARNESS = "codex"
CLI_PROCESS_NAME = "codex"


class CodexHookGateway(HarnessHookGateway):
    def handle(self, request: HarnessHookRequest) -> HarnessHookResponse:
        payload = request.payload
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
        raw_events = (
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
                terminal_window_id=request.terminal_window_id,
                harness_process_id=request.harness_process_id,
                account_id=request.account_id,
                account_display_name=request.account_display_name,
            ),
        )
        return HarnessHookResponse(raw_events, b"")
