"""Claude Code foreground-command rewriting into a recordable file watch."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass

from contracts.harness import FileWatch
from plugins.claude_code import shell

CHUNK_SOURCE_TYPE = "foreground_output"


@dataclass(frozen=True)
class PreparedForegroundCommand:
    output: bytes
    watch: FileWatch


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


def prepare(document: dict) -> PreparedForegroundCommand | None:
    """Rewrite one Bash command so its output lands in a watchable file.

    The returned watch is NOT applied here — the hook records it as a `watch`
    raw event and the interpreter does the following. The hook's only file act
    is creating the tee target, which the rewritten command itself requires.
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
        FileWatch(
            operation_id=operation_id,
            source_path=source_path,
            chunk_source_type=CHUNK_SOURCE_TYPE,
            delete_source=delete_source,
            initial_size=initial_size,
            initial_modified_at=initial_modified_at,
            wait_for_source_change=wait_for_source_change,
        ),
    )
