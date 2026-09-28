# Copyright (c) 2026 Zhambyl Yermagambet
"""Read Codex transcript backtrack screens."""

from __future__ import annotations

import re

from harness.impl.codex.controls import composer_state

ESCAPE_HINT = "esc again to edit previous message"
TRANSCRIPT_HEADER = "/ T R A N S C R I P T /"
# codex-cli 0.144 wrote "enter to edit message"; 0.156 writes "Browsing transcript · … · ↵ rewind".
TRANSCRIPT_FOOTERS = ("enter to edit message", "Browsing transcript")
SELECTED_STYLE_END_CODE = 27
REVERSE_VIDEO_CODE = 7
ANSI_STYLE_PATTERN = re.compile(r"\x1b\[([0-9;]*)m")


def transcript_open(screen: str | None) -> bool:
    """Return the transcript open.

    Returns:
        True if the transcript is open; otherwise, false.

    """
    text = screen or ""
    return TRANSCRIPT_HEADER in text and any(footer in text for footer in TRANSCRIPT_FOOTERS)


def _normalized(text: str) -> str:
    return " ".join(text.split())


def _reverse_video_text(screen: str) -> str:
    """Return the selected-row text.

    Returns:
        The selected-row text.

    """
    selected: list[str] = []
    reverse = False
    position = 0
    for match in ANSI_STYLE_PATTERN.finditer(screen):
        if reverse:
            selected.append(screen[position : match.start()])
        codes = _style_codes(match)
        if not codes or 0 in codes or SELECTED_STYLE_END_CODE in codes:
            reverse = False
        if REVERSE_VIDEO_CODE in codes:
            reverse = True
        position = match.end()
    if reverse:
        selected.append(screen[position:])
    return "".join(selected)


def _style_codes(match: re.Match[str]) -> list[int]:
    return [int(code) for code in match.group(1).split(";") if code]


def selected_prompt(screen: str | None, target: str) -> bool:
    """Return true if the target prompt is selected.

    Returns:
        True if the target prompt is selected; otherwise, false.

    """
    if not transcript_open(screen):
        return False
    selected = _normalized(_reverse_video_text(screen or ""))
    wanted = _normalized(target)
    if not wanted:
        return False
    if wanted in selected:
        return True
    plain = plain_screen(screen or "")
    return wanted in _normalized(plain)


def restored_draft(screen: str | None, target: str) -> bool:
    """Return true if the target prompt is now the composer draft.

    Returns:
        True if the target is the composer draft; otherwise, false.

    """
    text = screen or ""
    if transcript_open(text):
        return False
    lines = text.splitlines()
    marker = composer_state.last_prompt_marker(lines)
    if marker is None:
        return False
    composer_text = _normalized("\n".join(lines[marker:]))
    wanted = _normalized(target)
    return bool(wanted) and wanted in composer_text


def plain_screen(screen: str) -> str:
    """Remove ANSI styles from a screen.

    Returns:
        The screen without ANSI styles.

    """
    return ANSI_STYLE_PATTERN.sub("", screen)
