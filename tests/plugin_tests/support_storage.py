# Copyright (c) 2026 Zhambyl Yermagambet
"""Shared fixtures and builders for canonical harness tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

from domain import content as domain_content, event_base, event_shell, ids as domain_ids
from tests import storage_reads
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.support_values import text_of

if TYPE_CHECKING:
    from tests.canonical_runtime import CanonicalRuntime


def stored_payloads[PayloadType: event_base.EventPayload](
    runtime: CanonicalRuntime,
    session_id: domain_ids.SessionId,
    payload_type: type[PayloadType],
) -> list[PayloadType]:
    """Every fact of one kind a session accumulated, in the order it was accepted.

    Read straight off the canonical log, because that is what a PLUGIN test is
    about: the evidence became these facts. Folding them into something a reader
    sees belongs to the read model, and has its own tests.

    Returns:
        The payloads of the requested type, in event order.

    """
    return [
        event.payload
        for event in storage_reads.page_from(runtime.store, 0, fixture.CANONICAL_PAGE_LIMIT)
        if event.session_id == session_id and isinstance(event.payload, payload_type)
    ]


def shell_output_text(
    runtime: CanonicalRuntime,
    session_id: domain_ids.SessionId,
    shell_id: domain_ids.ShellId,
) -> str:
    """Read one command's output with append and replace operations.

    Returns:
        The combined output text for the requested shell.

    """
    output_text = ""
    for payload in stored_payloads(runtime, session_id, event_shell.ShellProgressed):
        content = _shell_output_content(payload, shell_id)
        if content is None:
            continue
        output_text = output_text + content if payload.mode == "append" else content
    return output_text


def _shell_output_content(
    payload: event_shell.ShellProgressed,
    shell_id: domain_ids.ShellId,
) -> str | None:
    if payload.shell_id != shell_id:
        return None
    if payload.stream != fixture.OUTPUT_FIELD:
        return None
    message = "shell output must contain text"
    assert isinstance(payload.content, domain_content.TextContent), message
    return text_of(payload.content)
