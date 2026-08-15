"""Committed harness activity followed by the two session lifecycle effects."""

from __future__ import annotations

from contracts.harness import (
    IngestionResult,
    RawEvent,
    RawEventDelivery,
    SessionLifecycleContext,
    SessionLifecycleRequest,
)
from domain.events import CanonicalEvent, SessionFinished, SessionStarted
from runtime.event_store import EventStore
from runtime.ingest import EventPipeline
from runtime.registry import HarnessRegistry


class SessionLifecycleService:
    """Apply generic terminal lifecycle for committed session facts."""

    def __init__(
        self,
        registry: HarnessRegistry,
        context: SessionLifecycleContext,
    ) -> None:
        self.registry = registry
        self.context = context

    def apply(self, event: CanonicalEvent) -> None:
        if isinstance(event.payload, SessionStarted):
            action = "started"
        elif isinstance(event.payload, SessionFinished):
            action = "finished"
        else:
            return

        registered_session = self.registry.registered_session(event.session_id)
        lifecycle = registered_session.plugin.lifecycle
        if lifecycle is None:
            return
        lifecycle.apply(
            SessionLifecycleRequest(action),
            registered_session.session,
            self.context,
        )


class ApplicationEventDelivery(RawEventDelivery):
    """Persist one raw observation, then apply its committed lifecycle facts."""

    def __init__(
        self,
        pipeline: EventPipeline,
        event_store: EventStore,
        session_lifecycle: SessionLifecycleService,
    ) -> None:
        self.pipeline = pipeline
        self.event_store = event_store
        self.session_lifecycle = session_lifecycle

    def deliver(self, raw_event: RawEvent) -> IngestionResult:
        result = self.pipeline.ingest(raw_event)
        for event_id in result.accepted_event_ids:
            self.session_lifecycle.apply(self.event_store.require_event(event_id).event)
        return result
