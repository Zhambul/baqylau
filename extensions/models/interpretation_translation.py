# Copyright (c) 2026 Zhambyl Yermagambet
"""Validate each translated input and retain all proposals while selecting first logical bodies."""

from typing import NamedTuple

from baqylau_extension_api.models import canonical, content, events
from baqylau_extension_api.models.translation_results import ExtensionTranslationResult, TranslatedInput
from baqylau_extension_api.translation.inputs import validate_translation_request
from baqylau_extension_api.translation.outputs import validate_translation_documents
from baqylau_extension_api.translation.results import translated_candidates, validate_translation_result

from domain.records import RecordedTranslationDecision
from extensions.models.interpretation_context import InterpretationContext
from extensions.models.interpretation_lifecycle import require_activity
from extensions.models.interpretation_steps import AppliedStep, CoreActivityStep, ExtensionTranslationStep
from extensions.models.observations import ExtensionObservation

type TranslationStep = CoreActivityStep | ExtensionTranslationStep


class TranslationTrace(NamedTuple):
    """Keep first logical candidates separate from complete per-input evidence."""

    candidates: tuple[canonical.CanonicalFact, ...]
    proposals: tuple[canonical.CanonicalFact, ...]


def translated_inputs(step: TranslationStep) -> tuple[events.RawInput, ...]:
    """Read the exact ordered source identities covered by one decoder call.

    Returns:
        The sources, without copying or changing their metadata.

    """
    if isinstance(step, CoreActivityStep):
        return (step.source,)
    return tuple(source.source for source in step.request.inputs)


def translate_step(
    context: InterpretationContext, step: TranslationStep, bundle: content.ContentBundle, translator_version: str,
) -> TranslationTrace:
    """Check original source bytes and the decoder's complete recorded reply.

    Returns:
        The first proposal for each logical ID in this call.

    Raises:
        ValueError: If source origin, bytes, metadata, or version has changed.

    """
    if isinstance(step, CoreActivityStep):
        return _core_step(step, bundle, translator_version)
    package = context.check_step(step.request.context, "translator")
    if package.manifest.package_version != translator_version:
        message = "extension translation changed its decoder version"
        raise ValueError(message)
    _check_content(bundle, step.request.content_snapshot, translated_inputs(step))
    _check_metadata(context, step)
    if isinstance(step.outcome, AppliedStep):
        validate_translation_request(package.manifest, package.schemas, step.request)
        reply = validate_translation_result(step.request, step.outcome.reply)
        validate_translation_documents(package.manifest, package.schemas, step.request, reply)
        require_activity(_all_proposals(reply))
        return TranslationTrace(translated_candidates(reply), _all_proposals(reply))
    return TranslationTrace((), ())


def _core_step(step: CoreActivityStep, bundle: content.ContentBundle, translator_version: str) -> TranslationTrace:
    if step.source.origin != "harness" or step.translator_version != translator_version:
        message = "core translation changed its origin or decoder version"
        raise ValueError(message)
    _check_content(bundle, step.content_snapshot, (step.source,))
    require_activity(step.facts)
    if bool(step.facts) != (step.decision == RecordedTranslationDecision.TRANSLATED):
        message = "core translation verdict does not match its output"
        raise ValueError(message)
    return TranslationTrace(step.facts, step.facts)


def _all_proposals(reply: ExtensionTranslationResult) -> tuple[canonical.CanonicalFact, ...]:
    proposed: list[canonical.CanonicalFact] = []
    for decision in reply.decisions:
        if isinstance(decision, TranslatedInput):
            proposed.extend(output.fact for output in decision.facts)
    return tuple(proposed)


def _check_content(
    original: content.ContentBundle, supplied: content.ContentBundle, sources: tuple[events.RawInput, ...],
) -> None:
    needed = frozenset(source.content for source in sources)
    blobs = tuple(blob for blob in original.blobs if blob.reference in needed)
    expected = content.ContentBundle(blobs=blobs)
    if supplied != expected:
        message = "translation content differs from its recorded raw output"
        raise ValueError(message)


def _check_metadata(context: InterpretationContext, step: ExtensionTranslationStep) -> None:
    original = context.original.observation
    if not isinstance(original, ExtensionObservation):
        message = "an extension decoder requires an extension original"
        raise TypeError(message)
    expected = original.candidate.document.schema_ref
    if step.request.context.extension_id != expected.owner:
        message = "extension translation must use the original source owner"
        raise ValueError(message)
    for source in step.request.inputs:
        if (source.schema_ref != expected or source.occurred_at != original.candidate.occurred_at
                or source.causes != original.candidate.causes):
            message = "translation changed original extension metadata"
            raise ValueError(message)
