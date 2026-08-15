"""The codex /model picker driver, against codex-cli 0.147.0's real screens.

The fixtures below are verbatim captures (session 01a0038a, 2026-08-15). They
exist because the driver was written for 0.144.1, whose picker opened on a
"Select Model" step with an 'All models' row, and 0.147.0 removed it. The wait
for that step still SUCCEEDED — "Select Model" is a substring of "Select Model
and Effort" — so the driver looked for a row that no longer exists and raised
with the picker left open (audited: status indeterminate, reason "row: no 'all
models' under 'Select Model'", 379ms against a 2.5s step timeout).
"""

from __future__ import annotations

import pytest

from plugins.codex import modeldialog

MODEL_ROWS = [
    "gpt-5.6-sol (default)   Latest frontier agentic coding model.",
    "gpt-5.6-terra           Balanced agentic coding model for everyday work.",
    "gpt-5.6-luna (current)  Fast and affordable agentic coding model.",
    "gpt-5.5                 Frontier model for complex coding, research, and real work.",
    "gpt-5.4                 Strong model for everyday coding.",
    "gpt-5.4-mini            Small, fast, and cost-efficient model for simpler tasks.",
    "gpt-5.3-codex-spark     Ultra-fast coding model.",
]
LEVEL_ROWS = [
    "Low               Fast responses with lighter reasoning",
    "Medium (default)  Balances speed and reasoning depth for everyday tasks",
    "High (current)    Greater reasoning depth for complex problems",
    "Extra high        Extra high reasoning depth for complex problems",
    "More reasoning…   Max consumes usage limits faster",
]
ADVANCED_ROWS = ["Max  For difficult problems when quality matters more than speed"]

STEPS = [
    ("Select Model and Effort", MODEL_ROWS, 2),      # cursor starts on (current)
    ("Select Reasoning Level for gpt-5.6-luna", LEVEL_ROWS, 2),
]
ADVANCED = ("Advanced Reasoning", ADVANCED_ROWS, 0)


class FakePicker:
    """codex 0.147.0's picker: model step → level step → (More reasoning…) →
    Advanced Reasoning. `enter` confirms the cursor row and advances."""

    def __init__(self, steps=None):
        self.steps = list(steps if steps is not None else STEPS)
        self.index = -1                     # -1 = the picker is not open yet
        self.cursor = 0
        self.chosen: list[str] = []

    # --- the driver's terminal interface -------------------------------------
    def paste_text(self, _win, text):
        assert text == "/model"
        self.index, self.cursor = 0, self.steps[0][2]
        return True

    def get_text(self, _win, extent="screen", ansi=False):
        if self.index < 0 or self.index >= len(self.steps):
            return "  gpt-5.6-luna high · ~/code/personal/baqylau"
        header, rows, _ = self.steps[self.index]
        lines = ["", "  " + header, " "]
        for number, label in enumerate(rows, start=1):
            mark = "› " if number - 1 == self.cursor else "  "
            lines.append("%s%d. %s" % (mark, number, label))
        lines += [" ", "  Press enter to confirm or esc to go back"]
        return "\n".join(lines)

    def send_key(self, _win, *keys):
        for key in keys:
            rows = self.steps[self.index][1] if 0 <= self.index < len(self.steps) else []
            if key == "down":
                self.cursor = min(self.cursor + 1, len(rows) - 1)
            elif key == "up":
                self.cursor = max(self.cursor - 1, 0)
            elif key == "enter":
                self.chosen.append(rows[self.cursor].split("  ")[0].strip())
                if self.steps[self.index][0].startswith("Select Reasoning") \
                        and "More reasoning" in rows[self.cursor]:
                    self.steps.append(ADVANCED)
                self.index += 1
                self.cursor = (
                    self.steps[self.index][2] if self.index < len(self.steps) else 0
                )
        return True


def _run(**kwargs):
    picker = FakePicker()
    result = modeldialog.set_model_effort(picker, "win", sleep=lambda _s: None, **kwargs)
    return picker, result


def test_the_picker_opens_straight_on_the_model_step():
    # the 0.144.1 'All models' step is gone; waiting for it is what broke
    picker, result = _run(model="gpt-5.6-sol")

    assert result == {"set": True}
    # model chosen, then the level step's pre-selected default accepted
    assert picker.chosen == ["gpt-5.6-sol (default)", "High (current)"]
    assert picker.index >= len(STEPS)          # the picker closed


def test_a_model_switch_preserves_the_current_level():
    picker, _ = _run(model="gpt-5.4", effort="low")

    assert picker.chosen == ["gpt-5.4", "Low"]


def test_an_effort_change_keeps_the_current_model():
    picker, _ = _run(effort="xhigh")

    # the `(current)` row, matched as a substring, then the explicit level
    assert picker.chosen == ["gpt-5.6-luna (current)", "Extra high"]


def test_the_top_level_is_reached_through_the_more_reasoning_substep():
    picker, _ = _run(effort="max")

    assert picker.chosen == ["gpt-5.6-luna (current)", "More reasoning…", "Max"]


def test_every_offered_model_is_a_row_the_picker_actually_has():
    listed = {label.split("  ")[0].replace(" (default)", "").replace(" (current)", "")
              for label in MODEL_ROWS}
    assert set(modeldialog.MODEL_CHOICES) == listed


def test_the_step_names_stay_mutually_disjoint():
    # the whole bug: "Select Model" was a PREFIX of "Select Model and Effort",
    # and the step detector tests `needle in screen`, so it could not fail safe
    assert modeldialog.MODEL_STEP not in modeldialog.LEVEL_STEP
    assert modeldialog.LEVEL_STEP not in modeldialog.MODEL_STEP


def test_a_missing_level_is_named_rather_than_guessed():
    picker = FakePicker()
    with pytest.raises(modeldialog.CodexModelError) as caught:
        modeldialog.set_model_effort(
            picker, "win", effort="ultra", sleep=lambda _s: None
        )
    assert caught.value.step == "row"
