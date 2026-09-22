# Copyright (c) 2026 Zhambyl Yermagambet
"""Own complete mixed interpretations and explicit history reads."""

from typing import Protocol

from baqylau_extension_api.models.canonical import CoreStateSnapshot
from baqylau_extension_api.models.scopes import ExtensionScope
from baqylau_extension_api.models.translation_inputs import TranslationState

from domain.ids import CanonicalEventId, RawEventId
from extensions.models.interpretation_reads import CanonicalPage, TranslationStateKey
from extensions.models.interpretation_snapshot import PriorStateRequest
from extensions.models.interpretations import InterpretationCommit, InterpretationOutcome, StoredCanonicalFact


class CanonicalFactReader(Protocol):
    """Read the live mixed stream without granting interpretation writes."""

    def current_fact_page(self, after_cursor: int, limit: int) -> CanonicalPage:
        """Supply one ordered mixed stream for live consumers."""
        ...


class InterpretationRepository(CanonicalFactReader, Protocol):
    """Store one complete decision without exposing a connection or a worker."""

    def record_interpretation(self, request: InterpretationCommit) -> InterpretationOutcome:
        """Commit the verdict, journal, final facts, source links, and live pending removal together."""
        ...

    def find_interpretation(self, history_revision: str, raw_event_id: RawEventId) -> InterpretationCommit | None:
        """Read the original complete decision without requiring an active extension."""
        ...

    def find_fact(self, history_revision: str, event_id: CanonicalEventId) -> StoredCanonicalFact | None:
        """Read one accepted fact body from an explicit history."""
        ...

    def facts_for_scope(
        self, history_revision: str, scope: ExtensionScope, after_cursor: int, limit: int,
    ) -> CanonicalPage:
        """Read an indexed scope page in accepted order."""
        ...

    def translator_state(self, key: TranslationStateKey) -> TranslationState:
        """Read the prior decoder state that the complete interpretation must compare and replace."""
        ...

    def capture_prior_state(self, request: PriorStateRequest) -> CoreStateSnapshot:
        """Read a byte- and count-bounded scope prefix with checked history and coverage."""
        ...
