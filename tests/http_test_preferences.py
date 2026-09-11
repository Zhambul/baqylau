# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide http test preferences."""

from __future__ import annotations

from tests import (
    http_contract_dependencies as contract_dependencies,
    http_library_dependencies as library_dependencies,
    http_runtime_dependencies as runtime_dependencies,
    http_test_control_models,
    http_test_pane_models,
    http_test_response_helpers,
    http_value_dependencies as standard_dependencies,
)
from tests.harness_names import CODEX_HARNESS

SESSION_ID_TEXT = "session-one"
FIXTURE_SOURCE = "fixture"
LOOPBACK_ADDRESS = "127.0.0.1"
HOOK_SESSION_ID = "hook-session"
SESSION_ID = runtime_dependencies.domain_ids.SessionId(SESSION_ID_TEXT)
ACTOR_ID = runtime_dependencies.domain_ids.ActorId("actor-one")
AGENT_MESSAGE_EVENT_TIME = 11.0
AGENT_MESSAGE_RAW_EVENT_TIME = 110.0
type JsonValue = bool | float | int | str | list[JsonValue] | dict[str, JsonValue] | None
type ControlInvocation = tuple[
    standard_dependencies.collections_abc.Callable[[], runtime_dependencies.control_models.ControlOutcome],
    runtime_dependencies.control_models.ControlRequest,
]


def control_invocations(
    service: contract_dependencies.control_services.HarnessControlService,
) -> list[ControlInvocation]:
    """Build the control calls used to check method audit records.

    Returns:
        Bound control calls with their request objects.

    """
    return [
        http_test_control_models.control_invocation(
            service.send_text,
            runtime_dependencies.control_models.SendText(
                SESSION_ID, runtime_dependencies.domain_ids.RequestId("r1"), text="hi",
            ),
        ),
        http_test_control_models.control_invocation(
            service.interrupt,
            runtime_dependencies.control_models.Interrupt(SESSION_ID, runtime_dependencies.domain_ids.RequestId("r2")),
        ),
        http_test_control_models.control_invocation(
            service.background,
            runtime_dependencies.control_models.Background(SESSION_ID, runtime_dependencies.domain_ids.RequestId("r3")),
        ),
        http_test_control_models.control_invocation(
            service.close_session,
            runtime_dependencies.control_models.CloseSession(
                SESSION_ID, runtime_dependencies.domain_ids.RequestId("r4"),
            ),
        ),
        http_test_control_models.control_invocation(
            service.rename_session,
            runtime_dependencies.control_models.RenameSession(
                SESSION_ID, runtime_dependencies.domain_ids.RequestId("r5"), name="new",
            ),
        ),
        http_test_control_models.control_invocation(
            service.auto_name_session,
            runtime_dependencies.control_models.AutoNameSession(
                SESSION_ID, runtime_dependencies.domain_ids.RequestId("r6"),
            ),
        ),
        http_test_control_models.control_invocation(
            service.open_rewind,
            runtime_dependencies.control_models.OpenRewind(SESSION_ID, runtime_dependencies.domain_ids.RequestId("r7")),
        ),
        http_test_control_models.control_invocation(
            service.apply_rewind,
            runtime_dependencies.control_models.ApplyRewind(
                SESSION_ID,
                runtime_dependencies.domain_ids.RequestId("r8"),
                target_message_id=runtime_dependencies.domain_ids.MessageId("m1"),
                target_text="was",
                newer_prompt_count=0,
                mode="restore",
            ),
        ),
        http_test_control_models.control_invocation(
            service.compact,
            runtime_dependencies.control_models.Compact(SESSION_ID, runtime_dependencies.domain_ids.RequestId("r9")),
        ),
        http_test_control_models.control_invocation(
            service.select_model,
            runtime_dependencies.control_models.SelectModel(
                SESSION_ID, runtime_dependencies.domain_ids.RequestId("r10"), model="x",
            ),
        ),
        http_test_control_models.control_invocation(
            service.select_effort,
            runtime_dependencies.control_models.SelectEffort(
                SESSION_ID, runtime_dependencies.domain_ids.RequestId("r11"), effort="high",
            ),
        ),
        http_test_control_models.control_invocation(
            service.answer_question,
            runtime_dependencies.control_models.AnswerQuestion(
                SESSION_ID,
                runtime_dependencies.domain_ids.RequestId("r12"),
                attention_id=runtime_dependencies.domain_ids.AttentionId("a1"),
                decision=runtime_dependencies.control_models.AnswerDecision.ANSWER,
            ),
        ),
        http_test_control_models.control_invocation(
            service.read_plan_choices,
            runtime_dependencies.control_models.ReadPlanChoices(
                SESSION_ID,
                runtime_dependencies.domain_ids.RequestId("r13"),
                attention_id=runtime_dependencies.domain_ids.AttentionId("a2"),
            ),
        ),
        http_test_control_models.control_invocation(
            service.decide_plan,
            runtime_dependencies.control_models.DecidePlan(
                SESSION_ID,
                runtime_dependencies.domain_ids.RequestId("r14"),
                attention_id=runtime_dependencies.domain_ids.AttentionId("a3"),
                decision="keep",
            ),
        ),
    ]


def read_sse_event(response: library_dependencies.http.client.HTTPResponse) -> tuple[str, JsonValue]:
    """Read one SSE event and skip comment lines.

    Returns:
        The event name and decoded payload.

    """
    event = None
    event_payload = None
    while True:
        line = http_test_response_helpers.sse_line(response)
        if line.startswith(":"):
            continue
        if not line:
            if event is not None:
                return (event, event_payload)
            continue
        event, event_payload = http_test_response_helpers.sse_event_values(line, event, event_payload)


def record_agent_message(application: contract_dependencies.canonical_runtime.ProviderGraph) -> None:
    """Process record agent message.

    The mirror hides the lead actor's own messages (the TUI already shows
        them), so pane assertions need a child agent's message.
    """
    event = runtime_dependencies.event_base.CanonicalEvent(
        event_id=runtime_dependencies.domain_ids.CanonicalEventId("agent-message"),
        session_id=SESSION_ID,
        actor_id=runtime_dependencies.domain_ids.ActorId("actor-two"),
        turn_id=None,
        parent_actor_id=ACTOR_ID,
        harness=CODEX_HARNESS,
        occurred_at=AGENT_MESSAGE_EVENT_TIME,
        terminal_window_id=None,
        harness_process_id=None,
        payload=runtime_dependencies.event_conversation.MessageCreated(
            runtime_dependencies.domain_ids.MessageId("message-two"),
            runtime_dependencies.messaging.MessageRole.ASSISTANT,
            runtime_dependencies.domain_content.TextContent("hello from the agent"),
            runtime_dependencies.messaging.MessagePhase.END_TURN,
            None,
        ),
    )
    http_test_pane_models.record(
        application,
        runtime_dependencies.raw_event_models.RawEvent(
            runtime_dependencies.domain_ids.RawEventId("raw-agent"),
            CODEX_HARNESS,
            FIXTURE_SOURCE,
            FIXTURE_SOURCE,
            "9",
            SESSION_ID,
            runtime_dependencies.domain_ids.ActorId("actor-two"),
            ACTOR_ID,
            AGENT_MESSAGE_RAW_EVENT_TIME,
            "json",
            b"{}",
        ),
        "1",
        runtime_dependencies.raw_event_models.TranslationResult(
            (event,), runtime_dependencies.domain_records.RecordedTranslationDecision.TRANSLATED,
        ),
    )


def post_hook(
    server: http_test_pane_models.RunningDaemon,
    harness: str,
    payload: bytes,
    observed: standard_dependencies.collections_abc.Mapping[str, str] | None = None,
) -> tuple[int, bytes]:
    """Post a harness hook with optional observation headers.

    Returns:
        The HTTP status and response bytes.

    """
    connection = library_dependencies.http.client.HTTPConnection(LOOPBACK_ADDRESS, server.server_port, timeout=2)
    headers = {"Content-Type": "application/json", "X-Baqylau": "1"}
    headers.update(observed or {})
    connection.request(
        "POST", f"/api/harnesses/{library_dependencies.quote(harness)}/hooks", body=payload, headers=headers,
    )
    response = connection.getresponse()
    response_body = response.read()
    connection.close()
    return (response.status, response_body)


def recorded_hook_events(
    application: contract_dependencies.canonical_runtime.ProviderGraph,
) -> tuple[runtime_dependencies.raw_event_models.RawEvent, ...]:
    """Read hook events recorded for the test session.

    Returns:
        The raw hook events from the session audit records.

    """
    return tuple(

            audit_record.raw_event
            for audit_record in http_test_pane_models.raw_event_audits(application).audits_for_session(
                runtime_dependencies.domain_ids.SessionId(HOOK_SESSION_ID),
            )
            if audit_record.raw_event.source_type == "hook"

    )


def watched_read[**CallParameters, Result](
    read: standard_dependencies.collections_abc.Callable[CallParameters, Result], locations: list[str],
) -> standard_dependencies.collections_abc.Callable[CallParameters, Result]:
    """Record whether a repository read runs on an event loop.

    Returns:
        A callable that records the read location and returns the read result.

    """
    return lambda *arguments, **keywords: http_test_pane_models.observe_read(read, locations, *arguments, **keywords)


def assert_no_validation_error_response(paths: dict[str, JsonValue]) -> None:
    """Check that no OpenAPI operation declares an unexpected HTTP 422 response."""
    assert not any(

            http_test_pane_models.declares_unexpected_validation_error(operation)
            for operations_value in paths.values()
            for operation in http_test_control_models.json_object(operations_value).values()

    )
