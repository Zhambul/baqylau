# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide the candidate projection rebuild and the candidate history stores."""

from functools import partial
from typing import Annotated

from fastapi import Depends

from app import provider_databases as database_providers
from app.injection import singleton
from app.provider_projections import Generations, Projections
from extensions.projection_rebuild import ProjectionRebuild
from repository.contract.history_reprocessing import HistoryReprocessingRepository, HistoryStores
from repository.impl.sqlite.candidate_records import SqliteCandidateRecordReader
from repository.impl.sqlite.history_reprocessing import SqliteHistoryReprocessingRepository
from repository.impl.sqlite.session_history import SqliteSessionHistoryReader


@singleton
def projection_rebuild(
    projections: Projections, generations: Generations, database: database_providers.MainDb,
) -> ProjectionRebuild:
    """Return the candidate projection rebuild over the live projection code.

    Returns:
        The rebuild service.

    """
    return ProjectionRebuild(
        live=projections, generations=generations,
        candidate_reader=partial(SqliteCandidateRecordReader, database),
    )


Rebuilds = Annotated[ProjectionRebuild, Depends(projection_rebuild)]


@singleton
def history_reprocessing(database: database_providers.MainDb) -> HistoryReprocessingRepository:
    """Return the candidate history repository.

    Returns:
        The reprocessing store over the main database.

    """
    return SqliteHistoryReprocessingRepository(database)


Histories = Annotated[HistoryReprocessingRepository, Depends(history_reprocessing)]


@singleton
def history_stores(histories: Histories, database: database_providers.MainDb) -> HistoryStores:
    """Pair the candidate store with the session reader for the engine.

    Returns:
        The history stores.

    """
    return HistoryStores(candidates=histories, sessions=SqliteSessionHistoryReader(database))


HistoryStoresDep = Annotated[HistoryStores, Depends(history_stores)]
