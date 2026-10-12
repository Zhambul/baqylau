# Copyright (c) 2026 Zhambyl Yermagambet
"""Paste a long Claude Code message in pieces that Claude Code keeps as plain text."""

import pytest

from domain.ids import WindowId
from harness.impl.claude_code.controls import tui as claude_tui
from harness.impl.claude_code.controls.paste_pieces import (
    LINE_BREAK,
    PIECE_CHARACTERS,
    PIECE_LINE_BREAKS,
    paste_pieces,
)
from tests.plugin_tests import support_terminal, vocabulary as fixture

PLAN_PROMPT = "\n".join(f"Step {number}: reply only with the plan marker." for number in range(6))
LONG_LINE = "x" * (PIECE_CHARACTERS * 2 + PIECE_LINE_BREAKS)
FULL_FIRST_LINE = "\n".join(("y" * (PIECE_CHARACTERS - PIECE_LINE_BREAKS), "z", "z", "z"))


@pytest.mark.parametrize(
    "text",
    ["one line", "", PLAN_PROMPT, LONG_LINE, "a\r\nb\r\nc\r\nd", "\n\n\n\nend", FULL_FIRST_LINE],
)
def test_pieces_stay_under_the_limit(text: str) -> None:
    """Keep each piece under Claude Code's limit, and keep the text the same."""
    pieces = paste_pieces(text)
    assert "".join(pieces) == text
    assert all(len(piece) <= PIECE_CHARACTERS for piece in pieces)
    assert all(len(LINE_BREAK.findall(piece)) <= PIECE_LINE_BREAKS for piece in pieces)


def test_long_message_reaches_the_box() -> None:
    """Paste every piece, submit the last one, and leave the whole message in the box."""
    driver = support_terminal.SubmitProbeDriver(sticky=fixture.EXHAUSTED_RETRY_COUNT)
    ok, _ = claude_tui.type_command(driver, WindowId(fixture.WINDOW_FIRST_ID), PLAN_PROMPT)
    assert ok is True
    assert driver.box == PLAN_PROMPT
