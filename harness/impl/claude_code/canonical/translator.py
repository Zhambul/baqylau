"""Claude Code canonical translation: dispatch by raw event source type."""

from __future__ import annotations

import base64
import json
from dataclasses import replace

from domain.codec import CanonicalCodecError, decode_document
from domain.events import ShellProgressed, TaskListChanged
from domain.ids import TaskId
from harness.contract import HarnessTranslator
from harness.impl.claude_code.canonical import transcript
from harness.impl.claude_code.canonical.hooks import translate_hook
from harness.impl.claude_code.canonical.messages import (
    launch_selections,
    session_events,
    task_event,
    transcript_metadata,
    translate_transcript,
)
from harness.impl.claude_code.canonical.otel import translate_otel
from harness.impl.claude_code.canonical.support import content, event
from harness.impl.claude_code.canonical.toolcalls import ToolCallSemantics
from harness.impl.claude_code.canonical.turns import TurnSemantics
from harness.models import RawEvent, TranslationError, TranslationResult, UnknownEvidence
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
        except UnknownEvidence as unknown:
            return TranslationResult((), "ignored_unknown", unknown.reason)

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
        try:
            text = raw_event.payload.decode("utf-8")
            document = json.loads(text)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise TranslationError("malformed Claude Code record", context=raw_event.source_position) from error
        if not isinstance(document, dict):
            raise TranslationError("Claude Code record is not an object", context=raw_event.source_position)
        if raw_event.source_type == "launch":
            events = launch_selections(raw_event, document, self._selections)
            if not events:
                return TranslationResult((), "ignored_nonsemantic", "launch selects no model or effort")
            return TranslationResult(tuple(events), "translated")
        if raw_event.source_type == "otel":
            events = translate_otel(raw_event, document)
            if not events:
                return TranslationResult((), "ignored_nonsemantic", "OTEL request carries no session usage")
            return TranslationResult(tuple(events), "translated")
        if raw_event.source_type == "foreground_output":
            # OURS on both ends: engine/interpret/output_source.py wrote this
            # one, so it is decoded as the declared shape rather than read key
            # by key the way a harness's own records have to be.
            try:
                chunk = decode_document(ShellOutputChunk, raw_event.payload)
                output_content = base64.b64decode(chunk.content_base64, validate=True)
            except (CanonicalCodecError, TypeError, ValueError) as error:
                raise TranslationError("malformed foreground output") from error
            progress = ShellProgressed(
                chunk.shell_id,
                chunk.ordinal,
                chunk.stream,
                content(output_content.decode("utf-8", errors="replace")),
                "append",
            )
            return TranslationResult(
                (event(
                    raw_event,
                    "shell",
                    str(chunk.shell_id),
                    f"progress:{chunk.ordinal}",
                    progress,
                ),),
                "translated",
            )
        if raw_event.source_type == "tasks":
            canonical = task_event(raw_event, document)
            return TranslationResult((canonical,), "translated")
        if raw_event.source_type == "task_list":
            task_ids = document.get("task_ids")
            list_id = document.get("list_id")
            if (
                not isinstance(list_id, str)
                or not isinstance(task_ids, list)
                or not all(isinstance(task_id, str) for task_id in task_ids)
            ):
                raise TranslationError("malformed Claude Code task list")
            payload = TaskListChanged(list_id, tuple(TaskId(task_id) for task_id in task_ids))
            canonical = event(raw_event, "task_list", raw_event.source_position, "changed", payload)
            return TranslationResult((canonical,), "translated")
        if raw_event.source_type in ("hook", "teammate_hook"):
            events = translate_hook(
                raw_event, document, self._toolcalls, self._turns, self._selections
            )
            if not events:
                return TranslationResult((), "ignored_nonsemantic", "hook carries no canonical activity")
            return TranslationResult(tuple(events), "translated")

        starts_lead_session = (
            raw_event.parent_actor_id is None
            and bool(document.get("cwd"))
            and document.get("parentUuid") is None
        )
        starts_child_actor = (
            raw_event.parent_actor_id is not None
            and raw_event.source_position == "0"
        )
        session_events_ = (
            session_events(raw_event, document)
            if starts_lead_session or starts_child_actor
            else []
        )
        metadata_events = transcript_metadata(raw_event, document)
        record = transcript.parse_line(text)
        if record is None:
            if session_events_ or metadata_events:
                return TranslationResult(tuple(session_events_ + metadata_events), "translated")
            return TranslationResult((), "ignored_nonsemantic", "transcript plumbing record")
        if record.get("kind") == "bad":
            raise TranslationError("malformed Claude Code transcript record", context=raw_event.source_position)
        transcript_events = translate_transcript(
            raw_event,
            document,
            record,
            self._toolcalls,
            self._turns,
            self._selections,
            actor_started=starts_child_actor,
        )
        events = session_events_ + metadata_events + transcript_events
        if not events:
            return TranslationResult((), "ignored_nonsemantic", f"nonsemantic Claude record {record['kind']!r}")
        return TranslationResult(tuple(events), "translated")
