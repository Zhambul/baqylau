# Copyright (c) 2026 Zhambyl Yermagambet
"""Apply each declared raw transform once and keep input after a failed result."""

from dataclasses import dataclass
from functools import partial

from baqylau_extension_api.models.raw_transforms import RawTransformResult
from baqylau_extension_api.models.transforms import RawTransformRequest
from baqylau_extension_api.translation.inputs import validate_translation_request

from extensions import interpretation_calls, interpretation_checks, interpretation_decoder, interpretation_selection
from extensions.models import interpretation_selections as selection, interpretation_steps as steps
from extensions.models.interpretation_admission import JournalAdmission, limited_step, rejected_outcome
from extensions.models.interpretation_batches import RawTrace
from extensions.models.interpretation_transforms import apply_raw_step
from extensions.models.observations import ExtensionObservation
from extensions.registry_package import RegistryPackage


@dataclass(frozen=True)
class InterpretationRaw:
    """Check the derived source document before it can reach its decoder."""

    checks: interpretation_checks.InterpretationChecks
    decoder: interpretation_decoder.InterpretationDecoder | None

    def transform(
        self, package: RegistryPackage, current: RawTrace, admission: JournalAdmission,
    ) -> tuple[steps.RawTransformStep | steps.LimitStep | None, RawTrace]:
        """Call only selected inputs while retaining all other input positions.

        Returns:
            The complete applied, failed, or rejected step, a limit record, or no call.

        """
        request = self._request(package, current)
        if not request.inputs:
            return None, current
        limited = _limited_step(admission, package, request)
        if limited is not None:
            return limited, current
        pending = interpretation_calls.InterpretationCall(
            partial(self._call, package, request), partial(self._apply, current, request),
        )
        checked = pending.run()
        step = steps.RawTransformStep(request=request, outcome=checked.outcome)
        if admission.admit(step) is None:
            return _rejected_step(request, checked, admission), current
        return step, current if checked.output is None else checked.output

    def _apply(self, current: RawTrace, request: RawTransformRequest, reply: RawTransformResult) -> RawTrace:
        step = steps.RawTransformStep(request=request, outcome=steps.AppliedStep(reply=reply))
        following = apply_raw_step(self.checks.context, current, step)
        self._validate_input(following)
        return following

    def _request(self, package: RegistryPackage, current: RawTrace) -> RawTransformRequest:
        declaration = self.checks.context.require_package(package.manifest.extension_id)
        selected = selection.raw_inputs(declaration, current.inputs)
        return RawTransformRequest(
            context=interpretation_selection.processing_context(self.checks.context, package.manifest.extension_id),
            inputs=selected,
            content_snapshot=selection.selected_content(current.content_snapshot, selected),
        )

    def _validate_input(self, following: RawTrace) -> None:
        original = self.checks.context.original.observation
        if not following.inputs or not isinstance(original, ExtensionObservation) or self.decoder is None:
            return
        package = self.checks.context.require_package(original.candidate.document.schema_ref.owner)
        validate_translation_request(package.manifest, package.schemas, self.decoder.request(following))

    def _call(self, package: RegistryPackage, request: RawTransformRequest) -> RawTransformResult:
        plugin = package.plugin
        if plugin is None or plugin.capabilities.raw_transformer is None:
            message = "selected raw transformer is unavailable"
            raise RuntimeError(message)
        return plugin.capabilities.raw_transformer.transform(request)


def _limited_step(
    admission: JournalAdmission, package: RegistryPackage, request: RawTransformRequest,
) -> steps.LimitStep | None:
    if admission.can_call():
        return None
    return limited_step(
        admission, "raw", package.manifest.extension_id,
        tuple(source.input_id for source in request.inputs),
    )


def _rejected_step(
    request: RawTransformRequest,
    checked: interpretation_calls.CheckedReply[RawTransformResult, RawTrace],
    admission: JournalAdmission,
) -> steps.RawTransformStep:
    """Record one reply that the journal cannot retain.

    Returns:
        The rejected step with its exact evidence.

    Raises:
        TypeError: If the checked outcome is not an applied reply.

    """
    if not isinstance(checked.outcome, steps.AppliedStep):
        message = "journal cannot retain a failed call record"
        raise TypeError(message)
    rejected = steps.RawTransformStep(request=request, outcome=rejected_outcome(checked.outcome.reply))
    admission.admit_reserved(rejected)
    return rejected
