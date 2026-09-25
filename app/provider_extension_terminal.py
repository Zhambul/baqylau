# Copyright (c) 2026 Zhambyl Yermagambet
"""Read the recorded snapshot that an extension terminal view presents."""

from typing import Annotated

from fastapi import Depends

from app.injection import singleton
from app.provider_databases import MainDb
from app.provider_extension_policy import WorkerLimits
from app.provider_extension_registry import CallLedger
from extensions.presentation_snapshots import PresentationSnapshots
from extensions.query_authority import QueryAuthority
from repository.impl.sqlite.extension_projections import SqliteExtensionProjectionRepository
from repository.impl.sqlite.projection_generations import SqliteProjectionGenerationRepository


@singleton
def presentation_snapshots(database: MainDb, ledger: CallLedger, limits: WorkerLimits) -> PresentationSnapshots:
    """Use the owner's committed projection cursor in its live generation, and the call deadline for view queries.

    Returns:
        The snapshot reader of terminal views.

    """
    return PresentationSnapshots(
        SqliteExtensionProjectionRepository(database), SqliteProjectionGenerationRepository(database),
        QueryAuthority(ledger, limits.request_seconds),
    )


Snapshots = Annotated[PresentationSnapshots, Depends(presentation_snapshots)]
