"""Claude Code command output as recordable output-location directives."""

from __future__ import annotations

import glob
import hashlib
import os
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from domain.events import ShellOutputLocated
from domain.ids import SessionId, ShellId
from domain.values import ShellFollowUntil
from harness.impl.claude_code import shell
from harness.impl.claude_code.canonical.records import HookPayload, ShellArguments, ToolResponse
from harness.impl.claude_code.ids import (
    ClaudeCodeCallId,
    ClaudeCodeSessionId,
    ClaudeCodeShellId,
    shell_id_from_claude_code,
    shell_id_from_claude_code_call,
    session_id_from_claude_code,
)

CHUNK_SOURCE_TYPE = "foreground_output"

# Claude Code writes a background command's output to
# /tmp/claude-<uid>/<cwd-slug>/<session-id>/tasks/<taskId>.output. The slug rule
# is Claude's own, so the file is FOUND by its unique (session, task) pair
# rather than derived — a miss simply means nothing to watch.
BACKGROUND_OUTPUT_ROOT = "/tmp"


@dataclass(frozen=True)
class PreparedForegroundCommand:
    reply: bytes
    located: ShellOutputLocated


class HookSpecificOutput(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)
    hook_event_name: str = Field(alias="hookEventName")
    permission_decision: str = Field(alias="permissionDecision")
    updated_input: ShellArguments = Field(alias="updatedInput")


class HookReply(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)
    hook_specific_output: HookSpecificOutput = Field(alias="hookSpecificOutput")


def _safe_identity(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _directory(session_id: SessionId) -> str:
    configuration_directory = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser(
        "~/.claude"
    )
    return os.path.join(
        configuration_directory,
        "baqylau",
        "foreground",
        _safe_identity(session_id),
    )


def _tee_path(session_id: SessionId, shell_id: ShellId) -> str:
    return os.path.join(_directory(session_id), _safe_identity(shell_id)) + ".out"


def _updated_input(
    shell_arguments: ShellArguments,
    command: str,
) -> bytes:
    reply = HookReply(hookSpecificOutput=HookSpecificOutput(
        hookEventName="PreToolUse",
        permissionDecision="allow",
        updatedInput=ShellArguments(
            command=command,
            description=shell_arguments.description,
            run_in_background=shell_arguments.run_in_background,
            timeout=shell_arguments.timeout,
        ),
    ))
    return (reply.model_dump_json(by_alias=True, exclude_none=True) + "\n").encode("utf-8")


def background_output(
    hook_payload: HookPayload,
) -> ShellOutputLocated | None:
    """The output location of a background command's native output file.

    Background commands are not rewritten (Claude Code redirects their output
    itself), so the location becomes known at the PostToolUse that reports the
    task id. The native file is Claude Code's — never deleted by us — and the
    following ends with the session (or the lifetime cap), never with the
    command, whose launch reports "finished" while output keeps flowing.
    """
    shell_arguments = hook_payload.shell_input()
    if not shell_arguments.run_in_background:
        return None
    call_id = ClaudeCodeCallId(hook_payload.tool_use_id or "")
    native_session_id = ClaudeCodeSessionId(hook_payload.session_id or "")
    response = hook_payload.tool_response
    task_id = response.backgroundTaskId if isinstance(response, ToolResponse) else None
    if not call_id or not native_session_id or not task_id:
        return None
    pattern = os.path.join(
        BACKGROUND_OUTPUT_ROOT, "claude-*", "*", native_session_id, "tasks", f"{task_id}.output"
    )
    matches = sorted(glob.glob(pattern))
    if not matches:
        return None
    return ShellOutputLocated(
        shell_id=shell_id_from_claude_code_call(call_id),
        source_path=os.path.realpath(matches[0]),
        chunk_source_type=CHUNK_SOURCE_TYPE,
        delete_source=False,
        initial_size=0,
        initial_modified_at=0,
        wait_for_source_change=False,
        until=ShellFollowUntil.SESSION_FINISHED,
    )


def prepare(
    hook_payload: HookPayload,
) -> PreparedForegroundCommand | None:
    """Rewrite one Bash command so its output lands in a readable file.

    The returned location is NOT applied here — the gateway records it as an
    output-location directive and the interpreter does the following. The
    gateway's only file act is creating the tee target, which the rewritten
    command itself requires.
    """
    shell_arguments = hook_payload.shell_input()
    command = shell_arguments.command if isinstance(shell_arguments.command, str) else ""
    if not command.strip() or shell_arguments.run_in_background:
        return None
    native_session_id = ClaudeCodeSessionId(hook_payload.session_id or "")
    call_id = ClaudeCodeCallId(hook_payload.tool_use_id or "")
    if not native_session_id or not call_id:
        raise ValueError("Claude Code foreground command has no session or command id")
    session_id = session_id_from_claude_code(native_session_id)
    shell_id = shell_id_from_claude_code_call(call_id)

    working_directory = hook_payload.cwd
    redirect = shell.redirected_output(
        command, str(working_directory) if working_directory is not None else None
    )
    if redirect is None:
        source_path = _tee_path(session_id, shell_id)
        os.makedirs(os.path.dirname(source_path), mode=0o700, exist_ok=True)
        descriptor = os.open(
            source_path,
            os.O_WRONLY | os.O_CREAT | os.O_APPEND,
            0o600,
        )
        os.close(descriptor)
        wrapped_command = shell.copy_output_to(command, source_path)
        delete_source = True
        initial_size = 0
        initial_modified_at = 0
        wait_for_source_change = False
    else:
        source_path, append = redirect
        try:
            source_stat = os.stat(source_path)
            initial_size = source_stat.st_size
            initial_modified_at = source_stat.st_mtime_ns
        except FileNotFoundError:
            initial_size = 0
            initial_modified_at = 0
        wrapped_command = command
        delete_source = False
        wait_for_source_change = not append

    return PreparedForegroundCommand(
        _updated_input(shell_arguments, wrapped_command),
        ShellOutputLocated(
            shell_id=shell_id_from_claude_code(ClaudeCodeShellId(shell_id)),
            source_path=source_path,
            chunk_source_type=CHUNK_SOURCE_TYPE,
            delete_source=delete_source,
            initial_size=initial_size,
            initial_modified_at=initial_modified_at,
            wait_for_source_change=wait_for_source_change,
            until=ShellFollowUntil.SHELL_FINISHED,
        ),
    )
