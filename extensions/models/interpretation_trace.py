# Copyright (c) 2026 Zhambyl Yermagambet
"""Reconstruct the final proposal from its recorded, ordered pure steps."""

from typing import NoReturn

from baqylau_extension_api.models.canonical import CanonicalFact, CoreFact

from extensions.models import (
    interpretation_lifecycle as lifecycle,
    interpretation_steps as steps,
    interpretation_transforms as transforms,
    interpretation_unavailable as unavailable,
    interpretation_verdicts as verdicts,
)
from extensions.models.interpretation_admission import validate_admission
from extensions.models.interpretation_batches import RawTrace
from extensions.models.interpretation_context import InterpretationContext
from extensions.models.interpretation_coverage import TransformCoverage
from extensions.models.interpretation_translation import TranslationStep, translate_step, translated_inputs
from extensions.models.interpretations import CURRENT_INTERPRETATION_FORMAT, InterpretationProposal
from extensions.models.observations import ExtensionObservation
from extensions.models.processing_input import capture_original


class InterpretationTrace:
    """Keep one private validation cursor; no worker can change this state."""

    def __init__(self, interpretation_context: InterpretationContext) -> None:
        """Start from the actual original source and exact stored content."""
        self.context = interpretation_context
        original = capture_original(interpretation_context.original)
        self.raw = RawTrace((original.source,), original.content_snapshot)
        self.candidates: dict[str, CanonicalFact] = {}
        self._proposals: list[CanonicalFact] = []
        self.phase = "raw"
        self.raw_coverage = TransformCoverage(interpretation_context)
        self.canonical_coverage = TransformCoverage(interpretation_context)

    def validate(self, proposal: InterpretationProposal) -> tuple[CanonicalFact, ...]:
        """Require every final fact to follow from the complete source trace.

        Returns:
            All valid proposals, including later values and suppressed facts.

        """
        required = _required_facts(self, proposal)
        self._proposals.extend(required)
        offset = 0 if isinstance(self.context.original.observation, ExtensionObservation) else 1
        for step in proposal.steps[offset:]:
            self._step(step, proposal.translator_version)
            lifecycle.require_separate_ids(required, tuple(self.candidates.values()))
        if self.raw.inputs:
            _fail("interpretation has untranslated raw output")
        if lifecycle.combined_facts(required, tuple(self.candidates.values())) != proposal.facts:
            _fail("interpretation final facts do not match its recorded steps")
        self.raw_coverage.raw(None, self.raw.inputs)
        self.canonical_coverage.canonical(None, tuple(self.candidates.values()))
        verdicts.require_verdict(proposal)
        return tuple(self._proposals)

    def _step(self, step: steps.InterpretationStep, translator_version: str) -> None:
        if isinstance(step, steps.LimitStep):
            _apply_limit(self, step)
            return
        self._step_kind(step, translator_version)

    def _step_kind(self, step: steps.InterpretationStep, translator_version: str) -> None:
        if isinstance(step, steps.RawTransformStep):
            self._raw_step(step)
        elif isinstance(step, steps.CanonicalTransformStep):
            self._canonical_step(step)
        elif isinstance(step, steps.CoreActivityStep | steps.ExtensionTranslationStep):
            self._translation_step(step, translator_version)
        else:
            _fail_type("step is not permitted after the original lifecycle boundary")

    def _raw_step(self, step: steps.RawTransformStep) -> None:
        if self.phase != step.stage:
            _fail("raw transforms must precede all translation and canonical steps")
        self.raw_coverage.raw(step.request.context.extension_id, self.raw.inputs)
        self.raw = transforms.apply_raw_step(self.context, self.raw, step)

    def _translation_step(self, step: TranslationStep, translator_version: str) -> None:
        if self.phase == "canonical":
            _fail("translation steps must precede canonical transforms")
        self.raw_coverage.raw(None, self.raw.inputs)
        self.phase = "translation"
        selected = translated_inputs(step)
        pending = self.raw.inputs
        if not selected or pending[:len(selected)] != selected:
            _fail("translation steps must cover each raw output once in its original order")
        result = translate_step(self.context, step, self.raw.content_snapshot, translator_version)
        self._proposals.extend(result.proposals)
        for fact in result.candidates:
            self.candidates.setdefault(fact.event_id, fact)
        self.raw = RawTrace(pending[len(selected):], self.raw.content_snapshot)

    def _canonical_step(self, step: steps.CanonicalTransformStep) -> None:
        if self.raw.inputs:
            _fail("canonical transforms require complete translation of all raw output")
        self.phase = step.stage
        self.raw_coverage.raw(None, self.raw.inputs)
        current = tuple(self.candidates.values())
        self.canonical_coverage.canonical(step.request.context.extension_id, current)
        output = transforms.apply_canonical_step(self.context, current, step)
        self._proposals.extend(output)
        self.candidates = {fact.event_id: fact for fact in output}


def validate_trace(context: InterpretationContext, proposal: InterpretationProposal) -> tuple[CanonicalFact, ...]:
    """Reconstruct processing without live services or external effects.

    Returns:
        Every proposal required for complete validation and cause checks.

    """
    if proposal.format_version != CURRENT_INTERPRETATION_FORMAT:
        _fail("new interpretations require the current lifecycle-aware format")
    required: tuple[CanonicalFact, ...]
    if any(isinstance(step, steps.UnavailableInputStep) for step in proposal.steps):
        required = unavailable.validate_unavailable(context, proposal)
    else:
        required = InterpretationTrace(context).validate(proposal)
    validate_admission(proposal)
    return required


def _required_facts(trace: InterpretationTrace, proposal: InterpretationProposal) -> tuple[CoreFact, ...]:
    if isinstance(trace.context.original.observation, ExtensionObservation):
        return ()
    if not proposal.steps or not isinstance(proposal.steps[0], steps.CoreLifecycleStep):
        _fail("core interpretation must start with its original lifecycle pass")
    required = proposal.steps[0]
    lifecycle.validate_lifecycle(trace.context, required, proposal.translator_version)
    return required.facts


def _apply_limit(trace: InterpretationTrace, step: steps.LimitStep) -> None:
    """Keep the recorded input unchanged after a call the journal did not allow."""
    if step.call_stage == "raw":
        if trace.phase != "raw":
            _fail("raw limit must precede all translation and canonical steps")
        trace.raw_coverage.raw(step.extension_id, trace.raw.inputs)
        return
    if step.call_stage == "canonical":
        if trace.raw.inputs:
            _fail("canonical limit requires complete translation of all raw output")
        trace.phase = "canonical"
        trace.canonical_coverage.canonical(step.extension_id, tuple(trace.candidates.values()))
        return
    trace.raw_coverage.raw(None, trace.raw.inputs)
    trace.phase = "translation"
    pending = trace.raw.inputs
    if tuple(source.input_id for source in pending) != step.input_ids:
        _fail("translation limit does not match the remaining raw input")
    trace.raw = RawTrace((), trace.raw.content_snapshot)


def _fail(message: str) -> NoReturn:
    raise ValueError(message)


def _fail_type(message: str) -> NoReturn:
    raise TypeError(message)
