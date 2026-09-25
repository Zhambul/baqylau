# Copyright (c) 2026 Zhambyl Yermagambet
"""Build a projection pass and a projector package over the projection fixture."""

from __future__ import annotations

from typing import TYPE_CHECKING

from baqylau_extension_api.schemas import SchemaSet

from extensions import projection_pass
from extensions.models.registry import RuntimeSettings
from extensions.projector_packages import ProjectorPackage
from repository.impl.sqlite import extension_projections, extension_records
from tests import projection_pass_fixture as fixture

if TYPE_CHECKING:
    from baqylau_extension_api.contracts import projection

    from repository.impl.sqlite.connection import SqliteDatabase

RUNTIME_REVISION = "runtime-one"


def build_pass(main: SqliteDatabase) -> projection_pass.ProjectionPass:
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
        settings=RuntimeSettings(),
        manifest=fixture.MANIFEST,
        schemas=SchemaSet(fixture.MANIFEST.schemas),
        projector=projector or fixture.LocalProjector(),
    )
