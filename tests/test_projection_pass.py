# Copyright (c) 2026 Zhambyl Yermagambet
"""Project accepted facts into extension entries and records."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import pytest
from baqylau_extension_api.errors import ExtensionContractError

from domain.entries import EntryTypeName
from domain.ids import SessionId
from repository.impl.sqlite import extension_projections, extension_records
from tests import (
    projection_pass_case as case,
    projection_pass_fixture as fixture,
    sqlite_repository_dependencies as repository_dependencies,
    sqlite_test_dependencies as test_dependencies,
)

if TYPE_CHECKING:
    from domain.entries import SessionEntry

HISTORY = "default"
GENERATION = "default"
EXPECTED_ENTRY_COUNT = 2


def feed(main: repository_dependencies.SqliteDatabase) -> tuple[SessionEntry, ...]:
    """Read the feed entries of the fixture session.

    Returns:
        The stored entries, oldest first.

    """
    store = test_dependencies.SqliteSessionDataRepository(main)
    return store.entries_page(SessionId(fixture.SCOPE.session_id), limit=10).entries


def test_projection_commits_entries_records(
    main: repository_dependencies.SqliteDatabase,
) -> None:
    """One projection commits its entries, its record, and its cursor together."""
    pass_ = case.build_pass(main)
    assert pass_.run(case.package(), fixture.SCOPE) == 1

    entries = feed(main)
    assert [entry.summary for entry in entries] == ["Card summary", "Card detail"]
    assert all(entry.entry_type == EntryTypeName.EXTENSION for entry in entries)

    states = extension_records.SqliteExtensionRecordRepository(main).record_states(
        (fixture.record_key(fixture.SCOPE),),
    )
    assert states[0].revision == fixture.FACT_CURSOR

    projection_store = extension_projections.SqliteExtensionProjectionRepository(main)
    assert projection_store.committed_cursor(fixture.OWNER, fixture.SCOPE, HISTORY, GENERATION) == fixture.FACT_CURSOR


def test_projection_advances_once(main: repository_dependencies.SqliteDatabase) -> None:
    """A second pass over the same facts commits nothing new."""
    pass_ = case.build_pass(main)
    assert pass_.run(case.package(), fixture.SCOPE) == 1
    assert pass_.run(case.package(), fixture.SCOPE) == 0

    entries = feed(main)
    assert len(entries) == EXPECTED_ENTRY_COUNT


def test_projection_rejects_a_foreign_key(main: repository_dependencies.SqliteDatabase) -> None:
    """A selected key owned by another package rejects the whole projection."""
    pass_ = case.build_pass(main)
    with pytest.raises(ExtensionContractError, match="context owner and scope"):
        pass_.run(case.package(fixture.ForeignKeyProjector()), fixture.SCOPE)

    assert not feed(main)


def test_projection_rejects_a_foreign_entry(main: repository_dependencies.SqliteDatabase) -> None:
    """An entry which names a fact outside its request rejects the whole projection."""
    pass_ = case.build_pass(main)
    with pytest.raises(ExtensionContractError, match="fact in this request"):
        pass_.run(case.package(fixture.ForeignEntryProjector()), fixture.SCOPE)

    assert not feed(main)


def test_unselected_facts_advance_only_the_cursor(main: repository_dependencies.SqliteDatabase) -> None:
    """A page with no selected fact type calls no projector and still moves the cursor."""
    unselected = fixture.fact(fixture.FACT_ID, fixture.FACT_CURSOR)
    unselected = unselected.model_copy(update={
        "fact": unselected.fact.model_copy(update={"event_type": "test.sample.other"}),
    })
    facts = fixture.FakeFacts(stored=(unselected,))
    pass_ = replace(case.build_pass(main), facts=facts)

    assert pass_.run(case.package(fixture.ForeignEntryProjector()), fixture.SCOPE) == 1

    projection_store = extension_projections.SqliteExtensionProjectionRepository(main)
    assert projection_store.committed_cursor(fixture.OWNER, fixture.SCOPE, HISTORY, GENERATION) == fixture.FACT_CURSOR
    assert not feed(main)
