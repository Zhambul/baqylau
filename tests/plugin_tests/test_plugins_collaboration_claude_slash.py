# Copyright (c) 2026 Zhambyl Yermagambet
"""Claude slash command tests."""

from __future__ import annotations

from dataclasses import replace

import pytest

from domain import (
    event_conversation,
    event_session,
    ids as domain_ids,
)
from harness.impl.claude_code.canonical.translator import ClaudeCanonicalTranslator
from tests.harness_names import CLAUDE_CODE_HARNESS
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.support_events import payloads, raw_event
from tests.plugin_tests.support_slash import _slash_turn_events


def test_claude_slash_model_command_turn_is_state() -> None:
    # Three raw transcript records (the caveat, the envelope, the echoed
    # stdout) collapse into ONE canonical fact: the model change itself. No
    # prompt bubble either — the dashboard's own model-change block shows the
    # switch, so echoing "/model opus" as a second, redundant message would
    # just duplicate it (and, with nothing to close the turn, permanently
    # stick the tab on "thinking" — see tabstate.py).
    """Verify claude slash model command turn is the state change not a prompt bubble."""
    events = _slash_turn_events()
    assert not any(isinstance(event.payload, event_conversation.MessageCreated) for event in events)
    models = [event.payload for event in events if isinstance(event.payload, event_session.ModelChanged)]
    assert len(models) == 1


def test_claude_slash_model_reports_selection_at() -> None:
    """Verify claude slash model reports the selection at the moment it was made."""
    models = [event.payload for event in _slash_turn_events() if isinstance(event.payload, event_session.ModelChanged)]
    assert len(models) == 1
    assert models[0].reason == "selected"
    # the transcript carries the ALIAS here; the native id arrives a turn later
    # on the next assistant record, as `reported_by_harness`
    assert models[0].current.name == "opus"


def test_claude_slash_effort_reports_selection() -> None:
    """Verify claude slash effort reports the selection."""
    translation = ClaudeCanonicalTranslator().translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.USER,
                fixture.UUID_FIELD: "eff",
                fixture.MESSAGE_FIELD: {
                    fixture.CONTENT_FIELD: "<command-name>/effort</command-name><command-args>high</command-args>",
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="slash-effort",
        ),
    )
    assert payloads(translation, event_session.EffortChanged)[0].payload.current == fixture.HIGH
    assert payloads(translation, event_session.EffortChanged)[0].payload.reason == "selected"


def test_claude_subagent_hook_reports_its_own() -> None:
    # launch_selections() and a typed /effort only ever see the LEAD actor; a
    # hook firing mid-turn inside the subagent's own process is the only place
    # its effort level is ever observed from.
    """Verify claude subagent hook reports its own effort."""
    translator = ClaudeCanonicalTranslator()
    translator.translate(
        replace(
            raw_event(
                {
                    fixture.HOOK_EVENT_NAME_FIELD: fixture.SUBAGENT_START_HOOK,
                    fixture.HOOK_EVENT_ID_FIELD: fixture.CHILD_START_ID,
                    fixture.AGENT_ID_FIELD: fixture.CHILD_ONE_ID,
                },
                harness=CLAUDE_CODE_HARNESS,
                source_type=fixture.HOOK_SOURCE,
                raw_event_id="child-start-hook",
            ),
            actor_id=domain_ids.ActorId(fixture.CHILD_ONE_ID),
            parent_actor_id=domain_ids.ActorId(fixture.SESSION_ONE_LEAD_ID),
        ),
    )
    pretool = translator.translate(
        replace(
            raw_event(
                {
                    fixture.HOOK_EVENT_NAME_FIELD: fixture.PRE_TOOL_USE_HOOK,
                    fixture.HOOK_EVENT_ID_FIELD: "child-pretool",
                    fixture.TOOL_USE_ID_FIELD: fixture.TOOL_ONE_ID,
                    fixture.TOOL_NAME_FIELD: fixture.READ_TOOL,
                    fixture.TOOL_INPUT_FIELD: {fixture.FILE_PATH_FIELD: fixture.WORK_A_PY_PATH},
                    fixture.EFFORT: {"level": fixture.HIGH},
                },
                harness=CLAUDE_CODE_HARNESS,
                source_type=fixture.HOOK_SOURCE,
                raw_event_id="child-pretool-hook",
            ),
            actor_id=domain_ids.ActorId(fixture.CHILD_ONE_ID),
            parent_actor_id=domain_ids.ActorId(fixture.SESSION_ONE_LEAD_ID),
        ),
    )

    effort_events = payloads(pretool, event_session.EffortChanged)
    assert len(effort_events) == 1
    assert effort_events[0].actor_id == domain_ids.ActorId(fixture.CHILD_ONE_ID)
    assert effort_events[0].payload.current == fixture.HIGH
    assert effort_events[0].payload.reason == "reported_by_harness"


def test_claude_pretool_without_effort_reports_no() -> None:
    """Verify claude pretool without effort reports no effort change."""
    translation = ClaudeCanonicalTranslator().translate(
        raw_event(
            {
                fixture.HOOK_EVENT_NAME_FIELD: fixture.PRE_TOOL_USE_HOOK,
                fixture.HOOK_EVENT_ID_FIELD: "no-effort-pretool",
                fixture.TOOL_USE_ID_FIELD: "tool-two",
                fixture.TOOL_NAME_FIELD: fixture.READ_TOOL,
                fixture.TOOL_INPUT_FIELD: {fixture.FILE_PATH_FIELD: "/work/b.py"},
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id="no-effort-hook",
        ),
    )

    assert not payloads(translation, event_session.EffortChanged)


def test_claude_argless_slash_command_settles_no() -> None:
    """Verify claude argless slash command settles no state and starts no turn."""
    translation = ClaudeCanonicalTranslator().translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.USER,
                fixture.UUID_FIELD: "bare",
                fixture.MESSAGE_FIELD: {fixture.CONTENT_FIELD: "<command-name>/model</command-name>"},
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="slash-bare",
        ),
    )
    # a bare `/model` opens the picker: no turn, no bubble, and no selection
    # until its own output record reports the choice
    assert translation.canonical_events == ()


@pytest.mark.parametrize(fixture.ARGUMENTS_FIELD, ["", "New session title"])
def test_claude_rename_is_only_separate_title(arguments: str) -> None:
    """Verify claude rename is only the separate title change."""
    rename_target = arguments or "automatic"
    translation = ClaudeCanonicalTranslator().translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.SYSTEM,
                fixture.SUBTYPE: "local_command",
                fixture.UUID_FIELD: "rename",
                fixture.CONTENT_FIELD: (
                    "<command-name>/rename</command-name>"
                    "<command-message>rename</command-message>"
                    f"<command-args>{arguments}</command-args>"
                ),
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id=f"slash-rename-{rename_target}",
        ),
    )

    assert translation.canonical_events == ()
    assert translation.decision == fixture.IGNORED_NONSEMANTIC
