"""Claude Code canonical translation: dispatch by raw event source type."""

from __future__ import annotations

import base64
from dataclasses import replace

from domain.events import ShellProgressed, TaskListChanged
from domain.ids import SessionId
from domain.records import RecordedTranslationDecision
from domain.values import OutputMode
from repository.mapper.documents import StoredDocumentError, decode_document
from harness.contract import HarnessTranslator
from harness.impl.claude_code.canonical import records, transcript
from harness.impl.claude_code.canonical.hooks import translate_hook
from harness.impl.claude_code.canonical.messages import (
    launch_selections,
    session_events,
    transcript_metadata,
    translate_transcript,
)
from harness.impl.claude_code.canonical.otel import translate_otel
from harness.impl.claude_code.canonical.support import content, event
from harness.impl.claude_code.canonical.tasks import task_file_event
from harness.impl.claude_code.canonical.toolcalls import ToolCallSemantics
from harness.impl.claude_code.canonical.turns import TurnSemantics
from harness.impl.claude_code.ids import (
    ClaudeCodeTaskId,
    task_id_from_claude_code,
    task_list_id_from_claude_code,
)
from harness.models import RawEvent, TranslationError, TranslationResult, UnknownRawEvent
from harness.models.directives import ShellOutputChunk
from harness.models.selections import SelectionSemantics


class ClaudeCanonicalTranslator(HarnessTranslator):
    def __init__(self) -> None:
        self._toolcalls = ToolCallSemantics()
        self._turns = TurnSemantics()
        self._selections = SelectionSemantics()

    def translate(self, raw_event: RawEvent) -> TranslationResult:
        try:
            return self._stamped(raw_event, self._translate(raw_event))
        except UnknownRawEvent as unknown:
            return TranslationResult((), RecordedTranslationDecision.IGNORED_UNKNOWN, unknown.reason)

    def release_session(self, session_id: SessionId) -> None:
        """Release all in-memory joins for one finished session."""
        self._toolcalls.clear_session(session_id)
        self._turns.release_session(session_id)
        self._selections.release_session(session_id)

    def _stamped(self, raw_event: RawEvent, translation_result: TranslationResult) -> TranslationResult:
        """Every fact of an open turn carries it.

        Stamped HERE, once, rather than by each of the forty places that build a
        fact: a turn is a property of WHEN the observation was made, not of what
        it said. The two events that name a turn themselves set it already and
        are left alone.
        """
        turn_id = self._turns.current(raw_event)
        if turn_id is None or not translation_result.canonical_events:
            return translation_result
        stamped = tuple(
            canonical if canonical.turn_id is not None else replace(canonical, turn_id=turn_id)
            for canonical in translation_result.canonical_events
        )
        return replace(translation_result, canonical_events=stamped)

    def _translate(self, raw_event: RawEvent) -> TranslationResult:
        if raw_event.source_type == "foreground_output":
            # OURS on both ends: engine/interpret/output_source.py wrote this
            # one, so it is decoded as the declared shape rather than read key
            # by key the way a harness's own records have to be.
            try:
                chunk = decode_document(ShellOutputChunk, raw_event.payload)
                output_content = base64.b64decode(chunk.content_base64, validate=True)
            except (StoredDocumentError, TypeError, ValueError) as error:
                raise TranslationError("malformed foreground output") from error
            progress = ShellProgressed(
                chunk.shell_id,
                chunk.ordinal,
                chunk.stream,
                content(output_content.decode("utf-8", errors="replace")),
                OutputMode.APPEND,
            )
            return TranslationResult(
                (event(
                    raw_event,
                    "shell",
                    str(chunk.shell_id),
                    f"progress:{chunk.ordinal}",
                    progress,
                ),),
                RecordedTranslationDecision.TRANSLATED,
            )
        try:
            return self._translate_json(raw_event)
        except UnicodeDecodeError as error:
            raise TranslationError(
                "malformed Claude Code record", context=raw_event.source_position
            ) from error

    def _translate_json(self, raw_event: RawEvent) -> TranslationResult:
        if raw_event.source_type == "launch":
            launch = records.LaunchSelectionDocument.model_validate_json(raw_event.payload)
            events = launch_selections(raw_event, launch, self._selections)
            if not events:
                return TranslationResult(
                    (), RecordedTranslationDecision.IGNORED_NONSEMANTIC, "launch selects no model or effort"
                )
            return TranslationResult(tuple(events), RecordedTranslationDecision.TRANSLATED)
        if raw_event.source_type == "otel":
            document = records.OTelMetricsDocument.model_validate_json(raw_event.payload)
            events = translate_otel(raw_event, document)
            if not events:
                return TranslationResult(
                    (), RecordedTranslationDecision.IGNORED_NONSEMANTIC, "OTEL request carries no session usage"
                )
            return TranslationResult(tuple(events), RecordedTranslationDecision.TRANSLATED)
        if raw_event.source_type == "tasks":
            task = records.TaskFile.model_validate_json(raw_event.payload)
            canonical = task_file_event(raw_event, task)
            return TranslationResult((canonical,), RecordedTranslationDecision.TRANSLATED)
        if raw_event.source_type == "task_list":
            task_list = records.TaskListDocument.model_validate_json(raw_event.payload)
            if task_list.list_id is None or task_list.task_ids is None:
                raise TranslationError("malformed Claude Code task list")
            payload = TaskListChanged(
                task_list_id_from_claude_code(task_list.list_id),
                tuple(
                    task_id_from_claude_code(ClaudeCodeTaskId(task_id))
                    for task_id in task_list.task_ids
                ),
            )
            canonical = event(raw_event, "task_list", raw_event.source_position, "changed", payload)
            return TranslationResult((canonical,), RecordedTranslationDecision.TRANSLATED)
        if raw_event.source_type in ("hook", "teammate_hook"):
            hook = records.HookPayload.model_validate_json(raw_event.payload)
            events = translate_hook(
                raw_event, hook, self._toolcalls, self._turns, self._selections
            )
            if not events:
                return TranslationResult(
                    (), RecordedTranslationDecision.IGNORED_NONSEMANTIC, "hook carries no canonical activity"
                )
            return TranslationResult(tuple(events), RecordedTranslationDecision.TRANSLATED)

        text = raw_event.payload.decode("utf-8")
        transcript_document = records.TranscriptDocument.model_validate_json(raw_event.payload)
        starts_lead_session = (
            raw_event.parent_actor_id is None
            and bool(transcript_document.cwd)
            and transcript_document.parentUuid is None
        )
        starts_child_actor = (
            raw_event.parent_actor_id is not None
            and raw_event.source_position == "0"
        )
        session_events_ = (
            session_events(raw_event, transcript_document)
            if starts_lead_session or starts_child_actor
            else []
        )
        metadata_events = transcript_metadata(raw_event, transcript_document)
        record = transcript.parse_line(text)
        if record is None:
            if session_events_ or metadata_events:
                return TranslationResult(
                    tuple(session_events_ + metadata_events), RecordedTranslationDecision.TRANSLATED
                )
            return TranslationResult((), RecordedTranslationDecision.IGNORED_NONSEMANTIC, "transcript plumbing record")
        if isinstance(record, transcript.BadTranscriptRecord):
            raise TranslationError("malformed Claude Code transcript record", context=raw_event.source_position)
        transcript_events = translate_transcript(
            raw_event,
            transcript_document,
            record,
            self._toolcalls,
            self._turns,
            self._selections,
            actor_started=starts_child_actor,
        )
        events = session_events_ + metadata_events + transcript_events
        if not events:
            return TranslationResult(
                (), RecordedTranslationDecision.IGNORED_NONSEMANTIC,
                f"nonsemantic Claude record {record.kind.value!r}",
            )
        return TranslationResult(tuple(events), RecordedTranslationDecision.TRANSLATED)
