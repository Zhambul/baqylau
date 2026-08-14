"""Canonical tab states mapped to the established terminal color palette."""

from contracts.terminal import RGB, TabAppearance
from runtime.projections import TabState

LIGHT_TEXT = RGB(230, 233, 239)
INACTIVE_TEXT = RGB(192, 196, 204)

TAB_APPEARANCES: dict[TabState, TabAppearance] = {
    "idle": TabAppearance(RGB(92, 99, 112), LIGHT_TEXT, RGB(51, 55, 63), INACTIVE_TEXT),
    "thinking": TabAppearance(RGB(198, 120, 221), RGB(26, 6, 32), RGB(74, 43, 82), INACTIVE_TEXT),
    "working": TabAppearance(RGB(198, 120, 221), RGB(26, 6, 32), RGB(74, 43, 82), INACTIVE_TEXT),
    "executing": TabAppearance(RGB(97, 175, 239), RGB(6, 18, 31), RGB(44, 74, 99), INACTIVE_TEXT),
    "awaiting_background": TabAppearance(
        RGB(97, 175, 239),
        RGB(6, 18, 31),
        RGB(44, 74, 99),
        INACTIVE_TEXT,
    ),
    "awaiting_attention": TabAppearance(
        RGB(224, 108, 117),
        RGB(42, 6, 8),
        RGB(94, 45, 49),
        INACTIVE_TEXT,
    ),
    "awaiting_response": TabAppearance(
        RGB(152, 195, 121),
        RGB(7, 24, 10),
        RGB(68, 87, 51),
        INACTIVE_TEXT,
    ),
}


def tab_appearance(state: TabState) -> TabAppearance:
    return TAB_APPEARANCES[state]
