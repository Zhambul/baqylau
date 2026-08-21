"""Claude Code transcript record translation: one canonical mapping per record kind."""

from __future__ import annotations

import json
import os
from typing import Any

from domain.events import (
    ActorAssignmentFinished,
    ActorNameChanged,
    ActorStarted,
    CanonicalEvent,
    CompactionFinished,
    ContextReported,
    EventPayload,
    GoalChanged,
    MessageCreated,
    ReasoningCreated,
    SessionAccountChanged,
    SessionStarted,
    SessionTitleChanged,
    ShellOutputFinished,
    ShellProgressed,
    TaskChanged,
    TurnStarted,
)
from domain.ids import ActorId, AssignmentId, MessageId, ShellId, TaskId, TurnId
from domain.values import AccountReference, MessagePhase, MessageRole, Outcome, TitleOrigin
from harness.impl.claude_code import model
from harness.impl.claude_code.canonical import transcript
from harness.impl.claude_code.canonical.support import SYNTHETIC_MODEL_ID, content, event, model_reference, timestamp
from harness.impl.claude_code.canonical.toolcalls import BACKGROUND_LAUNCH_STUB, ToolCallSemantics
from harness.impl.claude_code.canonical.turns import TurnSemantics
from harness.models import RawEvent, TranslationError
from harness.models.selections import SelectionSemantics


# How a background command ENDED, from the `<status>` on the completion
# notification Claude Code posts when the job is over. The four values it really
# uses, counted over every retained transcript (2026-08-18): completed 6563,
# failed 375, killed 83, stopped 22 — so "not completed" is a third of a percent
# of jobs and worth telling apart, and neither `killed` nor `stopped` is the
# `cancelled` an earlier reader guessed at. Anything else is unknown rather than
# assumed good: reporting a job as succeeded is the one answer that cannot be
# walked back by looking at it.
BACKGROUND_OUTCOMES: dict[str, Outcome] = {
    "completed": "succeeded",
    "failed": "failed",
    "killed": "cancelled",
    "stopped": "cancelled",
}


def background_outcome(status: object) -> Outcome | None:
    return BACKGROUND_OUTCOMES.get(str(status or "").strip().lower(), "unknown") if status else None


def launch_selections(
    raw_event: RawEvent,
    document: dict[str, Any],
    selection_semantics: SelectionSemantics,
) -> list[CanonicalEvent[EventPayload]]:
    """The launch observation the gateway recorded from the hook's inherited
    environment: the `--model`/`--effort` the launcher started the CLI with.

    A launch selection is the same fact a typed `/model x` records, with the
    same alias caveat: it carries a selection alias ("fable"), and the
    resolved native id arrives only on the first assistant record, as
    `reported_by_harness`. Without this event the selectors sit empty until
    then — and for the effort, forever: Claude Code never echoes it in any
    evidence stream."""
    subject_id = f"launch:{raw_event.source_position}"
    events = []
    model_selection = document.get("model")
    if isinstance(model_selection, str) and model_selection:
        changed = selection_semantics.model(
            raw_event.session_id,
            raw_event.actor_id,
            model_reference(model_selection),
            "selected",
        )
        if changed is not None:
            events.append(event(raw_event, "model", subject_id, "selected", changed))
    effort_selection = document.get("effort")
    if isinstance(effort_selection, str) and effort_selection:
        chosen = selection_semantics.effort(
            raw_event.session_id, raw_event.actor_id, effort_selection, "selected"
        )
        if chosen is not None:
            events.append(event(raw_event, "effort", subject_id, "selected", chosen))
    return events


def prompt_turn(
    raw_event: RawEvent,
    turn_semantics: TurnSemantics,
    native_identity: str,
    occurred_at: float | None,
) -> list[CanonicalEvent[EventPayload]]:
    """The turn this prompt opens, if it opens one.

    The prompt's own identity is the turn's: nothing else in Claude Code's
    evidence names a turn, and the prompt is what the turn answers.
    """
    turn_id = TurnId(native_identity)
    if not turn_semantics.begin(raw_event, turn_id):
        return []
    return [
        event(
            raw_event,
            "turn",
            str(turn_id),
            "started",
            TurnStarted(MessageId(native_identity)),
            turn_id=turn_id,
            occurred_at=occurred_at,
        )
    ]


def slash_command(
    raw_event: RawEvent,
    record: dict[str, Any],
    native_identity: str,
    occurred_at: float | None,
    turn_semantics: TurnSemantics,
    selection_semantics: SelectionSemantics,
) -> list[CanonicalEvent[EventPayload]]:
    """A `/command` turn: the SESSION-STATE event the command asked for,
    where there is one, otherwise a prompt bubble holding what the human
    typed.

    `/model`/`/effort` with a valid selection emit ONLY the state event —
    the model-change entry is what shows the switch, so a second, redundant
    prompt bubble echoing "/model opus" would just duplicate it. Every other
    slash command (and a bare `/model`/`/effort`, or one with more than
    one argument token — not a selection) has no such entry, so it still
    gets the typed-text bubble.

    The state event is emitted from the ARGUMENT, which is a selection ALIAS
    ("opus"), not a native model id — Claude Code's transcript never carries
    the native id here, and the true one arrives a turn later on the next
    assistant record as `reported_by_harness`. Recording the alias is what
    lets the switch be seen AT THE MOMENT it was made; the two events
    describe one switch and the later one is authoritative on the id.

    A bare `/model` (no argument) opens the picker and settles nothing, and a
    multi-token argument is not a selection, so neither emits a state event.
    """
    name = record["name"].lstrip("/").strip().lower()
    selection = record["args"].strip()
    if selection and len(selection.split()) == 1 and name in ("model", "effort"):
        payload: EventPayload | None = (
            selection_semantics.model(
                raw_event.session_id, raw_event.actor_id, model_reference(selection), "selected"
            )
            if name == "model"
            else selection_semantics.effort(
                raw_event.session_id, raw_event.actor_id, selection, "selected"
            )
        )
        # A `/model x` that selects what is already selected settles nothing,
        # and the typed text is not a prompt either — the command was still
        # about the state, not a thing to say.
        if payload is None:
            return []
        return [
            event(raw_event, name, native_identity, "selected", payload, occurred_at=occurred_at)
        ]
    role: MessageRole = "parent" if raw_event.parent_actor_id is not None else "user"
    events = [
        event(
            raw_event,
            "message",
            native_identity,
            "created",
            MessageCreated(MessageId(native_identity), role, content(record["text"]), "prompt", None),
            occurred_at=occurred_at,
        )
    ]
    if role == "user":
        events = prompt_turn(raw_event, turn_semantics, native_identity, occurred_at) + events
    return events


def transcript_metadata(raw_event: RawEvent, document: dict[str, Any]) -> list[CanonicalEvent[EventPayload]]:
    if raw_event.parent_actor_id is not None:
        return []
    record_type = document.get("type")
    if record_type == "agent-name":
        title = str(document.get("agentName") or "").strip()
        origin: TitleOrigin = "custom"
    elif record_type == "ai-title":
        title = str(document.get("aiTitle") or "").strip()
        origin = "automatic"
    elif record_type == "summary":
        title = str(document.get("summary") or "").strip()
        origin = "summary"
    else:
        return []
    if not title:
        return []
    return [
        event(
            raw_event,
            "session",
            str(raw_event.session_id),
            f"title:{origin}:{raw_event.source_position}",
            SessionTitleChanged(title, origin),
        )
    ]


def session_events(raw_event: RawEvent, document: dict[str, Any]) -> list[CanonicalEvent[EventPayload]]:
    lead_actor_id = raw_event.actor_id
    if raw_event.parent_actor_id is not None:
        metadata = {}
        if raw_event.source_type == "child_transcript":
            metadata_path = os.path.splitext(raw_event.source_name)[0] + ".meta.json"
            try:
                with open(metadata_path, encoding="utf-8") as metadata_file:
                    metadata = json.load(metadata_file)
            except (OSError, json.JSONDecodeError):
                metadata = {}
        events = [
            event(
                raw_event,
                "actor",
                str(raw_event.actor_id),
                "started",
                ActorStarted(
                    str(raw_event.actor_id),
                    "teammate" if raw_event.source_type == "teammate_transcript" else "child",
                ),
            )
        ]
        description = str(metadata.get("description") or "").strip()
        if description:
            events.append(event(
                raw_event,
                "actor",
                str(raw_event.actor_id),
                "name:description",
                ActorNameChanged(description),
            ))
        return events
    transcript_path = str(document.get("transcript_path") or "")
    session_started = SessionStarted(
        working_directory=document.get("cwd") or "",
        source_reference=(
            os.path.realpath(transcript_path) if transcript_path else raw_event.source_name
        ),
        resumed_from=None,
        title=None,
        model=None,
        effort=None,
        account=None,
    )
    events = [
        event(
            raw_event,
            "session",
            str(raw_event.session_id),
            "started",
            session_started,
        ),
        event(
            raw_event,
            "actor",
            str(lead_actor_id),
            "started",
            ActorStarted("claude", "lead"),
        ),
    ]
    if raw_event.account_id is not None or raw_event.account_display_name is not None:
        account_id = raw_event.account_id or ""
        display_name = raw_event.account_display_name or account_id or "default"
        events.append(event(
            raw_event,
            "session",
            str(raw_event.session_id),
            f"account:{raw_event.source_position}",
            SessionAccountChanged(AccountReference(account_id, display_name)),
        ))
    return events


def task_event(raw_event: RawEvent, task: dict[str, Any]) -> CanonicalEvent[EventPayload]:
    task_id = TaskId(str(task.get("id") or ""))
    if not task_id:
        raise TranslationError("Claude Code task has no id", context=raw_event.source_position)
    state = task.get("status")
    if state not in ("pending", "in_progress", "completed", "deleted"):
        raise TranslationError(
            f"unknown Claude Code task state: {state!r}",
            context=raw_event.source_position,
        )
    owner = str(task.get("owner") or "").strip()
    payload = TaskChanged(
        task_id,
        str(task.get("subject") or ""),
        str(task.get("description") or "").strip() or None,
        state,
        ActorId(owner) if owner else None,
    )
    return event(raw_event, "task", str(task_id), "changed", payload)


def translate_transcript(
    raw_event: RawEvent,
    document: dict[str, Any],
    record: dict[str, Any],
    tool_call_semantics: ToolCallSemantics,
    turn_semantics: TurnSemantics,
    selection_semantics: SelectionSemantics,
    *,
    actor_started: bool,
) -> list[CanonicalEvent[EventPayload]]:
    kind = record["kind"]
    native_identity = str(
        document.get("uuid")
        or document.get("message", {}).get("id")
        or raw_event.source_position
    )
    occurred_at = timestamp(document.get("timestamp"))
    if kind == "prompt":
        synthetic = bool(record.get("meta"))
        phase: MessagePhase = "synthetic" if synthetic else "prompt"
        role: MessageRole = (
            "system"
            if synthetic
            else "parent"
            if raw_event.parent_actor_id is not None
            else "user"
        )
        payload: EventPayload = MessageCreated(
            MessageId(native_identity), role, content(record["text"]), phase, None
        )
        created = event(raw_event, "message", native_identity, "created", payload, occurred_at=occurred_at)
        if role != "user":
            # A synthetic or parent-authored prompt is machinery or a brief; a
            # turn belongs to the person who asked for one.
            return [created]
        return [*prompt_turn(raw_event, turn_semantics, native_identity, occurred_at), created]
    if kind == "slash_command":
        return slash_command(
            raw_event, record, native_identity, occurred_at, turn_semantics, selection_semantics
        )
    if kind == "goal":
        payload = GoalChanged(record.get("objective"), record["state"], record.get("reason"))
        return [event(raw_event, "goal", native_identity, "changed", payload, occurred_at=occurred_at)]
    if kind == "background_command_completed":
        shell_id = ShellId(str(record.get("operation_id") or ""))
        if not shell_id:
            raise TranslationError(
                "Claude Code background completion has no command id",
                context=raw_event.source_position,
            )
        # The JOB's outcome, which the notification carries and this translation
        # used to drop — leaving the dashboard to report the LAUNCH's outcome, so a
        # background command that exited non-zero read as succeeded.
        payload = ShellOutputFinished(shell_id, background_outcome(record.get("status")))
        return [event(
            raw_event,
            "shell",
            str(shell_id),
            "output_finished",
            payload,
            occurred_at=occurred_at,
        )]
    if kind == "monitor_event":
        # One line the watched command printed. Recorded as progress on the
        # armed command — the same shape a command's output takes — under the
        # "status" stream, which is what a monitors panel reads as an EVENT
        # rather than as output.
        task_id = str(record.get("task") or "")
        armed = tool_call_semantics.monitor_shell(task_id)
        if armed is None:
            # A monitor armed before this translation began — a daemon restarted
            # mid-watch. The event belongs to a command we cannot name, and
            # inventing one would put a phantom monitor on the panel. Dropped;
            # the watch's own end still lands, because that notification names
            # its tool_use_id outright.
            return []
        ordinal = tool_call_semantics.next_monitor_ordinal(task_id)
        payload = ShellProgressed(
            armed,
            ordinal,
            "status",
            content(str(record.get("event") or "")),
            "append",
        )
        return [event(
            raw_event,
            "shell",
            str(armed),
            f"progress:status:{ordinal}",
            payload,
            occurred_at=occurred_at,
        )]
    if kind == "monitor_ended":
        # The watch itself ending, which is NOT its arm returning: the arm's
        # `shell.finished` arrived turns ago and the status writer deliberately
        # ignores it for a monitor. This is the same fact a background job's
        # completion is, so it is the same event.
        shell_id = ShellId(str(record.get("operation_id") or ""))
        if not str(shell_id):
            raise TranslationError(
                "Claude Code monitor end has no command id",
                context=raw_event.source_position,
            )
        payload = ShellOutputFinished(shell_id, background_outcome(record.get("status")))
        return [event(
            raw_event,
            "shell",
            str(shell_id),
            "output_finished",
            payload,
            occurred_at=occurred_at,
        )]
    if kind == "actor_assignment_finished":
        assignment_id = AssignmentId(record["assignment_id"])
        status = record["status"]
        outcome: Outcome = "failed" if status == "failed" else "cancelled" if status == "cancelled" else "succeeded"
        result = record.get("result")
        payload = ActorAssignmentFinished(
            assignment_id,
            outcome,
            content(result, markdown=True) if result else None,
            None,
        )
        return [
            event(
                raw_event,
                "actor_assignment",
                str(assignment_id),
                "finished",
                payload,
                occurred_at=occurred_at,
            )
        ]
    if kind == "teammsg":
        if not record.get("sender"):
            raise TranslationError(
                "Claude Code teammate message has no sender",
                context=raw_event.source_position,
            )
        payload = MessageCreated(MessageId(native_identity), "peer", content(record["body"]), None, None)
        events = []
        if raw_event.parent_actor_id is not None and not actor_started:
            events.append(event(
                raw_event,
                "actor",
                str(raw_event.actor_id),
                "started",
                ActorStarted(str(raw_event.actor_id), "teammate"),
                occurred_at=None,
            ))
        events.append(
            event(
                raw_event,
                "message",
                native_identity,
                "created",
                payload,
                occurred_at=occurred_at,
            )
        )
        return events
    if kind == "assistant":
        events = []
        message_identity = native_identity
        native_message = document.get("message") or {}
        native_blocks = native_message.get("content")
        if not isinstance(native_blocks, list):
            native_blocks = []
        # WHERE THE MODEL STOPPED, from the one field that says so structurally.
        # `stop_reason` is the API's own verdict on this response: "end_turn" is a
        # response that ended, "tool_use" is one that broke off to call a tool, and
        # an interrupted or truncated response says something else again. Read here
        # rather than joined from a hook because it rides the SAME record the
        # message is built from — the MessageDisplay hook does carry `final: true`,
        # but its `message_id` is a third id namespace (measured 2026-08-17: the
        # hook said b0cb4fd2…, the transcript record's uuid was 4bbcc159… and its
        # `message.id` was msg_011Ce8X6…), so using it would mean a heuristic join
        # across two sources with different arrival lag.
        #
        # Only the LAST text block carries it: one response may hold several text
        # blocks (`uuid:0`, `uuid:1`, …) and the stop belongs to the response, so
        # the earlier blocks are prose the model wrote on its way to stopping.
        ends_turn = native_message.get("stop_reason") == "end_turn"
        last_text_index = max(
            (
                index for index, block in enumerate(native_blocks)
                if isinstance(block, dict)
                and block.get("type") == "text"
                and str(block.get("text") or "").strip()
            ),
            default=-1,
        )
        for block_index, block in enumerate(native_blocks):
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "text" and str(block.get("text") or "").strip():
                block_identity = f"{message_identity}:{block_index}"
                payload = MessageCreated(
                    MessageId(block_identity),
                    "assistant",
                    content(block.get("text"), markdown=True),
                    "end_turn" if ends_turn and block_index == last_text_index else "intermediate",
                    None,
                )
                events.append(
                    event(
                        raw_event,
                        "message",
                        block_identity,
                        "created",
                        payload,
                        occurred_at=occurred_at,
                    )
                )
            elif block_type == "thinking" and str(block.get("thinking") or "").strip():
                block_identity = f"{message_identity}:{block_index}"
                payload = ReasoningCreated(
                    block_identity,
                    content(block.get("thinking"), markdown=True),
                )
                events.append(
                    event(
                        raw_event,
                        "reasoning",
                        block_identity,
                        "created",
                        payload,
                        occurred_at=occurred_at,
                    )
                )
            elif block_type == "tool_use":
                events.extend(tool_call_semantics.tool_started(raw_event, block))
        model_id = record.get("model")
        # "<synthetic>" is the transcript's marker on machine-injected
        # assistant records (interrupt notices, hook output). It names no model
        # anyone selected, so it reports nothing.
        model_reference_value = (
            model_reference(model_id)
            if model_id and model_id != SYNTHETIC_MODEL_ID
            else None
        )
        if model_reference_value is not None:
            reported = selection_semantics.model(
                raw_event.session_id,
                raw_event.actor_id,
                model_reference_value,
                "reported_by_harness",
            )
            if reported is not None:
                events.append(
                    event(
                        raw_event,
                        "model",
                        message_identity,
                        "reported",
                        reported,
                        occurred_at=occurred_at,
                    )
                )
        usage = record.get("usage")
        if isinstance(usage, dict) and model_reference_value is not None:
            events.append(
                event(
                    raw_event,
                    "context",
                    message_identity,
                    "reported",
                    ContextReported(
                        model.context_used(usage),
                        model.context_window(model_id),
                        model_reference_value,
                    ),
                    occurred_at=occurred_at,
                )
            )
        return events
    if kind == "results":
        events = []
        blocks = list(record.get("blocks") or ())
        # The line's `toolUseResult` sidecar carries what only the native
        # response document holds — a diff's structured patch, a background
        # launch's task id. It belongs to the line, so it can only be attributed
        # when the line holds exactly one result.
        sidecar = record.get("tur") if len(blocks) == 1 else None
        for block in blocks:
            call_id = str(block.get("tool_use_id") or native_identity)
            result_text = transcript.result_text(block.get("content"))
            # A background launch's tool_result is boilerplate ("Command
            # running in background with ID … Output is being written to …"),
            # and its REPLACE mode would wipe any watch chunk that committed
            # first. The real output arrives through the file watch.
            if result_text.startswith(BACKGROUND_LAUNCH_STUB):
                continue
            failed = bool(block.get("is_error"))
            events.extend(
                tool_call_semantics.tool_result(raw_event, call_id, result_text, failed, sidecar)
            )
            if failed and tool_call_semantics.pending_attention(call_id):
                events.append(tool_call_semantics.attention_declined(raw_event, call_id, result_text))
        for text_index, result_text in enumerate(record.get("texts") or ()):
            text_identity = f"{native_identity}:text:{text_index}"
            payload = MessageCreated(
                MessageId(text_identity),
                "system" if record.get("meta") else "user",
                content(result_text),
                "synthetic" if record.get("meta") else "prompt",
                None,
            )
            events.append(event(raw_event, "message", text_identity, "created", payload))
        return events
    if kind == "compact":
        before = (record.get("meta") or {}).get("preTokens")
        payload = CompactionFinished(int(before) if isinstance(before, int) else None, None)
        return [event(raw_event, "compaction", native_identity, "finished", payload, occurred_at=occurred_at)]
    if kind == "recap":
        payload = MessageCreated(
            MessageId(native_identity),
            "system",
            content(record["text"], markdown=True),
            "recap",
            None,
        )
        return [event(raw_event, "message", native_identity, "created", payload, occurred_at=occurred_at)]
    return []
