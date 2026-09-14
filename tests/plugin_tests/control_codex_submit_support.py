# Copyright (c) 2026 Zhambyl Yermagambet
"""Cross-harness canonical translation tests from native fixture shapes."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from tests.plugin_tests import vocabulary as fixture

if TYPE_CHECKING:
    from pathlib import Path
    from types import SimpleNamespace

    from terminal.models import input as terminal_input
    from tests.fake_terminal import FakeTerminal
    from tests.plugin_tests.control_callback_values import KeySendCallback, TextInsertCallback, TextSubmitCallback


def temporary_directory_name(temporary_directory: Path) -> str:
    """Return the temporary directory name.

    Returns:
        The temporary directory name.

    """
    return str(temporary_directory)


def submit_codex_plan(
    native_submit: TextSubmitCallback,
    terminal: FakeTerminal,
    request: terminal_input.TextSubmitRequest,
) -> terminal_input.TextSubmitResponse:
    """Submit text and show the Codex plan-mode composer.

    Returns:
        The response from the supplied submission callback.

    """
    response = native_submit(request)
    terminal.screen_text = "\u203a Ask Codex to do anything\nPlan mode (shift+tab to cycle)"
    return response


def submit_codex_plan_rollout(
    native_submit: TextSubmitCallback,
    source: Path,
    request: terminal_input.TextSubmitRequest,
) -> terminal_input.TextSubmitResponse:
    """Submit text and record the Codex plan-mode setting.

    Returns:
        The response from the supplied submission callback.

    """
    response = native_submit(request)
    with source.open("a", encoding=fixture.TEXT_ENCODING) as rollout_file:
        rollout_file.write(json.dumps({
            fixture.TYPE_FIELD: fixture.EVENT_MSG_ID,
            fixture.PAYLOAD_FIELD: {
                fixture.TYPE_FIELD: "thread_settings_applied",
                "thread_settings": {"collaboration_mode": {"mode": "plan"}},
            },
        }) + "\n")
    return response


def submit_codex_rename(
    native_submit: TextSubmitCallback,
    rename_state: SimpleNamespace,
    request: terminal_input.TextSubmitRequest,
) -> terminal_input.TextSubmitResponse:
    """Submit text and mark the simulated rename as complete.

    Returns:
        The response from the supplied submission callback.

    """
    response = native_submit(request)
    rename_state.completed = True
    return response


def insert_rewind_text(
    native_insert: TextInsertCallback,
    terminal: FakeTerminal,
    request: terminal_input.TextInsertRequest,
) -> terminal_input.TextInsertResponse:
    """Insert text and show it in the simulated rewind composer.

    Returns:
        The response from the supplied insertion callback.

    """
    response = native_insert(request)
    terminal.screen_text = f"\u203a {request.text}"
    return response


def send_rewind_key(
    native_key: KeySendCallback,
    new_source: Path,
    terminal: FakeTerminal,
    request: terminal_input.KeySendRequest,
) -> terminal_input.KeySendResponse:
    """Send a key and write a new turn record after Enter.

    Returns:
        The response from the supplied key callback.

    """
    response = native_key(request)
    if request.key == fixture.ENTER:
        with new_source.open("w", encoding=fixture.TEXT_ENCODING) as rollout_file:
            rollout_file.write(
                json.dumps(
                    {
                        fixture.TYPE_FIELD: fixture.EVENT_MSG_ID,
                        fixture.PAYLOAD_FIELD: {
                            fixture.TYPE_FIELD: fixture.TASK_STARTED_ID,
                            fixture.TURN_ID_FIELD: "rewind-turn",
                        },
                    },
                )
                + "\n",
            )
        terminal.screen_text = "Working (0s • esc to interrupt)"
    return response
