"""Record pushed telemetry — the daemon-side half of the telemetry channel.

The twin of `HookGatewayService`. A delivery arrives over HTTP as exact bytes,
the harness's own `HarnessTelemetryGateway` says what they meant, and what it
returns is written here. Recording only: translation stays with the
interpreter's next tick, exactly as it does for hooks.

This is what makes the OTLP receiver and the status-line shim thin clients
rather than a second and third writer of the store.
"""

from __future__ import annotations

from domain.ids import HarnessName, SessionId
from harness.models import (
    HarnessTelemetryRequest,
    Session,
    TelemetryContext,
)
from harness.registry import HarnessRegistry, HarnessRegistryError
from repository.contract.facts import RawEventRepository
from repository.contract.sessions import SessionRepository
from repository.contract.usage import AccountUsageRepository


class UnknownTelemetryHarness(LookupError):
    pass


class _SessionLookup(TelemetryContext):
    def __init__(self, session_repository: SessionRepository) -> None:
        self.sessions = session_repository

    def find_session(self, session_id: SessionId) -> Session | None:
        return self.sessions.find(session_id)


class TelemetryGatewayService:
    def __init__(
        self,
        harness_registry: HarnessRegistry,
        raw_event_repository: RawEventRepository,
        session_repository: SessionRepository,
        account_usage_repository: AccountUsageRepository,
    ) -> None:
        self.registry = harness_registry
        self.raw_events = raw_event_repository
        self.usage = account_usage_repository
        self.context = _SessionLookup(session_repository)

    def record(self, harness: HarnessName, harness_telemetry_request: HarnessTelemetryRequest) -> int:
        """One delivery in, the number of facts it produced out."""
        try:
            plugin = self.registry.plugin(harness)
        except HarnessRegistryError as error:
            raise UnknownTelemetryHarness(str(error)) from error
        if plugin.telemetry is None:
            raise UnknownTelemetryHarness(f"harness accepts no telemetry: {harness}")
        response = plugin.telemetry.handle(harness_telemetry_request, self.context)
        if response.raw_events:
            self.raw_events.record(response.raw_events)
        if response.usage is not None:
            self.usage.record(response.usage)
        return len(response.raw_events) + (1 if response.usage is not None else 0)
