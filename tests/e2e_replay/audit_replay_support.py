# Copyright (c) 2026 Zhambyl Yermagambet
"""Replay small, private-data-free records through the application."""

import json
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

from domain.ids import HarnessName
from engine.interpret.loop import Interpreter
from harness.models.raw_events import RawEvent
from tests.harness_names import CLAUDE_CODE_HARNESS, CODEX_HARNESS
from tests.plugin_tests.support_events import raw_event
from tests.provider_graph import ProviderGraph


def replay(filename: str, harness: HarnessName, source_type: str) -> ProviderGraph:
    """Store and process a recorded input shape.

    Returns:
        The separate application used by the test.

    """
    application = ProviderGraph()
    path = Path(__file__).parents[1] / "e2e" / "fixtures" / filename
    if harness == CLAUDE_CODE_HARNESS:
        application.raw_events.record((raw_event(
            {
                "hook_event_name": "SessionStart",
                "session_id": "session-one",
                "transcript_path": str(path),
                "cwd": str(path.parent),
            },
            harness=harness,
            source_type="hook",
            raw_event_id="audit-session-start",
        ),))
    for index, line in enumerate(path.read_text().splitlines()):
        application.raw_events.record((raw_event(
            json.loads(line),
            harness=harness,
            source_type=source_type,
            raw_event_id=f"audit-record-{index}",
            source_position=str(index),
        ),))
    application.provider("interpreter", Interpreter).tick()
    application.reaction_loop.tick()
    return application


def command_inputs(filename: str = "audit_command_batch.jsonl") -> Iterator[RawEvent]:
    """Read command records with their real byte positions.

    Yields:
        A recorded event that can recover earlier calls from the file.

    """
    path = Path(__file__).parents[1] / "e2e" / "fixtures" / filename
    with path.open("rb") as source:
        while line := source.readline():
            yield replace(raw_event(
                json.loads(line),
                harness=CODEX_HARNESS,
                source_type="rollout",
                raw_event_id=f"audit-batch-{source.tell()}",
                source_position=str(source.tell() - len(line)),
            ), source_name=str(path))
