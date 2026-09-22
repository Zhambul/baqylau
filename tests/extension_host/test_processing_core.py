# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep core decoding, post-commit reactions, and cleanup under the existing engine contract."""

from dataclasses import replace
from pathlib import Path

from domain import event_session, outcomes, records, work_state
from extensions.models import interpretations, observations, processing_input
from harness.models.raw_events import TranslationResult
from repository.impl.sqlite.raw_events import SqliteRawEventRepository
from tests import sqlite_test_fixtures as native
from tests.extension_host import interpretation_fixture, processing_core_fixture as fixture


def test_core_decode_does_not_accept_early() -> None:
    """Core translation produces public facts without an early write or input reaction."""
    case = fixture.phase()
    raw = native.a_raw_event()
    captured = processing_input.capture_original(observations.StoredObservation(1, raw))
    case.plugin.translator.translate.return_value = TranslationResult(
        (replace(native.a_started_event(), payload=event_session.SessionTitleChanged(
            "title", work_state.TitleOrigin.CUSTOM,
        )),),
        records.RecordedTranslationDecision.TRANSLATED,
    )
    step = case.phase.translate_input(raw, captured.source, captured.content_snapshot)
    assert step.facts[0].raw_event_ids == (raw.raw_event_id,)
    assert step.translator_version == "core-v1"
    case.reaction.react.assert_not_called()
    case.canonical.record_translation.assert_not_called()


def test_core_reactions_follow_mixed_commit(tmp_path: Path) -> None:
    """The real transaction supplies acceptance metadata before the core callback."""
    store = interpretation_fixture.installed(tmp_path)
    case = fixture.phase()
    SqliteRawEventRepository(store.store.database).record((native.a_raw_event(),))
    original = store.original.store.find_observation(native.a_raw_event().raw_event_id)
    assert original is not None
    commit = _commit(store, case, original)
    case.phase.accept_interpretation(original, store.store.record_interpretation(commit))
    case.cache.invalidate.assert_called_once()
    case.reaction.react.assert_called_once()
    call = case.reaction.react.call_args
    assert call.args[0].cursor == 1
    case.phase.accept_interpretation(original, store.store.record_interpretation(commit))
    case.reaction.react.assert_called_once()


def test_core_finish_releases_both_resources() -> None:
    """New finish facts release translator and source state after input reactions."""
    case = fixture.phase()
    raw = native.a_raw_event()
    original = observations.StoredObservation(1, raw)
    case.plugin.translator.translate.return_value = TranslationResult(
        (replace(native.a_started_event(), payload=event_session.SessionFinished(outcomes.Outcome.SUCCEEDED, None)),),
        records.RecordedTranslationDecision.TRANSLATED, None,
    )
    step = case.phase.translate_lifecycle(raw)
    case.phase.accept_interpretation(original, interpretations.InterpretationOutcome(accepted=(
        interpretations.StoredCanonicalFact(history_revision="default", fact=step.facts[0], cursor=1, accepted_at=1000),
    )))
    case.plugin.translator.release_session.assert_called_once_with(raw.session_id)
    case.plugin.sources.release_session.assert_called_once_with(raw.session_id)


def _commit(
    store: interpretation_fixture.InterpretationCase, case: fixture.CoreCase, original: observations.StoredObservation,
) -> interpretations.InterpretationCommit:
    captured = processing_input.capture_original(original)
    required = case.phase.translate_lifecycle(native.a_raw_event())
    step = case.phase.translate_input(native.a_raw_event(), captured.source, captured.content_snapshot)
    return interpretations.InterpretationCommit(completed_at=1000, proposal=interpretations.InterpretationProposal(
        format_version=2, binding=interpretation_fixture.binding(store, original),
        translator_version=step.translator_version,
        decision=required.decision, facts=(*required.facts, *step.facts),
        steps=(required, step),
    ))
