# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide test foundation registry."""

from __future__ import annotations

import pytest

from tests import (
    canonical_foundation_components as foundation_components,
    foundation_dependencies,
    foundation_test_events,
    foundation_test_interpreter,
    foundation_test_reactions,
    foundation_test_sources,
)
from tests.harness_names import CLAUDE_CODE_HARNESS, CODEX_HARNESS

IGNORED_TRANSLATION = foundation_components.raw_events.TranslationResult(
    (), foundation_dependencies.domain.domain_records.RecordedTranslationDecision.IGNORED_NONSEMANTIC,
)
SESSION_ID_TEXT = "session-one"
OWN_PROCESS_NAME = foundation_dependencies.standard.Path(
    foundation_dependencies.standard.subprocess.run(
        ["ps", "-o", "comm=", "-p", str(foundation_dependencies.standard.os.getpid())],
        capture_output=True,
        check=False,
        text=True,
    ).stdout.strip(),
).name
PRIMARY_SESSION = foundation_dependencies.domain.domain_ids.SessionId(SESSION_ID_TEXT)
ASSIGNMENT_ID_TEXT = "assignment-one"
CODEC_PROCESS_ID = 1234
UNSUPPORTED_SCHEMA_VERSION = 999


@pytest.mark.usefixtures("database_path", "tmp_path")
def test_sessions_carry_their_plugin_only_when(
    database: foundation_dependencies.repository.SqliteDatabase,
    ignored_plugin: foundation_dependencies.engine.harness_contract.HarnessPlugin,
) -> None:
    """Verify sessions carry their plugin only when harnesses are attached."""
    harnesses = foundation_dependencies.engine.harness_registry.HarnessRegistry()
    plugin = ignored_plugin
    harnesses.register(plugin)
    foundation_dependencies.repository.SqliteSessionRepository(database).save(
        CODEX_HARNESS, foundation_test_events.example_session(),
    )
    recorder_side = foundation_dependencies.repository.SqliteSessionRepository(database).find(PRIMARY_SESSION)
    server_side = foundation_dependencies.repository.SqliteSessionRepository(database, harnesses).find(PRIMARY_SESSION)
    assert recorder_side is not None
    assert server_side is not None
    assert recorder_side.plugin is None and server_side.plugin is plugin
    assert recorder_side == server_side
    assert (
        foundation_dependencies.repository.SqliteSessionRepository(database).find(
            foundation_dependencies.domain.domain_ids.SessionId("missing"),
        )
        is None
    )


def test_harness_registry_requires_one_explicit(
    ignored_plugin: foundation_dependencies.engine.harness_contract.HarnessPlugin,
) -> None:
    """Verify harness registry requires one explicit default when launchers exist."""
    registry = foundation_dependencies.engine.harness_registry.HarnessRegistry()
    registry.register(
        foundation_dependencies.standard.replace(ignored_plugin, launcher=foundation_test_sources.NullLauncher()),
    )
    with foundation_dependencies.standard.pytest.raises(
        foundation_dependencies.engine.harness_registry.HarnessRegistryError, match="no launchable harness",
    ):
        registry.validate()


def test_harness_registry_rejects_multiple_launch() -> None:
    """Verify harness registry rejects multiple launch defaults."""
    registry = foundation_dependencies.engine.harness_registry.HarnessRegistry()
    for name in (
        CODEX_HARNESS,
        CLAUDE_CODE_HARNESS,
    ):
        plugin = foundation_dependencies.standard.replace(
            foundation_test_reactions.example_plugin(IGNORED_TRANSLATION, name=name),
            harness_info=foundation_dependencies.engine.HarnessInfo(
                name,
                str(name).title(),
                "1",
                foundation_dependencies.domain.domain_events.SCHEMA_VERSION,
                OWN_PROCESS_NAME,
                default_for_launch=True,
            ),
            launcher=foundation_test_sources.NullLauncher(),
        )
        if name == CODEX_HARNESS:
            registry.register(plugin)
        else:
            with foundation_dependencies.standard.pytest.raises(
                foundation_dependencies.engine.harness_registry.HarnessRegistryError, match="multiple harnesses",
            ):
                registry.register(plugin)


def test_codec_round_trip_is_deterministic() -> None:
    """Verify codec round trip is deterministic and structured content is canonical."""
    event = foundation_test_events.canonical_message()
    insert_row = foundation_dependencies.repository.mapper.canonical_event_insert_row(event, accepted_at=100.0)
    decoded = foundation_dependencies.repository.mapper.row_canonical_event(
        foundation_test_interpreter.row_from_insert(insert_row),
    )
    assert foundation_dependencies.standard.replace(decoded, cursor=None, accepted_at=None) == event
    assert (
        foundation_dependencies.repository.mapper.canonical_event_insert_row(decoded, accepted_at=100.0) == insert_row
    )
    assert (
        foundation_dependencies.domain.domain_content.StructuredContent('{ "z": 1, "a": [true] }').json_text
        == '{"a":[true],"z":1}'
    )


def test_codec_round_trips_observation_location() -> None:
    """Verify codec round trips the observation location on the envelope."""
    event = foundation_dependencies.standard.replace(
        foundation_test_events.canonical_message(),
        terminal_window_id=foundation_dependencies.domain.domain_ids.WindowId("window-9"),
        harness_process_id=CODEC_PROCESS_ID,
    )
    insert_row = foundation_dependencies.repository.mapper.canonical_event_insert_row(event, accepted_at=100.0)
    decoded = foundation_dependencies.repository.mapper.row_canonical_event(
        foundation_test_interpreter.row_from_insert(insert_row),
    )
    assert decoded.terminal_window_id == "window-9"
    assert decoded.harness_process_id == CODEC_PROCESS_ID


def test_codec_rejects_unsupported_schema_version() -> None:
    """Verify codec rejects an unsupported schema version."""
    row = foundation_dependencies.standard.replace(
        foundation_test_interpreter.row_from_insert(
            foundation_dependencies.repository.mapper.canonical_event_insert_row(
                foundation_test_events.canonical_message(), accepted_at=100.0,
            ),
        ),
        schema_version=UNSUPPORTED_SCHEMA_VERSION,
    )
    with foundation_dependencies.standard.pytest.raises(
        foundation_components.documents.StoredDocumentError, match="schema version",
    ):
        foundation_dependencies.repository.mapper.row_canonical_event(row)


def test_codec_decodes_rows_written() -> None:
    """Verify codec decodes rows written before a defaulted field existed."""
    payload = foundation_dependencies.domain.event_actor.ActorAssignmentStarted(
        foundation_dependencies.domain.domain_ids.AssignmentId(ASSIGNMENT_ID_TEXT),
        foundation_dependencies.domain.domain_content.TextContent("Get Bali weather"),
    )
    event = foundation_dependencies.domain.event_base.CanonicalEvent(
        event_id=foundation_dependencies.domain.domain_ids.CanonicalEventId("event-one"),
        session_id=PRIMARY_SESSION,
        actor_id=foundation_dependencies.domain.domain_ids.ActorId("actor-one"),
        turn_id=None,
        parent_actor_id=None,
        harness=CODEX_HARNESS,
        occurred_at=1.0,
        terminal_window_id=None,
        harness_process_id=None,
        payload=payload,
    )
    event_type = foundation_dependencies.domain.domain_events.EVENT_TYPES[type(payload)]
    document = foundation_dependencies.standard.json.loads(
        foundation_dependencies.repository.mapper.payload_json(event),
    )
    document.pop("actor_name", None)
    document.pop("prompt", None)
    decoded = foundation_dependencies.repository.mapper.payload(
        event_type, foundation_dependencies.standard.json.dumps(document),
    )
    assert isinstance(decoded, foundation_dependencies.domain.event_actor.ActorAssignmentStarted)
    assert decoded.actor_name is None
    assert decoded.prompt is None
    document["glyph"] = "x"
    with foundation_dependencies.standard.pytest.raises(
        foundation_components.documents.StoredDocumentError, match="glyph",
    ):
        foundation_dependencies.repository.mapper.payload(
            event_type, foundation_dependencies.standard.json.dumps(document),
        )
    document.pop("glyph", None)
    document.pop("brief", None)
    with foundation_dependencies.standard.pytest.raises(
        foundation_components.documents.StoredDocumentError, match="Field required",
    ):
        foundation_dependencies.repository.mapper.payload(
            event_type, foundation_dependencies.standard.json.dumps(document),
        )
