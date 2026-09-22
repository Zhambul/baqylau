# Copyright (c) 2026 Zhambyl Yermagambet
"""Translate raw events and update translation-input state."""

from collections.abc import Callable

from baqylau_extension_api.models import content, events, scopes

from audit.failures import FailureContext
from domain import event_session, ids, records
from engine.interpret import consistency, dependencies, extension_mapping, snapshots
from extensions.interpretation_contract import CoreInterpretation
from extensions.models.interpretation_steps import CoreActivityStep, CoreLifecycleStep
from extensions.models.interpretations import InterpretationOutcome
from extensions.models.observations import StoredObservation
from extensions.models.processing_input import observation_scope
from harness.contract import CoreTranslator, HarnessPlugin
from harness.models import raw_events, translation_stages as stages

TRANSLATION_BATCH_SIZE = 500


class TranslationPhase(CoreInterpretation):
    """Translate unverdicted raw events and apply input reactions."""

    def __init__(
        self,
        interpreter_dependencies: dependencies.InterpreterDependencies,
        terminal_snapshot_cache: snapshots.TerminalSnapshotCache,
        audit_failure: Callable[[str, FailureContext], None],
    ) -> None:
        """Initialize the translation phase."""
        self.dependencies = interpreter_dependencies
        self.terminal_snapshots = terminal_snapshot_cache
        self.audit_failure = audit_failure

    def translate(self) -> int:
        """Translate one batch of raw events.

        Returns:
            The number of processed events.

        """
        batch = self.dependencies.repositories.raw_events.unverdicted(TRANSLATION_BATCH_SIZE)
        for raw_event in batch:
            plugin, outcome = _translate_one(self.dependencies, raw_event)
            self._react_to(outcome)
            self._release_finished(plugin, raw_event.session_id, outcome)
        return len(batch)

    def translate_lifecycle(self, raw_event: raw_events.RawEvent) -> CoreLifecycleStep:
        """Record required output against original bytes without a worker content copy.

        Returns:
            Required facts and a byte reference to the immutable stored input.

        """
        plugin = self.dependencies.services.harnesses.plugin(raw_event.harness)
        translator = self.dependencies.services.core_translators.get(raw_event.source_type, plugin.translator)
        translation = _translate_safely(translator, raw_event, stages.TranslationStage.LIFECYCLE)
        return extension_mapping.lifecycle_step(raw_event, translation, plugin.harness_info.plugin_version)

    def translate_input(
        self, raw_event: raw_events.RawEvent, source: events.RawInput, bundle: content.ContentBundle,
    ) -> CoreActivityStep:
        """Translate selected activity without an early canonical write.

        Returns:
            Recorded source content and a checked core translation decision.

        """
        plugin = self.dependencies.services.harnesses.plugin(raw_event.harness)
        translator = self.dependencies.services.core_translators.get(raw_event.source_type, plugin.translator)
        translation = _translate_safely(
            translator, extension_mapping.source_input(raw_event, source, bundle), stages.TranslationStage.ACTIVITY,
        )
        return CoreActivityStep(
            source=source, content_snapshot=bundle, translator_version=plugin.harness_info.plugin_version,
            decision=translation.decision, reason=translation.reason,
            facts=extension_mapping.translated_facts(raw_event, translation),
        )

    def accept_interpretation(
        self, original: StoredObservation, outcome: InterpretationOutcome,
    ) -> None:
        """Run input reactions and session cleanup only after new core fact acceptance."""
        accepted = records.TranslationOutcome(extension_mapping.accepted_core(outcome.accepted), ())
        self._react_to(accepted)
        scope = observation_scope(original)
        if isinstance(scope, scopes.SessionScope) and accepted.accepted:
            plugin = self.dependencies.services.harnesses.plugin(ids.HarnessName(scope.harness))
            self._release_finished(plugin, ids.SessionId(scope.session_id), accepted)

    def _react_to(self, outcome: records.TranslationOutcome) -> None:
        if any(
            isinstance(canonical_event.payload, event_session.SessionStarted) for canonical_event in outcome.accepted
        ):
            self.terminal_snapshots.invalidate()
        for reaction in self.dependencies.services.inputs:
            for canonical_event in outcome.accepted:
                try:
                    reaction.react(canonical_event)
                except Exception:  # noqa: BLE001 - Record each failed reaction and let other reactions run.
                    self.audit_failure(
                        type(reaction).__name__,
                        FailureContext(
                            session_id=canonical_event.session_id,
                            event_id=canonical_event.event_id,
                        ),
                    )

    def _release_finished(
        self,
        harness_plugin: HarnessPlugin,
        session_id: ids.SessionId,
        outcome: records.TranslationOutcome,
    ) -> None:
        has_finished = any(
            isinstance(canonical_event.payload, event_session.SessionFinished) for canonical_event in outcome.accepted
        )
        if not has_finished:
            return
        for name, release in (
            ("translation", harness_plugin.translator.release_session),
            ("source", harness_plugin.sources.release_session),
        ):
            try:
                release(session_id)
            except Exception:  # noqa: BLE001 - Record plugin cleanup failure and continue other cleanup.
                self.audit_failure(
                    f"{name} memory release",
                    FailureContext(session_id=session_id),
                )


def _translate_safely(
    core_translator: CoreTranslator,
    raw_event: raw_events.RawEvent,
    translation_stage: stages.TranslationStage = stages.TranslationStage.COMPLETE,
) -> raw_events.TranslationResult:
    try:
        translation = _checked_stage(core_translator, raw_event, translation_stage)
    except Exception as error:  # noqa: BLE001 - Store plugin failure as a translation decision.
        translation = raw_events.TranslationResult(
            (),
            records.RecordedTranslationDecision.TRANSLATION_FAILED,
            f"{type(error).__name__}: {error}",
        )
    else:
        translation = consistency.checked(raw_event, translation)
    return translation


def _checked_stage(
    core_translator: CoreTranslator, raw_event: raw_events.RawEvent, translation_stage: stages.TranslationStage,
) -> raw_events.TranslationResult:
    translation = core_translator.translate(raw_event, translation_stage=translation_stage)
    if stages.select_result(translation, translation_stage) != translation:
        message = "core translator returned facts outside its selected pass"
        raise ValueError(message)
    return translation


def _translate_one(
    interpreter_dependencies: dependencies.InterpreterDependencies, raw_event: raw_events.RawEvent,
) -> tuple[HarnessPlugin, records.TranslationOutcome]:
    plugin = interpreter_dependencies.services.harnesses.plugin(raw_event.harness)
    translator = interpreter_dependencies.services.core_translators.get(raw_event.source_type, plugin.translator)
    outcome = interpreter_dependencies.repositories.canonical_events.record_translation(
        raw_event, plugin.harness_info.plugin_version, _translate_safely(translator, raw_event),
        interpreter_dependencies.runtime.clock(),
    )
    return plugin, outcome
