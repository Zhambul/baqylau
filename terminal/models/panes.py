"""Pane operations — split, close, resize, focus."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping


@dataclass(frozen=True)
class PaneAnchor:
    """Where a split attaches, as INTENT rather than syntax.

    The caller states "next to that window" or "next to the pane tagged X"; the
    implementation renders its own match expression for it. Re-encoding the
    anchor as one terminal's match string above this
    layer would destroy the intent at the boundary — the caller's meaning could
    then only be recovered by parsing one terminal's grammar back apart.
    """

    window_id: str | None = None
    tag: tuple[str, str] | None = None

    def __post_init__(self) -> None:
        if (self.window_id is None) == (self.tag is None):
            raise ValueError("a pane anchor names exactly one of a window id or a tag")


@dataclass(frozen=True)
class PaneOpenRequest:
    """A new pane running `command`, split off the anchor.

    `same_tab_as` is a window id whose TAB the pane must open in. It is not
    redundant with `anchor`: an anchor may only be resolvable within one tab,
    so the tab has to be selected first — otherwise a pane anchored to a window
    in an unfocused tab splits whichever tab the user happens to be looking at.
    """

    command: tuple[str, ...]
    working_directory: str
    title: str
    # The SPLIT LINE's orientation: "vertical" puts the new pane beside the
    # anchor, "horizontal" stacks it under the anchor.
    split: Literal["vertical", "horizontal"]
    size_percent: int                    # the new pane's share of the split axis
    anchor: PaneAnchor
    same_tab_as: str
    tags: Mapping[str, str]
    keep_focus: bool = True

    def __post_init__(self) -> None:
        if not 1 <= self.size_percent <= 99:
            raise ValueError("pane size must be between 1 and 99 percent")


@dataclass(frozen=True)
class PaneOpenResponse:
    succeeded: bool
    window_id: str | None
    reason: str | None = None


@dataclass(frozen=True)
class PaneCloseRequest:
    window_id: str


@dataclass(frozen=True)
class PaneCloseResponse:
    succeeded: bool
    reason: str | None = None


@dataclass(frozen=True)
class PaneResizeRequest:
    window_id: str
    axis: Literal["horizontal", "vertical"]
    cells: int                           # grow (+) / shrink (−)


@dataclass(frozen=True)
class PaneResizeResponse:
    succeeded: bool
    reason: str | None = None


@dataclass(frozen=True)
class WindowFocusRequest:
    """Focus a window INSIDE its tab.

    The move must not raise or activate the terminal's OS window: a focus that
    activates a background application steals the user's screen away from
    whatever they are actually looking at.
    """

    window_id: str


@dataclass(frozen=True)
class WindowFocusResponse:
    succeeded: bool
    reason: str | None = None
