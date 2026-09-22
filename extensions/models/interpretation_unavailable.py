# Copyright (c) 2026 Zhambyl Yermagambet
"""Check host preflight failures against actual original bytes and selected owners."""

import hashlib

from baqylau_extension_api.models.canonical import CoreFact
from baqylau_extension_api.models.content import MAX_CONTENT_BYTES

from domain.records import RecordedTranslationDecision
from extensions.models import interpretation_lifecycle as lifecycle
from extensions.models.interpretation_context import InterpretationContext
from extensions.models.interpretation_steps import CoreLifecycleStep, UnavailableInputReason, UnavailableInputStep
from extensions.models.interpretations import InterpretationProposal
from extensions.models.observations import ExtensionObservation
from extensions.models.processing_input import original_bytes

UNAVAILABLE_TRANSLATOR_VERSION = "unavailable"


def unavailable_input(context: InterpretationContext) -> UnavailableInputStep | None:
    """Select only a failure that the host can prove before any transform or decoder call.

    Returns:
        Checked content identity and failure reason, or no preflight failure.

    """
    original = context.original.observation
    content = original_bytes(context.original)
    if len(content) > MAX_CONTENT_BYTES:
        return _step(content, "content_limit")
    if isinstance(original, ExtensionObservation):
        owner = original.candidate.document.schema_ref.owner
        if owner not in context.packages:
            return _step(content, "owner_disabled")
    return None


def unavailable_proposal(
    context: InterpretationContext, step: UnavailableInputStep, required: CoreLifecycleStep | None = None,
) -> InterpretationProposal:
    """Make unavailable input terminal for this history without removing its original.

    Returns:
        A complete failed or unknown verdict with no invented worker execution.

    """
    if required is not None:
        return _required_proposal(context, step, required)
    content_limit = step.reason == "content_limit"
    return InterpretationProposal(
        format_version=2,
        binding=context.binding, translator_version=UNAVAILABLE_TRANSLATOR_VERSION, steps=(step,),
        decision=(RecordedTranslationDecision.TRANSLATION_FAILED if content_limit
                  else RecordedTranslationDecision.IGNORED_UNKNOWN),
        reason=("Original input exceeds the worker content limit" if content_limit
                else "The source extension is not enabled in this runtime"),
    )


def validate_unavailable(context: InterpretationContext, proposal: InterpretationProposal) -> tuple[CoreFact, ...]:
    """Require the exact host reason and byte identity before accepting an unavailable verdict.

    Returns:
        Required facts which still need normal fact and cause validation.

    Raises:
        ValueError: If a caller tries to skip available input or invents its failure evidence.

    """
    step = unavailable_input(context)
    required = _required_step(context, proposal)
    if step is None or proposal != unavailable_proposal(context, step, required):
        message = "unavailable interpretation does not match its original input or runtime"
        raise ValueError(message)
    return () if required is None else required.facts


def _step(content: bytes, reason: UnavailableInputReason) -> UnavailableInputStep:
    return UnavailableInputStep(
        reason=reason, content_byte_length=len(content), content_digest=hashlib.sha256(content).hexdigest(),
    )


def _required_proposal(
    context: InterpretationContext, step: UnavailableInputStep, required: CoreLifecycleStep,
) -> InterpretationProposal:
    return InterpretationProposal(
        format_version=2, binding=context.binding, translator_version=required.translator_version,
        steps=(required, step), facts=lifecycle.combined_facts(required.facts, ()),
        decision=(RecordedTranslationDecision.TRANSLATED if required.facts
                  else RecordedTranslationDecision.TRANSLATION_FAILED),
        reason="Original activity exceeds the worker content limit; required lifecycle is retained",
    )


def _required_step(context: InterpretationContext, proposal: InterpretationProposal) -> CoreLifecycleStep | None:
    if isinstance(context.original.observation, ExtensionObservation):
        return None
    if not proposal.steps or not isinstance(proposal.steps[0], CoreLifecycleStep):
        message = "unavailable core activity still requires its original lifecycle pass"
        raise ValueError(message)
    required = proposal.steps[0]
    lifecycle.validate_lifecycle(context, required, proposal.translator_version)
    return required
