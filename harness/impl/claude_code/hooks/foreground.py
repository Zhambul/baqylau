"""Claude Code command output as recordable output-location directives."""

from __future__ import annotations

import glob
import hashlib
import json
import os
from dataclasses import dataclass

from domain.events import OperationOutputLocated
from domain.ids import OperationId
from harness.impl.claude_code import shell

CHUNK_SOURCE_TYPE = "foreground_output"

# Claude Code writes a background command's output to
# /tmp/claude-<uid>/<cwd-slug>/<session-id>/tasks/<taskId>.output. The slug rule
# is Claude's own, so the file is FOUND by its unique (session, task) pair
# rather than derived — a miss simply means nothing to watch.
BACKGROUND_OUTPUT_ROOT = "/tmp"


@dataclass(frozen=True)
class PreparedForegroundCommand:
    reply: bytes
    located: OperationOutputLocated


def _safe_identity(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _directory(session_id: str) -> str:
    configuration_directory = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser(
        "~/.claude"
    )
    return os.path.join(
        configuration_directory,
        "baqylau",
        "foreground",
        _safe_identity(session_id),
    )


def _tee_path(session_id: str, operation_id: str) -> str:
    return os.path.join(_directory(session_id), _safe_identity(operation_id)) + ".out"


def _updated_input(tool_input: dict, command: str) -> bytes:
    updated_input = dict(tool_input)
    updated_input["command"] = command
    return (
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "updatedInput": updated_input,
                }
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def background_output(document: dict) -> OperationOutputLocated | None:
    """The output location of a background command's native output file.

    Background commands are not rewritten (Claude Code redirects their output
    itself), so the location becomes known at the PostToolUse that reports the
    task id. The native file is Claude Code's — never deleted by us — and the
    following ends with the session (or the lifetime cap), never with the
    operation, whose launch reports "finished" while output keeps flowing.
    """
    tool_input = document.get("tool_input") or {}
    if not tool_input.get("run_in_background"):
        return None
    operation_id = str(document.get("tool_use_id") or "")
    session_id = str(document.get("session_id") or "")
    response = document.get("tool_response")
    task_id = str(response.get("backgroundTaskId") or "") if isinstance(response, dict) else ""
    if not operation_id or not session_id or not task_id:
        return None
    pattern = os.path.join(
        BACKGROUND_OUTPUT_ROOT, "claude-*", "*", session_id, "tasks", f"{task_id}.output"
    )
    matches = sorted(glob.glob(pattern))
    if not matches:
        return None
    return OperationOutputLocated(
        operation_id=OperationId(operation_id),
        source_path=os.path.realpath(matches[0]),
        chunk_source_type=CHUNK_SOURCE_TYPE,
        delete_source=False,
        initial_size=0,
        initial_modified_at=0,
        wait_for_source_change=False,
        until="session_finished",
    )


def prepare(document: dict) -> PreparedForegroundCommand | None:
    """Rewrite one Bash command so its output lands in a readable file.

    The returned location is NOT applied here — the gateway records it as an
    output-location directive and the interpreter does the following. The
    gateway's only file act is creating the tee target, which the rewritten
    command itself requires.
    """
    tool_input = document.get("tool_input") or {}
    command = str(tool_input.get("command") or "")
    if not command.strip() or tool_input.get("run_in_background"):
        return None
    session_id = str(document.get("session_id") or "")
    operation_id = str(document.get("tool_use_id") or "")
    if not session_id or not operation_id:
        raise ValueError("Claude Code foreground command has no session or operation id")

    redirect = shell.redirected_output(command, document.get("cwd"))
    if redirect is None:
        source_path = _tee_path(session_id, operation_id)
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
        _updated_input(tool_input, wrapped_command),
        OperationOutputLocated(
            operation_id=OperationId(operation_id),
            source_path=source_path,
            chunk_source_type=CHUNK_SOURCE_TYPE,
            delete_source=delete_source,
            initial_size=initial_size,
            initial_modified_at=initial_modified_at,
            wait_for_source_change=wait_for_source_change,
            until="operation_finished",
        ),
    )
