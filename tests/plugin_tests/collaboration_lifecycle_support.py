# Copyright (c) 2026 Zhambyl Yermagambet
"""Support for collaboration lifecycle tests."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from domain import ids as domain_ids
from harness.impl.codex.canonical.translator import CodexCanonicalTranslator
from harness.models.raw_events import (
    RawEvent,
    TranslationResult,
)
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.collaboration_values import PRIMARY_CHILD_ACTOR
from tests.plugin_tests.support_events import raw_event

if TYPE_CHECKING:
    from pathlib import Path

    from tests.plugin_tests.support_values import JsonValue


CODEX_HARNESS = domain_ids.HarnessName("codex")


def translate_codex_rollout_from_path(
    translator: CodexCanonicalTranslator,
    rollout_path: Path,
    document: JsonValue,
    raw_event_id: str,
    source_position: str = fixture.TEN_TEXT,
) -> TranslationResult:
    """Translate a Codex record using the supplied rollout path and position.

    Returns:
        The translation result for the encoded test document.

    """
    event = replace(
        raw_event(
            document,
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id=raw_event_id,
            source_position=source_position,
        ),
        source_name=str(rollout_path),
    )
    return translator.translate(event)


def codex_child_rollout_event(
    rollout_path: Path,
    document: JsonValue,
    raw_event_id: str,
) -> RawEvent:
    """Build a raw Codex child record with fixed actor identities.

    Returns:
        The child-rollout event for the supplied file and document.

    """
    return replace(
        raw_event(
            document,
            harness=CODEX_HARNESS,
            source_type=fixture.CHILD_ROLLOUT_ID,
            raw_event_id=raw_event_id,
        ),
        source_name=str(rollout_path),
        actor_id=PRIMARY_CHILD_ACTOR,
        parent_actor_id=domain_ids.ActorId(fixture.LEAD_ONE_ID),
    )


@dataclass(frozen=True)
class CodexChildLifecycle:
    """Hold a child translator and its start and finish results."""

    translator: CodexCanonicalTranslator
    started: TranslationResult
    finished: TranslationResult


def codex_child_lifecycle(tmp_path: Path) -> CodexChildLifecycle:
    """Write child metadata and translate the child's start and finish records.

    Returns:
        The translator and both lifecycle translation results.

    """
    rollout_path = tmp_path / "child.jsonl"
    rollout_path.write_text(
        json.dumps(
            {
                fixture.TYPE_FIELD: fixture.SESSION_META_ID,
                fixture.PAYLOAD_FIELD: {
                    fixture.SOURCE: {
                        fixture.SUBAGENT: {
                            "thread_spawn": {
                                fixture.PARENT_THREAD_ID_FIELD: fixture.LEAD_ONE_ID,
                                fixture.AGENT_PATH_FIELD: "/root/bali_weather",
                            },
                        },
                    },
                },
            },
        )
        + "\n",
    )
    translator = CodexCanonicalTranslator()
    started = translator.translate(
        codex_child_rollout_event(
            rollout_path,
            {
                fixture.TYPE_FIELD: fixture.EVENT_MSG_ID,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.TASK_STARTED_ID,
                    fixture.TURN_ID_FIELD: fixture.CHILD_TURN_ID,
                    fixture.STARTED_AT: 1,
                },
            },
            fixture.CHILD_START_ID,
        ),
    )
    finished = translator.translate(
        codex_child_rollout_event(
            rollout_path,
            {
                fixture.TYPE_FIELD: fixture.EVENT_MSG_ID,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: "task_complete",
                    fixture.TURN_ID_FIELD: fixture.CHILD_TURN_ID,
                    "completed_at": 2,
                    "last_agent_message": "Rain, 24°C",
                },
            },
            "child-finish",
        ),
    )
    return CodexChildLifecycle(translator, started, finished)
