# Copyright (c) 2026 Zhambyl Yermagambet
"""Read and require the stored evidence of the lifecycle daemon cases."""

from baqylau_extension_api.models import transforms

from extensions.models import interpretation_steps as steps, interpretations
from tests.extension_host import lifecycle_daemon_fixture as fixture


def only_journal(case: fixture.LifecycleDaemon) -> interpretations.InterpretationCommit:
    """Read the single complete journal of one case.

    Returns:
        The stored interpretation.

    """
    journals = case.journals()
    assert len(journals) == 1
    return journals[0]


def step_stages(commit: interpretations.InterpretationCommit) -> tuple[str, ...]:
    """Read the recorded step stages in journal order.

    Returns:
        The ordered stages.

    """
    return tuple(step.stage for step in commit.proposal.steps)


def fact_kinds(commit: interpretations.InterpretationCommit) -> tuple[str, ...]:
    """Read the accepted core fact kinds of one journal.

    Returns:
        The core payload kinds.

    """
    proposal_facts = commit.proposal.facts
    return tuple(fact.payload.kind for fact in proposal_facts if fact.kind == "core")


def operation_kinds(commit: interpretations.InterpretationCommit) -> tuple[str, ...]:
    """Read the final canonical reply's ordered operation kinds.

    Returns:
        The operation kinds.

    """
    step = commit.proposal.steps[-1]
    assert isinstance(step, steps.CanonicalTransformStep)
    assert isinstance(step.outcome, steps.AppliedStep)
    return tuple(operation.kind for operation in step.outcome.reply.operations)


def failed_step(commit: interpretations.InterpretationCommit) -> steps.FailedStep[transforms.CanonicalTransformResult]:
    """Read the final canonical step as an explicit failure.

    Returns:
        The failed outcome with any retained reply.

    """
    step = commit.proposal.steps[-1]
    assert isinstance(step, steps.CanonicalTransformStep)
    assert isinstance(step.outcome, steps.FailedStep)
    return step.outcome


def require_drained(case: fixture.LifecycleDaemon) -> None:
    """Require that no raw event still waits for a verdict."""
    assert case.pending_count() == 0


def require_extension_texts(case: fixture.LifecycleDaemon, expected: tuple[str, ...]) -> None:
    """Require the exact accepted extension documents."""
    assert fixture.extension_texts(case) == expected
