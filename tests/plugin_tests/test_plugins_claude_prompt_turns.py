# Copyright (c) 2026 Zhambyl Yermagambet
"""A Claude hook that arrives before its prompt record names the same turn."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from domain import event_conversation, event_shell, event_work, ids as domain_ids
from harness.impl.claude_code.canonical.translator import ClaudeCanonicalTranslator
from tests.harness_names import CLAUDE_CODE_HARNESS
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.support_events import payloads, raw_event

if TYPE_CHECKING:
    from harness.models.raw_events import RawEvent
    from tests.plugin_tests.support_values import JsonValue

PROMPT_ID = "prompt-one"
PROMPT_ID_FIELD = "prompt_id"
AGENT_ID = "agent-one"


def _hook(document: dict[str, JsonValue], raw_event_id: str) -> RawEvent:
    return raw_event(
        {**document, PROMPT_ID_FIELD: PROMPT_ID},
        harness=CLAUDE_CODE_HARNESS,
        source_type=fixture.HOOK_SOURCE,
        raw_event_id=raw_event_id,
    )


def _prompt_record(text: str, raw_event_id: str) -> RawEvent:
    return raw_event(
        {
            fixture.TYPE_FIELD: fixture.USER,
            fixture.UUID_FIELD: f"{raw_event_id}-uuid",
            "promptId": PROMPT_ID,
            fixture.MESSAGE_FIELD: {fixture.ROLE_FIELD: fixture.USER, fixture.CONTENT_FIELD: text},
        },
        harness=CLAUDE_CODE_HARNESS,
        source_type=fixture.TRANSCRIPT_SOURCE,
        raw_event_id=raw_event_id,
    )


def _shell_hook() -> dict[str, JsonValue]:
    return {
        fixture.HOOK_EVENT_NAME_FIELD: fixture.PRE_TOOL_USE_HOOK,
        fixture.TOOL_USE_ID_FIELD: fixture.TOOL_ONE_ID,
        fixture.TOOL_NAME_FIELD: fixture.BASH_TOOL,
        fixture.TOOL_INPUT_FIELD: {fixture.COMMAND_FIELD: fixture.PRINT_DIRECTORY_COMMAND},
    }


def test_claude_hook_before_prompt_record() -> None:
    """Verify a question hook that arrives before the prompt record gets the turn of that prompt."""
    translator = ClaudeCanonicalTranslator()
    hook = translator.translate(
        _hook(
            {
                fixture.HOOK_EVENT_NAME_FIELD: fixture.PRE_TOOL_USE_HOOK,
                fixture.TOOL_USE_ID_FIELD: fixture.QUESTION_ONE,
                fixture.TOOL_NAME_FIELD: fixture.ASK_USER_QUESTION_TOOL,
                fixture.TOOL_INPUT_FIELD: {
                    fixture.QUESTIONS_FIELD: [
                        {
                            fixture.QUESTION_FIELD: "Which shade?",
                            fixture.OPTIONS_FIELD: [{fixture.LABEL_FIELD: "Blue"}],
                        },
                    ],
                },
            },
            "hook-ask",
        ),
    )
    prompt = translator.translate(_prompt_record("Ask about the shade", "prompt"))

    asked = payloads(hook, event_work.QuestionAsked)[0]
    started = payloads(prompt, event_conversation.TurnStarted)[0]
    assert asked.turn_id == started.turn_id == PROMPT_ID


def test_claude_lead_shell_hook_before_prompt() -> None:
    """Verify a lead shell hook that arrives before the prompt record gets the turn of that prompt."""
    translator = ClaudeCanonicalTranslator()
    hook = translator.translate(_hook(_shell_hook(), "hook-shell"))
    prompt = translator.translate(_prompt_record("Print the directory", "prompt"))

    shell = payloads(hook, event_shell.ShellStarted)[0]
    started = payloads(prompt, event_conversation.TurnStarted)[0]
    assert shell.turn_id == started.turn_id == PROMPT_ID


def test_claude_subagent_hook_gets_no_turn() -> None:
    """Verify a subagent hook gets no turn, because a subagent fact has no turn of its own."""
    translator = ClaudeCanonicalTranslator()
    subagent_hook = replace(
        _hook({**_shell_hook(), "agent_id": AGENT_ID}, "subagent-hook"),
        actor_id=domain_ids.ActorId(AGENT_ID),
        parent_actor_id=domain_ids.ActorId(fixture.SESSION_ONE_LEAD_ID),
    )
    hook = translator.translate(subagent_hook)

    assert payloads(hook, event_shell.ShellStarted)[0].turn_id is None


def test_claude_stop_before_prompt_record() -> None:
    """Verify a Stop hook before the prompt record does not finish the prompt turn before it starts."""
    translator = ClaudeCanonicalTranslator()
    stop = translator.translate(_hook({fixture.HOOK_EVENT_NAME_FIELD: "Stop"}, "hook-stop"))

    assert [finished.turn_id for finished in payloads(stop, event_conversation.TurnFinished)] == [None]
