"""Drive Codex's native transcript backtrack view."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Protocol

from domain.ids import WindowId

from harness.impl.codex.controls import composer

POLL_SECONDS = 0.1
STEP_TIMEOUT_SECONDS = 10.0
ESCAPE_HINT = "esc again to edit previous message"
TRANSCRIPT_HEADER = "/ T R A N S C R I P T /"
TRANSCRIPT_FOOTER = "enter to edit message"
_SGR = re.compile(r"\x1b\[([0-9;]*)m")


class Driver(Protocol):
    def get_text(
        self,
        window_id: WindowId,
        extent: str = "screen",
        ansi: bool = False,
    ) -> str | None: ...

    def send_key(self, window_id: WindowId, *keys: str) -> bool: ...


class BacktrackError(Exception):
    """A native backtrack step did not reach its verified screen state."""

    def __init__(self, step: str, detail: str) -> None:
        super().__init__(f"{step}: {detail}")
        self.step = step
        self.detail = detail


def transcript_open(screen: str | None) -> bool:
    text = screen or ""
    return TRANSCRIPT_HEADER in text and TRANSCRIPT_FOOTER in text


def _normalized(text: str) -> str:
    return " ".join(text.split())


def _reverse_video_text(screen: str) -> str:
    """Return text drawn with the transcript's selected-row style."""
    selected: list[str] = []
    reverse = False
    position = 0
    for match in _SGR.finditer(screen):
        if reverse:
            selected.append(screen[position:match.start()])
        codes = [int(code) for code in match.group(1).split(";") if code]
        if not codes or 0 in codes or 27 in codes:
            reverse = False
        if 7 in codes:
            reverse = True
        position = match.end()
    if reverse:
        selected.append(screen[position:])
    return "".join(selected)


def selected_prompt(screen: str | None, target: str) -> bool:
    if not transcript_open(screen):
        return False
    selected = _normalized(_reverse_video_text(screen or ""))
    wanted = _normalized(target)
    if not wanted:
        return False
    if wanted in selected:
        return True
    # The native terminal keeps Codex's reverse-video selection in an ANSI
    # screen read. The test terminal keeps the text but not the style. There, the
    # feed-derived newer_prompt_count is the selection proof and this check
    # confirms that the target to which it points is on the live transcript.
    plain = _SGR.sub("", screen or "")
    return wanted in _normalized(plain)


def restored_draft(screen: str | None, target: str) -> bool:
    """Check the last composer, after the transcript view closes."""
    text = screen or ""
    if transcript_open(text):
        return False
    lines = text.splitlines()
    marker = next(
        (
            index
            for index in range(len(lines) - 1, -1, -1)
            if lines[index].lstrip().startswith(("›", "❯"))
        ),
        None,
    )
    if marker is None:
        return False
    composer = _normalized("\n".join(lines[marker:]))
    wanted = _normalized(target)
    return bool(wanted) and wanted in composer


def _wait(
    read: Callable[[], str | None],
    predicate: Callable[[str | None], bool],
    sleep: Callable[[float], None],
) -> str | None:
    deadline = time.monotonic() + STEP_TIMEOUT_SECONDS
    latest = read()
    while not predicate(latest) and time.monotonic() < deadline:
        sleep(POLL_SECONDS)
        latest = read()
    return latest if predicate(latest) else None


def _selection_screen(driver: Driver, window_id: WindowId) -> str | None:
    return driver.get_text(window_id, ansi=True) or driver.get_text(window_id)


def drive(
    driver: Driver,
    window_id: WindowId,
    target: str,
    *,
    newer_prompt_count: int,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Select one named prompt in Codex's native transcript and edit it."""
    if newer_prompt_count < 0:
        raise BacktrackError("target", "newer prompt count must not be negative")

    # A canonical turn finish can arrive a few frames before the native
    # composer. Wait for the composer and confirm that it is empty before an
    # Escape. An early Escape can land in the prior turn and do nothing.
    try:
        composer.clear(driver, window_id, sleep=sleep)
    except composer.ComposerError as error:
        raise BacktrackError("clear", str(error)) from error
    if not driver.send_key(window_id, "escape"):
        raise BacktrackError("open", "the first escape was not delivered")
    hint = _wait(
        lambda: driver.get_text(window_id),
        lambda screen: ESCAPE_HINT in (screen or ""),
        sleep,
    )
    if hint is None:
        raise BacktrackError("open", "the edit hint did not appear")

    if not driver.send_key(window_id, "escape"):
        raise BacktrackError("open", "the second escape was not delivered")
    opened = _wait(
        lambda: driver.get_text(window_id),
        transcript_open,
        sleep,
    )
    if opened is None:
        raise BacktrackError("open", "the transcript did not appear")

    for _ in range(newer_prompt_count):
        if not driver.send_key(window_id, "left"):
            raise BacktrackError("select", "a left arrow was not delivered")
        sleep(POLL_SECONDS)

    selected = _wait(
        lambda: _selection_screen(driver, window_id),
        lambda screen: selected_prompt(screen, target),
        sleep,
    )
    if selected is None:
        observed = _SGR.sub("", _selection_screen(driver, window_id) or "")
        raise BacktrackError(
            "select",
            f"the named prompt is not selected; transcript={observed[-1200:]!r}",
        )

    if not driver.send_key(window_id, "enter"):
        raise BacktrackError("restore", "enter was not delivered")
    restored = _wait(
        lambda: driver.get_text(window_id),
        lambda screen: restored_draft(screen, target),
        sleep,
    )
    if restored is None:
        raise BacktrackError("restore", "the named prompt did not become the draft")
