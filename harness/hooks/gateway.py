# Copyright (c) 2026 Zhambyl Yermagambet
"""Record pushed hook deliveries — the daemon-side half of the hook channel.

The one recorder of hook raw events: a delivery arrives over HTTP, the harness's
`HarnessHookGateway` turns it into raw events, and they are appended here. It
records only — translation stays with the interpreter's next tick.

It also RESOLVES the one observation a client cannot interpret for itself: the
hook process reports its own pid, and the CLI's pid is an ancestor of it, which
takes the harness's process name to recognise. That name is on the plugin
descriptor, here, in the daemon.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from core.process import nearest_ancestor_named
from harness.registry import HarnessRegistry, HarnessRegistryError

if TYPE_CHECKING:
    from domain.ids import HarnessName
    from harness.contract import HarnessPlugin
    from harness.hooks.recorder import RawEventWriter
    from harness.models.hooks import (
        HarnessHookRequest,
    )


class UnknownHookHarnessError(LookupError):
    """Represent unknown hook harness."""


def _with_harness_process(
    harness_plugin: HarnessPlugin,
    harness_hook_request: HarnessHookRequest,
) -> HarnessHookRequest:
    """Add the detected harness process to a hook request.

    Returns:
        The hook request with process data.

    """
    if harness_hook_request.harness_process_id is not None or harness_hook_request.client_process_id is None:
        return harness_hook_request
    return replace(
        harness_hook_request,
        harness_process_id=nearest_ancestor_named(
            harness_plugin.harness_info.cli_process_name,
            from_process_id=harness_hook_request.client_process_id,
        ),
    )


class HookGatewayService:
    """Represent hook gateway service."""

    def __init__(self, harness_registry: HarnessRegistry, raw_event_writer: RawEventWriter) -> None:
        """Use the harness registry and the writer of hook raw events."""
        self.registry = harness_registry
        self.raw_events = raw_event_writer

    def record(self, harness: HarnessName, harness_hook_request: HarnessHookRequest) -> bytes:
        """One delivery in, its synchronous reply out (b"" when there is none).

        The daemon's writer writes the raw events after the reply, in arrival
        order, so the harness does not wait for the database.

        Returns:
            Byte data.

        Raises:
            UnknownHookHarnessError: If no harness owns the hook.

        """
        try:
            plugin = self.registry.plugin(harness)
        except HarnessRegistryError as error:
            raise UnknownHookHarnessError(str(error)) from error
        if plugin.hooks is None:
            message = f"harness accepts no hook deliveries: {harness}"
            raise UnknownHookHarnessError(message)
        response = plugin.hooks.receive_hook(
            _with_harness_process(plugin, harness_hook_request),
        )
        self.raw_events.record(response.raw_events)
        return response.reply
