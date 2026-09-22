# Copyright (c) 2026 Zhambyl Yermagambet
"""Change one typed journal field without changing its original input row."""

from extensions.models import interpretation_steps as steps, interpretations


def with_steps(
    request: interpretations.InterpretationCommit, journal: tuple[steps.InterpretationStep, ...],
) -> interpretations.InterpretationCommit:
    """Change the proposed steps while preserving the original binding and final facts.

    Returns:
        A proposal which the real repository must check again.

    """
    proposal = request.proposal.model_copy(update={"steps": journal})
    return request.model_copy(update={"proposal": proposal})


def required(request: interpretations.InterpretationCommit) -> steps.CoreLifecycleStep:
    """Read the fixture's original required pass.

    Returns:
        Its typed lifecycle step.

    """
    step = request.proposal.steps[0]
    assert isinstance(step, steps.CoreLifecycleStep)
    return step


def activity(request: interpretations.InterpretationCommit) -> steps.CoreActivityStep:
    """Read the fixture's original activity pass.

    Returns:
        Its typed activity step.

    """
    step = request.proposal.steps[1]
    assert isinstance(step, steps.CoreActivityStep)
    return step
