# Copyright (c) 2026 Zhambyl Yermagambet
"""Cross-harness canonical translation tests from native fixture shapes."""

import json
from pathlib import Path

from domain import (
    ids as domain_ids,
)
from harness.contract import (
    HarnessController,
    HarnessPlugin,
)
from harness.models.probe import TerminalInputState
from harness.services.terminal_driver import TerminalDriver
from terminal.models import input as terminal_input
from tests.canonical_runtime import ProviderGraph
from tests.fake_terminal import FakeTerminal
from tests.plugin_tests import control_state_values, support_values, vocabulary as fixture


class InterruptingTerminal(FakeTerminal):
    """Append interruption evidence after an escape key."""

    def __init__(self, transcript_path: Path) -> None:
        """Keep the transcript path used to record an interruption."""
        super().__init__()
        self._transcript_path = transcript_path

    def send_key(self, request: terminal_input.KeySendRequest) -> terminal_input.KeySendResponse:
        """Send keys and append a native interruption record.

        Returns:
            The key-send response from the fake terminal.

        """
        response = super().send_key(request)
        with self._transcript_path.open(fixture.LETTER_A) as transcript_file:
            transcript_file.write(
                json.dumps(
                    {
                        fixture.TYPE_FIELD: fixture.USER,
                        fixture.INTERRUPTED_MESSAGE_ID: fixture.MESSAGE_ONE_ID,
                        fixture.MESSAGE_FIELD: {
                            fixture.ROLE_FIELD: fixture.USER,
                            fixture.CONTENT_FIELD: [
                                {
                                    fixture.TYPE_FIELD: fixture.TEXT_FIELD,
                                    fixture.TEXT_FIELD: fixture.REQUEST_INTERRUPTED_BY_USER_FOR_TOOL_USE_TEX,
                                },
                            ],
                        },
                    },
                )
                + "\n",
            )
        return response


class CursorScreenDriver:
    """Apply configured screen changes for dialog key presses."""

    terminal = None

    def __init__(self, screen: str, key_screens: dict[tuple[str, ...], str]) -> None:
        """Store the initial screen and the configured key responses."""
        self.screen = screen
        self.keys: list[str] = []
        self.text: list[str] = []
        self.reads: list[tuple[domain_ids.WindowId, str, bool]] = []
        self._key_screens = key_screens

    def read_text(
        self,
        window_id: domain_ids.WindowId,
        extent: str = fixture.SCREEN,
        *,
        ansi: bool = False,
    ) -> str:
        """Record a screen read.

        Returns:
            The current test screen.

        """
        self.reads.append((window_id, extent, ansi))
        return self.screen

    def send_key(self, _window: domain_ids.WindowId, *pressed: str) -> bool:
        """Record keys and apply their configured screen response.

        Returns:
            True to report successful key delivery.

        """
        self.keys.extend(pressed)
        self.screen = self._key_screens.get(pressed, self.screen)
        return True

    def send_text(self, _window: domain_ids.WindowId, text: str) -> bool:
        """Record text and clear the test screen.

        Returns:
            True to report successful text delivery.

        """
        self.text.append(text)
        self.screen = ""
        return True


def no_sleep(_seconds: float) -> None:
    """Do not delay a deterministic confirmation test."""


def controller(harness: domain_ids.HarnessName) -> HarnessController:
    """Return the controller installed for one harness.

    Returns:
        The controller installed for one harness.

    """
    plugin = ProviderGraph().registry.plugin(harness)
    return support_values.controller_of(plugin)


def read_composer_state(plugin: HarnessPlugin, terminal: FakeTerminal) -> TerminalInputState | None:
    """Read the composer state from one terminal.

    Returns:
        The observed state, or None if no composer or state is available.

    """
    if plugin.composer is None:
        return None
    driver = TerminalDriver(terminal.plugin())
    window_id = control_state_values.PRIMARY_WINDOW
    return plugin.composer.read(driver, window_id)


class RewindScreenDriver(CursorScreenDriver):
    """Add viewport operations to the cursor screen driver."""

    def __init__(
        self,
        screen: str,
        key_screens: dict[tuple[str, ...], str],
        *,
        line_count: int = fixture.TERMINAL_LINE_COUNT,
    ) -> None:
        """Set up the screen and empty viewport operation records."""
        super().__init__(screen, key_screens)
        self.resizes: list[int] = []
        self._line_count = line_count
        self._submissions: list[tuple[str, bool]] = []

    def lines(self, _window: domain_ids.WindowId) -> int:
        """Read the configured viewport height.

        Returns:
            The test viewport line count.

        """
        return self._line_count

    def resize_lines(self, _window: domain_ids.WindowId, cells: int) -> bool:
        """Record a viewport resize request.

        Returns:
            True to report successful resizing.

        """
        self.resizes.append(cells)
        return True

    def insert_text(self, _window: domain_ids.WindowId, text: str, *, paste: bool = True) -> bool:
        """Record a pasted piece that is not submitted.

        Returns:
            True to report successful insertion.

        """
        self._submissions.append((text, paste))
        return True

    def submit_text(self, _window: domain_ids.WindowId, text: str, *, paste: bool = True) -> bool:
        """Record text submission and its paste mode.

        Returns:
            True to report successful submission.

        """
        self._submissions.append((text, paste))
        return True
