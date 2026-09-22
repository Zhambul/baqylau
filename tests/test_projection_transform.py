# Copyright (c) 2026 Zhambyl Yermagambet
"""Apply enabled projection transforms to a complete proposal."""

from __future__ import annotations

from typing import TYPE_CHECKING

from baqylau_extension_api.schemas import SchemaSet

from domain.ids import SessionId
from extensions.projection_models import ProjectionTransformerPackage
from tests import (
    projection_pass_fixture as fixture,
    projection_transform_fixture as transform_fixture,
    sqlite_repository_dependencies as repository_dependencies,
    sqlite_test_dependencies as test_dependencies,
)
from tests.test_projection_pass import RUNTIME_REVISION, build_pass, package

if TYPE_CHECKING:
    from baqylau_extension_api.contracts import projection

EXPECTED_ENTRY_COUNT = 2
TRANSFORMED_ENTRY_COUNT = 3


def transformer(transform: projection.ExtensionProjectionTransformer) -> ProjectionTransformerPackage:
    """Build one transform package for the fixture owner.

    Returns:
        The complete transform package.

    """
    return ProjectionTransformerPackage(
        extension_id=fixture.OWNER,
        runtime_revision=RUNTIME_REVISION,
        settings_revision=0,
        manifest=fixture.TRANSFORM_MANIFEST,
        schemas=SchemaSet(fixture.TRANSFORM_MANIFEST.schemas),
        transformer=transform,
    )


def test_projection_transform_drops_a_record(main: repository_dependencies.SqliteDatabase) -> None:
    """An enabled transform can drop the proposed record and keep its entries."""
    pass_ = build_pass(main)
    transformers = (transformer(transform_fixture.DropRecordTransformer()),)
    assert pass_.run(package(), fixture.SCOPE, transformers) == 1

    session_store = test_dependencies.SqliteSessionDataRepository(main)
    entries = session_store.entries_page(SessionId(fixture.SCOPE.session_id), limit=10).entries
    assert len(entries) == EXPECTED_ENTRY_COUNT


def test_projection_transform_adds_an_entry(main: repository_dependencies.SqliteDatabase) -> None:
    """An enabled transform can add one derived entry to the same commit."""
    pass_ = build_pass(main)
    transformers = (transformer(transform_fixture.AddEntryTransformer()),)
    assert pass_.run(package(), fixture.SCOPE, transformers) == 1

    session_store = test_dependencies.SqliteSessionDataRepository(main)
    entries = session_store.entries_page(SessionId(fixture.SCOPE.session_id), limit=10).entries
    assert len(entries) == TRANSFORMED_ENTRY_COUNT
    assert [entry.summary for entry in entries] == ["Card summary", "Card extra", "Card detail"]
