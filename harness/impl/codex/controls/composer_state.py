# Copyright (c) 2026 Zhambyl Yermagambet
"""Parse the visible Codex prompt composer state."""

from harness.impl.codex.controls import dialog_screen_state, plan_screen

EMPTY_PROMPT = "Ask Codex to do anything"


def empty(screen: str | None) -> bool:
    """Return whether the last visible composer is empty.

    Returns:
        Whether the last visible composer is empty.

    """
    lines = (screen or "").splitlines()
    marker = last_prompt_marker(lines)
    if marker is None:
        return False
    return EMPTY_PROMPT in " ".join(lines[marker].split())


def typed(screen: str | None) -> str | None:
    """Return text from the last visible Codex composer.

    A plan picker or a question dialog takes the place of the composer, and
    its selected row starts with the same marker. The draft is then unknown.

    Returns:
        Text from the last visible Codex composer, or None when it is not visible.

    """
    if composer_hidden(screen or ""):
        return None
    lines = (screen or "").splitlines()
    marker = last_prompt_marker(lines)
    if marker is None:
        return None
    first_line = lines[marker].lstrip()[1:].strip()
    if EMPTY_PROMPT in first_line:
        return ""
    body = [first_line]
    for line in lines[marker + 1 :]:
        if not line.strip():
            break
        body.append(line.strip())
    return "\n".join(body).strip()


def composer_hidden(screen: str) -> bool:
    """Return whether a plan picker or a question dialog hides the composer.

    Returns:
        Whether the composer is hidden.

    """
    return plan_screen.picker_open(screen) or dialog_screen_state.dialog_open(screen)


def last_prompt_marker(lines: list[str]) -> int | None:
    """Return the last visible prompt marker line index.

    Returns:
        The last visible prompt marker line index.

    """
    for index in range(len(lines) - 1, -1, -1):
        if lines[index].lstrip().startswith(("\u203a", "\u276f")):
            return index
    return None
