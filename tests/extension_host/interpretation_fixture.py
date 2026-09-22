# Copyright (c) 2026 Zhambyl Yermagambet
"""Build complete mixed interpretation requests from real stored observations."""

from dataclasses import dataclass
from pathlib import Path

from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.models import events, translation_inputs

from domain.records import RecordedTranslationDecision
from extensions.models import observations, processing_input
from extensions.models.interpretation_reads import TranslationStateKey
from extensions.models.interpretation_steps import AppliedStep, ExtensionTranslationStep
from extensions.models.interpretations import InterpretationBinding, InterpretationCommit, InterpretationProposal
from repository.impl.sqlite.interpretations import SqliteInterpretationRepository
from tests.extension_api import translation_samples
from tests.extension_host import observation_fixture as originals


@dataclass(frozen=True)
class InterpretationCase:
    """Keep the committed runtime, observation store, and mixed repository together."""

    original: originals.ObservationCase
    store: SqliteInterpretationRepository


def installed(directory: Path, manifest: ExtensionManifest | None = None) -> InterpretationCase:
    """Use retained package declarations and actual lifecycle storage, without a worker.

    Returns:
        A fixture with one active source and translator declaration.

    """
    original = originals.installed(directory, manifest)
    return InterpretationCase(original, SqliteInterpretationRepository(original.store.database))


def proposal(
    case: InterpretationCase, observation: observations.ObservationAppend | None = None,
) -> InterpretationCommit:
    """Translate the selected original through the public pure decoder models.

    Returns:
        A complete proposal whose bytes and state came from the real repositories.

    """
    append = case.original.request if observation is None else observation
    stored = case.original.store.append_observations(append).accepted[0]
    selected = binding(case, stored)
    context = events.ProcessingContext(
        extension_id=case.original.request.extension_id, runtime_revision=selected.runtime_revision,
        history_revision=selected.history_revision, scope=selected.scope, input_cursor=selected.input_cursor,
        settings_revision=0,
    )
    request = translation_request(case, selected, context)
    return InterpretationCommit(completed_at=case.original.request.observed_at + 1, proposal=InterpretationProposal(
        format_version=2,
        binding=selected, translator_version="1.0.0", decision=RecordedTranslationDecision.TRANSLATED,
        facts=(translation_samples.translated_fact(request).fact,),
        steps=(ExtensionTranslationStep(
            request=request, outcome=AppliedStep(reply=translation_samples.result(request)),
        ),),
    ))


def binding(case: InterpretationCase, stored: observations.StoredObservation) -> InterpretationBinding:
    """Capture a complete storage boundary for either original branch.

    Returns:
        The exact runtime, raw input, scope, and canonical boundary.

    """
    captured = processing_input.capture_original(stored)
    return InterpretationBinding(
        manager_id=case.original.request.manager_id, runtime_revision=case.original.request.runtime_revision,
        history_revision="default", raw_event_id=stored.observation.raw_event_id,
        input_cursor=stored.cursor, scope=captured.source.scope,
        expected_canonical_cursor=case.store.current_fact_page(0, 1).head,
    )


def translation_request(
    case: InterpretationCase, binding: InterpretationBinding, context: events.ProcessingContext,
) -> translation_inputs.ExtensionTranslationRequest:
    """Capture the declared source metadata and exact existing decoder state.

    Returns:
        The complete public decoder input.

    """
    stored = case.original.store.find_observation(binding.raw_event_id)
    assert stored is not None
    captured = processing_input.capture_original(stored)
    candidate = originals.original(stored).candidate
    return translation_inputs.ExtensionTranslationRequest(
        context=context, content_snapshot=captured.content_snapshot,
        state=case.store.translator_state(
            state_key(case).model_copy(update={"scope": binding.scope}),
        ),
        inputs=(translation_inputs.TranslationInput(
            source=captured.source, schema_ref=candidate.document.schema_ref,
            occurred_at=candidate.occurred_at, causes=candidate.causes,
        ),),
    )


def state_key(case: InterpretationCase) -> TranslationStateKey:
    """Name the fixture's one exact decoder scope and source.

    Returns:
        Its default-history state identity.

    """
    positioned = case.original.request.observations[0]
    return TranslationStateKey(
        extension_id=case.original.request.extension_id, history_revision="default", scope=case.original.request.scope,
        source_identity=positioned.observation.source_identity,
    )
