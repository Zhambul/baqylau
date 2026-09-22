# Copyright (c) 2026 Zhambyl Yermagambet
"""Build raw transform and complete decoder evidence from the same captured bytes."""

from baqylau_extension_api.models import raw_transforms, transforms, translation_inputs, translation_results
from baqylau_extension_api.processing.raw import apply_raw_transform
from baqylau_extension_api.translation.results import translated_candidates

from domain.records import RecordedTranslationDecision
from extensions.models.interpretation_steps import AppliedStep, ExtensionTranslationStep, RawTransformStep
from extensions.models.interpretations import InterpretationCommit
from tests.extension_api import translation_samples
from tests.extension_host import interpretation_results as evidence


def request(original: InterpretationCommit) -> transforms.RawTransformRequest:
    """Select the one complete recorded source before the decoder.

    Returns:
        Its original transform context, references, and exact bytes.

    """
    translation = evidence.translation(original).request
    return transforms.RawTransformRequest(
        context=translation.context, content_snapshot=translation.content_snapshot,
        inputs=tuple(source.source for source in translation.inputs),
    )


def apply(original: InterpretationCommit, response: raw_transforms.RawTransformResult) -> InterpretationCommit:
    """Reconstruct translation from the actual transformed source content.

    Returns:
        A complete journal with no change to original storage.

    """
    selected = request(original)
    output = apply_raw_transform(selected, response)
    raw_step = RawTransformStep(request=selected, outcome=AppliedStep(reply=response))
    if not output.inputs:
        return original.model_copy(update={"proposal": original.proposal.model_copy(update={
            "steps": (raw_step,), "facts": (), "decision": RecordedTranslationDecision.SUPPRESSED,
            "reason": "All raw inputs were dropped",
        })})
    translation = _translated_output(evidence.translation(original).request, output)
    assert isinstance(translation.outcome, AppliedStep)
    return original.model_copy(update={"proposal": original.proposal.model_copy(update={
        "steps": (raw_step, translation), "facts": translated_candidates(translation.outcome.reply),
    })})


def _translated_output(
    original: translation_inputs.ExtensionTranslationRequest, output: transforms.RawTransformRequest,
) -> ExtensionTranslationStep:
    inputs = tuple(
        original.inputs[0].model_copy(update={"source": source}) for source in output.inputs
    )
    selected = original.model_copy(update={
        "content_snapshot": output.content_snapshot,
        "inputs": inputs,
    })
    reply = translation_results.ExtensionTranslationResult(
        context=selected.context, state_revision=selected.state.revision, next_state=selected.state.document,
        decisions=tuple(
            translation_samples.result(selected.model_copy(update={"inputs": (source,)})).decisions[0]
            for source in selected.inputs
        ),
    )
    return ExtensionTranslationStep(request=selected, outcome=AppliedStep(reply=reply))
