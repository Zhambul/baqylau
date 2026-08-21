"""Starting a harness CLI: the request, the plan, the outcome."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from domain.ids import AccountId, ModelId, SessionId, WindowId
from harness.models.controls import AttachmentReference


@dataclass(frozen=True)
class LaunchRequest:
    working_directory: str
    initial_text: str | None
    model_id: ModelId | None
    effort: str | None
    account_id: AccountId | None
    resume_session_id: SessionId | None
    attachments: tuple[AttachmentReference, ...] = ()

    @property
    def carries_first_message(self) -> bool:
        """Whether this launch hands the CLI something to work on at once — text,
        or attachments alone (every launcher turns those into the prompt's
        leading mentions, which is a turn as far as the CLI is concerned)."""
        return bool((self.initial_text or "").strip() or self.attachments)


@dataclass(frozen=True)
class LaunchResult:
    status: Literal["started", "rejected"]
    window_id: WindowId | None = None
    reason: str | None = None


@dataclass(frozen=True)
class HarnessLaunchPlan:
    command: str
    arguments: tuple[str, ...]
    title: str
    # Environment the launched CLI (and so its hook processes) must carry —
    # launch-time facts travel with the process to be observed as raw events.
    environment: tuple[tuple[str, str], ...] = ()


class LaunchRejected(ValueError):
    pass
