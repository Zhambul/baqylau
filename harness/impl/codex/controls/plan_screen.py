# Copyright (c) 2026 Zhambyl Yermagambet
"""Read the Codex plan-decision picker."""

from harness.impl.codex.controls.dialog import OptionRow, rows

# codex-cli 0.144 wrote "Press enter to confirm"; 0.156 writes "enter select · esc back".
FOOTS = ("enter to confirm", "enter select")
HEAD = "Implement this plan?"


def picker_open(screen: str) -> bool:
    """Return true when the plan picker header and footer are visible.

    Returns:
        True when the plan picker is open.

    """
    visible_screen = screen or ""
    return HEAD in visible_screen and any(foot in visible_screen for foot in FOOTS)


def _picker_region(screen: str) -> str:
    visible_screen = screen or ""
    header_index = visible_screen.find(HEAD)
    return visible_screen[header_index + len(HEAD) :] if header_index >= 0 else visible_screen


def option_rows(screen: str) -> list[OptionRow]:
    """Return decision rows below the plan header.

    Returns:
        The plan decision rows.

    """
    return rows(_picker_region(screen))
