# Copyright (c) 2026 Zhambyl Yermagambet
"""Shared fixtures and builders for canonical harness tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

from terminal.adapter import TerminalAdapter
from tests.fake_terminal import FakeSessions, FakeTerminal, window
from tests.plugin_tests import vocabulary as fixture

if TYPE_CHECKING:
    from pathlib import Path

    from domain import ids as domain_ids
    from tests.plugin_tests.support_values import JsonValue


class SubmitProbeDriver:
    """A driver whose input box keeps the text for `sticky` Enter presses."""

    def __init__(self, sticky: int) -> None:
        """Initialize the object."""
        self.sticky = sticky
        self.enters = 0
        self.box = ""
        self._read_requests: list[tuple[domain_ids.WindowId, str, bool]] = []
        self._submit_requests: list[tuple[domain_ids.WindowId, bool]] = []
        self._key_windows: list[domain_ids.WindowId] = []
        self._key_sequences: list[tuple[str, ...]] = []

    terminal = None  # the probe is monkeypatched; only the attribute must exist

    def read_text(
        self,
        window_id: domain_ids.WindowId,
        extent: str = fixture.SCREEN,
        *,
        ansi: bool = False,
    ) -> str:
        """Return text.

        Returns:
            Text.

        """
        self._read_requests.append((window_id, extent, ansi))
        divider = fixture.DIVIDER_CHARACTER * fixture.DIVIDER_WIDTH
        return f"{divider}\n\u276f\u00a0\n{divider}"

    def insert_text(
        self,
        _window_id: domain_ids.WindowId,
        text: str,
        *,
        paste: bool = True,
    ) -> bool:
        """Put one pasted piece into the input box without a submission.

        Returns:
            True to report successful insertion.

        """
        assert paste
        self.box += text
        return True

    def submit_text(
        self,
        window_id: domain_ids.WindowId,
        text: str,
        *,
        paste: bool = True,
    ) -> bool:
        """Record text submission and keep the text in the input box.

        Returns:
            True to report successful submission.

        """
        self._submit_requests.append((window_id, paste))
        self.box += text
        return True

    def send_key(self, window_id: domain_ids.WindowId, *keys: str) -> bool:
        """Record keys and clear the input box at the configured threshold.

        Returns:
            True to report successful key delivery.

        """
        self._key_windows.append(window_id)
        self._key_sequences.append(keys)
        self.enters += 1
        if self.enters >= self.sticky:
            self.box = ""
        return True


def background_post_tool_document(
    tmp_path: Path,
    session_id: str = fixture.CLAUDE_SESSION_ID,
    task_id: str = "btk000001",
) -> tuple[Path, dict[str, JsonValue]]:
    """Write background output and build its post-tool hook document.

    Returns:
        The output path and hook document for the supplied session and task.

    """
    output_path = tmp_path / "claude-503" / "-work-slug" / session_id / "tasks" / f"{task_id}.output"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(b"1\n2\n")
    return output_path, {
        fixture.SESSION_ID_FIELD: session_id,
        fixture.TRANSCRIPT_PATH: fixture.WORK_CLAUDE_JSONL_PATH,
        fixture.CWD_FIELD: fixture.WORK_PATH,
        fixture.HOOK_EVENT_NAME_FIELD: fixture.POST_TOOL_USE_HOOK,
        fixture.HOOK_EVENT_ID_FIELD: "post-background-one",
        fixture.TOOL_NAME_FIELD: fixture.BASH_TOOL,
        fixture.TOOL_USE_ID_FIELD: fixture.BACKGROUND_OP_ONE,
        fixture.TOOL_INPUT_FIELD: {
            fixture.COMMAND_FIELD: "for i in 1 2; do echo $i; done",
            fixture.RUN_IN_BACKGROUND_FIELD: True,
        },
        fixture.TOOL_RESPONSE_FIELD: {
            fixture.STDOUT: "",
            "stderr": "",
            fixture.BACKGROUND_TASK_ID_FIELD: task_id,
        },
    }


def pane_terminal() -> tuple[FakeTerminal, TerminalAdapter]:
    """Build a terminal with one session window.

    Returns:
        The fake terminal and its session adapter.

    """
    terminal = FakeTerminal(
        windows=[window(fixture.WINDOW_ONE_ID, tags={})],
        current_window=fixture.WINDOW_ONE_ID,
    )
    sessions = FakeSessions({fixture.SESSION_ONE_ID: fixture.WINDOW_ONE_ID})
    return terminal, TerminalAdapter(terminal.plugin(), sessions)


class Widths:
    """The width policy a pane gesture consults, with the store left out."""

    def __init__(self, remembered: list[tuple[str, int]]) -> None:
        """Keep the width record and initialize query counters."""
        self.remembered = remembered
        self.read_directories: list[str] = []
        self.configuration_reads = 0
        self.resize_reads = 0

    def width_percent(self, working_directory: str) -> int:
        """Record a directory-specific width query.

        Returns:
            The default test pane width percentage.

        """
        self.read_directories.append(working_directory)
        return fixture.PANE_DEFAULT_WIDTH_PERCENT

    def configured_width_percent(self) -> int:
        """Count a configured-width query.

        Returns:
            The default test pane width percentage.

        """
        self.configuration_reads += 1
        return fixture.PANE_DEFAULT_WIDTH_PERCENT

    def resize_columns(self) -> int:
        """Count a resize-step query.

        Returns:
            The fixed seven-column resize step.

        """
        self.resize_reads += 1
        return 7

    def remember_width(self, working_directory: str, width_percent: int) -> None:
        """Record the requested width for a working directory."""
        self.remembered.append((working_directory, width_percent))
