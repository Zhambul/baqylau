# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide canonical fact and shell-output repositories."""

from typing import Annotated

from fastapi import Depends

from app import provider_databases as database_providers
from app.injection import singleton
from repository.contract import (
    facts,
    interpretations as interpretation_contract,
    observations as observation_contract,
    shell_output as shell_output_contract,
    source_reads as source_contract,
)
from repository.impl.sqlite import (
    canonical_events as sqlite_canonical_events,
    interpretations as sqlite_interpretations,
    observations as sqlite_observations,
    raw_events as sqlite_raw_events,
    shell_output as sqlite_shell_output,
    source_reads as sqlite_source_reads,
)


@singleton
def canonical_events(
    database: database_providers.MainDb,
) -> facts.CanonicalEventRepository:
    """Return canonical event storage.

    Returns:
        Canonical event storage.

    """
    return sqlite_canonical_events.SqliteCanonicalEventRepository(database)


CanonicalEvents = Annotated[
    facts.CanonicalEventRepository,
    Depends(canonical_events),
]


@singleton
def raw_events(database: database_providers.MainDb) -> facts.RawEventRepository:
    """Return raw event storage.

    Returns:
        Raw event storage.

    """
    return sqlite_raw_events.SqliteRawEventRepository(database)


RawEvents = Annotated[facts.RawEventRepository, Depends(raw_events)]


@singleton
def observations(database: database_providers.MainDb) -> observation_contract.ObservationRepository:
    """Share the raw store and queue for core and extension observations.

    Returns:
        Typed extension writes and mixed original observation reads.

    """
    return sqlite_observations.SqliteObservationRepository(database)


@singleton
def interpretations(database: database_providers.MainDb) -> interpretation_contract.InterpretationRepository:
    """Share complete mixed writes and history reads without loading feature code.

    Returns:
        The typed interpretation storage boundary.

    """
    return sqlite_interpretations.SqliteInterpretationRepository(database)


@singleton
def extension_sources(database: database_providers.MainDb) -> source_contract.ExtensionSourceRepository:
    """Provide one transaction for source output and resume progress.

    Returns:
        The typed source storage boundary.

    """
    return sqlite_source_reads.SqliteExtensionSourceRepository(database)


@singleton
def shell_output(
    database: database_providers.MainDb,
) -> shell_output_contract.ShellOutputRepository:
    """Return followed shell-output storage.

    Returns:
        Followed shell-output storage.

    """
    return sqlite_shell_output.SqliteShellOutputRepository(database)


ShellOutput = Annotated[
    shell_output_contract.ShellOutputRepository,
    Depends(shell_output),
]
