# Copyright (c) 2026 Zhambyl Yermagambet
"""Count each extension's interpretation calls in its durable health, like source reads and projections.

A crashed or hung transform worker then fails a limited number of inputs and
is disabled through the normal failure operation; it does not fail every later
input without end.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from extensions.models.interpretation_steps import (
    AppliedStep,
    CanonicalTransformStep,
    ExtensionTranslationStep,
    RawTransformStep,
)

if TYPE_CHECKING:
    from extensions.models.interpretations import InterpretationProposal
    from extensions.pass_health import HealthTracker

WHERE = "extension interpretation %s"
# Core steps have no extension owner.
EXTENSION_STEPS = (RawTransformStep, ExtensionTranslationStep, CanonicalTransformStep)


def report_steps(proposal: InterpretationProposal, health: HealthTracker | None) -> None:
    """Report each step's owner as failed or succeeded; a failed or rejected reply is a failure."""
    if health is None:
        return
    for step in proposal.steps:
        if not isinstance(step, EXTENSION_STEPS):
            continue
        owner = step.request.context.extension_id
        if isinstance(step.outcome, AppliedStep):
            health.succeeded(owner)
        else:
            health.failed(owner, WHERE % step.stage)
