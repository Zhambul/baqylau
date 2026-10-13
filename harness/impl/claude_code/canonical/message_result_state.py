# Copyright (c) 2026 Zhambyl Yermagambet
"""Translate Claude Code result state."""

from harness.impl.claude_code.canonical import (
    message_models,
    message_result_content as result_content,
    message_result_dependencies as dependencies,
    toolcalls,
    transcript,
)
from harness.impl.claude_code.canonical.message_commands import prompt_turn
from harness.impl.claude_code.canonical.message_result_models import LoadedSkills, ResultInterruption, ResultPrompt
from harness.impl.claude_code.canonical.message_skills import _loaded_skill
from harness.models import raw_events


def results_events(
    source: message_models.TranscriptSource,
    record: transcript.ResultsTranscriptRecord,
    semantics: message_models.TranscriptSemantics,
) -> list[dependencies.event_base.CanonicalEvent[dependencies.event_base.EventPayload]]:
    """Translate result content and its effects on skills and turns.

    Returns:
        Skill, prompt, tool, and text events with interruption state applied.

    """
    loaded_skills = loaded_skill_results(source, record, semantics.tool_calls)
    interruption = result_interruption(source.raw_event, record, semantics.turns)
    prompt = result_prompt(source, record, semantics.turns)
    events = [
        *loaded_skills.events,
        *prompt.events,
        *result_content.tool_result_events(source, record, semantics.tool_calls),
        *result_content.result_text_events(
            source,
            record,
            loaded_skills.text_indexes,
            interruption.turn_id or prompt.turn_id,
        ),
    ]
    return result_content.finalize_result_interruption(source, record, interruption, events)


def loaded_skill_results(
    source: message_models.TranscriptSource,
    record: transcript.ResultsTranscriptRecord,
    tool_calls: toolcalls.ToolCallSemantics,
) -> LoadedSkills:
    """Find skill loads in synthetic result text.

    Returns:
        The skill events and the indexes of texts consumed by those events.

    """
    events: list[dependencies.event_base.CanonicalEvent[dependencies.event_base.EventPayload]] = []
    text_indexes: set[int] = set()
    if not record.meta:
        return LoadedSkills(events, text_indexes)
    for text_index, result_text in enumerate(record.texts):
        finished = loaded_skill_result(source.raw_event, result_text, tool_calls)
        if finished is not None:
            events.append(finished)
            text_indexes.add(text_index)
    return LoadedSkills(events, text_indexes)


def loaded_skill_result(
    raw_event: raw_events.RawEvent,
    result_text: str,
    tool_calls: toolcalls.ToolCallSemantics,
) -> dependencies.event_base.CanonicalEvent[dependencies.event_base.EventPayload] | None:
    """Translate one text that reports a skill load.

    Returns:
        The skill event, or None if no matching skill load is found.

    """
    loaded_skill = _loaded_skill(result_text)
    if loaded_skill is None:
        return None
    name, output = loaded_skill
    return tool_calls.skill_loaded(raw_event, name, output)


def result_interruption(
    raw_event: raw_events.RawEvent,
    record: transcript.ResultsTranscriptRecord,
    turns: dependencies.turns.TurnSemantics,
) -> ResultInterruption:
    """Read interruption state for a result record.

    Returns:
        The interrupted turn and its abort state, or no turn for a normal result.

    """
    if not record.interrupted:
        return ResultInterruption(None, abort_already_emitted=False)
    turn_id, abort_already_emitted = turns.interrupted(raw_event)
    return ResultInterruption(turn_id, abort_already_emitted)


def result_prompt(
    source: message_models.TranscriptSource,
    record: transcript.ResultsTranscriptRecord,
    turns: dependencies.turns.TurnSemantics,
) -> ResultPrompt:
    """Start a prompt turn for plain user result text.

    Returns:
        Turn events and the current turn identifier, or an empty result for other records.

    """
    if not record.texts or record.meta or record.blocks or record.interrupted:
        return ResultPrompt([], None)
    first_text_identity = f"{source.native_identity}:text:0"
    events = prompt_turn(source, turns, prompt_message_identity=first_text_identity)
    return ResultPrompt(events, turns.current(source.raw_event))
