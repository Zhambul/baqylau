# Copyright (c) 2026 Zhambyl Yermagambet
"""Call the source owner's pure decoder with recorded bytes and captured state."""

from dataclasses import dataclass
from functools import partial

from baqylau_extension_api.models import translation_inputs, translation_results
from baqylau_extension_api.translation.inputs import validate_translation_request

from extensions import interpretation_calls, interpretation_checks, interpretation_selection
from extensions.models import interpretation_steps as steps
from extensions.models.interpretation_admission import JournalAdmission, limited_step, rejected_outcome
from extensions.models.interpretation_batches import RawTrace
from extensions.models.interpretation_reads import TranslationStateKey
from extensions.models.interpretation_translation import TranslationTrace, translate_step
from extensions.models.observations import ExtensionObservation
from extensions.registry_package import RegistryPackage


@dataclass(frozen=True)
class InterpretationDecoder:
    """Keep failed replies as evidence without advancing decoder state."""

    checks: interpretation_checks.InterpretationChecks
    package: RegistryPackage

    def request(self, raw: RawTrace) -> translation_inputs.ExtensionTranslationRequest:
        """Read one scoped source state before the worker call.

        Returns:
            Complete source metadata and exact derived content.

        Raises:
            TypeError: If a core original selects an extension decoder.

        """
        context = self.checks.context
        original = context.original.observation
        if not isinstance(original, ExtensionObservation):
            message = "extension decoder requires an extension original"
            raise TypeError(message)
        candidate = original.candidate
        key = TranslationStateKey(
            extension_id=self.package.manifest.extension_id, history_revision=context.binding.history_revision,
            scope=context.binding.scope, source_identity=candidate.source_identity,
        )
        return translation_inputs.ExtensionTranslationRequest(
            context=interpretation_selection.processing_context(context, self.package.manifest.extension_id),
            inputs=tuple(translation_inputs.TranslationInput(
                source=source, schema_ref=candidate.document.schema_ref,
                occurred_at=candidate.occurred_at, causes=candidate.causes,
            ) for source in raw.inputs),
            state=self.checks.stores.facts.translator_state(key), content_snapshot=raw.content_snapshot,
        )

    def translate(
        self, raw: RawTrace, admission: JournalAdmission,
    ) -> tuple[steps.ExtensionTranslationStep | steps.LimitStep, TranslationTrace]:
        """Validate the entire reply before selecting any output or next state.

        Returns:
            A complete checked, rejected, or limited step and its candidate facts.

        """
        request = self.request(raw)
        limited = _limited_step(admission, self.package, request)
        if limited is not None:
            return limited, TranslationTrace((), ())
        pending = interpretation_calls.InterpretationCall(
            partial(self._call, request), partial(self._apply, raw, request),
        )
        checked = pending.run()
        step = steps.ExtensionTranslationStep(request=request, outcome=checked.outcome)
        if admission.admit(step) is None:
            return _rejected_step(request, checked, admission), TranslationTrace((), ())
        return step, TranslationTrace((), ()) if checked.output is None else checked.output

    def _apply(
        self, raw: RawTrace, request: translation_inputs.ExtensionTranslationRequest,
        reply: translation_results.ExtensionTranslationResult,
    ) -> TranslationTrace:
        step = steps.ExtensionTranslationStep(request=request, outcome=steps.AppliedStep(reply=reply))
        version = self.package.manifest.package_version
        translated = translate_step(self.checks.context, step, raw.content_snapshot, version)
        self.checks.facts(translated.proposals)
        return translated

    def _call(
        self, request: translation_inputs.ExtensionTranslationRequest,
    ) -> translation_results.ExtensionTranslationResult:
        package = self.checks.context.require_package(self.package.manifest.extension_id)
        validate_translation_request(package.manifest, package.schemas, request)
        plugin = self.package.plugin
        if plugin is None or plugin.capabilities.translator is None:
            message = "selected extension decoder is unavailable"
            raise RuntimeError(message)
        return plugin.capabilities.translator.translate(request)


def _limited_step(
    admission: JournalAdmission, package: RegistryPackage,
    request: translation_inputs.ExtensionTranslationRequest,
) -> steps.LimitStep | None:
    if admission.can_call():
        return None
    return limited_step(
        admission, "extension_translation", package.manifest.extension_id,
        tuple(source.source.input_id for source in request.inputs),
    )


def _rejected_step(
    request: translation_inputs.ExtensionTranslationRequest,
    checked: interpretation_calls.CheckedReply[translation_results.ExtensionTranslationResult, TranslationTrace],
    admission: JournalAdmission,
) -> steps.ExtensionTranslationStep:
    """Record one reply that the journal cannot retain.

    Returns:
        The rejected step with its exact evidence.

    Raises:
        TypeError: If the checked outcome is not an applied reply.

    """
    if not isinstance(checked.outcome, steps.AppliedStep):
        message = "journal cannot retain a failed call record"
        raise TypeError(message)
    rejected = steps.ExtensionTranslationStep(request=request, outcome=rejected_outcome(checked.outcome.reply))
    admission.admit_reserved(rejected)
    return rejected
