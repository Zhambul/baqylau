# Copyright (c) 2026 Zhambyl Yermagambet
"""A large followed output is few raw events, and each one fits the worker content limit."""

from __future__ import annotations

from typing import TYPE_CHECKING

from baqylau_extension_api.models.content import MAX_CONTENT_BYTES

from domain import ids, shells, work_state
from engine.interpret import output_source

if TYPE_CHECKING:
    from pathlib import Path

LARGE_OUTPUT_BYTES = 4 * MAX_CONTENT_BYTES
MAXIMUM_RAW_EVENTS = 8
BYTE_VALUES = 256
ALL_BYTES = bytes(range(BYTE_VALUES))


def _source(output_path: Path) -> output_source.ShellOutputRawEventSource:
    following = shells.ShellOutputFollowing(
        session_id=ids.SessionId("session-one"),
        shell_id=ids.ShellId("shell-one"),
        harness=ids.HarnessName("claude_code"),
        actor_id=ids.ActorId("session-one:lead"),
        parent_actor_id=None,
        source_path=str(output_path),
        chunk_source_type="foreground_output",
        delete_source=False,
        initial_size=0,
        initial_modified_at=0,
        wait_for_source_change=False,
        until=work_state.ShellFollowUntil.SHELL_FINISHED,
        state=shells.ShellFollowState.ACTIVE,
        created_at=0,
    )
    # A source that only reads never uses its repository.
    return output_source.ShellOutputRawEventSource(following, shell_output_repository=None)  # type: ignore[arg-type]


def test_large_output_is_few_raw_events(tmp_path: Path) -> None:
    """Four mebibytes of output are at most eight raw events, and a worker can take each one."""
    output_path = tmp_path / "large.out"
    output_path.write_bytes(ALL_BYTES * (LARGE_OUTPUT_BYTES // len(ALL_BYTES)))

    raw_events = _source(output_path).read(None)

    assert len(raw_events) <= MAXIMUM_RAW_EVENTS
    assert all(len(raw_event.payload) <= MAX_CONTENT_BYTES for raw_event in raw_events)
    assert raw_events[-1].source_position == str(LARGE_OUTPUT_BYTES)
