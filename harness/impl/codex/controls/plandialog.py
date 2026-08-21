# harness/impl/codex/controls/plandialog.py — drive codex's plan-mode DECISION picker.
#
# The codex analog of harness/impl/claude_code/controls/plandialog.py, and the sibling of
# harness/impl/codex/controls/dialog.py (the request_user_input driver). After codex presents
# a plan in Plan mode it shows a numbered decision picker on screen — the same
# `N. label  description` / `›` cursor geometry the /model picker uses (so this
# reuses dialog.py's `rows`/`_cursor_to`/`_poll`), but a DIFFERENT header +
# footer:
#
#     Implement this plan?
#   › 1. Yes, implement this plan          Switch to Default and start coding.
#     2. Yes, clear context and implement  Fresh thread. Context: 3% used.
#     3. No, stay in Plan mode             Continue planning with the model.
#     Press enter to confirm or esc to go back
#
# There is NO plan record for the decision itself (it is pure TUI, no rollout
# row until decided), so — like the ask driver — the ONLY way to answer from the
# web is to drive the picker, every step screen-verified. It lives in the PLUGIN
# (not beside harness/impl/claude_code/controls/plandialog.py) because codex's `plan` HostControl gesture
# drives it and the layering rule forbids a plugin importing the dashboard: the
# whole gesture, driver included, sits behind HostControl and the dashboard only
# calls host.plan (docs/codex.md *Plan mode*).
#
# Verified live (codex-cli 0.144.1) against a real plan-mode session.
import time
from collections.abc import Callable

from harness.impl.codex.controls.dialog import (CodexAskError, Driver, OptionRow,
                                   STEP_TIMEOUT_S, _cursor_to, _poll, rows)

# the decision picker's footer + header (distinct from the ask dialog's
# "submit answer" footer / "Question N/M" header) — the detectors that tell the
# plan picker apart from every other codex screen.
FOOT = "enter to confirm"
HEAD = "Implement this plan?"
# the "keep planning" row's label stem — matched case-insensitively as a
# substring so wording drift ("No, stay in Plan mode" / "keep planning") still
# resolves the dismiss row; also what options() drops from the APPROVE set.
KEEP_PLANNING = "stay in plan mode"

# The APPROVE rows the decision picker offers (verified live, codex-cli 0.144.1).
# Only the stable LABEL is declared — the trailing description varies (`Context:
# N% used`). The web plan card shows these as its decision buttons WITHOUT a
# screen read (the plan is proven pending read-side; these are static), and
# decide() re-reads + label-verifies the LIVE screen before pressing, so a codex
# wording drift fails SAFE (no press) rather than mis-deciding.
APPROVE_OPTIONS = ({"digit": "1", "label": "Yes, implement this plan"},
                   {"digit": "2", "label": "Yes, clear context and implement"})


class CodexPlanError(CodexAskError):
    """A plan-decision step's expected screen never appeared. Reuses the ask
    driver's error shape (`.step` names it for the audit) — the picker is left
    EXACTLY as it was (never Escape-closed: codex's Esc goes BACK a step, so a
    blind Esc could dismiss into an ambiguous state), so a re-decide normalizes."""


def picker_open(screen: str) -> bool:
    """Is codex's plan-DECISION picker on screen — its header + footer both
    visible (the footer alone also matches the /model picker)."""
    s = screen or ""
    return HEAD in s and FOOT in s


def _picker_region(screen: str) -> str:
    """Only the lines BELOW the `Implement this plan?` header — the plan's own
    body is a numbered list too (`1. Create …`), and rows() would read those as
    options. The decision rows are the ones after the header."""
    s = screen or ""
    at = s.find(HEAD)
    return s[at + len(HEAD):] if at >= 0 else s


def option_rows(screen: str) -> list[OptionRow]:
    """The DECISION rows only (the picker region's numbered rows), never the
    plan-body numbers above the header."""
    return rows(_picker_region(screen))


def options(driver: Driver, win: str) -> list[dict[str, str]]:
    """The APPROVE options on the live picker as [{digit, label}] — every
    decision row EXCEPT the keep-planning row (which the card offers as its own
    'keep planning' button, mapped to dismiss). Read-only: no key is pressed.
    Raises CodexPlanError('open') when the picker isn't up (the plan resolved in
    the terminal — the card self-heals on the next read)."""
    screen, ok = _poll(driver, win, picker_open, STEP_TIMEOUT_S, time.sleep)
    if not ok:
        raise CodexPlanError("open", "no plan-decision picker on screen")
    out: list[dict[str, str]] = []
    for r in option_rows(screen):
        if KEEP_PLANNING in r["label"].lower():
            continue
        out.append({"digit": r["num"], "label": r["label"]})
    return out


def _decide_row(driver: Driver, win: str, num: str, sleep: Callable[[float], None]) -> None:
    """Move the `›` cursor onto option `num` and ENTER, then verify the picker is
    GONE (the decision took). Raises CodexPlanError otherwise."""
    screen, ok = _poll(driver, win, picker_open, STEP_TIMEOUT_S, sleep)
    if not ok:
        raise CodexPlanError("open", "no plan-decision picker on screen")
    try:
        _cursor_to(driver, win, num, sleep)
    except CodexAskError as e:
        raise CodexPlanError("cursor", e.detail or str(e)) from e
    driver.send_key(win, "enter")
    _, gone = _poll(driver, win, lambda s: not picker_open(s), STEP_TIMEOUT_S, sleep)
    if not gone:
        raise CodexPlanError("submit", "picker still on screen after enter")


def decide(
    driver: Driver,
    win: str,
    digit: str,
    label: str,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, bool]:
    """APPROVE the plan: press the decision row whose LABEL matches `label` (a
    case-insensitive substring — the same label-guard Claude's plandialog.decide
    uses, but keyed on the LABEL not the digit so codex reordering the rows can't
    press the wrong one; `digit` is advisory). Label drift ⇒ CodexPlanError, and
    nothing is pressed. Returns {"decided": True}."""
    screen = driver.get_text(win) or ""
    if not picker_open(screen):
        raise CodexPlanError("open", "no plan-decision picker on screen")
    want = (label or "").strip().lower()
    match = next((r for r in option_rows(screen)
                  if want and want in r["label"].strip().lower()), None)
    if match is None:
        raise CodexPlanError("label", "no row matching %r on screen (digit %s)"
                             % (label, digit))
    _decide_row(driver, win, match["num"], sleep)
    return {"decided": True}


def dismiss(driver: Driver, win: str, sleep: Callable[[float], None] = time.sleep) -> dict[str, bool]:
    """KEEP PLANNING: pick the 'No, stay in Plan mode' row (an explicit choice,
    not an Esc — Esc only steps BACK). Returns {"dismissed": True}."""
    screen, ok = _poll(driver, win, picker_open, STEP_TIMEOUT_S, sleep)
    if not ok:
        raise CodexPlanError("open", "no plan-decision picker on screen")
    row = next((r for r in option_rows(screen)
                if KEEP_PLANNING in r["label"].lower()), None)
    if row is None:
        raise CodexPlanError("dismiss", "no keep-planning row on screen")
    _decide_row(driver, win, row["num"], sleep)
    return {"dismissed": True}
