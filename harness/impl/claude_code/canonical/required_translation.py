# Copyright (c) 2026 Zhambyl Yermagambet
"""Read required session state without processing tools, turns, or selections."""

from domain import event_base, records as domain_records
from harness.impl.claude_code.canonical import hook_lifecycle, messages, records
from harness.models import raw_events, translation_stages as stages


def translate(raw_event: raw_events.RawEvent) -> raw_events.TranslationResult:
    """Decode only the native fields used to start or finish a session.

    Returns:
        Required facts from the original input, with no activity-state changes.

    """
    if raw_event.source_type in {"foreground_output", "launch", "otel", "tasks", "task_list"}:
        return stages.empty_result(stages.TranslationStage.LIFECYCLE)
    events = (
        _hook(raw_event) if raw_event.source_type in {"hook", "teammate_hook"} else _transcript(raw_event)
    )
    if not events:
        return stages.empty_result(stages.TranslationStage.LIFECYCLE)
    return stages.select_result(
        raw_events.TranslationResult(tuple(events), domain_records.RecordedTranslationDecision.TRANSLATED),
        stages.TranslationStage.LIFECYCLE,
    )


def _hook(raw_event: raw_events.RawEvent) -> list[event_base.CanonicalEvent[event_base.EventPayload]]:
    hook = records.HookPayload.model_validate_json(raw_event.payload)
    if hook.hook_event_name == "SessionEnd":
        return hook_lifecycle.session_end_events(raw_event, hook)
    if raw_event.parent_actor_id is not None:
        return []
    if hook.hook_event_name == "SessionStart" or (hook.transcript_path and hook.cwd):
        return messages.session_events(raw_event, hook)
    return []


def _transcript(raw_event: raw_events.RawEvent) -> list[event_base.CanonicalEvent[event_base.EventPayload]]:
    if raw_event.parent_actor_id is not None:
        return []
    document = records.TranscriptDocument.model_validate_json(raw_event.payload)
    if document.cwd and document.parent_uuid is None:
        return messages.session_events(raw_event, document)
    return []
