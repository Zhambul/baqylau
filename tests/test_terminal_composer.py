"""Shared terminal driver and harness composer behavior."""

from __future__ import annotations

from typing import cast

from domain.ids import WindowId
from harness.impl.claude_code.probe import ClaudeCodeComposer
from harness.impl.codex.controls.composer import CodexComposer
from harness.contract import ComposerDriver
from harness.services.composer import with_preserved_draft
from harness.services.terminal_driver import TerminalDriver
from fake_terminal import FakeTerminal


class ClaudeDriver:
    def __init__(self, text: str, mode: str | None) -> None:
        self.text = text
        self.mode = mode
        self.keys: list[str] = []
        self.insertions: list[str] = []

    def get_text(self, _window_id, extent="screen", ansi=False):
        del extent, ansi
        divider = "\x1b[m\x1b[38:2:136:136:136m" + "─" * 20
        mode = "" if self.mode is None else f"\n-- {self.mode} --"
        return f"{divider}\n\x1b[m❯\xa0{self.text}\n{divider}{mode}"

    def send_key(self, _window_id, *keys):
        for key in keys:
            self.keys.append(key)
            if key == "escape":
                self.mode = "NORMAL"
            elif key == "i" and self.mode == "NORMAL":
                self.mode = "INSERT"
            elif key in ("ctrl+u", "ctrl+k"):
                self.text = ""
            elif key == "backspace":
                self.text = self.text[:-1]
        return True

    def insert_text(self, _window_id, text, *, paste=True):
        assert paste
        self.text += text
        self.insertions.append(text)
        return True

    def submit_text(self, _window_id, text, *, paste=True):
        del text, paste
        self.text = ""
        return True


class CodexDriver:
    def __init__(self, text: str) -> None:
        self.text = text
        self.keys: list[str] = []
        self.insertions: list[str] = []

    def get_text(self, _window_id, extent="screen", ansi=False):
        del extent, ansi
        content = self.text or "Ask Codex to do anything"
        return f"› {content}\n\n  gpt-5.6-sol high"

    def send_key(self, _window_id, *keys):
        self.keys.extend(keys)
        if any(key in ("ctrl+u", "ctrl+k") for key in keys):
            self.text = ""
        elif "backspace" in keys:
            self.text = self.text[:-1]
        return True

    def insert_text(self, _window_id, text, *, paste=True):
        assert paste
        self.text += text
        self.insertions.append(text)
        return True

    def submit_text(self, _window_id, text, *, paste=True):
        del paste
        self.text = ""
        return bool(text)


def test_the_shared_driver_maps_all_text_operations_to_one_terminal() -> None:
    terminal = FakeTerminal()
    driver = TerminalDriver(terminal.plugin())

    assert driver.insert_text(WindowId("window-one"), "draft")
    assert driver.submit_text(WindowId("window-one"), "message")
    assert driver.send_key(WindowId("window-one"), "escape", "i")

    assert terminal.inserted[0][1] == "draft"
    assert terminal.submitted[0][1] == "message"
    assert [key for _window, key in terminal.keys] == ["escape", "i"]


def test_claude_visual_mode_is_normalized_before_a_draft_change() -> None:
    driver = ClaudeDriver("test", "VISUAL")
    composer = ClaudeCodeComposer()

    composer.clear(cast(ComposerDriver, driver), WindowId("window-one"))
    composer.insert(cast(ComposerDriver, driver), WindowId("window-one"), "test")

    assert driver.text == "test"
    assert driver.keys[:2] == ["escape", "i"]
    assert driver.insertions == ["test"]


def test_claude_standard_editor_does_not_receive_vim_mode_keys() -> None:
    driver = ClaudeDriver("test", None)

    ClaudeCodeComposer().clear(cast(ComposerDriver, driver), WindowId("window-one"))

    assert "escape" not in driver.keys
    assert "i" not in driver.keys
    assert driver.text == ""


def test_codex_draft_preservation_never_sends_vim_keys() -> None:
    driver = CodexDriver("test")
    composer = CodexComposer()
    observed: list[str] = []

    with_preserved_draft(
        composer,
        cast(ComposerDriver, driver),
        WindowId("window-one"),
        lambda: observed.append(driver.text),
    )

    assert observed == [""]
    assert driver.text == "test"
    assert "escape" not in driver.keys
    assert "i" not in driver.keys
