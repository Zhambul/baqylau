# Copyright (c) 2026 Zhambyl Yermagambet
"""Project accepted facts into extension entries and records."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.schemas import SchemaSet

from domain.entries import EntryTypeName
from domain.ids import SessionId
from extensions import projection_pass
from extensions.projection_models import ProjectorPackage
from repository.impl.sqlite import extension_projections, extension_records
from tests import (
    projection_pass_fixture as fixture,
    sqlite_repository_dependencies as repository_dependencies,
    sqlite_test_dependencies as test_dependencies,
)

if TYPE_CHECKING:
    from baqylau_extension_api.contracts import projection

HISTORY = "default"
GENERATION = "default"
RUNTIME_REVISION = "runtime-one"
EXPECTED_ENTRY_COUNT = 2


def build_pass(
    main: repository_dependencies.SqliteDatabase,
) -> projection_pass.ProjectionPass:
    """Build a projection pass over hand-built facts and real storage.

    Returns:
        The pass with one stored fact.

    """
    return projection_pass.ProjectionPass(
        facts=fixture.FakeFacts(stored=(fixture.fact(fixture.FACT_ID, fixture.FACT_CURSOR),)),
        record_reader=extension_records.SqliteExtensionRecordRepository(main),
        store=extension_projections.SqliteExtensionProjectionRepository(main),
    )


def package(projector: projection.ExtensionProjector | None = None) -> ProjectorPackage:
    """Build one projector package for the fixture owner.

    Returns:
        The complete projector package.

    """
    return ProjectorPackage(
        extension_id=fixture.OWNER,
        runtime_revision=RUNTIME_REVISION,
        settings_revision=0,
        manifest=fixture.MANIFEST,
        schemas=SchemaSet(fixture.MANIFEST.schemas),
        projector=projector or fixture.LocalProjector(),
    )


def entry_ids(store: test_dependencies.SqliteSessionDataRepository) -> list[str]:
    """Read the feed entry identities of the fixture session.

    Returns:
        The stored entry identities, oldest first.

    """
    entries = store.entries_page(SessionId(fixture.SCOPE.session_id), limit=10).entries
    return [entry.entry_id for entry in entries]


def test_projection_commits_entries_records(
    main: repository_dependencies.SqliteDatabase,
) -> None:
    """One projection commits its entries, its record, and its cursor together."""
    pass_ = build_pass(main)
    assert pass_.run(package(), fixture.SCOPE) == 1

    session_store = test_dependencies.SqliteSessionDataRepository(main)
    entries = session_store.entries_page(SessionId(fixture.SCOPE.session_id), limit=10).entries
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
    pass_ = build_pass(main)
    assert pass_.run(package(), fixture.SCOPE) == 1
    assert pass_.run(package(), fixture.SCOPE) == 0

    session_store = test_dependencies.SqliteSessionDataRepository(main)
    entries = session_store.entries_page(SessionId(fixture.SCOPE.session_id), limit=10).entries
    assert len(entries) == EXPECTED_ENTRY_COUNT


def test_projection_rejects_a_foreign_key(main: repository_dependencies.SqliteDatabase) -> None:
    """A selected key owned by another package rejects the whole projection."""
    pass_ = build_pass(main)
    with pytest.raises(ExtensionContractError, match="context owner and scope"):
        pass_.run(package(fixture.ForeignKeyProjector()), fixture.SCOPE)

    session_store = test_dependencies.SqliteSessionDataRepository(main)
    assert not session_store.entries_page(SessionId(fixture.SCOPE.session_id), limit=10).entries


def test_projection_rejects_a_foreign_entry(main: repository_dependencies.SqliteDatabase) -> None:
    """An entry which names a fact outside its request rejects the whole projection."""
    pass_ = build_pass(main)
    with pytest.raises(ExtensionContractError, match="fact in this request"):
        pass_.run(package(fixture.ForeignEntryProjector()), fixture.SCOPE)

    session_store = test_dependencies.SqliteSessionDataRepository(main)
    assert not session_store.entries_page(SessionId(fixture.SCOPE.session_id), limit=10).entries
