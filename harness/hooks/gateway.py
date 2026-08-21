"""Record pushed hook deliveries — the daemon-side half of the hook channel.

The one recorder of hook evidence: a delivery arrives over HTTP, the harness's
`HarnessHookGateway` turns it into raw events, and they are appended here. It
records only — translation stays with the interpreter's next tick.

It also RESOLVES the one observation a client cannot interpret for itself: the
hook process reports its own pid, and the CLI's pid is an ancestor of it, which
takes the harness's process name to recognise. That name is on the plugin
descriptor, here, in the daemon.
"""

from __future__ import annotations

from dataclasses import replace

from core.process import nearest_ancestor_named
from harness.contract import HarnessPlugin
from harness.models import HarnessHookRequest
from harness.registry import HarnessRegistry, HarnessRegistryError
from repository.contract.facts import RawEventRepository


class UnknownHookHarness(LookupError):
    pass


class HookGatewayService:
    def __init__(self, harness_registry: HarnessRegistry, raw_event_repository: RawEventRepository) -> None:
        self.registry = harness_registry
        self.raw_events = raw_event_repository

    def record(self, harness: str, harness_hook_request: HarnessHookRequest) -> bytes:
        """One delivery in, its synchronous reply out (b"" when there is none)."""
        try:
            plugin = self.registry.plugin(harness)
        except HarnessRegistryError as error:
            raise UnknownHookHarness(str(error)) from error
        if plugin.hooks is None:
            raise UnknownHookHarness(f"harness accepts no hook deliveries: {harness}")
        response = plugin.hooks.handle(self._with_harness_process(plugin, harness_hook_request))
        self.raw_events.record(response.raw_events)
        return response.reply

    @staticmethod
    def _with_harness_process(
        harness_plugin: HarnessPlugin, harness_hook_request: HarnessHookRequest
    ) -> HarnessHookRequest:
        """The CLI pid, from the client's own pid and the plugin's process name.

        Walked HERE rather than in the hook process: it costs a `ps` per
        ancestry level, and the harness is blocked on the delivery while it runs
        — which is also what makes the chain safe to read this late.
        """
        if harness_hook_request.harness_process_id is not None or harness_hook_request.client_process_id is None:
            return harness_hook_request
        return replace(
            harness_hook_request,
            harness_process_id=nearest_ancestor_named(
                harness_plugin.info.cli_process_name,
                from_process_id=harness_hook_request.client_process_id,
            ),
        )
