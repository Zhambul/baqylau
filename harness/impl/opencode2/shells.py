# Copyright (c) 2026 Zhambyl Yermagambet
"""Translate native shell calls and their completed output."""

from domain import event_shell, ids, outcomes
from domain.content import TextContent
from domain.event_base import EventPayload
from harness.impl.opencode2 import background
from harness.impl.opencode2.records import NativeRecord


def payloads(native_record: NativeRecord) -> tuple[EventPayload, ...]:
    """Read shell facts from a saved tool event.

    Returns:
        Shell facts, or no facts for other tools.

    """
    details = native_record.event.details
    background_events = background.payloads(native_record)
    if background_events:
        return background_events
    if native_record.tool is None or details.id is None:
        return ()
    return _shell(native_record) if native_record.tool.name == "shell" else ()


def _shell(native_record: NativeRecord) -> tuple[EventPayload, ...]:
    details = native_record.event.details
    shell_id = ids.ShellId(str(details.id))
    if native_record.event.type == "session.tool.called" and details.input is not None:
        if details.input.command is None:
            return ()
        return (event_shell.ShellStarted(
            shell_id, TextContent(details.input.command), _mode(background=details.input.background), None,
        ),)
    return _finished(native_record, shell_id)


def _finished(native_record: NativeRecord, shell_id: ids.ShellId) -> tuple[EventPayload, ...]:
    details = native_record.event.details
    if native_record.event.type == "session.tool.failed":
        return (_failed(native_record, shell_id),)
    if native_record.event.type != "session.tool.success":
        return ()
    if details.metadata is not None and details.metadata.status == "running":
        return (event_shell.ShellBackgrounded(shell_id),)
    exit_code = None if details.metadata is None else details.metadata.exit
    return (event_shell.ShellFinished(
        shell_id,
        _outcome(exit_code),
        _output(native_record),
        exit_code,
    ),)


def _failed(native_record: NativeRecord, shell_id: ids.ShellId) -> event_shell.ShellFinished:
    error = native_record.event.details.error
    outcome = outcomes.Outcome.FAILED
    content = None
    if error is not None:
        content = TextContent(error.message)
        if error.type == "aborted":
            outcome = outcomes.Outcome.CANCELLED
    return event_shell.ShellFinished(shell_id, outcome, content, None)


def _mode(*, background: bool) -> outcomes.ExecutionMode:
    return outcomes.ExecutionMode.BACKGROUND if background else outcomes.ExecutionMode.FOREGROUND


def _outcome(exit_code: int | None) -> outcomes.Outcome:
    if exit_code is None:
        return outcomes.Outcome.UNKNOWN
    if exit_code == 0:
        return outcomes.Outcome.SUCCEEDED
    return outcomes.Outcome.FAILED


def _output(native_record: NativeRecord) -> TextContent:
    parts = native_record.event.details.content
    texts = (part.text for part in parts if part.type == "text")
    return TextContent("\n".join(text for text in texts if text is not None))
