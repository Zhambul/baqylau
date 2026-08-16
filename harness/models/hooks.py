"""One pushed hook delivery, in and out."""

from __future__ import annotations

from dataclasses import dataclass

from harness.models.evidence import RawEvent


@dataclass(frozen=True)
class HarnessHookRequest:
    """What one hook shipped: the exact stdin bytes plus what it saw around itself."""

    payload: bytes
    terminal_window_id: str | None
    harness_process_id: int | None
    account_id: str | None
    account_display_name: str | None
    launch_model: str | None = None
    launch_effort: str | None = None


@dataclass(frozen=True)
class HarnessHookResponse:
    raw_events: tuple[RawEvent, ...]
    reply: bytes
