# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep candidate interpretation facts and decoder state outside live processing."""

from pathlib import Path

from domain.ids import CanonicalEventId
from extensions.models.interpretation_steps import AppliedStep
from extensions.models.interpretations import InterpretationCommit
from tests.extension_host import interpretation_fixture as fixtures, interpretation_results as evidence

CANDIDATE = "candidate-one"


def test_replay_keeps_live_pending_and_state(tmp_path: Path) -> None:
    """A registered candidate history has its own facts, journal, and decoder state."""
    case = fixtures.installed(tmp_path)
    request = fixtures.proposal(case)
    with case.store.database.write() as connection:
        connection.execute("INSERT INTO canonical_histories VALUES(?, 2000.0)", (CANDIDATE,))
    request = _replay(request)
    accepted = case.store.record_interpretation(request).accepted
    assert case.store.facts_for_scope(
        CANDIDATE, request.proposal.binding.scope, 0, 10,
    ).facts == accepted
    assert not case.store.current_fact_page(0, 10).facts
    assert len(case.original.store.pending_observations(10)) == 1
    assert case.store.translator_state(fixtures.state_key(case)).revision == 0
    assert case.store.find_fact(
        "default", CanonicalEventId(accepted[0].fact.event_id),
    ) is None


def _replay(request: InterpretationCommit) -> InterpretationCommit:
    step = evidence.translation(request)
    context = step.request.context.model_copy(update={"history_revision": CANDIDATE, "mode": "replay"})
    step = step.model_copy(update={
        "request": step.request.model_copy(update={"context": context}),
        "outcome": AppliedStep(reply=evidence.reply(request).model_copy(update={"context": context})),
    })
    return request.model_copy(update={"proposal": request.proposal.model_copy(update={
        "binding": request.proposal.binding.model_copy(update={"history_revision": CANDIDATE, "mode": "replay"}),
        "steps": (step,),
    })})
