"""One deterministic raw-observation to canonical-fact coordinator."""

from __future__ import annotations

from contracts.harness import IngestionResult, RawEvent, TranslationError
from runtime.event_store import EventStore
from runtime.registry import HarnessRegistry


class EventPipeline:
    def __init__(self, registry: HarnessRegistry, event_store: EventStore) -> None:
        self.registry = registry
        self.event_store = event_store

    def ingest(self, raw_event: RawEvent) -> IngestionResult:
        plugin = self.registry.plugin(raw_event.harness)
        try:
            translation = plugin.events.translate(raw_event)
        except TranslationError as error:
            return self.event_store.record_failure(raw_event, plugin.info.plugin_version, error)

        recorded = self.event_store.record(raw_event, plugin.info.plugin_version, translation)
        return IngestionResult(
            raw_event_id=raw_event.raw_event_id,
            translation_decision=translation.decision,
            accepted_event_ids=tuple(stored.event.event_id for stored in recorded.accepted),
            deduplicated_event_ids=recorded.duplicate_event_ids,
            latest_cursor=recorded.latest_cursor,
        )
