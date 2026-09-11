# Copyright (c) 2026 Zhambyl Yermagambet
"""Claude planning tool tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

from domain.event_conversation import TurnFinished, TurnStarted
from domain.event_work import (
    PlanProposed,
    PlanResolved,
)
from domain.ids import (
    TurnId,
)
from harness.impl.claude_code.canonical.translator import ClaudeCanonicalTranslator
from tests.harness_names import CLAUDE_CODE_HARNESS
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.support_events import payloads, raw_event
from tests.plugin_tests.support_values import JsonValue, text_of

if TYPE_CHECKING:
    from harness.models.raw_events import (
        TranslationResult,
    )

type CodexTranslationDecisionCase = tuple[dict[str, JsonValue], str]


def test_claude_plan_is_proposed() -> None:
    """Verify claude plan is proposed and then resolved with what the person decided."""
    translator = ClaudeCanonicalTranslator()
    proposed = translator.translate(
        raw_event(
            {
                fixture.HOOK_EVENT_NAME_FIELD: fixture.PRE_TOOL_USE_HOOK,
                fixture.TOOL_USE_ID_FIELD: fixture.PLAN_ONE,
                fixture.TOOL_NAME_FIELD: "ExitPlanMode",
                fixture.TOOL_INPUT_FIELD: {"plan": "1. Read it\n2. Change it"},
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id="plan-proposed",
        ),
    )
    changes_requested = translator.translate(
        raw_event(
            {
                fixture.HOOK_EVENT_NAME_FIELD: "PostToolUseFailure",
                fixture.TOOL_USE_ID_FIELD: fixture.PLAN_ONE,
                fixture.TOOL_NAME_FIELD: "ExitPlanMode",
                fixture.TOOL_INPUT_FIELD: {},
                fixture.TOOL_RESPONSE_FIELD: (
                    "The user doesn't want to proceed. To tell you how to proceed, the user said:\nstart with the tests"
                ),
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id="plan-resolved",
        ),
    )

    assert text_of(payloads(proposed, PlanProposed)[0].payload.plan) == "1. Read it\n2. Change it"
    resolved = payloads(changes_requested, PlanResolved)[0].payload
    assert resolved.attention_id == fixture.PLAN_ONE
    assert resolved.state == "changes_requested"
    assert resolved.feedback == "start with the tests"
    assert resolved.edited is False


def test_claude_enter_plan_mode_is_deliberate() -> None:
    """Verify claude enter plan mode is a deliberate ignore not drift.

    `EnterPlanMode` is `ExitPlanMode`'s sibling, but it carries nothing to
        show: measured against the real corpus, every call sends no arguments and
        every result is the one fixed instruction Claude Code always sends back
        ("Entered plan mode. You should now focus on..."). Nothing there is
        session-specific, so it must land as a named, deliberate ignore — not
        `ignored_unknown`, which means a shape nobody has decided about.
    """
    translator = ClaudeCanonicalTranslator()
    started = translator.translate(
        raw_event(
            {
                fixture.HOOK_EVENT_NAME_FIELD: fixture.PRE_TOOL_USE_HOOK,
                fixture.TOOL_USE_ID_FIELD: "enter-plan-one",
                fixture.TOOL_NAME_FIELD: "EnterPlanMode",
                fixture.TOOL_INPUT_FIELD: {},
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id="enter-plan-started",
        ),
    )
    finished = translator.translate(
        raw_event(
            {
                fixture.HOOK_EVENT_NAME_FIELD: fixture.POST_TOOL_USE_HOOK,
                fixture.TOOL_USE_ID_FIELD: "enter-plan-one",
                fixture.TOOL_NAME_FIELD: "EnterPlanMode",
                fixture.TOOL_INPUT_FIELD: {},
                fixture.TOOL_RESPONSE_FIELD: (
                    "Entered plan mode. You should now focus on exploring the "
                    "codebase and designing an implementation approach."
                ),
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id="enter-plan-finished",
        ),
    )

    assert started.decision == fixture.IGNORED_NONSEMANTIC
    assert finished.decision == fixture.IGNORED_NONSEMANTIC


def test_claude_turn_opens_on_prompt_and_closes() -> None:
    """Verify claude turn opens on the prompt and closes on the stop hook.

    Claude Code emits no turn boundary of its own — its Stop hook says a turn
        ended and nothing says one began — so the prompt opens the turn and every
        fact until the Stop rides it. Without this the feed has nothing to group by.
    """
    translator = ClaudeCanonicalTranslator()
    prompt = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.USER,
                fixture.UUID_FIELD: fixture.PROMPT_ONE_ID,
                fixture.MESSAGE_FIELD: {fixture.CONTENT_FIELD: "fix it"},
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id=fixture.PROMPT_KIND,
        ),
    )
    assert translator.translate(
        raw_event(
            {
                fixture.HOOK_EVENT_NAME_FIELD: fixture.PRE_TOOL_USE_HOOK,
                fixture.TOOL_USE_ID_FIELD: fixture.TOOL_ONE_ID,
                fixture.TOOL_NAME_FIELD: fixture.BASH_TOOL,
                fixture.TOOL_INPUT_FIELD: {fixture.COMMAND_FIELD: fixture.PRINT_DIRECTORY_COMMAND},
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id=fixture.TOOL_KIND,
        ),
    ).canonical_events[0].turn_id == TurnId(fixture.PROMPT_ONE_ID)
    injected = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.USER,
                fixture.UUID_FIELD: "prompt-two",
                fixture.MESSAGE_FIELD: {fixture.CONTENT_FIELD: "and also this"},
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="injection",
        ),
    )
    stop = translator.translate(
        raw_event(
            {fixture.HOOK_EVENT_NAME_FIELD: fixture.STOP_HOOK, fixture.HOOK_EVENT_ID_FIELD: "stop-one"},
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id="stop",
        ),
    )
    after = translator.translate(
        raw_event(
            {
                fixture.HOOK_EVENT_NAME_FIELD: fixture.PRE_TOOL_USE_HOOK,
                fixture.TOOL_USE_ID_FIELD: "tool-two",
                fixture.TOOL_NAME_FIELD: fixture.BASH_TOOL,
                fixture.TOOL_INPUT_FIELD: {fixture.COMMAND_FIELD: "ls"},
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id="after",
        ),
    )

    _assert_claude_turn_boundaries(prompt, injected, stop, after)


def _assert_claude_turn_boundaries(
    prompt: TranslationResult,
    injected: TranslationResult,
    stop: TranslationResult,
    after: TranslationResult,
) -> None:
    """Verify the Claude prompt, injection, and stop turn boundaries."""
    assert payloads(prompt, TurnStarted)[0].payload.prompt_message_id == fixture.PROMPT_ONE_ID
    # An injection is part of the turn it interrupted, not a turn of its own.
    assert payloads(injected, TurnStarted) == []
    assert injected.canonical_events[0].turn_id == TurnId(fixture.PROMPT_ONE_ID)
    assert payloads(stop, TurnFinished)[0].turn_id == TurnId(fixture.PROMPT_ONE_ID)
    # Nothing after the Stop belongs to the turn it closed.
    assert after.canonical_events[0].turn_id is None
