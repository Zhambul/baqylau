"""One pushed hook delivery, in and out."""

from __future__ import annotations

from dataclasses import dataclass

from harness.models.evidence import RawEvent


@dataclass(frozen=True)
class HarnessHookRequest:
    """What one hook shipped: the exact stdin bytes plus what it saw around itself.

    Two of these fields are the same fact at different stages, because a client
    observes and the daemon interprets: `client_process_id` is what the hook
    process reported about ITSELF, and `harness_process_id` is the CLI pid
    `HookGatewayService` resolves from it by walking the ancestry with the
    plugin's own process name. A plugin gateway reads the resolved one.
    """

    payload: bytes
    terminal_window_id: str | None
    harness_process_id: int | None
    account_id: str | None
    account_display_name: str | None
    launch_model: str | None = None
    launch_effort: str | None = None
    client_process_id: int | None = None


@dataclass(frozen=True)
class HarnessHookResponse:
    raw_events: tuple[RawEvent, ...]
    reply: bytes
