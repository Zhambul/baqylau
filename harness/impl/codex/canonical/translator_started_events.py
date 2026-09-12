# Copyright (c) 2026 Zhambyl Yermagambet
"""Split Codex canonical translation."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from harness.impl.codex.canonical import (
    translator_call_recovery,
    translator_dependencies as dependencies,
)
from harness.impl.codex.canonical.translator_core_values import (
    SHELL_SUBJECT,
    SKILL_SUBJECT,
    STARTED_PHASE,
)

if TYPE_CHECKING:
    from harness.impl.codex.canonical.translator_state_models import RecordSource


def started_skill_event(
    record_source: RecordSource,
    record: dependencies.record_canonical_namespaces.record_tool_records.ExecRecord,
    call_id: dependencies.translator_id_dependencies.ids_session_types.CodexCallId,
    skill_name: str,
) -> dependencies.translator_type_dependencies.event_base.CanonicalEvent[
    dependencies.translator_type_dependencies.event_base.EventPayload
]:
    """Build a skill-start event from a native execution call.

    Returns:
        The skill event with its source time and optional turn identifier.

    """
    skill_id = dependencies.translator_id_dependencies.ids_work.skill_id_from_codex(
        dependencies.translator_id_dependencies.ids_work_types.CodexSkillId(call_id),
    )
    return dependencies.translator_codex_dependencies.support.event(
        record_source.raw_event,
        dependencies.translator_service_dependencies.CanonicalEventDraft(
            SKILL_SUBJECT,
            str(skill_id),
            STARTED_PHASE,
            dependencies.translator_domain_events.event_resource.SkillStarted(skill_id, skill_name, None),
            turn_id=dependencies.translator_id_dependencies.ids_conversation.turn_id_from_codex(
                dependencies.translator_id_dependencies.ids_conversation_types.CodexTurnId(record.turn),
            )
            if record.turn
            else None,
            occurred_at=record_source.occurred_at,
        ),
    )


def started_shell_event(
    record_source: RecordSource,
    record: dependencies.record_canonical_namespaces.record_tool_records.ExecRecord,
    call_id: dependencies.translator_id_dependencies.ids_session_types.CodexCallId,
) -> dependencies.translator_type_dependencies.event_base.CanonicalEvent[
    dependencies.translator_type_dependencies.event_base.EventPayload
]:
    """Build a foreground shell-start event from a native call.

    Returns:
        The shell event with its command and source identities.

    """
    shell_id = dependencies.translator_id_dependencies.ids_session.shell_id_from_codex_call(call_id)
    payload = dependencies.translator_domain_events.event_shell.ShellStarted(
        shell_id,
        dependencies.translator_codex_dependencies.support.content(record.cmd),
        dependencies.translator_domain_values.outcomes.ExecutionMode.FOREGROUND,
        None,
    )
    return dependencies.translator_codex_dependencies.support.event(
        record_source.raw_event,
        dependencies.translator_service_dependencies.CanonicalEventDraft(
            SHELL_SUBJECT,
            str(shell_id),
            STARTED_PHASE,
            payload,
            turn_id=dependencies.translator_id_dependencies.ids_conversation.turn_id_from_codex(record.turn)
            if record.turn
            else None,
            occurred_at=record_source.occurred_at,
        ),
    )


def stdin_input_event(
    record_source: RecordSource,
    record: dependencies.record_canonical_namespaces.record_tool_records.StdinRecord,
    call_id: dependencies.translator_id_dependencies.ids_session_types.CodexCallId,
    shell_id: dependencies.translator_type_dependencies.ids.ShellId,
) -> dependencies.translator_type_dependencies.event_base.CanonicalEvent[
    dependencies.translator_type_dependencies.event_base.EventPayload
]:
    """Build an input event for an existing shell.

    Returns:
        The supplied text as a canonical shell input event.

    """
    payload = dependencies.translator_domain_events.event_shell.ShellInputProvided(
        shell_id=shell_id,
        content=dependencies.translator_codex_dependencies.support.content(record.text),
        closed=False,
    )
    return dependencies.translator_codex_dependencies.support.event(
        record_source.raw_event,
        dependencies.translator_service_dependencies.CanonicalEventDraft(
            SHELL_SUBJECT,
            str(shell_id),
            f"input:{call_id}",
            payload,
            occurred_at=record_source.occurred_at,
        ),
    )


def collaboration_call_from_line(
    line: bytes,
    call_id: dependencies.translator_id_dependencies.ids_session_types.CodexCallId,
) -> (
    tuple[
        str,
        dependencies.record_payload_namespaces.record_collaboration_registry.CollaborationArguments,
    ]
    | None
):
    """Find a collaboration call by its native identifier.

    Returns:
        The call name and arguments, or None for an invalid or different record.

    """
    try:
        record = dependencies.translator_codex_dependencies.rollout.parse_line(line.decode())
    except (UnicodeDecodeError, dependencies.translator_service_dependencies.ValidationError):
        return None
    if (
        not isinstance(
            record,
            dependencies.record_canonical_namespaces.record_actor_records.CollaborationCallRecord,
        )
        or record.call_id != call_id
    ):
        return None
    return record.name, record.args


def call_from_line(
    line: bytes,
    call_id: dependencies.translator_id_dependencies.ids_session_types.CodexCallId,
) -> (
    dependencies.record_canonical_namespaces.record_tool_records.ExecRecord
    | dependencies.record_canonical_namespaces.record_tool_records.ToolRecord
    | dependencies.record_canonical_namespaces.record_actor_records.ToolBatchRecord
    | dependencies.record_canonical_namespaces.record_interaction_records.AskRecord
    | None
):
    """Find a tool call by its native identifier.

    Returns:
        The call record, or None for an invalid or different record.

    """
    try:
        record = translator_call_recovery.requested_call(line, call_id)
    except (UnicodeDecodeError, dependencies.translator_service_dependencies.ValidationError):
        return None
    if isinstance(record, dependencies.record_canonical_namespaces.record_actor_records.ToolBatchRecord):
        return translator_call_recovery.batch_call(record, call_id)
    if (
        not isinstance(
            record,
            (
                dependencies.record_canonical_namespaces.record_tool_records.ExecRecord,
                dependencies.record_canonical_namespaces.record_tool_records.ToolRecord,
                dependencies.record_canonical_namespaces.record_actor_records.ToolBatchRecord,
                dependencies.record_canonical_namespaces.record_interaction_records.AskRecord,
            ),
        )
        or record.call_id != call_id
    ):
        return None
    return record


def source_key(raw_event: dependencies.translator_service_dependencies.raw_events.RawEvent) -> str:
    """Resolve the source path used for event correlation.

    Returns:
        The source path with symbolic links resolved.

    """
    return os.path.realpath(raw_event.source_name)


def translate_title_source(
    raw_event: dependencies.translator_service_dependencies.raw_events.RawEvent,
) -> dependencies.translator_service_dependencies.raw_events.TranslationResult:
    """Translate a stored native title observation.

    Returns:
        The session title change and its translation decision.

    Raises:
        TranslationError: If the stored observation cannot be decoded.

    """
    try:
        title_observation = dependencies.translator_service_dependencies.decode_document(
            dependencies.translator_service_dependencies.NativeTitleObservation,
            raw_event.payload,
        )
    except dependencies.translator_service_dependencies.StoredDocumentError as error:
        msg = "malformed Codex title observation"
        raise dependencies.translator_service_dependencies.raw_events.TranslationError(
            msg,
            context=raw_event.source_position,
        ) from error
    changed = dependencies.translator_domain_events.event_session.SessionTitleChanged(
        title_observation.title,
        dependencies.translator_domain_values.work_state.TitleOrigin(title_observation.origin),
    )
    return dependencies.translator_service_dependencies.raw_events.TranslationResult(
        (
            dependencies.translator_codex_dependencies.support.event(
                raw_event,
                dependencies.translator_service_dependencies.CanonicalEventDraft(
                    "session",
                    str(raw_event.session_id),
                    f"title:{title_observation.origin}:{raw_event.source_position}",
                    changed,
                ),
            ),
        ),
        dependencies.translator_domain_values.records.RecordedTranslationDecision.TRANSLATED,
    )
