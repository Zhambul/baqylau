# Copyright (c) 2026 Zhambyl Yermagambet
"""Codex composer screen parsing tests."""

from harness.impl.codex.controls import composer_state

PROMPT_MARKER = "\u203a"
PLAN_PICKER_SCREEN = f"""  Implement this plan?

{PROMPT_MARKER} 1. Yes, implement this plan          Switch to Default and start coding.
  2. Yes, clear context and implement  Fresh thread. Context: 2% used.
  3. No, stay in Plan mode             Continue planning with the model.

  enter select \u00b7 esc back"""


def test_codex_plan_picker_hides_composer() -> None:
    """Verify the Codex 0.156 plan picker is not read as the composer draft.

    The selected picker row starts with the same marker as the composer line.
    """
    assert composer_state.typed(PLAN_PICKER_SCREEN) is None
    assert composer_state.typed(f"{PROMPT_MARKER} Run the tests") == "Run the tests"
