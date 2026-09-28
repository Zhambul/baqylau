# Copyright (c) 2026 Zhambyl Yermagambet
"""A refused compaction finish does not keep a session compacting.

Session 01a0c77b ran Codex 0.156, whose `compacted` records had a new field.
The strict record model refused them, so five compactions started and none
finished, and the session showed "compacting" for days. This replay has the
same shape: a PreCompact hook, a refused `compacted` record, and a turn end.
"""

import json
from pathlib import Path
from typing import TYPE_CHECKING

from domain.ids import SessionId
from engine.interpret.loop import Interpreter
from harness.models.raw_events import RawEvent
from tests import http_test_assets, http_test_controls, http_test_pane_models
from tests.plugin_tests import translation_stage_fixture as fixture
from tests.provider_graph import ProviderGraph

if TYPE_CHECKING:
    from pydantic import JsonValue

TURN = "turn-compact"
# A field that no Codex version has sent: the strict model refuses the record, as it refused 0.156's fields.
REFUSED_FINISH = '{"type": "compacted", "payload": {"message": "summary", "replacement_history": [], "future": 1}}'


# Harness limit: codex only. Only Codex reports a compaction finish in a separate record that drift can refuse.
def test_a_refused_finish_ends_with_the_turn(tmp_path: Path) -> None:
    """The finish record fails its own translation, and the turn end still ends the compaction."""
    application = ProviderGraph()
    application.raw_events.record(_records(tmp_path))
    application.provider("interpreter", Interpreter).tick()
    application.reaction_loop.tick()

    assert _verdicts(application, "codex-2") == ["translation_failed"]
    contexts = _actor_contexts(application)
    assert contexts
    assert not any(context["compacting"] for context in contexts)


def _verdicts(application: ProviderGraph, raw_event_id: str) -> list[str]:
    audits = http_test_pane_models.raw_event_audits(application).audits_for_session(SessionId("session-one"))
    interpretations = [audit.interpretation for audit in audits if audit.raw_event.raw_event_id == raw_event_id]
    return [interpretation.decision for interpretation in interpretations if interpretation]


def _actor_contexts(application: ProviderGraph) -> list[dict[str, object]]:
    with http_test_assets.running_server(application) as server:
        document = json.loads(http_test_controls.get(server, "/sessionData/session-one").body.raw)
    return [actor["context"] for actor in document["actors"]]


def _records(tmp_path: Path) -> tuple[RawEvent, ...]:
    return (
        _event_message("task_started", "1"),
        fixture.codex_compaction(tmp_path),
        fixture.codex_record(json.loads(REFUSED_FINISH), position="2"),
        _event_message("task_complete", "3"),
    )


def _event_message(kind: str, position: str) -> RawEvent:
    payload: dict[str, JsonValue] = {"type": kind, "turn_id": TURN}
    return fixture.codex_record({"type": "event_msg", "payload": payload}, position=position)
