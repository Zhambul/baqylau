# Copyright (c) 2026 Zhambyl Yermagambet
"""Name a Claude turn by its prompt, so a hook and the transcript agree.

Claude Code puts the same prompt id on a transcript prompt record and on each
hook of that prompt. A hook can arrive before the transcript has the prompt
record. With one name from one value, a hook fact and the transcript put the
same turn on a fact in any order.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from domain import event_conversation
from harness.impl.claude_code.canonical import records
from harness.impl.claude_code.ids import ClaudeCodePromptId, ClaudeCodeTurnId, turn_id_from_claude_code

if TYPE_CHECKING:
    from domain.event_base import CanonicalEvent, EventPayload
    from domain.ids import TurnId
    from harness.models.raw_events import RawEvent, TranslationResult

TURN_LIFECYCLE_PAYLOADS = (
    event_conversation.TurnStarted,
    event_conversation.TurnFinished,
    event_conversation.TurnAborted,
)


def prompt_turn_id(raw_event: RawEvent, prompt_id: ClaudeCodePromptId) -> TurnId:
    """Name the turn of one native prompt.

    A subagent's records carry the prompt id of its parent, so the name of a
    subagent turn also contains the agent.

    Returns:
        The turn identifier.

    """
    if raw_event.parent_actor_id is None:
        return turn_id_from_claude_code(ClaudeCodeTurnId(prompt_id))
    return turn_id_from_claude_code(ClaudeCodeTurnId(f"{raw_event.actor_id}:{prompt_id}"))


def hook_prompt_turn(raw_event: RawEvent) -> TurnId | None:
    """Name the turn of the prompt that a lead hook reports.

    A subagent fact has no turn of its own, so a subagent hook gets none.

    Returns:
        The turn identifier, or None for a record that is not a lead hook or has no prompt id.

    """
    if raw_event.source_type not in {"hook", "teammate_hook"} or raw_event.parent_actor_id is not None:
        return None
    prompt_id = records.HookPayload.model_validate_json(raw_event.payload).prompt_id
    return None if prompt_id is None else prompt_turn_id(raw_event, prompt_id)


def stamped_by_prompt(translation_result: TranslationResult, prompt_turn: TurnId | None) -> TranslationResult:
    """Put the prompt turn on a hook fact that arrived before its turn opened.

    A turn start, finish, or abort is left alone: only the open turn decides
    those, so a finish never comes before its start.

    Returns:
        The translation result.

    """
    if prompt_turn is None or not translation_result.canonical_events:
        return translation_result
    stamped = tuple(
        replace(canonical, turn_id=prompt_turn) if _takes_prompt_turn(canonical) else canonical
        for canonical in translation_result.canonical_events
    )
    return replace(translation_result, canonical_events=stamped)


def _takes_prompt_turn(canonical: CanonicalEvent[EventPayload]) -> bool:
    return canonical.turn_id is None and not isinstance(canonical.payload, TURN_LIFECYCLE_PAYLOADS)
