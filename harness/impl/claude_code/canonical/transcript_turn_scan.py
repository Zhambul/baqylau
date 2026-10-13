# Copyright (c) 2026 Zhambyl Yermagambet
"""Recover Claude prompt ancestry from transcript history."""

from pydantic import ValidationError

from harness.impl.claude_code.canonical import records
from harness.impl.claude_code.canonical.transcript_assignment_scan import _binary_lines
from harness.impl.claude_code.canonical.transcript_model_core import PromptTranscriptRecord
from harness.impl.claude_code.canonical.transcript_model_notifications import AncestryLine
from harness.impl.claude_code.canonical.transcript_parser import parse_line


def is_task_prompt(document: records.TranscriptDocument) -> bool:
    """Check for a delivered task notification.

    Returns:
        True if the record is a task prompt, not a queue update.

    """
    return (
        document.type == "user"
        and document.origin is not None
        and document.origin.kind == "task-notification"
    )


def prompt_turn_before(
    path: str,
    before_position: str,
    parent_uuid: str | None,
) -> AncestryLine | None:
    """Find the prompt ancestor of a response after an application restart.

    Returns:
        The prompt record that opened the turn of the response.

    """
    if parent_uuid is None:
        return None
    try:
        end_position = int(before_position)
    except ValueError:
        return None
    return _prompt_ancestor(parent_uuid, _turn_ancestry(path, end_position))


def _turn_ancestry(
    path: str,
    end_position: int,
) -> dict[str, AncestryLine]:
    ancestry_lines: dict[str, AncestryLine] = {}
    for line in _binary_lines(path, end_position):
        ancestry = _ancestry_line(line)
        if ancestry is not None:
            ancestry_lines[ancestry.identity] = ancestry
    return ancestry_lines


def _ancestry_line(line: bytes) -> AncestryLine | None:
    try:
        document = records.TranscriptDocument.model_validate_json(line)
    except ValidationError:
        return None
    if document.uuid is None:
        return None
    try:
        parsed = parse_line(line.decode())
    except (UnicodeDecodeError, ValidationError):
        parsed = None
    is_prompt = is_task_prompt(document) or (isinstance(parsed, PromptTranscriptRecord) and not parsed.meta)
    return AncestryLine(document.uuid, document.parent_uuid, is_prompt, document.prompt_id)


def _prompt_ancestor(parent_uuid: str, ancestry_lines: dict[str, AncestryLine]) -> AncestryLine | None:
    identity: str | None = parent_uuid
    visited: set[str] = set()
    while identity is not None and identity not in visited:
        ancestry = ancestry_lines.get(identity)
        if ancestry is None:
            return None
        if ancestry.is_prompt:
            return ancestry
        visited.add(identity)
        identity = ancestry.parent_identity
    return None
