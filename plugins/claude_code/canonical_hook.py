"""Claude Code hook entry: parse stdin, record raw events, print the reply.

A hook is a RECORDER: it writes evidence and its synchronous stdout reply, and
nothing else. It never registers sessions (the launch wrapper does), never
translates, never touches the terminal — the interpreter reacts to what it
recorded on its next tick.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from contracts.harness import (
    RawEvent,
    RawEventSourceContext,
    terminal_window_raw_event,
    watch_finish_raw_event,
    watch_start_raw_event,
)
from domain.ids import ActorId, RawEventId, SessionId
from plugins.claude_code import account
from plugins.claude_code import foreground
from plugins.claude_code import model

HARNESS = "claude_code"


def hook_raw_events(
    payload: bytes,
    terminal_window_id: str | None = None,
) -> tuple[tuple[RawEvent, ...], bytes]:
    """Everything one hook delivery says, as raw events, plus the stdout reply."""
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
            harness=HARNESS,
            source_type=source_type,
            source_name=hook_name,
            source_position=native_event_id,
            session_id=session_id,
            actor_id=actor_id,
            parent_actor_id=lead_actor_id if native_actor_id else None,
            observed_at=observed_at,
            encoding="json",
            payload=payload,
            source_identity=f"claude_code:hook:{session_id}",
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
                harness=HARNESS,
                source_type="account",
                source_name="environment",
                source_position=native_event_id,
                session_id=session_id,
                actor_id=lead_actor_id,
                parent_actor_id=None,
                observed_at=observed_at + 0.000001,
                encoding="json",
                payload=account_payload,
                source_identity=f"claude_code:account:{session_id}",
            )
        )
    output = b""
    context = RawEventSourceContext(
        session_id=session_id,
        lead_actor_id=lead_actor_id,
        actor_id=actor_id,
        parent_actor_id=lead_actor_id if native_actor_id else None,
        source_reference=source_reference,
    )
    if hook_name == "PreToolUse" and document.get("tool_name") == "Bash":
        prepared = foreground.prepare(document)
        if prepared is not None:
            output = prepared.output
            raw_events.append(watch_start_raw_event(context, HARNESS, prepared.watch))
    elif hook_name in {"PostToolUse", "PostToolUseFailure"} \
            and document.get("tool_name") == "Bash":
        background = foreground.background_watch(document)
        if background is not None:
            # A background command STARTS its watch here — its native output file
            # only becomes known (and nameable) once the task id exists. It shares
            # the operation id, so no finish directive may accompany it.
            raw_events.append(watch_start_raw_event(context, HARNESS, background))
        else:
            operation_id = str(document.get("tool_use_id") or "")
            if operation_id:
                raw_events.append(watch_finish_raw_event(context, HARNESS, operation_id))
    if terminal_window_id:
        # The hook runs INSIDE the session's terminal window — the one process
        # that can name the pane anchor exactly. One row per (session, window).
        raw_events.append(terminal_window_raw_event(context, HARNESS, terminal_window_id))
    return tuple(raw_events), output


def record_hook(payload: bytes) -> bytes:
    raw_events, output = hook_raw_events(payload, os.environ.get("KITTY_WINDOW_ID") or None)

    from app.data import data_directory
    from app.host import ApplicationHost
    from runtime.recorder import RawEventRecorder

    RawEventRecorder(os.path.join(data_directory(), "events.db")).record(raw_events)
    ApplicationHost().ensure_running()
    return output


def main() -> None:
    payload = sys.stdin.buffer.read()
    try:
        output = record_hook(payload)
    except Exception:
        # A hook must never block or fail its harness; the audit row is the trace.
        try:
            from core import audit

            audit.error("", "claude hook (record)", {"payload_bytes": len(payload)})
        except Exception:
            pass
        output = b""
    if output:
        sys.stdout.buffer.write(output)


if __name__ == "__main__":
    main()
