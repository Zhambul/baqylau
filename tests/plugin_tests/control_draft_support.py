# Copyright (c) 2026 Zhambyl Yermagambet
"""Cross-harness canonical translation tests from native fixture shapes."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from harness.impl.codex.controls import backtrack, composer_state
from terminal.models import input as terminal_input, viewport as terminal_viewport
from tests.fake_terminal import FakeTerminal
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.control_basic_support import backtrack_next_state

if TYPE_CHECKING:
    from pathlib import Path

    from domain import (
        ids as domain_ids,
    )


class BacktrackDriver:
    """Model the Codex backtrack screens and selection."""

    def __init__(self, prompts: tuple[str, ...]) -> None:
        """Store prompt history and start at the composer screen."""
        self.keys: list[str] = []
        self.state = "composer"
        self.selected = 1
        self._prompts = prompts
        self._read_extents: list[str] = []

    def read_text(
        self,
        _window: domain_ids.WindowId,
        extent: str = fixture.SCREEN,
        *,
        ansi: bool = False,
    ) -> str:
        """Read the simulated backtrack screen.

        Returns:
            The hint, selected transcript prompt, or composer text for the current state.

        """
        self._read_extents.append(extent)
        if self.state == "hint":
            return backtrack.ESCAPE_HINT
        if self.state == fixture.TRANSCRIPT_SOURCE:
            selected_prompt = self._prompts[self.selected]
            styled_prompt = f"\x1b[7m{selected_prompt}\x1b[27m" if ansi else selected_prompt
            return f"{backtrack.TRANSCRIPT_HEADER}\n{styled_prompt}\n{backtrack.TRANSCRIPT_FOOTERS[0]}"
        if self.state == "restored":
            restored_prompt = self._prompts[self.selected]
            return f"\u203a {restored_prompt}\n  gpt-5.6-luna low"
        return fixture.ASK_CODEX_TO_DO_ANYTHING_TEXT

    def send_key(self, _window: domain_ids.WindowId, *pressed: str) -> bool:
        """Record keys and update the backtrack state and selection.

        Returns:
            True to report successful key delivery.

        """
        self.keys.extend(pressed)
        self.state = backtrack_next_state(self.state, pressed)
        if self.state == fixture.TRANSCRIPT_SOURCE and pressed == ("left",):
            self.selected = max(0, self.selected - 1)
        return True


class RestoredComposerDriver:
    """Model a restored multi-line Codex composer."""

    def __init__(self) -> None:
        """Start with a restored multi-line prompt and empty key records."""
        self.lines = ["old prompt", "continued prompt", "final prompt"]
        self.keys: list[str] = []
        self._reads: list[tuple[str, bool]] = []

    def read_text(
        self,
        _window: domain_ids.WindowId,
        extent: str = fixture.SCREEN,
        *,
        ansi: bool = False,
    ) -> str:
        """Record a read of the restored composer.

        Returns:
            The remaining prompt lines, or the empty-composer screen.

        """
        self._reads.append((extent, ansi))
        if not self.lines or self.lines == [""]:
            return f"\u203a {composer_state.EMPTY_PROMPT}\n  gpt-5.6-luna low"
        joined_lines = "\n".join(self.lines)
        return f"\u203a {joined_lines}\n  gpt-5.6-luna low"

    def send_key(self, _window: domain_ids.WindowId, *pressed: str) -> bool:
        """Record keys and apply line clearing or removal.

        Returns:
            True to report successful key delivery.

        """
        self.keys.extend(pressed)
        if pressed == (fixture.CLEAR_BEFORE_CURSOR_KEY, fixture.CLEAR_AFTER_CURSOR_KEY):
            self.lines[-1] = ""
        elif pressed == (fixture.BACKSPACE,):
            self.lines.pop()
        return True


class ClaudeDraftTerminal(FakeTerminal):
    """Model a Claude visual-mode draft during rename."""

    def __init__(self, source: Path) -> None:
        """Start a visual-mode draft and keep the transcript path for rename records."""
        super().__init__()
        self.text = fixture.TEST
        self.mode = "VISUAL"
        self._source = source

    def read_screen(self, request: terminal_viewport.ScreenReadRequest) -> terminal_viewport.ScreenReadResponse:
        """Record a screen read for the Claude draft.

        Returns:
            A successful response with the draft text and editor mode.

        """
        self.screen_reads.append(request)
        divider = fixture.GREY_ANSI_SEQUENCE + fixture.DIVIDER_CHARACTER * fixture.DIVIDER_WIDTH
        return terminal_viewport.ScreenReadResponse(
            succeeded=True,
            text=f"{divider}\n\x1b[m\u276f\xa0{self.text}\n{divider}\n-- {self.mode} --",
        )

    def send_key(self, request: terminal_input.KeySendRequest) -> terminal_input.KeySendResponse:
        """Record a key and update the editor mode or draft text.

        Returns:
            A successful key-send response.

        """
        self.keys.append((request.window_id, request.key))
        if request.key == fixture.ESCAPE:
            self.mode = "NORMAL"
        elif request.key == "i" and self.mode == "NORMAL":
            self.mode = "INSERT"
        elif request.key in {fixture.CLEAR_BEFORE_CURSOR_KEY, fixture.CLEAR_AFTER_CURSOR_KEY}:
            self.text = ""
        return terminal_input.KeySendResponse(succeeded=True)

    def insert_text(self, request: terminal_input.TextInsertRequest) -> terminal_input.TextInsertResponse:
        """Record inserted text and append it to the draft.

        Returns:
            A successful text-insert response.

        """
        self.inserted.append((request.window_id, request.text, request.mode))
        self.text += request.text
        return terminal_input.TextInsertResponse(succeeded=True)

    def submit_text(self, request: terminal_input.TextSubmitRequest) -> terminal_input.TextSubmitResponse:
        """Submit a rename command and append the native title record.

        Returns:
            A successful text-submit response after the record is written.

        """
        self.submitted.append((request.window_id, request.text, request.mode))
        self.text = ""
        name = request.text.removeprefix("/rename ")
        with self._source.open(fixture.LETTER_A, encoding=fixture.TEXT_ENCODING) as transcript_file:
            transcript_file.write(
                json.dumps(
                    {
                        fixture.TYPE_FIELD: "agent-name",
                        "agentName": name,
                        "sessionId": fixture.SESSION_ONE_ID,
                    },
                )
                + "\n",
            )
        return terminal_input.TextSubmitResponse(succeeded=True)


class CodexDraftTerminal(FakeTerminal):
    """Model a Codex draft during rename."""

    def __init__(self) -> None:
        """Start the fake terminal with a fixed Codex draft."""
        super().__init__()
        self.text = fixture.TEST

    def read_screen(self, request: terminal_viewport.ScreenReadRequest) -> terminal_viewport.ScreenReadResponse:
        """Record a screen read for the Codex draft.

        Returns:
            A successful response with the draft or empty-composer prompt.

        """
        self.screen_reads.append(request)
        content = self.text or fixture.ASK_CODEX_TO_DO_ANYTHING_TEXT
        return terminal_viewport.ScreenReadResponse(succeeded=True, text=f"\u203a {content}\n\n  gpt-5.6-sol high")

    def send_key(self, request: terminal_input.KeySendRequest) -> terminal_input.KeySendResponse:
        """Record a key and clear the draft when requested.

        Returns:
            A successful key-send response.

        """
        self.keys.append((request.window_id, request.key))
        if request.key in {fixture.CLEAR_BEFORE_CURSOR_KEY, fixture.CLEAR_AFTER_CURSOR_KEY}:
            self.text = ""
        return terminal_input.KeySendResponse(succeeded=True)

    def insert_text(self, request: terminal_input.TextInsertRequest) -> terminal_input.TextInsertResponse:
        """Record inserted text and append it to the Codex draft.

        Returns:
            A successful text-insert response.

        """
        self.inserted.append((request.window_id, request.text, request.mode))
        self.text += request.text
        return terminal_input.TextInsertResponse(succeeded=True)

    def submit_text(self, request: terminal_input.TextSubmitRequest) -> terminal_input.TextSubmitResponse:
        """Record text submission and clear the Codex draft.

        Returns:
            A successful text-submit response.

        """
        self.submitted.append((request.window_id, request.text, request.mode))
        self.text = ""
        return terminal_input.TextSubmitResponse(succeeded=True)
