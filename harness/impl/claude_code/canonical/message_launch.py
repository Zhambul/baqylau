# Copyright (c) 2026 Zhambyl Yermagambet
"""Translate Claude Code launch selections and outcomes."""

import hashlib

from domain import event_base, outcomes as domain_outcomes, work_state
from harness.impl.claude_code.canonical import (
    message_background_values as background_values,
    message_launch_dependencies as dependencies,
    message_subject_values as subject_values,
    records,
)
from harness.models import raw_event_builders, raw_events, selections


def _assignment_finish_phase(status: str, result: str | None) -> str:
    result_text = result or ""
    revision = hashlib.sha256(f"{status}\0{result_text}".encode()).hexdigest()
    return f"finished:{revision}"


def background_outcome(status: str | None) -> domain_outcomes.Outcome | None:
    """Return the background outcome.

    Returns:
        The background outcome.

    """
    if not status:
        return None
    normalized = str(status).strip().lower()
    return background_values.BACKGROUND_OUTCOMES.get(normalized, domain_outcomes.Outcome.UNKNOWN)


def launch_selections(
    raw_event: raw_events.RawEvent,
    launch: records.LaunchSelectionDocument,
    selection_semantics: selections.SelectionSemantics,
) -> list[event_base.CanonicalEvent[event_base.EventPayload]]:
    """Translate launch selections.

    Returns:
        The selection events.

    """
    subject_id = f"launch:{raw_event.source_position}"
    model_event = _launch_model_event(raw_event, launch, selection_semantics, subject_id)
    effort_event = _launch_effort_event(raw_event, launch, selection_semantics, subject_id)
    return [selected for selected in (model_event, effort_event) if selected is not None]


def _launch_model_event(
    raw_event: raw_events.RawEvent,
    launch: records.LaunchSelectionDocument,
    selection_semantics: selections.SelectionSemantics,
    subject_id: str,
) -> event_base.CanonicalEvent[event_base.EventPayload] | None:
    model_selection = launch.model
    if not isinstance(model_selection, str) or not model_selection:
        return None
    changed = selection_semantics.model(
        raw_event.session_id,
        raw_event.actor_id,
        dependencies.support.model_reference(dependencies.model.ClaudeCodeModel(model_selection)),
        work_state.ModelChangeReason.SELECTED,
        dependencies.model_names.selection_key(model_selection),
    )
    if changed is None:
        return None
    draft = raw_event_builders.CanonicalEventDraft(
        subject_values.MODEL_SUBJECT,
        subject_id,
        "selected",
        changed,
    )
    return dependencies.support.event(raw_event, draft)


def _launch_effort_event(
    raw_event: raw_events.RawEvent,
    launch: records.LaunchSelectionDocument,
    selection_semantics: selections.SelectionSemantics,
    subject_id: str,
) -> event_base.CanonicalEvent[event_base.EventPayload] | None:
    effort_selection = launch.effort
    if not isinstance(effort_selection, str) or not effort_selection:
        return None
    chosen = selection_semantics.effort(
        raw_event.session_id,
        raw_event.actor_id,
        effort_selection,
        work_state.EffortChangeReason.SELECTED,
    )
    if chosen is None:
        return None
    draft = raw_event_builders.CanonicalEventDraft("effort", subject_id, "selected", chosen)
    return dependencies.support.event(raw_event, draft)
