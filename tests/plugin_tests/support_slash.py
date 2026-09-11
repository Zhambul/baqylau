# Copyright (c) 2026 Zhambyl Yermagambet
"""Shared fixtures and builders for canonical harness tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness.impl.claude_code.canonical.translator import ClaudeCanonicalTranslator
from tests.harness_names import CLAUDE_CODE_HARNESS
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.support_events import raw_event

if TYPE_CHECKING:
    from domain import event_base
    from tests.plugin_tests.support_values import JsonValue

CLAUDE_SLASH_COMMAND_TURN = (
    (
        "caveat",
        (
            "<local-command-caveat>Caveat: The messages below were generated "
            "by the user while running local commands.</local-command-caveat>"
        ),
        True,
    ),
    (
        "envelope",
        (
            "<command-name>/model</command-name>\n            "
            "<command-message>model</command-message>\n            "
            "<command-args>opus</command-args>"
        ),
        False,
    ),
    (
        fixture.STDOUT,
        "<local-command-stdout>Set model to Opus 5 and saved as your default for new sessions</local-command-stdout>",
        False,
    ),
)


def _slash_turn_event(
    translator: ClaudeCanonicalTranslator,
    event_key: str,
    content: str,
    *,
    is_meta: bool,
) -> tuple[event_base.CanonicalEvent[event_base.EventPayload], ...]:
    document: dict[str, JsonValue] = {
        fixture.TYPE_FIELD: fixture.USER,
        fixture.UUID_FIELD: event_key,
        fixture.MESSAGE_FIELD: {fixture.CONTENT_FIELD: content},
    }
    if is_meta:
        document[fixture.IS_META] = True
    return translator.translate(
        raw_event(
            document,
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id=f"slash-{event_key}",
        ),
    ).canonical_events


def _slash_turn_events() -> list[event_base.CanonicalEvent[event_base.EventPayload]]:
    translator = ClaudeCanonicalTranslator()
    events: list[event_base.CanonicalEvent[event_base.EventPayload]] = []
    for event_key, content, is_meta in CLAUDE_SLASH_COMMAND_TURN:
        events.extend(_slash_turn_event(translator, event_key, content, is_meta=is_meta))
    return events
