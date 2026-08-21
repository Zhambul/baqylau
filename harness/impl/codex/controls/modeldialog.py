# harness/impl/codex/controls/modeldialog.py — drive codex's interactive /model picker.
#
# codex has NO `/model <arg>` and NO `/effort` — model AND reasoning effort are
# both set through ONE interactive 3-step picker (`/model`), so the dashboard's
# ✦ model / ✧ effort buttons are driven here rather than by a slash-command
# paste. The sibling of harness/impl/codex/controls/dialog.py (ask) and plandialog.py (plan):
# the same numbered-row / `›`-cursor / `enter to confirm` geometry, every step
# screen-verified. It lives in the PLUGIN because codex's `model`/`effort`
# HostControl gestures drive it and the layering rule forbids a plugin importing
# the dashboard.
#
# The picker (verified live, codex-cli 0.147.0):
#   Step 1  "Select Model and Effort"            → the numbered model list
#                                                   (gpt-5.6-sol … gpt-5.3-codex-spark)
#   Step 2  "Select Reasoning Level for <model>" → Low/Medium/High/Extra high +
#                                                   a "More reasoning…" row
#   Step 2a "Advanced Reasoning"                 → what that row opens (Max)
#
# 0.144.1 opened on a THIRD screen in front of these — a "Select Model" step
# whose 'All models' row browsed the full list — and this driver waited for it.
# 0.147.0 removed it, and the wait SUCCEEDED anyway: "Select Model" is a
# substring of "Select Model and Effort", and the step detector tests
# `needle in screen`. So the driver matched the model list, believed it was one
# screen earlier, looked for an 'All models' row that no longer exists, and
# raised in 379ms with the picker left open on screen (measured, session
# 01a0038a: `control` audit row, status indeterminate, reason "row: no 'all
# models' under 'Select Model'"). The lesson is the one docs/styleguide.md
# already states about screen markers: a detector that is a PREFIX of the next
# step's cannot fail safe, so the step names here must stay mutually disjoint —
# "Select Model and Effort" and "Select Reasoning Level" are.
#
# Every step's footer is "Press enter to confirm or esc to go back". codex
# couples the two axes: switching MODEL lands on step 3 at that model's DEFAULT
# effort, so the ✦ button changes model + accepts the default (codex's own
# behaviour), and the ✧ button keeps the CURRENT model (its `(current)` row) and
# changes only the level.
import time
from collections.abc import Callable

from domain.ids import WindowId

from harness.impl.codex.controls.dialog import (Driver, STEP_TIMEOUT_S, _cursor_to, _poll, rows)

# step headers + the shared footer (disjoint from the ask "to submit" / plan
# "Implement this plan?" detectors)
FOOT = "to confirm"
MODEL_STEP = "Select Model and Effort"
LEVEL_STEP = "Select Reasoning Level"
ADVANCED = "Advanced Reasoning"      # the sub-step Max lives under
MORE = "more reasoning"              # the level row that opens ADVANCED
CURRENT = "(current)"                # the model-step marker on the active model

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
# the codex models the ✦ menu offers, in the picker's own order (label == the
# picker row + the -m arg). Read off 0.147.0's model step.
MODEL_CHOICES = ("gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna",
                 "gpt-5.5", "gpt-5.4", "gpt-5.4-mini", "gpt-5.3-codex-spark")
EFFORT_CHOICES = ("low", "medium", "high", "xhigh", "max", "ultra")


class CodexModelError(Exception):
    """A /model picker step's expected screen never appeared. `.step` names it
    for the audit; the picker is left as-is (Esc only steps BACK, so we never
    blind-Esc it) for a retry."""

    def __init__(self, step: str, detail: str = "") -> None:
        super().__init__(step + ((": " + detail) if detail else ""))
        self.step = step
        self.detail = detail


def _norm(label: str) -> str:
    """A picker row label stripped for EQUALITY matching — lowercased, with the
    `(current)`/`(default)` markers removed. So `gpt-5.6-sol (current)` matches
    the model `gpt-5.6-sol`, and `Extra high (default)` matches `Extra high`."""
    s = (label or "").lower()
    for mark in ("(current)", "(default)"):
        s = s.replace(mark, "")
    return s.strip()


def _await(driver: Driver, win: WindowId, needle: str, sleep: Callable[[float], None]) -> str:
    screen, ok = _poll(driver, win, lambda s: needle in (s or ""), STEP_TIMEOUT_S,
                       sleep)
    if not ok:
        raise CodexModelError("step", "%r never appeared" % needle)
    return screen


def _goto(driver: Driver, win: WindowId, num: str, sleep: Callable[[float], None]) -> None:
    """Move the `›` cursor onto row `num`, mapping dialog._cursor_to's error into
    a CodexModelError so the gesture's one except clause owns it."""
    try:
        _cursor_to(driver, win, num, sleep)
    except Exception as e:                   # dialog._cursor_to's CodexAskError
        raise CodexModelError("cursor", str(e)) from e


def _pick(driver: Driver, win: WindowId, header: str, want: str, sleep: Callable[[float], None]) -> None:
    """On the picker step whose header contains `header`, move the `›` cursor to
    the row whose normalized label EQUALS `want` (or, when `want` is "", accept
    the pre-selected row) and ENTER. `want` may also be a marker like `(current)`
    matched as a substring."""
    _await(driver, win, header, sleep)
    if want:
        screen = driver.get_text(win) or ""
        w = want.lower()
        row = next((r for r in rows(screen)
                    if _norm(r["label"]) == w or w in r["label"].lower()), None)
        if row is None:
            raise CodexModelError("row", "no %r under %r" % (want, header))
        _goto(driver, win, row["num"], sleep)
    driver.send_key(win, "enter")


def _pick_level(
    driver: Driver,
    win: WindowId,
    want: str,
    sleep: Callable[[float], None],
    strict: bool = True,
) -> None:
    """The level step, with the INDIRECTION the top level sits behind. Measured
    on 0.147.0/gpt-5.6-luna: Low / Medium (default) / High / Extra high, plus a
    `More reasoning…` row opening an `Advanced Reasoning` sub-step holding Max
    alone — no Ultra row anywhere, though only this one model's list was read, so
    EFFORT_LABEL keeps its Ultra spelling rather than assume the level is gone
    everywhere. `want` is the on-screen level LABEL (an EFFORT_LABEL value) or ""
    to accept the pre-selected default.

    `strict` governs a level the CURRENT model does NOT offer (reasoning levels
    are model-dependent):
    strict=True (an EXPLICIT ✧ effort) raises; strict=False (a ✦ model switch
    PRESERVING the old level) accepts the new model's DEFAULT instead — a preserve
    must never fail the switch just because the target model can't do that level."""
    _await(driver, win, LEVEL_STEP, sleep)
    if not want:
        driver.send_key(win, "enter")            # accept the model's default level
        return
    w = want.lower()
    screen = driver.get_text(win) or ""
    row = next((r for r in rows(screen) if _norm(r["label"]) == w), None)
    if row is not None:                      # listed directly on this model
        _goto(driver, win, row["num"], sleep)
        driver.send_key(win, "enter")
        return
    # not listed — open 'More reasoning…' and pick it in the Advanced sub-step
    more = next((r for r in rows(screen) if MORE in r["label"].lower()), None)
    if more is None:
        # this model has no such level and no Advanced sub-step
        if not strict:
            driver.send_key(win, "enter")        # best-effort preserve → its default
            return
        raise CodexModelError("row", "no %r (nor a More-reasoning row) under %r"
                              % (want, LEVEL_STEP))
    _goto(driver, win, more["num"], sleep)
    driver.send_key(win, "enter")
    _await(driver, win, ADVANCED, sleep)
    screen = driver.get_text(win) or ""
    row = next((r for r in rows(screen) if _norm(r["label"]) == w), None)
    if row is None:
        raise CodexModelError("row", "no %r under %r" % (want, ADVANCED))
    _goto(driver, win, row["num"], sleep)
    driver.send_key(win, "enter")


def set_model_effort(
    driver: Driver,
    win: WindowId,
    model: str = "",
    effort: str | None = "",
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, bool]:
    """Drive the /model picker. `model` = a codex model id (✦ — changes model,
    accepts that model's DEFAULT effort); `effort` = a token in EFFORT_CHOICES
    (✧ — keeps the CURRENT model, changes only the level). Exactly one is set by
    a given gesture. Opens the picker itself (paste `/model`), which lands
    STRAIGHT on the model step, then model→level, verified. Returns
    {"set": True}; raises CodexModelError on any unverified step."""
    if not driver.paste_text(win, "/model"):
        raise CodexModelError("open", "/model paste refused")
    # Step 1 — the model: the chosen one (✦), else keep the current (✧).
    _pick(driver, win, MODEL_STEP, model or CURRENT, sleep)
    # Step 2 — the reasoning level: the chosen one (✧), else the model's default
    # (✦ accepts the pre-selected row with a bare Enter). Handles the `More
    # reasoning…` sub-step the top level sits behind. When a MODEL is
    # being set the effort is a PRESERVE (best-effort — strict=False: a target
    # model that lacks the old level gets its default, never a failed switch);
    # when only the effort is set it is an EXPLICIT ✧ choice (strict).
    want = EFFORT_LABEL.get(effort, "") if effort else ""
    _pick_level(driver, win, want, sleep, strict=not model)
    _, gone = _poll(driver, win, lambda s: FOOT not in (s or ""), STEP_TIMEOUT_S,
                    sleep)
    if not gone:
        raise CodexModelError("submit", "picker still open after the level")
    return {"set": True}
