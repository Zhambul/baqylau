# Copyright (c) 2026 Zhambyl Yermagambet
"""Claude Code canonical translation: dispatch by raw event source type."""

from __future__ import annotations

import base64
from dataclasses import replace

from domain import event_shell, ids as domain_ids, outcomes, records as domain_records
from harness.contract import HarnessTranslator
from harness.impl.claude_code.canonical import (
    hooks,
    messages,
    records,
    source_translators,
    support,
)

# Keep tool and turn translation separate from shared source dispatch helpers.
# isort: split

from harness.impl.claude_code.canonical import (
    toolcalls,
    transcript,
    transcript_start,
    turns,
)
from harness.impl.claude_code.ids import (
    ClaudeCodeCompactionId,
    turn_id_from_claude_code,
)
from harness.models import (
    directives,
    raw_event_builders,
    raw_events,
    selections,
)
from repository.mapper.documents import StoredDocumentError, decode_document

MALFORMED_TRANSCRIPT_RECORD = "malformed Claude Code transcript record"


def _recovered_turn_id(
    raw_event: raw_events.RawEvent,
    transcript_document: records.TranscriptDocument,
    record: transcript.TranscriptRecord,
) -> domain_ids.TurnId | None:
    if not (
        isinstance(record, transcript.AssistantTranscriptRecord)
        and record.message is not None
        and record.message.stop_reason == "end_turn"
        and raw_event.parent_actor_id is None
    ):
        return None
    recovered_turn = transcript.prompt_turn_before(
        raw_event.source_name,
        raw_event.source_position,
        transcript_document.parent_uuid,
    )
    return None if recovered_turn is None else turn_id_from_claude_code(recovered_turn)


def _nonsemantic_record_reason(record: transcript.TranscriptRecord) -> str:
    record_kind = record.kind.value
    return f"nonsemantic Claude record {record_kind!r}"


def _decode_foreground_output(raw_event: raw_events.RawEvent) -> tuple[directives.ShellOutputChunk, bytes]:
    chunk = decode_document(directives.ShellOutputChunk, raw_event.payload)
    output_content = base64.b64decode(chunk.content_base64, validate=True)
    return chunk, output_content


class _ClaudeTranslatorState:
    """Store state shared by Claude translation parts."""

    def __init__(self) -> None:
        """Initialize the object."""
        self._toolcalls = toolcalls.ToolCallSemantics()
        self._turns = turns.TurnSemantics()
        self._selections = selections.SelectionSemantics()
        self._pending_compactions: dict[
            tuple[domain_ids.SessionId, str],
            tuple[ClaudeCodeCompactionId, int | None],
        ] = {}

    def _translate(self, raw_event: raw_events.RawEvent) -> raw_events.TranslationResult:
        raise NotImplementedError


class _ClaudeTurnStamping(_ClaudeTranslatorState, HarnessTranslator):
    """Translate events and attach their open turn."""

    def translate(self, raw_event: raw_events.RawEvent) -> raw_events.TranslationResult:
        """Translate translate.

        Returns:
            The translation result.

        """
        try:
            return self._translate_stamped(raw_event)
        except raw_events.UnknownRawEventError as unknown:
            return raw_events.TranslationResult(
                (), domain_records.RecordedTranslationDecision.IGNORED_UNKNOWN, unknown.reason,
            )

    def release_session(self, session_id: domain_ids.SessionId) -> None:
        """Release all in-memory joins for one finished session."""
        self._toolcalls.clear_session(session_id)
        self._turns.release_session(session_id)
        self._selections.release_session(session_id)
        for key in tuple(self._pending_compactions):
            if key[0] == session_id:
                self._pending_compactions.pop(key, None)

    def _translate_stamped(self, raw_event: raw_events.RawEvent) -> raw_events.TranslationResult:
        observed_turn = self._turns.current(raw_event)
        return self._stamped(raw_event, self._translate(raw_event), observed_turn)

    def _stamped(
        self,
        raw_event: raw_events.RawEvent,
        translation_result: raw_events.TranslationResult,
        observed_turn: domain_ids.TurnId | None,
    ) -> raw_events.TranslationResult:
        """Every fact of an open turn carries it.

        Stamped HERE, once, rather than by each of the forty places that build a
        fact: a turn is a property of WHEN the observation was made, not of what
        it said. The two events that name a turn themselves set it already and
        are left alone.

        Returns:
            The translation result.

        """
        # A response can close its turn while it is translated. Use the turn
        # that was open when translation started in that case. A prompt opens
        # its turn during translation, so use the new current turn when there
        # was no turn before it.
        turn_id = (
            observed_turn
            or self._turns.current(raw_event)
            or next(
                (
                    canonical.turn_id
                    for canonical in translation_result.canonical_events
                    if canonical.turn_id is not None
                ),
                None,
            )
        )
        if turn_id is None or not translation_result.canonical_events:
            return translation_result
        stamped = tuple(
            replace(canonical, turn_id=turn_id) if canonical.turn_id is None else canonical
            for canonical in translation_result.canonical_events
        )
        return replace(translation_result, canonical_events=stamped)


class _ClaudeSourceTranslation(_ClaudeTranslatorState):
    """Translate Claude source records."""

    def _translate(self, raw_event: raw_events.RawEvent) -> raw_events.TranslationResult:
        if raw_event.source_type == "foreground_output":
            # OURS on both ends: engine/interpret/output_source.py wrote this
            # one, so it is decoded as the declared shape rather than read key
            # by key the way a harness's own records have to be.
            try:
                chunk, output_content = _decode_foreground_output(raw_event)
            except (StoredDocumentError, TypeError, ValueError) as error:
                message = "malformed foreground output"
                raise raw_events.TranslationError(message) from error
            progress = event_shell.ShellProgressed(
                chunk.shell_id,
                chunk.ordinal,
                chunk.stream,
                support.content(output_content.decode("utf-8", errors="replace")),
                outcomes.OutputMode.APPEND,
            )
            source_phase = "" if chunk.source_key is None else f":{chunk.source_key}"
            return raw_events.TranslationResult(
                (
                    support.event(
                        raw_event,
                        raw_event_builders.CanonicalEventDraft(
                            "shell",
                            str(chunk.shell_id),
                            f"progress{source_phase}:{chunk.ordinal}",
                            progress,
                        ),
                    ),
                ),
                domain_records.RecordedTranslationDecision.TRANSLATED,
            )
        try:
            return self._translate_json(raw_event)
        except UnicodeDecodeError as error:
            message = "malformed Claude Code record"
            raise raw_events.TranslationError(
                message,
                context=raw_event.source_position,
            ) from error

    def _translate_json(self, raw_event: raw_events.RawEvent) -> raw_events.TranslationResult:
        translator_by_source = {
            "launch": self._translate_launch,
            "otel": source_translators.translate_otel_source,
            "tasks": source_translators.translate_task_source,
            "task_list": source_translators.translate_task_list_source,
            "hook": self._translate_hook_source,
            "teammate_hook": self._translate_hook_source,
        }
        translator = translator_by_source.get(raw_event.source_type, self._translate_transcript_source)
        return translator(raw_event)

    def _translate_launch(self, raw_event: raw_events.RawEvent) -> raw_events.TranslationResult:
        launch = records.LaunchSelectionDocument.model_validate_json(raw_event.payload)
        events = messages.launch_selections(raw_event, launch, self._selections)
        if not events:
            return raw_events.TranslationResult(
                (),
                domain_records.RecordedTranslationDecision.IGNORED_NONSEMANTIC,
                "launch selects no model or effort",
            )
        return raw_events.TranslationResult(tuple(events), domain_records.RecordedTranslationDecision.TRANSLATED)

    def _translate_hook_source(self, raw_event: raw_events.RawEvent) -> raw_events.TranslationResult:
        hook = records.HookPayload.model_validate_json(raw_event.payload)
        events = hooks.translate_hook(
            raw_event,
            hook,
            self._toolcalls,
            self._turns,
            self._selections,
        )
        if (
            raw_event.parent_actor_id is None
            and hook.hook_event_name not in {"SessionStart", "SessionEnd"}
            and hook.transcript_path
            and hook.cwd
        ):
            events = [*messages.session_events(raw_event, hook), *events]
        if not events:
            return raw_events.TranslationResult(
                (),
                domain_records.RecordedTranslationDecision.IGNORED_NONSEMANTIC,
                "hook carries no canonical activity",
            )
        return raw_events.TranslationResult(tuple(events), domain_records.RecordedTranslationDecision.TRANSLATED)

    def _translate_transcript_source(self, raw_event: raw_events.RawEvent) -> raw_events.TranslationResult:
        transcript_document = records.TranscriptDocument.model_validate_json(raw_event.payload)
        transcript_events = transcript_start.build(raw_event, transcript_document)
        record = transcript.parse_line(raw_event.payload.decode("utf-8"))
        if record is None:
            return transcript_start.plumbing_result(transcript_events)
        if isinstance(record, transcript.BadTranscriptRecord):
            raise raw_events.TranslationError(
                MALFORMED_TRANSCRIPT_RECORD, context=raw_event.source_position,
            )
        record = self._compaction_record(raw_event, transcript_document, record)
        recovered_turn_id = _recovered_turn_id(raw_event, transcript_document, record)
        events = [
            *transcript_events.session_events,
            *transcript_events.metadata_events,
            *messages.translate_transcript(
                raw_event,
                transcript_document,
                record,
                messages.TranscriptSemantics(
                    self._toolcalls,
                    self._turns,
                    self._selections,
                    transcript_events.starts_child_actor,
                    recovered_turn_id,
                ),
            ),
        ]
        if not events:
            return raw_events.TranslationResult(
                (),
                domain_records.RecordedTranslationDecision.IGNORED_NONSEMANTIC,
                _nonsemantic_record_reason(record),
            )
        return raw_events.TranslationResult(tuple(events), domain_records.RecordedTranslationDecision.TRANSLATED)

    def _compaction_record(
        self,
        raw_event: raw_events.RawEvent,
        transcript_document: records.TranscriptDocument,
        record: transcript.TranscriptRecord,
    ) -> transcript.TranscriptRecord:
        compaction_key = raw_event.session_id, str(raw_event.actor_id)
        if isinstance(record, transcript.CompactTranscriptRecord):
            boundary_id = ClaudeCodeCompactionId(str(transcript_document.uuid or raw_event.source_position))
            self._pending_compactions[compaction_key] = (boundary_id, record.before_tokens)
            return record
        if not isinstance(record, transcript.CompactSummaryTranscriptRecord):
            return record
        pending = self._pending_compactions.pop(compaction_key, None)
        if pending is None:
            return record
        boundary_id, before_tokens = pending
        return replace(
            record,
            boundary_id=record.boundary_id or boundary_id,
            before_tokens=before_tokens,
        )


class ClaudeCanonicalTranslator(_ClaudeTurnStamping, _ClaudeSourceTranslation):
    """Translate Claude Code records to canonical events."""
