"""Explicit selectors that bind exactly one stable product identity."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TypeVar

from api.sessiondata.models.entry import (
    FileBodyResponse,
    MessageBodyResponse,
)
from sdk.client import SessionWatch
from sdk.state import AssignmentState, SessionSnapshot, ShellState
from tests.e2e.testkit.references import (
    ActorRef,
    AssignmentRef,
    CompactionRef,
    FileOperationRef,
    PlanRef,
    QuestionRef,
    ShellRef,
    SkillRef,
    TaskRef,
    TurnRef,
)

T = TypeVar("T")


def _one(items: Sequence[T], description: str) -> T | None:
    if len(items) > 1:
        raise AssertionError(f"{description} matched {len(items)} objects: {items}")
    return items[0] if items else None


def next_prompt_cursor(
    snapshot: SessionSnapshot,
    reference: TurnRef,
    *,
    after: int,
) -> int | None:
    if reference.actor_id is None:
        raise AssertionError("turn does not have a resolved actor identity")
    found = [
        entry.cursor
        for entry in snapshot.entries
        if entry.cursor > after
        and entry.actor_id == reference.actor_id
        and isinstance(entry.body, MessageBodyResponse)
        and entry.body.role == "user"
        and entry.body.phase == "prompt"
    ]
    return min(found) if found else None


def cursor_is_in_turn(snapshot: SessionSnapshot, reference: TurnRef, cursor: int) -> bool:
    if reference.prompt_cursor is None or cursor <= reference.prompt_cursor:
        return False
    boundary = next_prompt_cursor(snapshot, reference, after=reference.prompt_cursor)
    return boundary is None or cursor < boundary


def belongs_to_turn(
    snapshot: SessionSnapshot,
    reference: TurnRef,
    *,
    turn_id: str | None,
    cursor: int,
) -> bool:
    return turn_id == reference.turn_id or cursor_is_in_turn(snapshot, reference, cursor)


def turn(watch: SessionWatch, reference: TurnRef, timeout: float) -> TurnRef:
    if reference.turn_id is not None:
        return reference

    def found(snapshot: SessionSnapshot) -> TurnRef | None:
        prompts = [
            entry
            for entry in snapshot.entries
            if entry.cursor > reference.cursor_before
            and (reference.actor_id is None or entry.actor_id == reference.actor_id)
            and isinstance(entry.body, MessageBodyResponse)
            and entry.body.role == "user"
            and entry.body.phase == "prompt"
            and entry.body.content.text.strip() == reference.prompt
        ]
        prompt = _one(prompts, f"prompt {reference.prompt!r}")
        if (
            prompt is None
            or prompt.turn_id is None
            or not isinstance(prompt.body, MessageBodyResponse)
        ):
            return None
        body = prompt.body
        return TurnRef(
            session=reference.session,
            prompt=reference.prompt,
            cursor_before=reference.cursor_before,
            expected_prompt_count=reference.expected_prompt_count,
            actor_id=prompt.actor_id,
            turn_id=prompt.turn_id,
            prompt_cursor=prompt.cursor,
            prompt_message_id=body.message_id,
            completion_after_cursor=reference.completion_after_cursor,
        )

    return watch.wait(
        f"one prompt for the named turn with text {reference.prompt!r}",
        found,
        timeout=timeout,
    )


def launched_turn(watch: SessionWatch, timeout: float) -> TurnRef:
    """The first user turn in a newly launched session.

    The harness owns the delivered prompt text. It can add attachment paths,
    so the client reads that text instead of reconstructing it.
    """

    def found(snapshot: SessionSnapshot) -> TurnRef | None:
        prompts = [
            entry
            for entry in snapshot.entries
            if entry.actor_id == snapshot.data.session.lead_actor_id
            and isinstance(entry.body, MessageBodyResponse)
            and entry.body.role == "user"
            and entry.body.phase == "prompt"
        ]
        prompt = _one(prompts, "first user prompt in the launched session")
        if (
            prompt is None
            or prompt.turn_id is None
            or not isinstance(prompt.body, MessageBodyResponse)
        ):
            return None
        body = prompt.body
        return TurnRef(
            session=SessionRef(snapshot.session_id),
            prompt=body.content.text,
            cursor_before=0,
            expected_prompt_count=1,
            actor_id=prompt.actor_id,
            turn_id=prompt.turn_id,
            prompt_cursor=prompt.cursor,
            prompt_message_id=body.message_id,
        )

    from sdk.client import SessionRef  # noqa: PLC0415

    return watch.wait("one first user prompt in the launched session", found, timeout=timeout)


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
                or belongs_to_turn(
                    snapshot,
                    turn_reference,
                    turn_id=item.turn_id,
                    cursor=item.started_cursor,
                )
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
            if belongs_to_turn(
                snapshot,
                turn_reference,
                turn_id=item.turn_id,
                cursor=item.started_cursor,
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
            if belongs_to_turn(
                snapshot,
                turn_reference,
                turn_id=entry.turn_id,
                cursor=entry.cursor,
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


def skill(
    watch: SessionWatch,
    *,
    turn_reference: TurnRef,
    exact_name: str,
    timeout: float,
) -> SkillRef:
    def found(snapshot: SessionSnapshot) -> SkillRef | None:
        candidates = [
            item
            for item in snapshot.skills()
            if belongs_to_turn(
                snapshot,
                turn_reference,
                turn_id=item.turn_id,
                cursor=item.started_cursor,
            )
            and item.name.casefold() == exact_name.casefold()
        ]
        item = _one(candidates, f"skill named {exact_name!r}")
        return None if item is None else SkillRef(SessionRef(snapshot.session_id), item.skill_id)

    from sdk.client import SessionRef  # noqa: PLC0415

    return watch.wait(f"one skill named {exact_name!r}", found, timeout=timeout)


def question(
    watch: SessionWatch,
    *,
    turn_reference: TurnRef,
    turn_name: str,
    prompt_contains: str,
    timeout: float,
) -> QuestionRef:
    def found(snapshot: SessionSnapshot) -> QuestionRef | None:
        candidates = [
            (item, question_item)
            for item in snapshot.questions()
            if item.pending
            and belongs_to_turn(
                snapshot,
                turn_reference,
                turn_id=item.turn_id,
                cursor=item.asked_cursor,
            )
            for question_item in item.questions
            if prompt_contains in question_item.question
        ]
        item = _one(candidates, f"pending question containing {prompt_contains!r}")
        return (
            None
            if item is None
            else QuestionRef(
                SessionRef(snapshot.session_id),
                item[0].attention_id,
                item[1].question_id,
                turn_name,
            )
        )

    from sdk.client import SessionRef  # noqa: PLC0415

    return watch.wait(
        f"one pending question containing {prompt_contains!r}",
        found,
        timeout=timeout,
    )


def plan(
    watch: SessionWatch,
    *,
    turn_reference: TurnRef,
    turn_name: str,
    text_contains: str,
    timeout: float,
) -> PlanRef:
    def found(snapshot: SessionSnapshot) -> PlanRef | None:
        candidates = [
            item
            for item in snapshot.plans()
            if item.pending
            and belongs_to_turn(
                snapshot,
                turn_reference,
                turn_id=item.turn_id,
                cursor=item.proposed_cursor,
            )
            and text_contains in item.text
        ]
        item = _one(candidates, f"pending plan containing {text_contains!r}")
        return (
            None
            if item is None
            else PlanRef(SessionRef(snapshot.session_id), item.attention_id, turn_name)
        )

    from sdk.client import SessionRef  # noqa: PLC0415

    return watch.wait(f"one pending plan containing {text_contains!r}", found, timeout=timeout)


def task(
    watch: SessionWatch,
    *,
    exact_subject: str,
    timeout: float,
) -> TaskRef:
    def found(snapshot: SessionSnapshot) -> TaskRef | None:
        candidates = [
            item for item in snapshot.data.session.tasks if item.subject == exact_subject
        ]
        item = _one(candidates, f"task with subject {exact_subject!r}")
        return None if item is None else TaskRef(SessionRef(snapshot.session_id), item.task_id)

    from sdk.client import SessionRef  # noqa: PLC0415

    return watch.wait(f"one task with subject {exact_subject!r}", found, timeout=timeout)


def compaction(
    watch: SessionWatch,
    *,
    after_cursor: int,
    timeout: float,
) -> CompactionRef:
    def found(snapshot: SessionSnapshot) -> CompactionRef | None:
        candidates = [
            item for item in snapshot.compactions() if item.started_cursor > after_cursor
        ]
        item = _one(candidates, "compaction after the named control")
        return (
            None
            if item is None
            else CompactionRef(
                SessionRef(snapshot.session_id),
                item.actor_id,
                item.started_cursor,
            )
        )

    from sdk.client import SessionRef  # noqa: PLC0415

    return watch.wait("one compaction after the named control", found, timeout=timeout)
