# Copyright (c) 2026 Zhambyl Yermagambet
"""Build one complete ordered interpretation without a database write lock."""

from dataclasses import dataclass, field

from baqylau_extension_api.models.canonical import CanonicalFact, CoreFact, CoreStateSnapshot

from extensions import (
    interpretation_canonical,
    interpretation_checks,
    interpretation_contract,
    interpretation_decoder,
    interpretation_raw,
)
from extensions.models import (
    interpretation_lifecycle as lifecycle,
    interpretation_selections as selection,
    interpretation_steps as steps,
    interpretation_unavailable as preflight,
)
from extensions.models.interpretation_admission import JournalAdmission
from extensions.models.interpretation_batches import RawTrace
from extensions.models.interpretation_context import InterpretationContext
from extensions.models.interpretation_verdicts import interpretation_verdict
from extensions.models.interpretations import MAX_INTERPRETATION_BYTES, InterpretationProposal
from extensions.models.observations import ExtensionObservation
from extensions.models.processing_input import capture_original
from extensions.registry_package import RegistryPackage


@dataclass
class InterpretationProgress:
    """Keep mutable step collection private to one host call."""

    journal: list[steps.InterpretationStep] = field(default_factory=list)
    candidates: dict[str, CanonicalFact] = field(default_factory=dict)
    proposals: tuple[CanonicalFact, ...] = ()
    translator_version: str = "suppressed"
    required: steps.CoreLifecycleStep | None = None
    admission: JournalAdmission = field(default_factory=JournalAdmission)

    @property
    def required_facts(self) -> tuple[CoreFact, ...]:
        """Read the original required output, separate from current activity.

        Returns:
            Required facts, or no lifecycle for an extension original.

        """
        return () if self.required is None else self.required.facts

    def proposal(self, context: InterpretationContext) -> InterpretationProposal:
        """Freeze the complete trace after all processing stages.

        Returns:
            Final facts and their recorded decisions.

        """
        facts = lifecycle.combined_facts(self.required_facts, tuple(self.candidates.values()))
        journal = tuple(self.journal)
        return InterpretationProposal(
            format_version=2, binding=context.binding, translator_version=self.translator_version,
            facts=facts, steps=journal, decision=interpretation_verdict(facts, journal),
            reason="See the recorded processing steps",
        )


@dataclass(frozen=True)
class InterpretationPipeline:
    """Run dependency order once; generated output moves only to later stages."""

    checks: interpretation_checks.InterpretationChecks
    packages: tuple[RegistryPackage, ...]
    core: interpretation_contract.CoreInterpretation
    prior: CoreStateSnapshot
    admission_limit: int = MAX_INTERPRETATION_BYTES

    def interpret(self) -> InterpretationProposal:
        """Retain all decisions before the single complete write.

        Returns:
            A final proposal ready for independent storage validation.

        """
        progress = InterpretationProgress(admission=JournalAdmission(self.admission_limit))
        self._required(progress)
        unavailable = preflight.unavailable_input(self.checks.context)
        if unavailable is not None:
            return preflight.unavailable_proposal(self.checks.context, unavailable, progress.required)
        decoder = self._decoder()
        original = capture_original(self.checks.context.original)
        current = self._raw(RawTrace((original.source,), original.content_snapshot), progress, decoder)
        self._translate(current, progress, decoder)
        self._canonical(progress)
        return progress.proposal(self.checks.context)

    def _required(self, progress: InterpretationProgress) -> None:
        original = self.checks.context.original.observation
        if isinstance(original, ExtensionObservation):
            return
        step = self.core.translate_lifecycle(original)
        lifecycle.validate_lifecycle(self.checks.context, step, step.translator_version)
        self.checks.facts(step.facts)
        progress.required = step
        progress.proposals = step.facts
        progress.translator_version = step.translator_version
        progress.admission.admit(step)  # required work is never optional
        progress.journal.append(step)

    def _decoder(self) -> interpretation_decoder.InterpretationDecoder | None:
        original = self.checks.context.original.observation
        if not isinstance(original, ExtensionObservation):
            return None
        owner = original.candidate.document.schema_ref.owner
        package = next(package for package in self.packages if package.manifest.extension_id == owner)
        return interpretation_decoder.InterpretationDecoder(self.checks, package)

    def _raw(
        self, current: RawTrace, progress: InterpretationProgress,
        decoder: interpretation_decoder.InterpretationDecoder | None,
    ) -> RawTrace:
        transform = interpretation_raw.InterpretationRaw(self.checks, decoder)
        for package in self.packages:
            step, current = transform.transform(package, current, progress.admission)
            if step is not None:
                progress.journal.append(step)
        return current

    def _translate(
        self, current: RawTrace, progress: InterpretationProgress,
        decoder: interpretation_decoder.InterpretationDecoder | None,
    ) -> None:
        original = self.checks.context.original.observation
        if not current.inputs:
            return
        if decoder is not None:
            step, translated = decoder.translate(current, progress.admission)
            progress.journal.append(step)
            progress.translator_version = decoder.package.manifest.package_version
            progress.proposals = translated.proposals
            progress.candidates.update((fact.event_id, fact) for fact in translated.candidates)
        elif not isinstance(original, ExtensionObservation):
            self._core(current, progress)

    def _core(self, current: RawTrace, progress: InterpretationProgress) -> None:
        original = self.checks.context.original.observation
        if isinstance(original, ExtensionObservation):
            return
        for source in current.inputs:
            bundle = selection.selected_content(current.content_snapshot, (source,))
            step = self.core.translate_input(original, source, bundle)
            lifecycle.require_activity(step.facts)
            lifecycle.require_separate_ids(progress.required_facts, step.facts)
            self.checks.facts(step.facts)
            progress.admission.admit(step)  # core activity is never optional
            progress.journal.append(step)
            progress.translator_version = step.translator_version
            progress.proposals += step.facts
            for fact in step.facts:
                progress.candidates.setdefault(fact.event_id, fact)

    def _canonical(self, progress: InterpretationProgress) -> None:
        transform = interpretation_canonical.InterpretationCanonical(self.checks, self.prior, progress.required_facts)
        for package in self.packages:
            current = tuple(progress.candidates.values())
            step, following = transform.transform(package, current, progress.proposals, progress.admission)
            if step is not None:
                progress.journal.append(step)
                progress.proposals += following
                progress.candidates = {fact.event_id: fact for fact in following}
