# Copyright (c) 2026 Zhambyl Yermagambet
"""Apply selected canonical transforms before any core input reaction."""

from dataclasses import dataclass
from functools import partial

from baqylau_extension_api.models.canonical import CanonicalFact, CoreFact, CoreStateSnapshot
from baqylau_extension_api.models.transforms import CanonicalTransformRequest, CanonicalTransformResult

from extensions import prior_state_selection
from extensions.interpretation_calls import CheckedReply, InterpretationCall
from extensions.interpretation_checks import InterpretationChecks
from extensions.interpretation_selection import processing_context
from extensions.models import (
    interpretation_lifecycle,
    interpretation_selections as selection,
    interpretation_steps as steps,
    interpretation_transforms,
)
from extensions.models.interpretation_admission import JournalAdmission, limited_step, rejected_outcome
from extensions.registry_package import RegistryPackage


@dataclass(frozen=True)
class InterpretationCanonical:
    """Preserve complete earlier proposals for cause and first-acceptance checks."""

    checks: InterpretationChecks
    prior: CoreStateSnapshot
    required: tuple[CoreFact, ...] = ()

    def transform(
        self, package: RegistryPackage,
        current: tuple[CanonicalFact, ...], proposed: tuple[CanonicalFact, ...], admission: JournalAdmission,
    ) -> tuple[
        steps.CanonicalTransformStep | steps.LimitStep | None, tuple[CanonicalFact, ...],
    ]:
        """Discard one invalid or oversized result in full and continue with its input.

        Returns:
            A checked step and the complete next batch.

        """
        request = self._request(package, current)
        if not request.inputs:
            return None, current
        limited = _limited_step(admission, package, request)
        if limited is not None:
            return limited, current
        checked = InterpretationCall(
            partial(self._call, package, request), partial(self._apply, current, request, proposed),
        ).run()
        step = steps.CanonicalTransformStep(request=request, outcome=checked.outcome)
        if admission.admit(step) is None:
            return _rejected_step(request, checked, admission), current
        return step, current if checked.output is None else checked.output

    def _apply(
        self, current: tuple[CanonicalFact, ...], request: CanonicalTransformRequest,
        proposed: tuple[CanonicalFact, ...], reply: CanonicalTransformResult,
    ) -> tuple[CanonicalFact, ...]:
        step = steps.CanonicalTransformStep(request=request, outcome=steps.AppliedStep(reply=reply))
        following = interpretation_transforms.apply_canonical_step(self.checks.context, current, step)
        interpretation_lifecycle.require_separate_ids(self.required, following)
        self.checks.facts((*proposed, *following))
        return following

    def _request(self, package: RegistryPackage, current: tuple[CanonicalFact, ...]) -> CanonicalTransformRequest:
        declaration = self.checks.context.require_package(package.manifest.extension_id)
        selected = selection.canonical_inputs(declaration, current)
        return CanonicalTransformRequest(
            context=processing_context(self.checks.context, package.manifest.extension_id), inputs=selected,
            prior_state=prior_state_selection.prior_for(package.manifest, self.prior),
        )

    def _call(self, package: RegistryPackage, request: CanonicalTransformRequest) -> CanonicalTransformResult:
        plugin = package.plugin
        if plugin is None or plugin.capabilities.canonical_transformer is None:
            message = "selected canonical transformer is unavailable"
            raise RuntimeError(message)
        return plugin.capabilities.canonical_transformer.transform(request)


def _limited_step(
    admission: JournalAdmission, package: RegistryPackage, request: CanonicalTransformRequest,
) -> steps.LimitStep | None:
    if admission.can_call():
        return None
    return limited_step(
        admission, "canonical", package.manifest.extension_id,
        tuple(fact.event_id for fact in request.inputs),
    )


def _rejected_step(
    request: CanonicalTransformRequest,
    checked: CheckedReply[CanonicalTransformResult, tuple[CanonicalFact, ...]],
    admission: JournalAdmission,
) -> steps.CanonicalTransformStep:
    """Record one reply that the journal cannot retain.

    Returns:
        The rejected step with its exact evidence.

    Raises:
        TypeError: If the checked outcome is not an applied reply.

    """
    if not isinstance(checked.outcome, steps.AppliedStep):
        message = "journal cannot retain a failed call record"
        raise TypeError(message)
    rejected = steps.CanonicalTransformStep(request=request, outcome=rejected_outcome(checked.outcome.reply))
    admission.admit_reserved(rejected)
    return rejected
