"""Claude Code hook intake into the canonical event transaction."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from contracts.harness import (
    HookIntake,
    MigrateAccount,
    RawEvent,
    RecognizedSession,
)
from domain.ids import ActorId, RawEventId, SessionId
from plugins.claude_code import account
from plugins.claude_code import cmd_pre
from plugins.claude_code import foreground
from plugins.claude_code import model
from plugins.claude_code.memory_state import CaptureMemory


class ClaudeHook:
    def receive(self, payload: bytes) -> HookIntake:
        document = json.loads(payload)
        if not isinstance(document, dict):
            raise ValueError("Claude Code hook payload must be an object")
        session_id = SessionId(str(document["session_id"]))
        lead_actor_id = ActorId(f"{session_id}:lead")
        hook_name = str(document.get("hook_event_name") or "hook")
        native_actor_id = document.get("agent_id")
        if hook_name in {"SubagentStart", "SubagentStop"} and not native_actor_id:
            raise ValueError(f"Claude Code {hook_name} payload has no agent id")
        actor_id = ActorId(str(native_actor_id)) if native_actor_id else lead_actor_id
        source_reference = str(document.get("transcript_path") or "")
        if not source_reference:
            raise ValueError("Claude Code hook payload has no transcript path")
        native_event_id_value = document.get("hook_event_id") or document.get("uuid")
        native_event_id = str(native_event_id_value or hashlib.sha256(payload).hexdigest())
        observed_at = time.time()
        source_type = "hook"
        if (
            hook_name == "SubagentStart"
            and native_actor_id
            and model.agent_meta(source_reference, str(native_actor_id)).get("taskKind")
            == "in_process_teammate"
        ):
            source_type = "teammate_hook"
        raw_events = [
            RawEvent(
                raw_event_id=RawEventId(
                    f"claude_code:hook:{session_id}:{hook_name}:{native_event_id}"
                ),
                harness="claude_code",
                source_type=source_type,
                source_name=hook_name,
                source_position=native_event_id,
                session_id=session_id,
                actor_id=actor_id,
                parent_actor_id=lead_actor_id if native_actor_id else None,
                observed_at=observed_at,
                encoding="json",
                payload=payload,
            )
        ]
        if hook_name == "SessionStart":
            account_payload = json.dumps(
                account.current(),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            raw_events.append(
                RawEvent(
                    raw_event_id=RawEventId(
                        f"claude_code:account:{session_id}:{native_event_id}"
                    ),
                    harness="claude_code",
                    source_type="account",
                    source_name="environment",
                    source_position=native_event_id,
                    session_id=session_id,
                    actor_id=lead_actor_id,
                    parent_actor_id=None,
                    observed_at=observed_at + 0.000001,
                    encoding="json",
                    payload=account_payload,
                )
            )
        controls = ()
        if (
            hook_name == "StopFailure"
            and not native_actor_id
            and document.get("error") == "rate_limit"
        ):
            controls = (
                MigrateAccount(
                    session_id,
                    f"claude_code:rate_limit:{session_id}:{native_event_id}",
                ),
            )
        output = b""
        actions = ()
        if hook_name == "PreToolUse" and document.get("tool_name") == "Bash":
            prepared = cmd_pre.prepare(document)
            if prepared is not None:
                output = prepared.output
                actions = (prepared.action,)
        elif hook_name in {"PostToolUse", "PostToolUseFailure"} \
                and document.get("tool_name") == "Bash":
            actions = (foreground.finish_action(document),)
        if hook_name == "PostToolUse":
            actions = (*actions, CaptureMemory(document))
        return HookIntake(
            session=RecognizedSession(
                session_id=session_id,
                lead_actor_id=lead_actor_id,
                native_session_id=str(session_id),
                source_reference=source_reference,
                working_directory=document.get("cwd") or None,
            ),
            raw_events=tuple(raw_events),
            output=output,
            controls=controls,
            actions=actions,
        )


hook = ClaudeHook()


def record_hook(payload: bytes) -> dict:
    document = json.loads(payload)
    if not isinstance(document, dict):
        raise ValueError("Claude Code hook payload must be an object")

    from app.bootstrap import build_default_application

    application = build_default_application()
    output = application.hooks.receive("claude_code", payload)
    if output:
        import sys

        sys.stdout.buffer.write(output)
    return document


def main() -> None:
    record_hook(sys.stdin.buffer.read())


if __name__ == "__main__":
    main()
