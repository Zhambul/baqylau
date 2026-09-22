# Copyright (c) 2026 Zhambyl Yermagambet
"""Separate core interpretation from external worker and storage details."""

from typing import Protocol

from baqylau_extension_api.models.content import ContentBundle
from baqylau_extension_api.models.events import RawInput

from extensions.models.interpretation_steps import CoreActivityStep, CoreLifecycleStep
from extensions.models.interpretations import InterpretationOutcome
from extensions.models.observations import StoredObservation
from harness.models.raw_events import RawEvent


class CoreInterpretation(Protocol):
    """Keep the harness decoder and input reactions under engine ownership."""

    def translate_lifecycle(self, raw_event: RawEvent) -> CoreLifecycleStep:
        """Read required state from the original without processing tools or turns."""
        ...

    def translate_input(self, raw_event: RawEvent, source: RawInput, bundle: ContentBundle) -> CoreActivityStep:
        """Translate activity without writing facts or running input reactions."""
        ...

    def accept_interpretation(self, original: StoredObservation, outcome: InterpretationOutcome) -> None:
        """Run core input reactions only for new facts after the complete transaction."""
        ...
