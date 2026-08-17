"""Record pushed hook deliveries — the daemon-side half of the hook channel.

The one recorder of hook evidence: a delivery arrives over HTTP, the harness's
`HarnessHookGateway` turns it into raw events, and they are appended here. It
records only — translation stays with the interpreter's next tick.
"""

from __future__ import annotations

from harness.models import HarnessHookRequest
from harness.registry import HarnessRegistry, HarnessRegistryError
from repository.contract.facts import RawEventRepository


class UnknownHookHarness(LookupError):
    pass


class HookGatewayService:
    def __init__(self, registry: HarnessRegistry, raw_events: RawEventRepository) -> None:
        self.registry = registry
        self.raw_events = raw_events

    def record(self, harness: str, request: HarnessHookRequest) -> bytes:
        """One delivery in, its synchronous reply out (b"" when there is none)."""
        try:
            plugin = self.registry.plugin(harness)
        except HarnessRegistryError as error:
            raise UnknownHookHarness(str(error)) from error
        if plugin.hooks is None:
            raise UnknownHookHarness(f"harness accepts no hook deliveries: {harness}")
        response = plugin.hooks.handle(request)
        self.raw_events.record(response.raw_events)
        return response.reply
