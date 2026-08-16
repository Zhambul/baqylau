"""Record pushed hook deliveries — the daemon-side half of the hook channel.

The one recorder of hook evidence: a delivery arrives over HTTP, the harness's
`HarnessHookGateway` turns it into raw events, and they are appended here. It
records only — translation stays with the interpreter's next tick.
"""

from __future__ import annotations

from collections.abc import Mapping

from contracts.harness import RawEvent
from runtime.harnesses import HarnessRegistry, HarnessRegistryError
from runtime.recorder import RawEventRecorder


class UnknownHookHarness(LookupError):
    pass


class HookGatewayService:
    def __init__(self, registry: HarnessRegistry, recorder: RawEventRecorder) -> None:
        self.registry = registry
        self.recorder = recorder

    def record(
        self, harness: str, payload: bytes, environment: Mapping[str, str]
    ) -> bytes:
        """One delivery in, its synchronous reply out (b"" when there is none)."""
        raw_events, output = self._gateway_events(harness, payload, environment)
        self.recorder.record(raw_events)
        return output

    def _gateway_events(
        self, harness: str, payload: bytes, environment: Mapping[str, str]
    ) -> tuple[tuple[RawEvent, ...], bytes]:
        try:
            plugin = self.registry.plugin(harness)
        except HarnessRegistryError as error:
            raise UnknownHookHarness(str(error)) from error
        if plugin.hooks is None:
            raise UnknownHookHarness(f"harness accepts no hook deliveries: {harness}")
        return plugin.hooks.raw_events(payload, environment)
