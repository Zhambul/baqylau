# Copyright (c) 2026 Zhambyl Yermagambet
"""Codex's hook gateway: one pushed delivery → raw events (no reply channel).

Runs INSIDE the daemon (`HarnessHookGateway`). One raw event per delivery, the
request's flat fields stamped on the row; interpretation stays with the
interpreter's next tick.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import override

from domain.ids import ActorId, HarnessName, RawEventId, SessionId
from harness.contract import HarnessHookGateway
from harness.impl.codex.canonical.records import CodexHookPayload
from harness.impl.codex.ids_session import actor_id_from_codex, lead_actor_id_from_codex, session_id_from_codex
from harness.impl.codex.ids_session_types import CodexActorId, CodexSessionId
from harness.models.hooks import (
    HarnessHookRequest,
    HarnessHookResponse,
)
from harness.models.raw_events import (
    RawEvent,
)

HARNESS = HarnessName("codex")
CLI_PROCESS_NAME = "codex"


@dataclass(frozen=True)
class _HookContext:
    session_id: SessionId
    actor_id: ActorId
    parent_actor_id: ActorId | None
    hook_name: str
    event_id: str


class CodexHookGateway(HarnessHookGateway):
    """Represent codex hook gateway."""

    @override
    def receive_hook(self, harness_hook_request: HarnessHookRequest) -> HarnessHookResponse:
        """Receive one hook delivery.

        Payload validation raises ValueError if required session fields are absent.

        Returns:
            The harness hook response.

        """
        payload = harness_hook_request.payload
        document = _hook_document(payload)
        context = _hook_context(document, payload)
        raw_event = _raw_event(harness_hook_request, payload, context)
        return HarnessHookResponse((raw_event,), b"")


def _hook_document(payload: bytes) -> CodexHookPayload:
    document = CodexHookPayload.model_validate_json(payload)
    if document.session_id is None:
        message = "Codex hook payload has no session id"
        raise ValueError(message)
    if not document.transcript_path:
        message = "Codex hook payload has no rollout path"
        raise ValueError(message)
    return document


def _hook_context(codex_hook_payload: CodexHookPayload, payload: bytes) -> _HookContext:
    return _HookContext(
        *_hook_actor_context(codex_hook_payload),
        *_hook_identity(codex_hook_payload, payload),
    )


def _hook_actor_context(
    codex_hook_payload: CodexHookPayload,
) -> tuple[SessionId, ActorId, ActorId | None]:
    codex_session_id = CodexSessionId(codex_hook_payload.session_id or "")
    session_id = session_id_from_codex(codex_session_id)
    lead_actor_id = lead_actor_id_from_codex(codex_session_id)
    actor_id = (
        actor_id_from_codex(CodexActorId(codex_hook_payload.agent_id)) if codex_hook_payload.agent_id else lead_actor_id
    )
    parent_actor_id = lead_actor_id if codex_hook_payload.agent_id else None
    return session_id, actor_id, parent_actor_id


def _hook_identity(codex_hook_payload: CodexHookPayload, payload: bytes) -> tuple[str, str]:
    hook_name = codex_hook_payload.hook_event_name or "hook"
    native_event_id = codex_hook_payload.hook_event_id or codex_hook_payload.uuid
    event_id = str(native_event_id or hashlib.sha256(payload).hexdigest())
    return hook_name, event_id


def _raw_event(
    harness_hook_request: HarnessHookRequest,
    payload: bytes,
    hook_context: _HookContext,
) -> RawEvent:
    return RawEvent(
        raw_event_id=RawEventId(
            f"codex:hook:{hook_context.session_id}:{hook_context.hook_name}:{hook_context.event_id}",
        ),
        harness=HARNESS,
        source_type="hook",
        source_name=hook_context.hook_name,
        source_position=hook_context.event_id,
        session_id=hook_context.session_id,
        actor_id=hook_context.actor_id,
        parent_actor_id=hook_context.parent_actor_id,
        observed_at=time.time(),
        encoding="json",
        payload=payload,
        source_identity=f"codex:hook:{hook_context.session_id}",
        terminal_window_id=harness_hook_request.terminal_window_id,
        harness_process_id=harness_hook_request.harness_process_id,
        account_id=harness_hook_request.account_id,
        account_display_name=harness_hook_request.account_display_name,
    )
