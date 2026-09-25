# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide the extension projection pass over the main database."""

from typing import Annotated

from fastapi import Depends

from app import (
    provider_databases as database_providers,
    provider_fact_storage as fact_providers,
)
from app.injection import singleton
from core.change_signal import ChangeSignal
from extensions.projection_pass import ProjectionPass
from repository.contract.extension_records import ExtensionRecordRepository
from repository.contract.interpretations import InterpretationRepository
from repository.contract.projection_generations import ProjectionGenerationRepository
from repository.impl.sqlite.extension_projections import SqliteExtensionProjectionRepository
from repository.impl.sqlite.extension_records import SqliteExtensionRecordRepository
from repository.impl.sqlite.projection_generations import SqliteProjectionGenerationRepository


@singleton
def projection_pass(
    facts: Annotated[InterpretationRepository, Depends(fact_providers.interpretations)],
    database: database_providers.MainDb,
) -> ProjectionPass:
    """Return the extension projection pass.

    Returns:
        The projection pass over the shared fact reader and main database.

    """
    return ProjectionPass(
        facts=facts,
        record_reader=SqliteExtensionRecordRepository(database),
        store=SqliteExtensionProjectionRepository(database),
        heads=SqliteProjectionGenerationRepository(database),
    )


Projections = Annotated[
    ProjectionPass,
    Depends(projection_pass),
]


@singleton
def projection_generations(database: database_providers.MainDb) -> ProjectionGenerationRepository:
    """Return the projection generation repository.

    Returns:
        The generation store over the main database.

    """
    return SqliteProjectionGenerationRepository(database)


Generations = Annotated[ProjectionGenerationRepository, Depends(projection_generations)]


@singleton
def extension_records(database: database_providers.MainDb) -> ExtensionRecordRepository:
    """Return the extension record read repository.

    Returns:
        The record repository over the main database.

    """
    return SqliteExtensionRecordRepository(database)


ExtensionRecords = Annotated[
    ExtensionRecordRepository,
    Depends(extension_records),
]


@singleton
def extension_changes(database: database_providers.MainDb) -> ChangeSignal:
    """Return the main database change signal.

    Returns:
        The signal the record stream subscribes to.

    Raises:
        RuntimeError: If the main database has no change signal.

    """
    changes = database.changes
    if changes is None:
        message = "main database has no change signal"
        raise RuntimeError(message)
    return changes


ExtensionChanges = Annotated[
    ChangeSignal,
    Depends(extension_changes),
]
