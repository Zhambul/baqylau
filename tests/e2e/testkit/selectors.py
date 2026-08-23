"""Explicit selectors that bind exactly one stable product identity."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TypeVar

from api.sessiondata.models.entry import FileBodyResponse, MessageBodyResponse
from sdk.client import SessionWatch
from sdk.state import AssignmentState, SessionSnapshot, ShellState
from tests.e2e.testkit.references import (
    ActorRef,
    AssignmentRef,
    FileOperationRef,
    ShellRef,
    TurnRef,
)

T = TypeVar("T")


def _one(items: Sequence[T], description: str) -> T | None:
    if len(items) > 1:
        raise AssertionError(f"{description} matched {len(items)} objects: {items}")
    return items[0] if items else None


def cursor_is_in_turn(snapshot: SessionSnapshot, reference: TurnRef, cursor: int) -> bool:
    if reference.prompt_cursor is None or cursor <= reference.prompt_cursor:
        return False
    later_prompts = [
        entry.cursor
        for entry in snapshot.entries
        if entry.cursor > reference.prompt_cursor
        and isinstance(entry.body, MessageBodyResponse)
        and entry.body.role == "user"
        and entry.body.phase == "prompt"
    ]
    boundary = min(later_prompts) if later_prompts else None
    return boundary is None or cursor < boundary


def turn(watch: SessionWatch, reference: TurnRef, timeout: float) -> TurnRef:
    if reference.turn_id is not None:
        return reference

    def found(snapshot: SessionSnapshot) -> TurnRef | None:
        prompts = [
            entry
            for entry in snapshot.entries
            if entry.cursor > reference.cursor_before
            and isinstance(entry.body, MessageBodyResponse)
            and entry.body.role == "user"
            and entry.body.phase == "prompt"
            and entry.body.content.text.strip() == reference.prompt
        ]
        prompt = _one(prompts, f"prompt {reference.prompt!r}")
        if prompt is None or prompt.turn_id is None:
            return None
        return TurnRef(
            session=reference.session,
            prompt=reference.prompt,
            cursor_before=reference.cursor_before,
            expected_prompt_count=reference.expected_prompt_count,
            turn_id=prompt.turn_id,
            prompt_cursor=prompt.cursor,
        )

    return watch.wait(
        f"one prompt for the named turn with text {reference.prompt!r}",
        found,
        timeout=timeout,
    )


def shell(
    watch: SessionWatch,
    *,
    turn_reference: TurnRef | None = None,
    actor_id: str | None = None,
    command_contains: str,
    predicate: Callable[[ShellState], bool] | None = None,
    timeout: float,
) -> ShellRef:
    def found(snapshot: SessionSnapshot) -> ShellRef | None:
        candidates = [
            item
            for item in snapshot.shells(actor_id=actor_id)
            if command_contains in item.command
            and (
                turn_reference is None
                or item.turn_id == turn_reference.turn_id
                or cursor_is_in_turn(snapshot, turn_reference, item.started_cursor)
            )
            and (predicate is None or predicate(item))
        ]
        item = _one(candidates, f"shell command containing {command_contains!r}")
        return None if item is None else ShellRef(SessionRef(snapshot.session_id), item.shell_id)

    # The local import avoids a cycle in the type-only reference module.
    from sdk.client import SessionRef  # noqa: PLC0415

    return watch.wait(
        f"one shell command containing {command_contains!r}",
        found,
        timeout=timeout,
    )


def actor(watch: SessionWatch, *, exact_name: str, timeout: float) -> ActorRef:
    def found(snapshot: SessionSnapshot) -> ActorRef | None:
        candidates = [
            item for item in snapshot.data.actors
            if item.parent_actor_id is not None and item.name.casefold() == exact_name.casefold()
        ]
        item = _one(candidates, f"subagent named {exact_name!r}")
        return None if item is None else ActorRef(SessionRef(snapshot.session_id), item.actor_id)

    from sdk.client import SessionRef  # noqa: PLC0415

    return watch.wait(f"one subagent named {exact_name!r}", found, timeout=timeout)


def assignment(
    watch: SessionWatch,
    *,
    turn_reference: TurnRef,
    exact_actor_name: str | None = None,
    timeout: float,
) -> AssignmentRef:
    def found(snapshot: SessionSnapshot) -> AssignmentRef | None:
        candidates = [
            item
            for item in snapshot.assignments()
            if (
                item.turn_id == turn_reference.turn_id
                or cursor_is_in_turn(snapshot, turn_reference, item.started_cursor)
            )
            and (
                exact_actor_name is None
                or (item.assigned_actor_name or "").casefold() == exact_actor_name.casefold()
            )
        ]
        item: AssignmentState | None = _one(candidates, "agent assignment")
        return (
            None
            if item is None
            else AssignmentRef(SessionRef(snapshot.session_id), item.assignment_id)
        )

    from sdk.client import SessionRef  # noqa: PLC0415

    return watch.wait("one agent assignment in the named turn", found, timeout=timeout)


def file_operation(
    watch: SessionWatch,
    *,
    turn_reference: TurnRef,
    path: str,
    action: str,
    timeout: float,
) -> FileOperationRef:
    def found(snapshot: SessionSnapshot) -> FileOperationRef | None:
        candidates = [
            entry
            for entry in snapshot.entries
            if (
                entry.turn_id == turn_reference.turn_id
                or cursor_is_in_turn(snapshot, turn_reference, entry.cursor)
            )
            and isinstance(entry.body, FileBodyResponse)
            and entry.body.path == path
            and entry.body.action == action
        ]
        item = _one(candidates, f"{action} file operation for {path!r}")
        return (
            None
            if item is None
            else FileOperationRef(SessionRef(snapshot.session_id), item.entry_id)
        )

    from sdk.client import SessionRef  # noqa: PLC0415

    return watch.wait(f"one {action} file operation for {path!r}", found, timeout=timeout)
