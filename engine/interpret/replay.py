# Copyright (c) 2026 Zhambyl Yermagambet
"""Translate replayed input with the live translators and run no input reactions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from extensions.interpretation_contract import CoreInterpretation

if TYPE_CHECKING:
    from baqylau_extension_api.models.content import ContentBundle
    from baqylau_extension_api.models.events import RawInput

    from extensions.models.interpretation_steps import CoreActivityStep, CoreLifecycleStep
    from extensions.models.interpretations import InterpretationOutcome
    from extensions.models.observations import StoredObservation
    from harness.models.raw_events import RawEvent


@dataclass(frozen=True)
class ReplayInterpretation(CoreInterpretation):
    """Use the live translation for a candidate history; a replay changes no live state.

    A V1 replay is of a closed session only. The live translators hold no
    state for it, so their state for the replay stays separate, and the
    caller releases it when the replay ends.
    """

    translation: CoreInterpretation

    def translate_lifecycle(self, raw_event: RawEvent) -> CoreLifecycleStep:
        """Translate required lifecycle facts with the live translator.

        Returns:
            The lifecycle step.

        """
        return self.translation.translate_lifecycle(raw_event)

    def translate_input(self, raw_event: RawEvent, source: RawInput, bundle: ContentBundle) -> CoreActivityStep:
        """Translate activity with the live translator.

        Returns:
            The activity step.

        """
        return self.translation.translate_input(raw_event, source, bundle)

    def accept_interpretation(self, original: StoredObservation, outcome: InterpretationOutcome) -> None:
        """Run no input reaction for a replayed fact."""
