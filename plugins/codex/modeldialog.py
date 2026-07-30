# plugins/codex/modeldialog.py — drive codex's interactive /model picker.
#
# codex has NO `/model <arg>` and NO `/effort` — model AND reasoning effort are
# both set through ONE interactive 3-step picker (`/model`), so the dashboard's
# ✦ model / ✧ effort buttons are driven here rather than by a slash-command
# paste. The sibling of plugins/codex/dialog.py (ask) and plandialog.py (plan):
# the same numbered-row / `›`-cursor / `enter to confirm` geometry, every step
# screen-verified. It lives in the PLUGIN because codex's `model`/`effort`
# HostControl gestures drive it and the layering rule forbids a plugin importing
# the dashboard.
#
# The picker (verified live, codex-cli 0.144.1):
#   Step 1  "Select Model"           → 'All models' (browse the full list)
#   Step 2  "Select Model and Effort" → the numbered model list (gpt-5.6-sol …)
#   Step 3  "Select Reasoning Level for <model>" → Low/Medium/High/Extra high/
#                                                   Max/Ultra
# Every step's footer is "Press enter to confirm or esc to go back". codex
# couples the two axes: switching MODEL lands on step 3 at that model's DEFAULT
# effort, so the ✦ button changes model + accepts the default (codex's own
# behaviour), and the ✧ button keeps the CURRENT model (its `(current)` row) and
# changes only the level.
import time

from plugins.codex.dialog import (STEP_TIMEOUT_S, _cursor_to, _poll, rows)

# step headers + the shared footer (disjoint from the ask "to submit" / plan
# "Implement this plan?" detectors)
FOOT = "to confirm"
STEP1 = "Select Model"
STEP2 = "Select Model and Effort"
STEP3 = "Select Reasoning Level"
ALL_MODELS = "all models"            # the step-1 row that opens the full list
CURRENT = "(current)"                # the step-2 marker on the active model

# the ✧ effort tokens the dashboard sends → the on-screen reasoning-level LABEL
# (matched EXACTLY, not as a substring, so 'high' can't hit 'Extra high'). Also
# the map the ✦ model gesture runs the CURRENT effort (from read.context) through
# to PRESERVE it across a model switch, so a few spelling aliases for the higher
# levels are included defensively (the config token codex records for them is
# less certain than low/medium/high) — an unmapped token falls back to the
# picker default, never a wrong level.
EFFORT_LABEL = {"low": "Low", "medium": "Medium", "high": "High",
                "xhigh": "Extra high", "extra_high": "Extra high",
                "extra-high": "Extra high", "max": "Max", "ultra": "Ultra"}
# the codex models the ✦ menu offers (label == the picker row + the -m arg).
MODEL_CHOICES = ("gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna",
                 "gpt-5.5", "gpt-5.4", "gpt-5.4-mini")
EFFORT_CHOICES = ("low", "medium", "high", "xhigh", "max", "ultra")


class CodexModelError(Exception):
    """A /model picker step's expected screen never appeared. `.step` names it
    for the audit; the picker is left as-is (Esc only steps BACK, so we never
    blind-Esc it) for a retry."""

    def __init__(self, step, detail=""):
        super().__init__(step + ((": " + detail) if detail else ""))
        self.step = step
        self.detail = detail


def _norm(label):
    """A picker row label stripped for EQUALITY matching — lowercased, with the
    `(current)`/`(default)` markers removed. So `gpt-5.6-sol (current)` matches
    the model `gpt-5.6-sol`, and `Extra high (default)` matches `Extra high`."""
    s = (label or "").lower()
    for mark in ("(current)", "(default)"):
        s = s.replace(mark, "")
    return s.strip()


def _await(fe, win, needle, sleep):
    screen, ok = _poll(fe, win, lambda s: needle in (s or ""), STEP_TIMEOUT_S,
                       sleep)
    if not ok:
        raise CodexModelError("step", "%r never appeared" % needle)
    return screen


def _pick(fe, win, header, want, sleep):
    """On the picker step whose header contains `header`, move the `›` cursor to
    the row whose normalized label EQUALS `want` (or, when `want` is "", accept
    the pre-selected row) and ENTER. `want` may also be a marker like `(current)`
    matched as a substring."""
    _await(fe, win, header, sleep)
    if want:
        screen = fe.get_text(win) or ""
        w = want.lower()
        row = next((r for r in rows(screen)
                    if _norm(r["label"]) == w or w in r["label"].lower()), None)
        if row is None:
            raise CodexModelError("row", "no %r under %r" % (want, header))
        try:
            _cursor_to(fe, win, row["num"], sleep)
        except Exception as e:               # dialog._cursor_to's CodexAskError
            raise CodexModelError("cursor", str(e)) from e
    fe.send_key(win, "enter")


def set_model_effort(fe, win, model="", effort="", sleep=time.sleep):
    """Drive the /model picker. `model` = a codex model id (✦ — changes model,
    accepts that model's DEFAULT effort); `effort` = a token in EFFORT_CHOICES
    (✧ — keeps the CURRENT model, changes only the level). Exactly one is set by
    a given gesture. Opens the picker itself (paste `/model`), then Step1→'All
    models'→Step2→model→Step3→level, verified. Returns {"set": True}; raises
    CodexModelError on any unverified step."""
    if not fe.paste_text(win, "/model"):
        raise CodexModelError("open", "/model paste refused")
    _pick(fe, win, STEP1, ALL_MODELS, sleep)
    # Step 2 — the model: the chosen one (✦), else keep the current (✧).
    _pick(fe, win, STEP2, model or CURRENT, sleep)
    # Step 3 — the reasoning level: the chosen one (✧), else the model's default
    # (✦ accepts the pre-selected row with a bare Enter).
    want = EFFORT_LABEL.get(effort, "") if effort else ""
    _pick(fe, win, STEP3, want, sleep)
    _, gone = _poll(fe, win, lambda s: FOOT not in (s or ""), STEP_TIMEOUT_S,
                    sleep)
    if not gone:
        raise CodexModelError("submit", "picker still open after the level")
    return {"set": True}
