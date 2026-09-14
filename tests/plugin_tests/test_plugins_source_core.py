# Copyright (c) 2026 Zhambyl Yermagambet
"""Core harness source tests."""

import json
from pathlib import Path

import pytest

from domain import (
    event_session,
    event_shell,
    event_work,
    ids as domain_ids,
    work_state,
)
from harness.impl.claude_code.canonical.sources import (
    ClaudeTranscriptRawEventSource,
)
from harness.impl.claude_code.canonical.translator import ClaudeCanonicalTranslator
from harness.impl.claude_code.reactors import ClaudeOtelCanonicalEventReactor
from harness.impl.codex.canonical.source_readers import (
    CodexRolloutRawEventSource,
)
from harness.impl.discovery import installed
from harness.models.session import (
    Session,
)
from tests.canonical_runtime import ProviderGraph
from tests.plugin_tests import (
    source_common_support,
    support_events as plugin_support,
    vocabulary as fixture,
)

CLAUDE_CODE_HARNESS = domain_ids.HarnessName("claude_code")


def test_claude_registers_no_auto_account() -> None:
    # A rate limit must not relaunch the CLI: the resumed run's
    # `session.started` deduplicates against the first run's, so the first
    # run's `session.finished` would keep the session out of `watchable()`.
    """Verify claude registers no automatic account migration reactor."""
    reactors = ProviderGraph().registry.plugin(CLAUDE_CODE_HARNESS).reactors

    assert [type(reactor).__name__ for reactor in reactors] == [
        "ClaudeOtelCanonicalEventReactor",
    ]


def test_claude_stop_failure_rate_limit_yields() -> None:
    """Verify claude stop failure rate limit yields the usage limited goal fact."""
    translation = ClaudeCanonicalTranslator().translate(
        plugin_support.raw_event(
            {fixture.HOOK_EVENT_NAME_FIELD: "StopFailure", fixture.ERROR: "rate_limit"},
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id="stop-failure",
        ),
    )
    unrelated = ClaudeCanonicalTranslator().translate(
        plugin_support.raw_event(
            {fixture.HOOK_EVENT_NAME_FIELD: "StopFailure", fixture.ERROR: "network"},
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id="stop-network",
        ),
    )

    goals = plugin_support.payloads(translation, event_work.GoalChanged)
    assert [goal.payload.state for goal in goals] == ["usage_limited"]
    assert goals[0].payload.reason == "rate_limit"
    assert plugin_support.payloads(unrelated, event_work.GoalChanged) == []


def test_claude_reactor_starts_telemetry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify claude reactor starts telemetry on session start."""
    telemetry_starts = []
    monkeypatch.setattr(
        "harness.impl.claude_code.reactors.otel.start",
        lambda: telemetry_starts.append(fixture.STARTED),
    )
    reactor = ClaudeOtelCanonicalEventReactor()
    controls = ProviderGraph().controls
    reactor.react(
        plugin_support.committed(
            event_session.SessionStarted(
                fixture.WORK_PATH, fixture.WORK_SESSION_JSONL_PATH, None, None, None, None, None,
            ),
        ),
        controls,
    )
    goal_changed = event_work.GoalChanged(
        fixture.SHIP_IT_TEXT,
        work_state.GoalState.ACTIVE,
        None,
    )
    reactor.react(plugin_support.committed(goal_changed), controls)

    assert telemetry_starts == [fixture.STARTED]


def test_plugin_folder_descriptors_are_discovered() -> None:
    """Verify plugin folder descriptors are discovered without harness branches."""
    assert [plugin.harness_info.name for plugin in installed()] == [
        fixture.CLAUDE_CODE_HARNESS,
        fixture.CODEX_HARNESS,
        "opencode2",
    ]


def test_file_sources_preserve_exact_complete(tmp_path: Path) -> None:
    """Verify file sources preserve the exact complete line."""
    source_path = tmp_path / "source.jsonl"
    exact_line = b'{"type":"example"}\n'
    source_path.write_bytes(exact_line)
    for source_type in (ClaudeTranscriptRawEventSource, CodexRolloutRawEventSource):
        source = source_type(
            Session(
                domain_ids.SessionId(fixture.SESSION_ONE_ID),
                source_common_support.PRIMARY_LEAD_ACTOR,
                source_path.as_posix(),
                fixture.WORK_PATH,
            ).source_context,
        )
        raw_events = source.read(None)

        assert raw_events[0].payload == exact_line
        assert raw_events[0].source_identity == source.source_identity
        # the position names the last consumed record; resuming after it is empty
        assert source.read(raw_events[-1].source_position) == ()


def test_claude_parent_queue_attributes_bg(tmp_path: Path) -> None:
    """Verify claude parent queue attributes a background completion to its child."""
    child_path = tmp_path / fixture.SESSION_ONE_ID / fixture.SUBAGENTS / "agent-child-one.jsonl"
    child_path.parent.mkdir(parents=True)
    child_path.write_text(
        json.dumps(
            {
                fixture.TYPE_FIELD: fixture.ASSISTANT,
                fixture.MESSAGE_FIELD: {
                    fixture.ROLE_FIELD: fixture.ASSISTANT,
                    fixture.CONTENT_FIELD: [
                        {
                            fixture.TYPE_FIELD: fixture.TOOL_USE_ID,
                            fixture.ID_FIELD: fixture.BACKGROUND_OP_ONE,
                            fixture.NAME_FIELD: fixture.BASH_TOOL,
                            fixture.INPUT_FIELD: {
                                fixture.COMMAND_FIELD: "sleep 1",
                                fixture.RUN_IN_BACKGROUND_FIELD: True,
                            },
                        },
                    ],
                },
            },
        )
        + "\n",
    )
    (tmp_path / fixture.SESSION_ONE_JSONL_PATH).write_text(
        json.dumps(
            {
                fixture.TYPE_FIELD: fixture.QUEUE_OPERATION_ID,
                fixture.OPERATION_FIELD: fixture.ENQUEUE,
                fixture.CONTENT_FIELD: (
                    "<task-notification><task-id>bkdr7jbeo</task-id>"
                    "<tool-use-id>background-op-one</tool-use-id>"
                    "<status>completed</status>"
                    '<summary>Background command "Wait" completed (exit code 0)</summary>'
                    "</task-notification>"
                ),
            },
        )
        + "\n",
    )
    session = Session(
        domain_ids.SessionId(fixture.SESSION_ONE_ID),
        source_common_support.PRIMARY_LEAD_ACTOR,
        str(tmp_path / fixture.SESSION_ONE_JSONL_PATH),
        fixture.WORK_PATH,
    )

    raw = ClaudeTranscriptRawEventSource(session.source_context).read(None)[0]
    translated = ClaudeCanonicalTranslator().translate(raw)

    assert raw.actor_id == domain_ids.ActorId(fixture.CHILD_ONE_ID)
    assert raw.parent_actor_id == source_common_support.PRIMARY_LEAD_ACTOR
    shell_outputs = plugin_support.payloads(translated, event_shell.ShellOutputFinished)
    assert shell_outputs[0].actor_id == domain_ids.ActorId(fixture.CHILD_ONE_ID)
