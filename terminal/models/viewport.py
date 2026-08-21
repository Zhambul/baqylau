"""Viewport operations — reading and scrolling what a window shows."""

from __future__ import annotations

from dataclasses import dataclass

from terminal.models.values import WindowId


@dataclass(frozen=True)
class ScreenReadRequest:
    """Read the window's VISIBLE viewport — the scrolled-to rows, not the live
    screen's bottom, which is what lets the mirror restore an exact scroll
    position across a reflow. `ansi=True` keeps the SGR formatting escapes (the
    ghost-suggestion probe detects the faint input line by them)."""

    window_id: WindowId
    ansi: bool = False


@dataclass(frozen=True)
class ScreenReadResponse:
    succeeded: bool
    text: str | None
    reason: str | None = None


@dataclass(frozen=True)
class ViewportScrollRequest:
    """One scroll gesture: `to_bottom` is applied first, then `up_lines`.

    A restore is therefore ONE call. The order is load-bearing: a repaint's
    clear-scrollback under a scrolled viewport leaves the terminal's scroll
    state clamped somewhere undefined, so relative math needs the absolute
    bottom as its deterministic base first.
    """

    window_id: WindowId
    to_bottom: bool = False
    up_lines: int = 0


@dataclass(frozen=True)
class ViewportScrollResponse:
    succeeded: bool
    reason: str | None = None
