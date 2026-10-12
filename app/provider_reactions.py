# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide reactions to committed canonical events."""

from typing import Annotated

from fastapi import Depends

from app import provider_audit_storage, provider_reaction_resources as resource_providers
from app.injection import singleton
from dashboard.services import queued_prompts
from engine.interpret import reactions as interpreter_reactions
from harness import contract
from naming.reaction import AutomaticNamingReaction
from terminal.panes.reaction import PaneCanonicalEventReaction
from terminal.panes.reaction_thread import PaneReactionThread


@singleton
def pane_reaction(
    terminal_resources: resource_providers.ReactionTerminal,
    audit: provider_audit_storage.Recorder,
) -> PaneReactionThread:
    """Return the pane reaction, which runs on its own thread.

    Returns:
        The pane reaction.

    """
    return PaneReactionThread(
        PaneCanonicalEventReaction(
            terminal_resources.terminal_adapter,
            terminal_resources.session_repository,
            terminal_resources.pane_width_service,
        ),
        audit,
    )


PaneReaction = Annotated[PaneReactionThread, Depends(pane_reaction)]


@singleton
def reactions(
    terminal_resources: resource_providers.ReactionTerminal,
    control_resources: resource_providers.ReactionControl,
    panes: PaneReaction,
) -> tuple[contract.CanonicalEventReaction, ...]:
    """Return ordered reactions to committed canonical events.

    Returns:
        Ordered reactions to committed canonical events.

    """
    return (
        AutomaticNamingReaction(
            control_resources.harness_registry,
            control_resources.naming_job_repository,
        ),
        control_resources.session_renamer,
        panes,
        queued_prompts.QueuedPromptCanonicalEventReaction(
            terminal_resources.workspace_repository,
        ),
        interpreter_reactions.InterruptCanonicalEventReaction(
            control_resources.interrupt_registry,
        ),
    )


Reactions = Annotated[
    tuple[contract.CanonicalEventReaction, ...],
    Depends(reactions),
]
