# Copyright (c) 2026 Zhambyl Yermagambet
"""Follow native background output until the native shell exits."""

from domain import event_shell, outcomes, work_state
from domain.event_base import EventPayload
from harness.impl.opencode2.records import NativeRecord
from harness.impl.opencode2.shell_records import NativeShell


def payloads(native_record: NativeRecord) -> tuple[EventPayload, ...]:
    """Read background transitions from a saved shell join.

    Returns:
        Background facts, or no facts for another event.

    """
    shell = native_record.shell
    if shell is None:
        return ()
    if native_record.event.type == "shell.exited":
        outcome = _outcome(shell)
        return (
            event_shell.ShellFinished(shell.call_id, outcome, None, shell.exit),
            event_shell.ShellOutputFinished(shell.call_id, outcome),
        )
    metadata = native_record.event.details.metadata
    if native_record.event.type != "session.tool.success" or metadata is None or metadata.status != "running":
        return ()
    return (
        event_shell.ShellBackgrounded(shell.call_id),
        event_shell.ShellOutputLocated(
            shell_id=shell.call_id,
            source_path=shell.file,
            chunk_source_type="foreground_output",
            delete_source=False,
            initial_size=0,
            initial_modified_at=0,
            wait_for_source_change=False,
            until=work_state.ShellFollowUntil.SESSION_FINISHED,
        ),
    )


def _outcome(native_shell: NativeShell) -> outcomes.Outcome:
    if native_shell.status == "killed":
        return outcomes.Outcome.CANCELLED
    if native_shell.exit == 0:
        return outcomes.Outcome.SUCCEEDED
    return outcomes.Outcome.FAILED
