"""Claude Code's hook gateway: one pushed delivery → raw events + the reply.

Runs INSIDE the daemon (`HarnessHookGateway`), invoked by the hook-delivery
endpoint. The hook process itself is a thin client (`client/claude_hook.py`) that
ships its exact stdin plus a few flat header values — everything below is a pure
function of that delivery, plus reads of the harness's own transcript files.
"""

from __future__ import annotations

import hashlib
import time

from harness.contract import HarnessHookGateway
from harness.models import (
    HarnessHookRequest,
    HarnessHookResponse,
    RawEvent,
    RawEventSourceContext,
    output_location_raw_event,
)
from domain.ids import HarnessName, RawEventId
from harness.impl.claude_code.canonical.records import HookPayload, LaunchSelectionDocument
from harness.impl.claude_code.ids import (
    ClaudeCodeActorId,
    ClaudeCodeSessionId,
    actor_id_from_claude_code,
    lead_actor_id_from_claude_code,
    session_id_from_claude_code,
)
from harness.impl.claude_code.hooks import foreground
from harness.impl.claude_code import account, model
from repository.mapper.documents import encode_document

HARNESS = HarnessName.CLAUDE_CODE
CLI_PROCESS_NAME = "claude"


class ClaudeHookGateway(HarnessHookGateway):
    def handle(self, harness_hook_request: HarnessHookRequest) -> HarnessHookResponse:
        """Everything one hook delivery says, as raw events, plus the stdout reply."""
        payload = harness_hook_request.payload
        document = HookPayload.model_validate_json(payload)
        if document.session_id is None:
            raise ValueError("Claude Code hook payload has no session id")
        claude_code_session_id = ClaudeCodeSessionId(document.session_id)
        session_id = session_id_from_claude_code(claude_code_session_id)
        lead_actor_id = lead_actor_id_from_claude_code(claude_code_session_id)
        hook_name = document.hook_event_name or "hook"
        native_actor_id = document.agent_id
        if hook_name in {"SubagentStart", "SubagentStop"} and not native_actor_id:
            raise ValueError(f"Claude Code {hook_name} payload has no agent id")
        actor_id = (
            actor_id_from_claude_code(ClaudeCodeActorId(native_actor_id))
            if native_actor_id else lead_actor_id
        )
        source_reference = document.transcript_path or ""
        if not source_reference:
            raise ValueError("Claude Code hook payload has no transcript path")
        native_event_id_value = document.hook_event_id or document.uuid
        native_event_id = str(native_event_id_value or hashlib.sha256(payload).hexdigest())
        # The client forwarded its environment's two account values raw; what a
        # valid account id looks like is decided here.
        account_id, account_display_name = account.normalize(
            harness_hook_request.account_id, harness_hook_request.account_display_name
        )
        source_type = "hook"
        if (
            hook_name == "SubagentStart"
            and native_actor_id
            and model.agent_meta(source_reference, actor_id).taskKind
            == "in_process_teammate"
        ):
            source_type = "teammate_hook"
        raw_events = [
            RawEvent(
                raw_event_id=RawEventId(
                    f"claude_code:hook:{session_id}:{hook_name}:{native_event_id}"
                ),
                harness=HARNESS,
                source_type=source_type,
                source_name=hook_name,
                source_position=native_event_id,
                session_id=session_id,
                actor_id=actor_id,
                parent_actor_id=lead_actor_id if native_actor_id else None,
                observed_at=time.time(),
                encoding="json",
                payload=payload,
                source_identity=f"claude_code:hook:{session_id}",
                terminal_window_id=harness_hook_request.terminal_window_id,
                harness_process_id=harness_hook_request.harness_process_id,
                account_id=account_id,
                account_display_name=account_display_name,
            )
        ]
        if hook_name == "SessionStart" and (harness_hook_request.launch_model or harness_hook_request.launch_effort):
            # The launch-time selections, observed from the CLI's environment.
            # SessionStart is the one delivery that marks a launch; the native
            # event id keys the observation, so a resume that re-asserts the
            # same environment converges on the same raw event.
            selections = LaunchSelectionDocument(
                model=harness_hook_request.launch_model or None,
                effort=harness_hook_request.launch_effort or None,
            )
            raw_events.append(
                RawEvent(
                    raw_event_id=RawEventId(
                        f"claude_code:launch:{session_id}:{native_event_id}"
                    ),
                    harness=HARNESS,
                    source_type="launch",
                    source_name=hook_name,
                    source_position=native_event_id,
                    session_id=session_id,
                    actor_id=lead_actor_id,
                    parent_actor_id=None,
                    observed_at=time.time(),
                    encoding="json",
                    payload=selections.model_dump_json().encode("utf-8"),
                    source_identity=f"claude_code:launch:{session_id}",
                )
            )
        reply = b""
        context = RawEventSourceContext(
            session_id=session_id,
            lead_actor_id=lead_actor_id,
            actor_id=actor_id,
            parent_actor_id=lead_actor_id if native_actor_id else None,
            source_reference=source_reference,
        )
        if hook_name == "PreToolUse" and document.tool_name == "Bash":
            prepared = foreground.prepare(document)
            if prepared is not None:
                reply = prepared.reply
                raw_events.append(
                    output_location_raw_event(
                        context, HARNESS, prepared.located, payload=encode_document(prepared.located)
                    )
                )
        elif hook_name in {"PostToolUse", "PostToolUseFailure"} \
                and document.tool_name == "Bash":
            background = foreground.background_output(document)
            if background is not None:
                # A background command's output file only becomes known (and
                # nameable) once the task id exists, at PostToolUse. Its launch
                # reports "finished" while output keeps flowing, so the
                # directive says until="session_finished".
                raw_events.append(
                    output_location_raw_event(
                        context, HARNESS, background, payload=encode_document(background)
                    )
                )
        return HarnessHookResponse(tuple(raw_events), reply)
