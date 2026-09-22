# Copyright (c) 2026 Zhambyl Yermagambet
"""Build a canonical request from real accepted prior facts."""

from baqylau_extension_api.models import canonical, documents, transforms

from extensions.models.interpretation_steps import CanonicalTransformStep, FailedStep
from extensions.models.interpretations import InterpretationCommit
from tests.extension_host import (
    interpretation_fixture as fixtures,
    interpretation_transforms as operations,
    observation_requests as originals,
)


def proposal(case: fixtures.InterpretationCase) -> InterpretationCommit:
    """Accept one fact, then capture it as prior state for a later observation.

    Returns:
        A complete canonical keep operation with an actual prior snapshot.

    """
    seed = operations.apply(fixtures.proposal(case), transforms.CanonicalTransformResult())
    accepted = case.store.record_interpretation(seed).accepted[0]
    request = fixtures.proposal(case, originals.new_key(case.original.request, "later"))
    request = operations.apply(request, transforms.CanonicalTransformResult())
    prior = canonical.CommittedFact(fact=accepted.fact, cursor=accepted.cursor, accepted_at=accepted.accepted_at)
    return with_prior(request, prior)


def prior_fact(request: InterpretationCommit) -> canonical.CommittedFact:
    """Select the captured prior body without rereading current state.

    Returns:
        The fixture's first prior fact.

    """
    step = request.proposal.steps[-1]
    assert isinstance(step, CanonicalTransformStep)
    return step.request.prior_state.facts[0]


def with_prior(request: InterpretationCommit, prior: canonical.CommittedFact) -> InterpretationCommit:
    """Replace only a prior snapshot, keeping final output unchanged.

    Returns:
        The complete proposal for acceptance checks.

    """
    snapshot = canonical.CoreStateSnapshot(
        after_cursor=request.proposal.binding.expected_canonical_cursor, facts=(prior,),
    )
    return with_snapshot(request, snapshot)


def with_snapshot(
    request: InterpretationCommit, snapshot: canonical.CoreStateSnapshot, *, failed: bool = False,
) -> InterpretationCommit:
    """Replace only the recorded prior snapshot.

    Returns:
        A complete request with unchanged candidate facts and transform result.

    """
    step = request.proposal.steps[-1]
    assert isinstance(step, CanonicalTransformStep)
    step = step.model_copy(update={"request": step.request.model_copy(
        update={"prior_state": snapshot},
    )})
    if failed:
        step = step.model_copy(update={"outcome": FailedStep[transforms.CanonicalTransformResult](
            diagnostic=documents.Diagnostic(code="timeout", message="The worker timed out"),
        )})
    return request.model_copy(update={"proposal": request.proposal.model_copy(update={
        "steps": (*request.proposal.steps[:-1], step),
    })})
