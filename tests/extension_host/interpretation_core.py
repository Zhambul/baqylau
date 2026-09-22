# Copyright (c) 2026 Zhambyl Yermagambet
"""Build real core translation and mixed session output without worker or engine calls."""

import hashlib
from dataclasses import replace

from baqylau_extension_api.core.sessions import SessionFinished
from baqylau_extension_api.models import scopes, translation_results
from baqylau_extension_api.translation_identity import TranslationIdentity, translated_event_id

from domain.records import RecordedTranslationDecision
from extensions.mapper.core_events import public_candidate
from extensions.models import interpretation_steps as steps, interpretations, processing_input
from repository.impl.sqlite import raw_events
from tests import sqlite_test_fixtures as native
from tests.extension_api import samples, transform_samples
from tests.extension_host import (
    interpretation_fixture as fixtures,
    interpretation_results as evidence,
    observation_fixture,
)


def core_proposal(case: fixtures.InterpretationCase) -> interpretations.InterpretationCommit:
    """Use an actual stored harness input and the existing core mapper.

    Returns:
        One complete core-only proposal through the mixed repository protocol.

    """
    raw = native.a_raw_event()
    raw_events.SqliteRawEventRepository(case.store.database).record((raw,))
    stored = case.original.store.find_observation(raw.raw_event_id)
    assert stored is not None
    captured = processing_input.capture_original(stored)
    fact = public_candidate(replace(native.a_started_event(), raw_event_ids=(raw.raw_event_id,)))
    return interpretations.InterpretationCommit(
        completed_at=raw.observed_at + 1, proposal=interpretations.InterpretationProposal(
            format_version=2, binding=fixtures.binding(case, stored), translator_version="core-v1",
            decision=RecordedTranslationDecision.TRANSLATED, facts=(fact,),
            steps=(steps.CoreLifecycleStep(
                content_byte_length=len(raw.payload), content_digest=hashlib.sha256(raw.payload).hexdigest(),
                translator_version="core-v1",
                decision=RecordedTranslationDecision.TRANSLATED, facts=(fact,),
            ), steps.CoreActivityStep(
                source=captured.source, content_snapshot=captured.content_snapshot, translator_version="core-v1",
                decision=RecordedTranslationDecision.IGNORED_NONSEMANTIC,
            )),
        ),
    )


def mixed_proposal(
    case: fixtures.InterpretationCase, *, finished: bool = False,
) -> interpretations.InterpretationCommit:
    """Use a session-scoped extension original with ordered core and extension output.

    Returns:
        A decoder proposal with two distinct stable keys.

    """
    request = fixtures.proposal(case, observation_fixture.select_scope(case.original.request, samples.SESSION))
    response = evidence.reply(request)
    decision = response.decisions[0]
    assert isinstance(decision, translation_results.TranslatedInput)
    decision = decision.model_copy(update={
        "facts": (_core_output(request, finished=finished), *decision.facts),
    })
    return evidence.with_reply(request, response.model_copy(update={"decisions": (decision,)}))


def _core_output(
    request: interpretations.InterpretationCommit, *, finished: bool,
) -> translation_results.TranslatedFact:
    scope = request.proposal.binding.scope
    assert isinstance(scope, scopes.SessionScope)
    selected = evidence.translation(request).request.context
    identity = TranslationIdentity(extension_id=selected.extension_id, scope=scope, fact_key="core")
    fact = transform_samples.core_fact().model_copy(update={
        "event_id": translated_event_id(identity), "scope": scope,
        "raw_event_ids": (request.proposal.binding.raw_event_id,),
    })
    if finished:
        fact = fact.model_copy(update={"payload": SessionFinished(outcome="succeeded", reason=None)})
    return translation_results.TranslatedFact(fact_key=identity.fact_key, fact=fact)
