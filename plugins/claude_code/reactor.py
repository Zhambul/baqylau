"""Claude Code's reactions to its own committed evidence, run by the interpreter."""

from __future__ import annotations

import json

from contracts.harness import (
    HarnessReactor,
    HarnessReactorContext,
    MigrateAccount,
    RawEvent,
)
from plugins.claude_code import memory_state
from plugins.claude_code.otel import launch as otel


class ClaudeReactor(HarnessReactor):
    def react(self, raw_event: RawEvent, context: HarnessReactorContext) -> None:
        if raw_event.source_type not in ("hook", "teammate_hook"):
            return
        document = json.loads(raw_event.payload)
        if not isinstance(document, dict):
            return
        hook_name = str(document.get("hook_event_name") or "")
        if hook_name == "SessionStart":
            otel.start()
        elif hook_name == "PostToolUse":
            memory_state.capture(document)
        elif (
            hook_name == "StopFailure"
            and not document.get("agent_id")
            and document.get("error") == "rate_limit"
        ):
            context.execute(
                MigrateAccount(
                    raw_event.session_id,
                    f"claude_code:rate_limit:{raw_event.session_id}:{raw_event.source_position}",
                )
            )


reactor = ClaudeReactor()
