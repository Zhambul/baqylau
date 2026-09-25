# Copyright (c) 2026 Zhambyl Yermagambet
"""Test canonical sessiondata api store pages."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from domain import (
    attention,
    content as domain_content,
    entry_attention,
    entry_conversation,
    entry_shells,
    ids as domain_ids,
    messaging,
    outcomes,
)
from domain.entry_base import TurnState
from repository.contract import session_data as session_data_contract
from tests import canonical_sessiondata_api_entries as api_entries, canonical_sessiondata_api_values as api_values

if TYPE_CHECKING:

    from repository.impl.sqlite.session_data import SqliteSessionDataRepository


@pytest.mark.usefixtures("tmp_path")
def test_page_taken_at_snapshots_cursor(session_data_store: SqliteSessionDataRepository) -> None:
    """Verify a page taken at the snapshots cursor and a stream from it never overlap.

    The whole boundary contract in one test: whatever the page ends with, the
        stream starts after — no entry twice, none missed.
    """
    read_model = session_data_store
    read_model.apply(
        api_values.SESSION,
        session_data_contract.SessionDataChanges(session=api_values.FACTS, actors=api_values.ACTOR_ROWS),
        1,
    )
    for ordinal in range(1, 4):
        read_model.apply(
            api_values.SESSION,
            session_data_contract.SessionDataChanges(
                entries=(api_entries.entry(
                    entry_conversation.MessageBody(
                        domain_ids.MessageId(f"m{ordinal}"),
                        messaging.MessageRole.USER,
                        messaging.MessagePhase.PROMPT,
                        domain_content.TextContent(api_values.PROMPT_TEXT),
                    ),
                    entry_id=domain_ids.CanonicalEventId(f"event-{ordinal}"),
                ),),
            ),
            ordinal + 1,
        )

    snapshot = read_model.read(api_values.SESSION)
    assert snapshot is not None
    page = read_model.entries_page(api_values.SESSION, at=snapshot.cursor, limit=100)
    assert [session_entry.entry_id for session_entry in page.entries] == ["event-1", "event-2", "event-3"]

    # Nothing new yet: the stream's first poll from that cursor is empty.
    assert read_model.delta(api_values.SESSION, snapshot.cursor).empty

    read_model.apply(
        api_values.SESSION,
        session_data_contract.SessionDataChanges(
            entries=(api_entries.entry(
                entry_conversation.MessageBody(
                    domain_ids.MessageId("m4"),
                    messaging.MessageRole.USER,
                    messaging.MessagePhase.PROMPT,
                    domain_content.TextContent("more"),
                ),
                entry_id=domain_ids.CanonicalEventId("event-4"),
            ),),
        ),
        5,
    )
    delta = read_model.delta(api_values.SESSION, snapshot.cursor)
    assert [session_entry.entry_id for session_entry in delta.entries] == ["event-4"]


@pytest.mark.usefixtures("tmp_path")
def test_pending_question_is_derived_and_stops(session_data_store: SqliteSessionDataRepository) -> None:
    """Verify a pending question is derived and stops being pending when answered.

    No stored flag: an asked entry whose answer has not arrived. A flag would
        be a second answer to the same question, and it could disagree with the feed
        the person is looking at.
    """
    read_model = session_data_store
    read_model.apply(
        api_values.SESSION,
        session_data_contract.SessionDataChanges(
            entries=(api_entries.entry(
                entry_attention.QuestionAskedBody(domain_ids.AttentionId("att-1"), ()),
                entry_id=domain_ids.CanonicalEventId("asked-1"),
            ),),
        ),
        1,
    )
    read_model.apply(
        api_values.SESSION,
        session_data_contract.SessionDataChanges(
            entries=(api_entries.entry(
                entry_attention.PlanProposedBody(domain_ids.AttentionId("att-2"), domain_content.TextContent("do it")),
                entry_id=domain_ids.CanonicalEventId("proposed-2"),
            ),),
        ),
        2,
    )
    assert [session_entry.entry_id for session_entry in read_model.pending_attention(api_values.SESSION)] == [
        "asked-1",
        "proposed-2",
    ]

    read_model.apply(
        api_values.SESSION,
        session_data_contract.SessionDataChanges(
            entries=(api_entries.entry(
                entry_attention.QuestionAnsweredBody(
                    domain_ids.AttentionId("att-1"),
                    (attention.AttentionAnswer(domain_ids.QuestionId("q1"), ("Yes",)),),
                ),
                entry_id=domain_ids.CanonicalEventId("answered-1"),
            ),),
        ),
        3,
    )
    read_model.apply(
        api_values.SESSION,
        session_data_contract.SessionDataChanges(
            entries=(api_entries.entry(
                entry_attention.PlanResolvedBody(domain_ids.AttentionId("att-2"), outcomes.PlanState.APPROVED),
                entry_id=domain_ids.CanonicalEventId("resolved-2"),
            ),),
        ),
        4,
    )
    assert read_model.pending_attention(api_values.SESSION) == ()


def test_pending_question_ends_with_its_turn(session_data_store: SqliteSessionDataRepository) -> None:
    """Verify a question stops being pending when its turn ends.

    A source can lose the tool join of the aborted call and never resolve the
    attention. The turn end is the one fact that cannot be lost with it: a
    harness cannot end a turn while its dialog waits.
    """
    read_model = session_data_store
    read_model.apply(
        api_values.SESSION,
        session_data_contract.SessionDataChanges(
            entries=(api_entries.entry(
                entry_attention.QuestionAskedBody(domain_ids.AttentionId("att-3"), ()),
                entry_id=domain_ids.CanonicalEventId("asked-3"),
            ),),
        ),
        1,
    )
    assert [entry.entry_id for entry in read_model.pending_attention(api_values.SESSION)] == ["asked-3"]

    read_model.apply(
        api_values.SESSION,
        session_data_contract.SessionDataChanges(
            entries=(api_entries.entry(
                entry_conversation.TurnFinishedBody(TurnState.ABORTED),
                entry_id=domain_ids.CanonicalEventId("aborted-3"),
            ),),
        ),
        2,
    )
    assert read_model.pending_attention(api_values.SESSION) == ()


@pytest.mark.usefixtures("tmp_path")
def test_entries_of_one_kind_are_read(session_data_store: SqliteSessionDataRepository) -> None:
    """Verify the entries of one kind are read without paging the whole feed."""
    read_model = session_data_store
    read_model.apply(
        api_values.SESSION,
        session_data_contract.SessionDataChanges(
            entries=(api_entries.entry(
                entry_conversation.MessageBody(
                    domain_ids.MessageId(api_values.MESSAGE_ID_TEXT),
                    messaging.MessageRole.USER,
                    messaging.MessagePhase.PROMPT,
                    domain_content.TextContent(api_values.PROMPT_TEXT),
                ),
                entry_id=domain_ids.CanonicalEventId("message-1"),
            ),),
        ),
        1,
    )
    read_model.apply(
        api_values.SESSION,
        session_data_contract.SessionDataChanges(
            entries=(api_entries.entry(
                entry_shells.ShellStartedBody(
                    domain_ids.ShellId("sh1"),
                    domain_content.TextContent("make test"),
                    outcomes.ExecutionMode.FOREGROUND,
                ),
                entry_id=domain_ids.CanonicalEventId("shell-1"),
            ),),
        ),
        2,
    )

    assert [
        session_entry.entry_id for session_entry in read_model.entries_of_types(api_values.SESSION, ("message",))
    ] == [
        "message-1",
    ]
    assert read_model.entries_of_types(api_values.SESSION, ()) == ()
