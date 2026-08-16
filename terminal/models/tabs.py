"""Tab operations — open, close, rename, colour."""

from __future__ import annotations

from dataclasses import dataclass

from terminal.models.values import TabAppearance


@dataclass(frozen=True)
class TabOpenRequest:
    """A new tab running `command` in `working_directory`.

    `environment` rides the launch: assignments the command needs that are
    facts of the launch itself (the selected account, model, effort), not of
    the daemon that requested it.
    """

    working_directory: str
    command: tuple[str, ...]
    # The tab's intended title. An implementation whose only way to set one is
    # a STICKY title leaves it to the program instead — a harness publishes its
    # own tab title, and freezing that out at launch loses more than it gains.
    # `TabRenameRequest` is the deliberate override.
    title: str
    environment: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class TabOpenResponse:
    succeeded: bool
    window_id: str | None
    reason: str | None = None


@dataclass(frozen=True)
class TabCloseRequest:
    window_id: str          # closes the whole tab CONTAINING this window


@dataclass(frozen=True)
class TabCloseResponse:
    succeeded: bool
    reason: str | None = None


@dataclass(frozen=True)
class TabRenameRequest:
    window_id: str
    title: str              # a sticky, explicit title


@dataclass(frozen=True)
class TabRenameResponse:
    succeeded: bool
    reason: str | None = None


@dataclass(frozen=True)
class TabColorSetRequest:
    window_id: str
    appearance: TabAppearance


@dataclass(frozen=True)
class TabColorSetResponse:
    succeeded: bool
    reason: str | None = None


@dataclass(frozen=True)
class TabColorClearRequest:
    window_id: str


@dataclass(frozen=True)
class TabColorClearResponse:
    succeeded: bool
    reason: str | None = None
