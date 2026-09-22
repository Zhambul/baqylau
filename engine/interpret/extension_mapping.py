# Copyright (c) 2026 Zhambyl Yermagambet
"""Map accepted core facts without exposing extension documents to core reactions."""

from dataclasses import replace
from hashlib import sha256

from baqylau_extension_api.models import content, events
from baqylau_extension_api.models.canonical import CoreFact

from domain.event_base import CanonicalEvent, EventPayload
from extensions.mapper.core_events import private_committed, public_candidate
from extensions.models.interpretation_steps import CoreLifecycleStep
from extensions.models.interpretations import StoredCanonicalFact
from harness.models.raw_events import RawEvent, TranslationResult


def source_input(raw_event: RawEvent, source: events.RawInput, bundle: content.ContentBundle) -> RawEvent:
    """Map derived content without changing core source metadata.

    Returns:
        The original metadata and exact selected bytes.

    """
    payload = content.decode_base64(bundle.resolve(source.content).base64_text)
    return replace(raw_event, payload=payload)


def translated_facts(raw_event: RawEvent, translation_result: TranslationResult) -> tuple[CoreFact, ...]:
    """Retain exact original links, as the legacy canonical write does.

    Returns:
        Strict public core candidates, without acceptance metadata.

    """
    return tuple(public_candidate(replace(event, raw_event_ids=(raw_event.raw_event_id,)))
                 for event in translation_result.canonical_events)


def lifecycle_step(
    raw_event: RawEvent, translation_result: TranslationResult, translator_version: str,
) -> CoreLifecycleStep:
    """Map required facts with an exact reference to the stored original bytes.

    Returns:
        The required pass without a worker content copy.

    """
    return CoreLifecycleStep(
        content_byte_length=len(raw_event.payload), content_digest=sha256(raw_event.payload).hexdigest(),
        translator_version=translator_version,
        decision=translation_result.decision, reason=translation_result.reason,
        facts=translated_facts(raw_event, translation_result),
    )


def accepted_core(facts: tuple[StoredCanonicalFact, ...]) -> tuple[CanonicalEvent[EventPayload], ...]:
    """Keep exact storage cursors and timestamps for newly accepted core facts.

    Returns:
        Core events only, with no invented session for extension facts.

    """
    return tuple(private_committed(stored) for stored in facts if isinstance(stored.fact, CoreFact))
