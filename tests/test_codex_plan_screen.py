# Copyright (c) 2026 Zhambyl Yermagambet
"""Read the Codex plan-decision picker of each footer form."""

import pytest

from harness.impl.codex.controls import plan_screen

PICKER = """  Implement this plan?
\u203a 1. Yes, implement this plan          Switch to Default and start coding.
  2. Yes, clear context and implement  Fresh thread. Context: 2% used.
  3. No, stay in Plan mode             Continue planning with the model.
  {footer}
"""


@pytest.mark.parametrize("footer", ["Press enter to confirm or esc to go back", "enter select · esc back"])
def test_picker_is_read_with_either_footer(footer: str) -> None:
    """The 0.144 and the 0.156 footer both show the open picker and its rows."""
    screen = PICKER.format(footer=footer)
    assert plan_screen.picker_open(screen)
    assert [row.num for row in plan_screen.option_rows(screen)] == ["1", "2", "3"]


def test_footer_alone_is_not_the_picker() -> None:
    """Another picker with the same footer is not the plan picker."""
    assert not plan_screen.picker_open("  Select model\n  enter select · esc back\n")
