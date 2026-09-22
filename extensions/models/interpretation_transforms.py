# Copyright (c) 2026 Zhambyl Yermagambet
"""Validate selected transform input and apply its operations to the complete batch."""

from baqylau_extension_api.models import canonical, transforms
from baqylau_extension_api.processing.canonical import apply_canonical_transform
from baqylau_extension_api.processing.raw import apply_raw_transform
from baqylau_extension_api.schemas import SchemaSet

from extensions.models import interpretation_selections as selection
from extensions.models.interpretation_batches import RawTrace
from extensions.models.interpretation_context import InterpretationContext, ProcessingPackage
from extensions.models.interpretation_lifecycle import require_activity
from extensions.models.interpretation_steps import AppliedStep, CanonicalTransformStep, RawTransformStep


def apply_raw_step(context: InterpretationContext, current: RawTrace, step: RawTransformStep) -> RawTrace:
    """Validate a selected call and preserve all unselected source positions.

    A core raw change cannot remove the required lifecycle facts: the host
    translates them from the stored original before any worker call, and the
    activity pass only changes what the decoder reads afterwards.

    Returns:
        The complete next raw batch, or unchanged input after failure.

    """
    package = context.check_step(step.request.context, "raw_transformer")
    complete = _raw_request(package, current, step)
    if isinstance(step.outcome, AppliedStep):
        apply_raw_transform(step.request, step.outcome.reply)
        output = apply_raw_transform(complete, step.outcome.reply)
        return RawTrace(output.inputs, output.content_snapshot)
    return current


def _raw_request(
    package: ProcessingPackage, current: RawTrace, step: RawTransformStep,
) -> transforms.RawTransformRequest:
    selected = selection.raw_inputs(package, current.inputs)
    expected_content = selection.selected_content(current.content_snapshot, selected)
    supplied = step.request.inputs, step.request.content_snapshot
    if not selected or supplied != (selected, expected_content):
        message = "raw step does not match its selected input and content"
        raise ValueError(message)
    return transforms.RawTransformRequest(
        context=step.request.context, inputs=current.inputs, content_snapshot=current.content_snapshot,
    )


def apply_canonical_step(
    context: InterpretationContext, current: tuple[canonical.CanonicalFact, ...], step: CanonicalTransformStep,
) -> tuple[canonical.CanonicalFact, ...]:
    """Preserve unselected facts and keep generated facts moving forward.

    Returns:
        The complete next canonical batch, or unchanged input after failure.

    """
    complete = _canonical_request(context, current, step)
    if isinstance(step.outcome, AppliedStep):
        declarations = (package.manifest for package in context.packages.values())
        schemas = SchemaSet(tuple(schema for manifest in declarations for schema in manifest.schemas))
        apply_canonical_transform(step.request, step.outcome.reply, schemas)
        output = apply_canonical_transform(complete, step.outcome.reply, schemas)
        selection.require_lifecycle_identity(current, output)
        require_activity(output)
        return output
    return current


def _canonical_request(
    context: InterpretationContext, current: tuple[canonical.CanonicalFact, ...], step: CanonicalTransformStep,
) -> transforms.CanonicalTransformRequest:
    package = context.check_step(step.request.context, "canonical_transformer")
    selected = selection.canonical_inputs(package, current)
    if not selected or step.request.inputs != selected:
        message = "canonical step does not match its selected input"
        raise ValueError(message)
    if step.request.prior_state.after_cursor != context.binding.expected_canonical_cursor:
        message = "canonical step changed its captured prior cursor"
        raise ValueError(message)
    return transforms.CanonicalTransformRequest(
        context=step.request.context, inputs=current, prior_state=step.request.prior_state,
    )
