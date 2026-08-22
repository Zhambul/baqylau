"""Codex's hook gateway: one pushed delivery → raw events (no reply channel).

Runs INSIDE the daemon (`HarnessHookGateway`). One raw event per delivery, the
request's flat fields stamped on the row; interpretation stays with the
interpreter's next tick.
"""

from __future__ import annotations

import hashlib
import time

from harness.contract import HarnessHookGateway
from harness.models import HarnessHookRequest, HarnessHookResponse, RawEvent
from domain.ids import HarnessName, RawEventId
from harness.impl.codex.canonical.records import CodexHookPayload
from harness.impl.codex.ids import (
    CodexActorId,
    CodexSessionId,
    actor_id_from_codex,
    lead_actor_id_from_codex,
    session_id_from_codex,
)

HARNESS = HarnessName.CODEX
CLI_PROCESS_NAME = "codex"


class CodexHookGateway(HarnessHookGateway):
    def handle(self, harness_hook_request: HarnessHookRequest) -> HarnessHookResponse:
        payload = harness_hook_request.payload
        document = CodexHookPayload.model_validate_json(payload)
        if document.session_id is None:
            raise ValueError("Codex hook payload has no session id")
        codex_session_id = CodexSessionId(document.session_id)
        session_id = session_id_from_codex(codex_session_id)
        lead_actor_id = lead_actor_id_from_codex(codex_session_id)
        native_actor_id = document.agent_id
        actor_id = (
            actor_id_from_codex(CodexActorId(native_actor_id))
            if native_actor_id else lead_actor_id
        )
        if not document.transcript_path:
            raise ValueError("Codex hook payload has no rollout path")
        hook_name = document.hook_event_name or "hook"
        native_event_id_value = document.hook_event_id or document.uuid
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
                terminal_window_id=harness_hook_request.terminal_window_id,
                harness_process_id=harness_hook_request.harness_process_id,
                account_id=harness_hook_request.account_id,
                account_display_name=harness_hook_request.account_display_name,
            ),
        )
        return HarnessHookResponse(raw_events, b"")
