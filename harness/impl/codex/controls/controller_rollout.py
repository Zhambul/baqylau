# Copyright (c) 2026 Zhambyl Yermagambet
"""Own Codex control rollout."""

from __future__ import annotations

import pathlib

from pydantic import ValidationError

from harness.impl.codex.canonical import rollout, source_catalog
from harness.impl.codex.canonical.records import (
    ChatRecord,
    RolloutRecord,
    TurnAbortedRecord,
)
from harness.impl.codex.controls.controller_results import RolloutPosition
from harness.impl.codex.controls.controller_rollout_state import _has_queued_turn


def source_positions(
    rollouts: source_catalog.RolloutCatalog,
    source_reference: str,
    *,
    discover: bool = True,
) -> tuple[RolloutPosition, ...]:
    """Read the current end position of each known rollout file.

    Returns:
        The positions, with zero for files that cannot be read.

    """
    paths = {*rollouts.paths(), source_reference} if discover else {source_reference}
    positions: list[RolloutPosition] = []
    for path in paths:
        try:
            position = pathlib.Path(path).stat().st_size
        except OSError:
            position = 0
        positions.append(RolloutPosition(path, position))
    return tuple(positions)


def position_for(
    source_positions: tuple[RolloutPosition, ...],
    path: str,
) -> int:
    """Find the stored position for a rollout path.

    Returns:
        The matching position, or zero for an unknown path.

    """
    return next(
        (source_position.position for source_position in source_positions if source_position.path == path),
        0,
    )


def command_argument(message: str, prefix: str) -> str | None:
    """Read the argument of a slash command such as `/rename ` or `/goal `.

    Returns:
        The argument, or None if the message is not that command or has none.

    """
    if not message.startswith(prefix):
        return None
    argument = message.removeprefix(prefix).strip()
    return argument or None


def rollout_lines_after(path: str, position: int) -> tuple[str, ...]:
    """Read complete rollout lines after a byte position.

    Returns:
        The decoded lines.

    """
    if position < 0:
        return ()
    try:
        with pathlib.Path(path).open("rb") as source:
            source.seek(position)
            lines = source.read().split(b"\n")[:-1]
    except OSError:
        return ()
    decoded: list[str] = []
    for line in lines:
        try:
            decoded_line = line.decode()
        except UnicodeDecodeError:
            decoded_line = None
        if decoded_line is not None:
            decoded.append(decoded_line)
    return tuple(decoded)


def rollout_records_after(path: str, position: int) -> tuple[RolloutRecord | None, ...]:
    """Parse complete rollout lines after a byte position.

    Returns:
        The parsed records, with None for invalid or unsupported records.

    """
    records: list[RolloutRecord | None] = []
    for line in rollout_lines_after(path, position):
        try:
            record = rollout.parse_line(line)
        except ValidationError:
            records.append(None)
            continue
        records.append(record)
    return tuple(records)


def confirmed_prompt_after(
    path: str,
    position: int,
    expected_text: str,
) -> bool:
    """Check new rollout records for the expected user prompt.

    Returns:
        True if a user record contains exactly the expected text.

    """
    for line in rollout_lines_after(path, position):
        try:
            record = rollout.parse_line(line)
        except ValidationError:
            continue
        if isinstance(record, ChatRecord) and record.role == "user" and record.text == expected_text:
            return True
    return False


def rollout_abort_state(path: str, position: int) -> tuple[bool, bool]:
    """Read abort data and check for a later queued turn.

    Returns:
        Whether an abort exists and whether a queued turn follows it.

    """
    records = rollout_records_after(path, position)
    abort_index = None
    for index, record in enumerate(records):
        if abort_index is None and isinstance(record, TurnAbortedRecord):
            abort_index = index
    if abort_index is None:
        return False, False
    queued = _has_queued_turn(records, abort_index + 1)
    return True, queued
