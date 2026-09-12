# Copyright (c) 2026 Zhambyl Yermagambet
"""Select a saved call before parsing its tool arguments."""

from harness.impl.codex.canonical import record_actor_records as actors, record_tool_records as tools, rollout
from harness.impl.codex.canonical.record_terminal_records import RolloutRecord
from harness.impl.codex.canonical.record_tool_headers import ToolCallHeaderDocument
from harness.impl.codex.ids_session_types import CodexCallId


def requested_call(line: bytes, call_id: CodexCallId) -> RolloutRecord | None:
    """Read only the requested call or its command batch.

    Returns:
        The parsed call, or None for an unrelated record.

    """
    header = ToolCallHeaderDocument.model_validate_json(line)
    native_call = header.payload.call_id
    if (
        header.type != "response_item" or native_call is None
        or (call_id != native_call and not call_id.startswith(f"{native_call}:"))
    ):
        return None
    return rollout.parse_line(line.decode())


def batch_call(
    tool_batch_record: actors.ToolBatchRecord, call_id: CodexCallId,
) -> actors.ToolBatchRecord | tools.ExecRecord | tools.ToolRecord | None:
    """Find a command inside its original batch.

    Returns:
        The batch or the matching command.

    """
    if tool_batch_record.call_id == call_id:
        return tool_batch_record
    return next((
        action for action in tool_batch_record.actions
        if isinstance(action, (tools.ExecRecord, tools.ToolRecord)) and action.call_id == call_id
    ), None)
