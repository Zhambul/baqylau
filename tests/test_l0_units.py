# L0 — tiny pure-unit pins for shared vocabulary refactors:
#
#   1. fmt_dur grew a decimals flag replacing the private _dur — pin both
#      output shapes byte-for-byte so the scoreboard's ⏱ chip and the
#      command-duration chips can't drift apart again.
#   2. frontends.base.INACTIVE_FG / TAB_COLOR_NONE — the hoisted constants
#      the kitty adapter (and tests/colors.py) must agree on.
#   3. core.noaudit — the centralized audit-import-degradation helper: the
#      stub swallows every call, and load_audit() returns the real module
#      when it imports.
import sys

from conftest import REPO

sys.path.insert(0, REPO)

from core.noaudit import NoAudit, load_audit                 # noqa: E402
from core.ops import fmt_dur                                 # noqa: E402
from frontends.base import INACTIVE_FG, TAB_COLOR_NONE       # noqa: E402


def test_fmt_dur_decimal_shape():
    assert fmt_dur(0) == "0.0s"
    assert fmt_dur(3.24) == "3.2s"
    assert fmt_dur(59.96) == "60.0s"     # sub-minute branch formats, not rounds up
    assert fmt_dur(60) == "1m00s"
    assert fmt_dur(247) == "4m07s"
    assert fmt_dur(-5) == "0.0s"         # negatives clamp to 0


def test_fmt_dur_integer_shape():        # the old private _dur, byte-identical
    assert fmt_dur(0, decimals=False) == "0s"
    assert fmt_dur(3.9, decimals=False) == "3s"       # truncates, never rounds
    assert fmt_dur(59.9, decimals=False) == "59s"
    assert fmt_dur(60.5, decimals=False) == "1m00s"
    assert fmt_dur(247, decimals=False) == "4m07s"
    assert fmt_dur(-5, decimals=False) == "0s"


def test_frontend_color_constants():
    assert INACTIVE_FG == "#c0c4cc"
    assert TAB_COLOR_NONE == "NONE"


def test_noaudit_stub_swallows_everything():
    stub = NoAudit()
    assert stub.hook_event({"x": 1}, handler="h", decision="d") is None
    assert stub.anything_at_all(1, 2, kw=3) is None


def test_load_audit_returns_real_module():
    import core.audit
    assert load_audit() is core.audit


# ---- core.hostpane._anchored_tab_windows — the shared anchor traversal ------
# One helper now backs both close_stale_mirrors and tab_host_sid; these pins
# hold the anchoring invariant (docs/mirror-pane.md § Anchoring): an anchor
# selects the tab CONTAINING that window id; no anchor falls back to the
# focused tab of the focused os-window; neither → nothing.

from core.hostpane import _anchored_tab_windows              # noqa: E402


class _FakeFE:
    def __init__(self, ls):
        self._ls = ls

    def ls(self):
        return self._ls


def _win(wid, **user_vars):
    return {"id": wid, "user_vars": user_vars}


def _ls():
    return [
        {"is_focused": False, "tabs": [
            {"is_focused": True, "windows": [_win(1), _win(2, claude_mirror="s1")]},
        ]},
        {"is_focused": True, "tabs": [
            {"is_focused": False, "windows": [_win(3)]},
            {"is_focused": True, "windows": [_win(4, claude_session="s2"),
                                             _win(5, claude_scorebar="s2")]},
        ]},
    ]


def test_anchored_traversal_anchor_match():
    tabs = list(_anchored_tab_windows(_FakeFE(_ls()), "2"))
    assert len(tabs) == 1
    assert [w["id"] for w in tabs[0]] == [1, 2]   # the whole tab, not just the anchor


def test_anchored_traversal_anchor_absent():
    assert list(_anchored_tab_windows(_FakeFE(_ls()), "99")) == []


def test_anchored_traversal_focused_fallback():
    tabs = list(_anchored_tab_windows(_FakeFE(_ls()), None))
    assert len(tabs) == 1                          # focused tab of focused osw only
    assert [w["id"] for w in tabs[0]] == [4, 5]


def test_anchored_traversal_nothing_focused():
    ls = _ls()
    for osw in ls:
        osw["is_focused"] = False
    assert list(_anchored_tab_windows(_FakeFE(ls), None)) == []
