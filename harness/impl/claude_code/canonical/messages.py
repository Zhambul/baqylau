"""Claude Code transcript record translation: one canonical mapping per record kind."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib

import os

from pydantic import ValidationError

from domain.events import (
    ActorAssignmentFinished,
    ActorDescriptionChanged,
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
    TurnAborted,
    TurnStarted,
    UsageReported,
)
from domain.ids import AccountId
from harness.impl.claude_code.ids import (
    ClaudeCodeActorId,
    ClaudeCodeCallId,
    ClaudeCodeMessageId,
    ClaudeCodeReasoningId,
    ClaudeCodeShellId,
    ClaudeCodeTurnId,
    actor_id_from_claude_code,
    assignment_id_from_claude_code_call,
    message_id_from_claude_code,
    reasoning_id_from_claude_code,
    shell_id_from_claude_code_call,
    turn_id_from_claude_code,
)
from harness.impl.claude_code.model import ClaudeCodeModel
from domain.values import (
    AccountReference,
    ActorRole,
    EffortChangeReason,
    GoalState,
    MessagePhase,
    MessageRole,
    ModelChangeReason,
    Outcome,
    OutputMode,
    ProgressStream,
    TitleOrigin,
    TokenUsage,
    UsageScope,
)
from harness.impl.claude_code import model
from harness.impl.claude_code.canonical import records, transcript
from harness.impl.claude_code.canonical.support import SYNTHETIC_MODEL_ID, content, event, model_reference, timestamp
from harness.impl.claude_code.canonical.toolcalls import BACKGROUND_LAUNCH_STUB, ToolCallSemantics
from harness.impl.claude_code.canonical.turns import TurnSemantics
from harness.models import RawEvent, TranslationError, session_run_started_events
from harness.models.selections import SelectionSemantics


# How a background command ENDED, from the `<status>` on the completion
# notification Claude Code posts when the job is over. The four values it really
# uses, counted over every retained transcript (2026-08-18): completed 6563,
# failed 375, killed 83, stopped 22 — so "not completed" is a third of a percent
# of jobs and worth telling apart, and neither `killed` nor `stopped` is the
# `cancelled` an earlier reader guessed at. Anything else is unknown rather than
# assumed good: reporting a job as succeeded is the one answer that cannot be
# walked back by looking at it.
BACKGROUND_OUTCOMES: Mapping[str, Outcome] = {
    "completed": Outcome.SUCCEEDED,
    "failed": Outcome.FAILED,
    "killed": Outcome.CANCELLED,
    "stopped": Outcome.CANCELLED,
}

SKILL_OUTPUT_PREFIX = "Base directory for this skill: "
SKILL_ARGUMENTS_MARKER = "\nARGUMENTS:"


def _loaded_skill(text: str) -> tuple[str, str] | None:
    """Recognize Claude's injected SKILL.md prompt and separate its args.

    Claude appends ``ARGUMENTS: ...`` to the injected file.  Arguments already
    belong to ``SkillStarted``; leaving that trailer in the result would show
    them twice in the folded skill card.
    """
    first_line, separator, _rest = text.partition("\n")
    if not separator or not first_line.startswith(SKILL_OUTPUT_PREFIX):
        return None
    directory = first_line[len(SKILL_OUTPUT_PREFIX) :].strip().rstrip("/")
    if "/.claude/skills/" not in directory:
        return None
    name = os.path.basename(directory)
    if not name:
        return None
    marker = text.rfind(SKILL_ARGUMENTS_MARKER)
    output = text[:marker].rstrip() if marker >= 0 else text.rstrip()
    return name, output


def _assignment_finish_phase(status: str, result: str | None) -> str:
    """Identify one reported revision of a resumable agent's result.

    Claude Code can report the same agent as completed more than once when it
    stops, receives an automatic background-job notification, and resumes. Its
    queue and user copies of one report must converge, while a later result
    must remain a newer fact instead of colliding with the first one.
    """
    revision = hashlib.sha256(
        f"{status}\0{result or ''}".encode("utf-8")
    ).hexdigest()
    return f"finished:{revision}"


def background_outcome(status: str | None) -> Outcome | None:
    if not status:
        return None
    normalized = str(status).strip().lower()
    return BACKGROUND_OUTCOMES.get(normalized, Outcome.UNKNOWN)


def launch_selections(
    raw_event: RawEvent,
    launch: records.LaunchSelectionDocument,
    selection_semantics: SelectionSemantics,
) -> list[CanonicalEvent[EventPayload]]:
    """The launch observation the gateway recorded from the hook's inherited
    environment: the `--model`/`--effort` the launcher started the CLI with.

    A launch selection is the same fact a typed `/model x` records, with the
    same alias caveat: it carries a selection alias ("fable"), and the
    resolved native id arrives only on the first assistant record, as
    `reported_by_harness`. Without this event the selectors sit empty until
    then — and for the effort, forever: Claude Code never echoes it in any
    raw event stream."""
    subject_id = f"launch:{raw_event.source_position}"
    events = []
    model_selection = launch.model
    if isinstance(model_selection, str) and model_selection:
        changed = selection_semantics.model(
            raw_event.session_id,
            raw_event.actor_id,
            model_reference(ClaudeCodeModel(model_selection)),
            ModelChangeReason.SELECTED,
            model.family(model_selection) or model_selection,
        )
        if changed is not None:
            events.append(event(raw_event, "model", subject_id, "selected", changed))
    effort_selection = launch.effort
    if isinstance(effort_selection, str) and effort_selection:
        chosen = selection_semantics.effort(
            raw_event.session_id, raw_event.actor_id, effort_selection, EffortChangeReason.SELECTED
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
    raw events name a turn, and the prompt is what the turn answers.
    """
    turn_id = turn_id_from_claude_code(ClaudeCodeTurnId(native_identity))
    if not turn_semantics.begin(raw_event, turn_id):
        return []
    return [
        event(
            raw_event,
            "turn",
            str(turn_id),
            "started",
            TurnStarted(message_id_from_claude_code(ClaudeCodeMessageId(native_identity))),
            turn_id=turn_id,
            occurred_at=occurred_at,
        )
    ]


def slash_command(
    raw_event: RawEvent,
    record: transcript.SlashCommandTranscriptRecord,
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
    name = record.name.lstrip("/").strip().lower()
    selection = record.arguments.strip()
    if selection and len(selection.split()) == 1 and name in ("model", "effort"):
        payload: EventPayload | None = (
            selection_semantics.model(
                raw_event.session_id, raw_event.actor_id, model_reference(ClaudeCodeModel(selection)),
                ModelChangeReason.SELECTED,
                model.family(selection) or selection,
            )
            if name == "model"
            else selection_semantics.effort(
                raw_event.session_id, raw_event.actor_id, selection, EffortChangeReason.SELECTED
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
    role: MessageRole = MessageRole.PARENT if raw_event.parent_actor_id is not None else MessageRole.USER
    events = [
        event(
            raw_event,
            "message",
            native_identity,
            "created",
            MessageCreated(
                message_id_from_claude_code(ClaudeCodeMessageId(native_identity)),
                role,
                content(record.text),
                MessagePhase.PROMPT,
                None,
            ),
            occurred_at=occurred_at,
        )
    ]
    if role == MessageRole.USER:
        events = prompt_turn(raw_event, turn_semantics, native_identity, occurred_at) + events
    return events


def transcript_metadata(
    raw_event: RawEvent,
    transcript_document: records.TranscriptDocument,
) -> list[CanonicalEvent[EventPayload]]:
    if raw_event.parent_actor_id is not None:
        return []
    record_type = transcript_document.type
    if record_type not in ("agent-name", "ai-title", "summary"):
        return []
    if record_type == "agent-name":
        title = str(transcript_document.agentName or "").strip()
        origin: TitleOrigin = TitleOrigin.CUSTOM
    elif record_type == "ai-title":
        title = str(transcript_document.aiTitle or "").strip()
        origin = TitleOrigin.AUTOMATIC
    else:
        title = str(transcript_document.summary or "").strip()
        origin = TitleOrigin.SUMMARY
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


def session_events(
    raw_event: RawEvent,
    document: records.TranscriptDocument | records.HookPayload,
) -> list[CanonicalEvent[EventPayload]]:
    lead_actor_id = raw_event.actor_id
    if raw_event.parent_actor_id is not None:
        metadata = records.AgentMetaFile()
        if raw_event.source_type in ("child_transcript", "teammate_transcript"):
            metadata_path = os.path.splitext(raw_event.source_name)[0] + ".meta.json"
            try:
                with open(metadata_path, encoding="utf-8") as metadata_file:
                    metadata = records.AgentMetaFile.model_validate_json(metadata_file.read())
            except OSError:
                metadata = records.AgentMetaFile()
            except ValidationError as error:
                if any(detail["type"] != "json_invalid" for detail in error.errors()):
                    raise
                metadata = records.AgentMetaFile()
        events = [
            event(
                raw_event,
                "actor",
                str(raw_event.actor_id),
                "started",
                ActorStarted(
                    str(raw_event.actor_id),
                    ActorRole.TEAMMATE if raw_event.source_type == "teammate_transcript" else ActorRole.CHILD,
                ),
            )
        ]
        native_name = str(metadata.name or "").strip()
        description = str(metadata.description or "").strip()
        display_name = native_name or description
        if display_name:
            events.append(event(
                raw_event,
                "actor",
                str(raw_event.actor_id),
                "name:metadata",
                ActorNameChanged(display_name),
            ))
        if description:
            events.append(event(
                raw_event,
                "actor",
                str(raw_event.actor_id),
                "description:metadata",
                ActorDescriptionChanged(description),
            ))
        return events
    transcript_path = str(document.transcript_path or "")
    session_started = SessionStarted(
        working_directory=str(document.cwd or ""),
        source_reference=(
            os.path.realpath(transcript_path) if transcript_path else raw_event.source_name
        ),
        resumed_from=None,
        title=None,
        model=None,
        effort=None,
        account=None,
    )
    actor_started = ActorStarted("claude", ActorRole.LEAD)
    if raw_event.source_type == "hook" and raw_event.terminal_window_id is not None:
        events = list(session_run_started_events(raw_event, session_started, actor_started))
    else:
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
                actor_started,
            ),
        ]
    if raw_event.account_id is not None or raw_event.account_display_name is not None:
        account_id = raw_event.account_id or AccountId("")
        display_name = raw_event.account_display_name or account_id or "default"
        events.append(event(
            raw_event,
            "session",
            str(raw_event.session_id),
            f"account:{raw_event.source_position}",
            SessionAccountChanged(AccountReference(account_id, display_name)),
        ))
    return events


def translate_transcript(
    raw_event: RawEvent,
    transcript_document: records.TranscriptDocument,
    record: transcript.TranscriptRecord,
    tool_call_semantics: ToolCallSemantics,
    turn_semantics: TurnSemantics,
    selection_semantics: SelectionSemantics,
    *,
    actor_started: bool,
) -> list[CanonicalEvent[EventPayload]]:
    native_message_id = transcript_document.message.id if transcript_document.message is not None else None
    native_identity = str(
        transcript_document.uuid
        or native_message_id
        or raw_event.source_position
    )
    occurred_at = timestamp(transcript_document.timestamp)
    if isinstance(record, transcript.PromptTranscriptRecord):
        synthetic = record.meta
        if synthetic:
            loaded_skill = _loaded_skill(record.text)
            if loaded_skill is not None:
                name, output = loaded_skill
                finished = tool_call_semantics.skill_loaded(raw_event, name, output)
                if finished is not None:
                    return [finished]
        phase: MessagePhase = MessagePhase.SYNTHETIC if synthetic else MessagePhase.PROMPT
        role: MessageRole = (
            MessageRole.SYSTEM
            if synthetic
            else MessageRole.PARENT
            if raw_event.parent_actor_id is not None
            else MessageRole.USER
        )
        payload: EventPayload = MessageCreated(
            message_id_from_claude_code(ClaudeCodeMessageId(native_identity)),
            role,
            content(record.text),
            phase,
            None,
        )
        turn_id = turn_semantics.current(raw_event) if record.interrupted else None
        created = event(
            raw_event,
            "message",
            native_identity,
            "created",
            payload,
            turn_id=turn_id,
            occurred_at=occurred_at,
        )
        if record.interrupted:
            turn_semantics.close(raw_event)
            return [
                created,
                event(
                    raw_event,
                    "turn",
                    str(turn_id) if turn_id else native_identity,
                    "aborted",
                    TurnAborted(None),
                    turn_id=turn_id,
                    occurred_at=occurred_at,
                ),
            ]
        if role != MessageRole.USER:
            # A synthetic or parent-authored prompt is machinery or a brief; a
            # turn belongs to the person who asked for one.
            return [created]
        return [*prompt_turn(raw_event, turn_semantics, native_identity, occurred_at), created]
    if isinstance(record, transcript.SlashCommandTranscriptRecord):
        return slash_command(
            raw_event, record, native_identity, occurred_at, turn_semantics, selection_semantics
        )
    if isinstance(record, transcript.GoalTranscriptRecord):
        objective = record.objective
        reason = record.reason
        # The state string is ours (built by parse_line/_task_notification,
        # never read back off Claude Code's own JSON), so the enum member it
        # constructs is a fact about THIS module, not a foreign claim.
        state = GoalState(record.state)
        payload = GoalChanged(
            str(objective) if objective is not None else None,
            state,
            str(reason) if reason is not None else None,
        )
        return [event(raw_event, "goal", native_identity, "changed", payload, occurred_at=occurred_at)]
    if isinstance(record, transcript.BackgroundCommandCompletedTranscriptRecord):
        shell_id = shell_id_from_claude_code_call(record.operation_id)
        if not shell_id:
            raise TranslationError(
                "Claude Code background completion has no command id",
                context=raw_event.source_position,
            )
        # The JOB's outcome, which the notification carries and this translation
        # used to drop — leaving the dashboard to report the LAUNCH's outcome, so a
        # background command that exited non-zero read as succeeded.
        payload = ShellOutputFinished(shell_id, background_outcome(record.status))
        return [event(
            raw_event,
            "shell",
            str(shell_id),
            "output_finished",
            payload,
            occurred_at=occurred_at,
        )]
    if isinstance(record, transcript.MonitorEventTranscriptRecord):
        # One line the watched command printed. Recorded as progress on the
        # armed command — the same shape a command's output takes — under the
        # "status" stream, which is what a monitors panel reads as an EVENT
        # rather than as output.
        task_id = ClaudeCodeShellId(record.task)
        armed = tool_call_semantics.monitor_shell(raw_event, task_id)
        if armed is None:
            # A monitor armed before this translation began — a daemon restarted
            # mid-watch. The event belongs to a command we cannot name, and
            # inventing one would put a phantom monitor on the panel. Dropped;
            # the watch's own end still lands, because that notification names
            # its tool_use_id outright.
            return []
        ordinal = tool_call_semantics.next_monitor_ordinal(raw_event, task_id)
        payload = ShellProgressed(
            armed,
            ordinal,
            ProgressStream.STATUS,
            content(record.event),
            OutputMode.APPEND,
        )
        return [event(
            raw_event,
            "shell",
            str(armed),
            f"progress:status:{ordinal}",
            payload,
            occurred_at=occurred_at,
        )]
    if isinstance(record, transcript.MonitorEndedTranscriptRecord):
        # The watch itself ending, which is NOT its arm returning: the arm's
        # `shell.finished` arrived turns ago and the status writer deliberately
        # ignores it for a monitor. This is the same fact a background job's
        # completion is, so it is the same event.
        shell_id = shell_id_from_claude_code_call(record.operation_id)
        if not str(shell_id):
            raise TranslationError(
                "Claude Code monitor end has no command id",
                context=raw_event.source_position,
            )
        payload = ShellOutputFinished(shell_id, background_outcome(record.status))
        tool_call_semantics.monitor_finished(raw_event, shell_id)
        return [event(
            raw_event,
            "shell",
            str(shell_id),
            "output_finished",
            payload,
            occurred_at=occurred_at,
        )]
    if isinstance(record, transcript.ActorAssignmentFinishedTranscriptRecord):
        assignment_call = tool_call_semantics.assignment_call(
            raw_event,
            record.actor_id,
            record.assignment_id,
        )
        assignment_id = assignment_id_from_claude_code_call(assignment_call)
        tool_call_semantics.assignment_finished(raw_event, record.actor_id)
        status = record.status
        outcome: Outcome = (
            Outcome.FAILED if status == "failed"
            else Outcome.CANCELLED if status == "cancelled"
            else Outcome.SUCCEEDED
        )
        result = record.result
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
                _assignment_finish_phase(status, result),
                payload,
                occurred_at=occurred_at,
            )
        ]
    if isinstance(record, transcript.TeammateIdleTranscriptRecord):
        events = []
        notifications_by_actor = {}
        for notification in record.notifications:
            actor_native_id = (
                transcript.teammate_actor_id(raw_event.source_name, notification.from_)
                or ClaudeCodeActorId(notification.from_)
            )
            resolved_actor_id = actor_id_from_claude_code(actor_native_id)
            notifications_by_actor[resolved_actor_id] = (
                actor_native_id,
                notification,
            )
        for resolved_actor_id, (
            actor_native_id,
            notification,
        ) in notifications_by_actor.items():
            legacy_lead_observation = raw_event.parent_actor_id is None
            if not legacy_lead_observation and resolved_actor_id != raw_event.actor_id:
                continue
            actor_raw_event = raw_event
            assignment_call = tool_call_semantics.assignment_call(
                actor_raw_event,
                actor_native_id,
                ClaudeCodeCallId(""),
            )
            if not assignment_call:
                raise TranslationError(
                    f"Claude Code teammate {notification.from_!r} has no assignment",
                    context=raw_event.source_position,
                )
            tool_call_semantics.assignment_finished(actor_raw_event, actor_native_id)
            outcome = (
                Outcome.SUCCEEDED
                if notification.idleReason == "available"
                else Outcome.FAILED
                if notification.idleReason == "failed"
                else Outcome.CANCELLED
                if notification.idleReason in ("stopped", "cancelled")
                else Outcome.UNKNOWN
            )
            assignment_id = assignment_id_from_claude_code_call(assignment_call)
            events.append(event(
                actor_raw_event,
                "actor_assignment",
                str(assignment_id),
                "finished",
                ActorAssignmentFinished(
                    assignment_id,
                    outcome,
                    content(notification.failureReason) if notification.failureReason else None,
                    None,
                ),
                occurred_at=timestamp(notification.timestamp),
            ))
        return events
    if isinstance(record, transcript.TeamMessageTranscriptRecord):
        if not record.sender:
            raise TranslationError(
                "Claude Code teammate message has no sender",
                context=raw_event.source_position,
            )
        is_parent_prompt = (
            record.sender == transcript.LEAD_TEAMMATE_ID
            and raw_event.parent_actor_id is not None
        )
        payload = MessageCreated(
            message_id_from_claude_code(ClaudeCodeMessageId(native_identity)),
            MessageRole.PARENT if is_parent_prompt else MessageRole.PEER,
            content(record.body),
            MessagePhase.PROMPT if is_parent_prompt else None,
            None,
        )
        events = []
        if raw_event.parent_actor_id is not None and not actor_started:
            events.append(event(
                raw_event,
                "actor",
                str(raw_event.actor_id),
                "started",
                ActorStarted(str(raw_event.actor_id), ActorRole.TEAMMATE),
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
    if isinstance(record, transcript.AssistantTranscriptRecord):
        events = []
        message_identity = native_identity
        assistant_message = record.message
        native_content = assistant_message.content if assistant_message is not None else None
        native_blocks = native_content if isinstance(native_content, list) else []
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
        ends_turn = assistant_message is not None and assistant_message.stop_reason == "end_turn"
        last_text_index = max(
            (
                index for index, block in enumerate(native_blocks)
                if isinstance(block, records.TextBlock) and (block.text or "").strip()
            ),
            default=-1,
        )
        for block_index, block in enumerate(native_blocks):
            if isinstance(block, records.TextBlock) and (block.text or "").strip():
                block_identity = f"{message_identity}:{block_index}"
                payload = MessageCreated(
                    message_id_from_claude_code(ClaudeCodeMessageId(block_identity)),
                    MessageRole.ASSISTANT,
                    content(block.text, markdown=True),
                    MessagePhase.END_TURN
                    if ends_turn and block_index == last_text_index
                    else MessagePhase.INTERMEDIATE,
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
            elif isinstance(block, records.ThinkingBlock) and (block.thinking or "").strip():
                block_identity = f"{message_identity}:{block_index}"
                payload = ReasoningCreated(
                    reasoning_id_from_claude_code(ClaudeCodeReasoningId(block_identity)),
                    content(block.thinking, markdown=True),
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
            elif isinstance(block, records.ToolUseBlock):
                events.extend(
                    tool_call_semantics.tool_started(
                        raw_event,
                        records.ToolCallNative(
                            id=block.id,
                            name=block.name,
                            input=block.input,
                        ),
                    )
                )
        model_id = record.message.model if record.message else None
        # "<synthetic>" is the transcript's marker on machine-injected
        # assistant records (interrupt notices, hook output). It names no model
        # anyone selected, so it reports nothing.
        model_reference_value = (
            model_reference(ClaudeCodeModel(model_id))
            if model_id and model_id != SYNTHETIC_MODEL_ID
            else None
        )
        if model_reference_value is not None:
            reported = selection_semantics.model(
                raw_event.session_id,
                raw_event.actor_id,
                model_reference_value,
                ModelChangeReason.REPORTED_BY_HARNESS,
                model.family(model_id) or model_id or "",
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
        usage = record.message.usage if record.message else None
        if usage is not None and model_reference_value is not None:
            cache_creation = usage.cache_creation
            cache_write_tokens = (
                int(cache_creation.ephemeral_5m_input_tokens)
                if cache_creation is not None
                else int(usage.cache_creation_input_tokens or 0)
            )
            one_hour_cache_write_tokens = (
                int(cache_creation.ephemeral_1h_input_tokens)
                if cache_creation is not None
                else 0
            )
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
            # Every assistant response records its own token usage in the
            # transcript. This is the durable session-token source; OTEL is
            # optional and therefore owns cost only (see canonical/otel.py).
            events.append(
                event(
                    raw_event,
                    "usage",
                    message_identity,
                    "reported",
                    UsageReported(
                        UsageScope.SESSION,
                        str(raw_event.session_id),
                        model_reference_value,
                        None,
                        TokenUsage(
                            input_tokens=int(usage.input_tokens or 0),
                            output_tokens=int(usage.output_tokens or 0),
                            cache_read_tokens=int(usage.cache_read_input_tokens or 0),
                            cache_write_tokens=cache_write_tokens,
                            one_hour_cache_write_tokens=one_hour_cache_write_tokens,
                        ),
                        False,
                        None,
                    ),
                    occurred_at=occurred_at,
                )
            )
        return events
    if isinstance(record, transcript.ResultsTranscriptRecord):
        events = []
        loaded_skill_texts: set[int] = set()
        if record.meta:
            # Current Claude Code stores an injected skill as a text block in
            # a user-shaped results record, not as the plain-string prompt
            # handled above. Correlate it before the result block can release
            # the remembered tool call.
            for text_index, result_text in enumerate(record.texts):
                loaded_skill = _loaded_skill(result_text)
                if loaded_skill is None:
                    continue
                name, output = loaded_skill
                finished = tool_call_semantics.skill_loaded(raw_event, name, output)
                if finished is not None:
                    events.append(finished)
                    loaded_skill_texts.add(text_index)
        interrupted_turn_id = (
            turn_semantics.current(raw_event) if record.interrupted else None
        )
        blocks = record.blocks
        # The line's `toolUseResult` sidecar carries what only the native
        # response document holds — a diff's structured patch, a background
        # launch's task id. It belongs to the line, so it can only be attributed
        # when the line holds exactly one result.
        sidecar = record.tool_response if len(blocks) == 1 else None
        for tool_result_block in blocks:
            call_id = ClaudeCodeCallId(tool_result_block.tool_use_id or native_identity)
            result_text = transcript.result_text(tool_result_block.content)
            # A background launch's tool_result is boilerplate ("Command
            # running in background with ID … Output is being written to …"),
            # and its REPLACE mode would wipe any watch chunk that committed
            # first. The real output arrives through the file watch.
            if result_text.startswith(BACKGROUND_LAUNCH_STUB):
                tool_call_semantics.forget(raw_event, call_id)
                continue
            failed = bool(tool_result_block.is_error)
            declined_attention = (
                failed
                and tool_call_semantics.pending_attention(raw_event, call_id)
            )
            events.extend(
                tool_call_semantics.tool_result(
                    raw_event,
                    call_id,
                    result_text,
                    failed,
                    sidecar,
                    cancelled=record.cancelled,
                )
            )
            if declined_attention:
                events.append(tool_call_semantics.attention_declined(raw_event, call_id, result_text))
            # A successful Skill result is only the "Launching skill…"
            # acknowledgement. Its actual result arrives in a following
            # injected text record, so keep the correlation until then.
            if not tool_call_semantics.is_skill(raw_event, call_id):
                tool_call_semantics.forget(raw_event, call_id)
        for text_index, result_text in enumerate(record.texts):
            if text_index in loaded_skill_texts:
                continue
            text_identity = f"{native_identity}:text:{text_index}"
            payload = MessageCreated(
                message_id_from_claude_code(ClaudeCodeMessageId(text_identity)),
                MessageRole.SYSTEM if record.meta else MessageRole.USER,
                content(result_text),
                MessagePhase.SYNTHETIC if record.meta else MessagePhase.PROMPT,
                None,
            )
            events.append(event(
                raw_event,
                "message",
                text_identity,
                "created",
                payload,
                turn_id=interrupted_turn_id,
            ))
        if record.interrupted:
            turn_semantics.close(raw_event)
            events.append(event(
                raw_event,
                "turn",
                str(interrupted_turn_id) if interrupted_turn_id else native_identity,
                "aborted",
                TurnAborted(None),
                turn_id=interrupted_turn_id,
                occurred_at=occurred_at,
            ))
        return events
    if isinstance(record, transcript.CompactTranscriptRecord):
        payload = CompactionFinished(record.before_tokens, None)
        return [event(raw_event, "compaction", native_identity, "finished", payload, occurred_at=occurred_at)]
    if isinstance(record, transcript.TextTranscriptRecord) and record.kind == transcript.TranscriptKind.RECAP:
        payload = MessageCreated(
            message_id_from_claude_code(ClaudeCodeMessageId(native_identity)),
            MessageRole.SYSTEM,
            content(record.text, markdown=True),
            MessagePhase.RECAP,
            None,
        )
        return [event(raw_event, "message", native_identity, "created", payload, occurred_at=occurred_at)]
    return []
