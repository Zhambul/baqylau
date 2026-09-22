# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide grouped dependencies for the reaction loop."""

from typing import Annotated

from fastapi import Depends

from app import (
    loop_resources,
    provider_audit_storage as audit_providers,
    provider_controls as control_providers,
    provider_fact_storage as fact_providers,
    provider_harness_registry as registry_providers,
    provider_reactions as reaction_providers,
    provider_session_storage as session_data_providers,
    provider_session_writers as writer_providers,
)
from app.injection import singleton
from repository.contract.interpretations import CanonicalFactReader


@singleton
def reaction_data(
    events: Annotated[CanonicalFactReader, Depends(fact_providers.interpretations)],
    read_model: session_data_providers.SessionDataStore,
    event_reactions: reaction_providers.Reactions,
    entries: writer_providers.EntryWrites,
    writers: writer_providers.SessionDataWriters,
) -> loop_resources.ReactionData:
    """Return repositories and writers for the reaction loop.

    Returns:
        Repositories and writers for the reaction loop.

    """
    return loop_resources.ReactionData(
        events,
        read_model,
        event_reactions,
        entries,
        writers,
    )


ReactionDataSet = Annotated[
    loop_resources.ReactionData,
    Depends(reaction_data),
]


@singleton
def reaction_services(
    listeners: writer_providers.AppliedListeners,
    harnesses: registry_providers.Registry,
    control_service: control_providers.Controls,
    audit: audit_providers.Recorder,
) -> loop_resources.ReactionServices:
    """Return supporting services for the reaction loop.

    Returns:
        Supporting services for the reaction loop.

    """
    return loop_resources.ReactionServices(
        listeners,
        harnesses,
        control_service,
        audit,
    )


ReactionServiceSet = Annotated[
    loop_resources.ReactionServices,
    Depends(reaction_services),
]
