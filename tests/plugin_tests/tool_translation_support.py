# Copyright (c) 2026 Zhambyl Yermagambet
"""Support for tool translation tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness.impl.codex.canonical.translator import CodexCanonicalTranslator
from tests.harness_names import CLAUDE_CODE_HARNESS, CODEX_HARNESS
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.support_events import raw_event
from tests.plugin_tests.support_values import JsonValue

if TYPE_CHECKING:
    from domain.records import RecordedTranslationDecision
    from harness.impl.claude_code.canonical.translator import ClaudeCanonicalTranslator
    from harness.models.raw_events import (
        TranslationResult,
    )

type CodexTranslationDecisionCase = tuple[dict[str, JsonValue], str]


def translate_claude_hook(
    translator: ClaudeCanonicalTranslator,
    document: JsonValue,
    raw_event_id: str,
) -> TranslationResult:
    """Translate a test document as a Claude hook.

    Returns:
        The result from the supplied translator.

    """
    return translator.translate(
        raw_event(
            document,
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id=raw_event_id,
        ),
    )


def codex_translation_decision(
    document: dict[str, JsonValue],
) -> RecordedTranslationDecision:
    """Translate a Codex rollout document and read its decision.

    Returns:
        The recorded translation decision for the document.

    """
    return (
        CodexCanonicalTranslator()
        .translate(
            raw_event(
                document,
                harness=CODEX_HARNESS,
                source_type=fixture.ROLLOUT_SOURCE,
                raw_event_id=f"codex-{document.get(fixture.TYPE_FIELD)}-{id(document)}",
            ),
        )
        .decision
    )


def completed_codex_item(completed_item: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """Wrap an item in a native Codex completion record.

    Returns:
        The event-message document for the fixed test turn.

    """
    return {
        fixture.TYPE_FIELD: fixture.EVENT_MSG_ID,
        fixture.PAYLOAD_FIELD: {
            fixture.TYPE_FIELD: fixture.ITEM_COMPLETED,
            fixture.TURN_ID_FIELD: fixture.TURN_ONE_ID,
            fixture.ITEM_FIELD: completed_item,
        },
    }
