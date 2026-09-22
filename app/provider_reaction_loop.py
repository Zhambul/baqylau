# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide the canonical reaction loop."""

from app import provider_notifications as notification_providers, provider_reaction_loop_resources as resource_providers
from app.injection import singleton
from engine.react import loop


@singleton
def reaction_loop(
    reaction_data: resource_providers.ReactionDataSet,
    services: resource_providers.ReactionServiceSet,
    updates: notification_providers.ApplicationUpdates,
) -> loop.ReactionLoop:
    """Return the canonical reaction loop.

    Returns:
        Canonical reaction loop.

    """
    return loop.ReactionLoop(
        loop.ReactionLoopDependencies(
            canonical_fact_reader=reaction_data.canonical_fact_reader,
            session_data_repository=reaction_data.session_data_repository,
            reactions=reaction_data.event_reactions,
            session_entry_writer=reaction_data.entry_writer,
            writers=reaction_data.session_data_writers,
            listeners=services.applied_listeners,
            harness_registry=services.harness_registry,
            harness_reactor_context=services.control_service,
            audit_recorder=services.audit_recorder,
            changes=updates.changes,
        ),
    )
