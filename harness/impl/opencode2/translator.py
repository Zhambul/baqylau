# Copyright (c) 2026 Zhambyl Yermagambet
"""Translate recorded OpenCode2 events without a memory-only join."""

from dataclasses import replace

from domain import event_actor, event_resource, event_session, ids, messaging, records
from domain.event_base import CanonicalEvent, EventPayload
from harness.contract import HarnessTranslator
from harness.impl.opencode2 import output, payloads as native_payloads
from harness.impl.opencode2.records import NativeRecord
from harness.models import raw_event_builders as builders, translation_stages as stages
from harness.models.raw_events import RawEvent, TranslationResult


def _starts(raw_event: RawEvent, native_record: NativeRecord) -> tuple[CanonicalEvent[EventPayload], ...]:
    session = native_record.session
    if raw_event.parent_actor_id is not None:
        return (builders.canonical_event(raw_event, builders.CanonicalEventDraft(
            "actor", str(raw_event.actor_id), "started",
            event_actor.ActorStarted(session.title or "subagent", messaging.ActorRole.CHILD),
        )),)
    return builders.session_run_started_events(
        replace(raw_event, source_position=str(raw_event.session_id)),
        event_session.SessionStarted(
            session.location.directory, raw_event.source_name, None, session.title, None, None, None,
        ),
        event_actor.ActorStarted("opencode2", messaging.ActorRole.LEAD),
    )


def _session_matches(native_record: NativeRecord) -> bool:
    session_id = native_record.event.details.session_id
    if session_id is not None:
        return session_id == native_record.session.id
    return native_record.event.type == "shell.exited" and native_record.shell is not None


def _phase(event_payload: EventPayload) -> str:
    if isinstance(event_payload, event_resource.FileAccessed):
        return f"FileAccessed:{event_payload.action}:{event_payload.path}"
    return type(event_payload).__name__


class OpenCodeTranslator(HarnessTranslator):
    """Translate each stored native_record independently."""

    def translate(
        self, raw_event: RawEvent, *, translation_stage: stages.TranslationStage = stages.TranslationStage.COMPLETE,
    ) -> TranslationResult:
        """Translate one native native_record.

        Returns:
            Validated session and conversation facts.

        Raises:
            ValueError: If the event names another session.

        """
        if raw_event.source_type == "foreground_output":
            if translation_stage == stages.TranslationStage.LIFECYCLE:
                return stages.empty_result(translation_stage)
            return output.translate(raw_event)
        native_record = NativeRecord.model_validate_json(raw_event.payload)
        if (
            (native_record.root_id or native_record.session.id) != raw_event.session_id
            or not _session_matches(native_record)
        ):
            message = "OpenCode2 event belongs to another session"
            raise ValueError(message)
        if raw_event.source_type == "hook" or translation_stage == stages.TranslationStage.LIFECYCLE:
            return stages.select_result(TranslationResult(
                _starts(raw_event, native_record), records.RecordedTranslationDecision.TRANSLATED,
            ), translation_stage)
        events = tuple(
            builders.canonical_event(raw_event, builders.CanonicalEventDraft(
                "native", native_record.event.id, _phase(payload), payload,
                turn_id=ids.TurnId(native_record.turn_id) if native_record.turn_id else None,
                occurred_at=None if native_record.event.created is None else native_record.event.created / 1000,
            ))
            for payload in native_payloads.payloads(native_record)
        )
        return stages.select_result(TranslationResult(
            (*_starts(raw_event, native_record), *events),
            records.RecordedTranslationDecision.TRANSLATED,
        ), translation_stage)

    def release_session(self, session_id: ids.SessionId) -> None:
        """No session state is held by this translator."""
