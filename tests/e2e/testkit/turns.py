"""Turn resolution and checks shared by turn and work steps."""

from __future__ import annotations

from api.sessiondata.models.entry import EntryResponse, MessageBodyResponse
from api.sessiondata.models.entry import TurnFinishedBodyResponse
from sdk.client import BaqylauClient
from sdk.state import SessionSnapshot
from tests.e2e.testkit import selectors
from tests.e2e.testkit.references import TurnRef


def enders(snapshot: SessionSnapshot, reference: TurnRef) -> list[EntryResponse]:
    start_cursor = reference.activity_cursor
    if start_cursor is None:
        raise AssertionError("turn does not have a resolved start cursor")
    if reference.actor_id is None:
        raise AssertionError("turn does not have a resolved actor identity")
    answer_after = max(
        start_cursor,
        reference.completion_after_cursor or start_cursor,
    )
    boundaries = [
        cursor
        for cursor in (
            selectors.next_prompt_cursor(snapshot, reference, after=answer_after),
            _next_completion_cursor(snapshot, reference),
        )
        if cursor is not None
    ]
    boundary = min(boundaries) if boundaries else None
    return [
        entry
        for entry in snapshot.messages(
            actor_id=reference.actor_id,
            role="assistant",
            phase="end_turn",
        )
        if entry.cursor > answer_after
        and (boundary is None or entry.cursor < boundary)
        and isinstance(entry.body, MessageBodyResponse)
        and entry.body.recipient_actor_id is None
    ]


def _next_completion_cursor(
    snapshot: SessionSnapshot,
    reference: TurnRef,
) -> int | None:
    """The next autonomous turn for this actor ends the selected answer window.

    Claude Code can write a Stop hook before the selected turn's final message.
    It can later run a notification turn without a new user prompt. The second
    completion is therefore the stable boundary that a next-prompt-only window
    cannot supply.
    """
    if reference.actor_id is None or reference.turn_id is None:
        return None
    selected_finishes = [
        entry.cursor
        for entry in snapshot.entries
        if entry.actor_id == reference.actor_id
        and entry.turn_id == reference.turn_id
        and isinstance(entry.body, TurnFinishedBodyResponse)
    ]
    if len(selected_finishes) > 1:
        raise AssertionError(
            f"turn {reference.turn_id!r} has {len(selected_finishes)} completion facts"
        )
    if not selected_finishes:
        return None
    selected_finish = selected_finishes[0]
    later = [
        entry.cursor
        for entry in snapshot.entries
        if entry.cursor > selected_finish
        and entry.actor_id == reference.actor_id
        and isinstance(entry.body, TurnFinishedBodyResponse)
    ]
    return min(later) if later else None


def resolved(
    client: BaqylauClient,
    reference: TurnRef,
    *,
    timeout: float,
) -> TurnRef:
    return selectors.turn(client.sessions.watch(reference.session), reference, timeout)


def wait_until_complete(
    client: BaqylauClient,
    reference: TurnRef,
    *,
    name: str,
    timeout: float,
) -> TurnRef:
    current = resolved(client, reference, timeout=timeout)

    def completed(snapshot: SessionSnapshot) -> bool | None:
        final_answers = enders(snapshot, current)
        if current.actor_id is None:
            raise AssertionError("turn does not have a resolved actor identity")
        prompt_count = snapshot.actor(current.actor_id).statistics.prompt_count
        if len(final_answers) > 1:
            raise AssertionError(f"turn {name!r} has {len(final_answers)} final answers")
        return (
            True
            if len(final_answers) == 1
            and prompt_count >= current.expected_prompt_count
            else None
        )

    client.sessions.watch(current.session).wait(
        f"turn {name!r} to have exactly one final answer and its prompt",
        completed,
        timeout=timeout,
    )
    return current


def final_answer_texts(client: BaqylauClient, reference: TurnRef) -> list[str]:
    return [
        entry.body.content.text.strip()
        for entry in enders(client.sessions.snapshot(reference.session), reference)
        if isinstance(entry.body, MessageBodyResponse)
    ]
