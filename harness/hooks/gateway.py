"""Record pushed hook deliveries — the daemon-side half of the hook channel.

The one recorder of hook evidence: a delivery arrives over HTTP, the harness's
`HarnessHookGateway` turns it into raw events, and they are appended here. It
records only — translation stays with the interpreter's next tick.
"""

from __future__ import annotations

from harness.models import HarnessHookRequest
from harness.registry import HarnessRegistry, HarnessRegistryError
from runtime.recorder import RawEventRecorder


class UnknownHookHarness(LookupError):
    pass


class HookGatewayService:
    def __init__(self, registry: HarnessRegistry, recorder: RawEventRecorder) -> None:
        self.registry = registry
        self.recorder = recorder

    def record(self, harness: str, request: HarnessHookRequest) -> bytes:
        """One delivery in, its synchronous reply out (b"" when there is none)."""
        try:
            plugin = self.registry.plugin(harness)
        except HarnessRegistryError as error:
            raise UnknownHookHarness(str(error)) from error
        if plugin.hooks is None:
            raise UnknownHookHarness(f"harness accepts no hook deliveries: {harness}")
        response = plugin.hooks.handle(request)
        self.recorder.record(response.raw_events)
        return response.reply
