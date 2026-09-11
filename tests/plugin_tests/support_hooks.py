# Copyright (c) 2026 Zhambyl Yermagambet
"""Shared fixtures and builders for canonical harness tests."""

import os
import typing
from pathlib import Path

from domain import ids as domain_ids
from harness import contract as harness_contract
from harness.impl.claude_code.canonical.translator import ClaudeCanonicalTranslator
from harness.models import raw_events as raw_event_models
from harness.models.hooks import (
    HarnessHookRequest,
)
from repository.impl.sqlite.databases import main_database
from repository.impl.sqlite.raw_events import SqliteRawEventRepository
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.support_events import raw_event

CLAUDE_CODE_HARNESS = domain_ids.HarnessName("claude_code")


DEFAULT_CONTROL_WINDOW_ID = domain_ids.WindowId(fixture.WINDOW_ONE_ID)


class HookObservation(typing.TypedDict, total=False):
    """Values that the hook client observes around a delivery."""

    terminal_window_id: domain_ids.WindowId | None
    harness_process_id: int | None
    account_id: domain_ids.AccountId | None
    account_display_name: str | None
    launch_model: str | None
    launch_effort: str | None
    client_process_id: int | None


def hook_request(
    payload: bytes,
    **observed: typing.Unpack[HookObservation],
) -> HarnessHookRequest:
    """Build a hook request with optional observed session fields.

    Returns:
        The request with the supplied payload and observations.

    """
    return HarnessHookRequest(
        payload=payload,
        terminal_window_id=observed.get("terminal_window_id"),
        harness_process_id=observed.get("harness_process_id"),
        account_id=observed.get("account_id"),
        account_display_name=observed.get("account_display_name"),
        launch_model=observed.get("launch_model"),
        launch_effort=observed.get("launch_effort"),
        client_process_id=observed.get("client_process_id"),
    )


def deliver_hook(
    gateway: harness_contract.HarnessHookGateway,
    payload: bytes,
    **observed: typing.Unpack[HookObservation],
) -> bytes:
    """Deliver a hook and store its raw events without HTTP.

    Returns:
        The gateway reply after its events are stored in the test database.

    """
    response = gateway.receive_hook(hook_request(payload, **observed))
    database_path = str(Path(os.environ[fixture.BAQYLAU_DATA_DIR_ENV]) / fixture.MAIN_DB_PATH)
    SqliteRawEventRepository(main_database(database_path)).record(response.raw_events)
    return response.reply


def monitor_notification(uuid: str, body: str) -> raw_event_models.RawEvent:
    """Build a queued monitor notification record.

    Returns:
        The raw transcript event with the supplied identity and message body.

    """
    return raw_event(
        {
            fixture.TYPE_FIELD: fixture.QUEUE_OPERATION_ID,
            fixture.OPERATION_FIELD: fixture.ENQUEUE,
            fixture.CONTENT_FIELD: f"<task-notification>{body}</task-notification>",
        },
        harness=CLAUDE_CODE_HARNESS,
        source_type=fixture.TRANSCRIPT_SOURCE,
        raw_event_id=uuid,
    )


def armed_monitor(
    translator: ClaudeCanonicalTranslator,
    shell_id: str = fixture.MONITOR_OP_ONE,
    task_id: str = "bmfwjr03l",
) -> None:
    """Translate a completed Monitor call that announces its task identity."""
    translator.translate(
        raw_event(
            {
                fixture.HOOK_EVENT_NAME_FIELD: fixture.POST_TOOL_USE_HOOK,
                fixture.TOOL_USE_ID_FIELD: shell_id,
                fixture.TOOL_NAME_FIELD: fixture.MONITOR_TOOL,
                fixture.TOOL_INPUT_FIELD: {
                    fixture.COMMAND_FIELD: "tail -f log",
                    fixture.DESCRIPTION_FIELD: "ticks",
                },
                fixture.TOOL_RESPONSE_FIELD: {"taskId": task_id, "timeoutMs": 300000, "persistent": False},
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id=f"arm-{shell_id}",
        ),
    )
