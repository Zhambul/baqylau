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
from core.ops import fmt_dur, split_tokens                   # noqa: E402
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


# ---- core.ops.split_tokens — the ONE usage-fields → Σ-row tk_* mapping ------
# All five former per-site encodings (three bump_transcript branches, the
# subagent_fmt reconcile, the codex footer) now call this; these pins hold the
# arithmetic each site relied on.

def test_split_tokens_subtracts_cache_creation():
    # Anthropic shape: input_tokens INCLUDES cache creation — tk_in is the
    # fresh remainder, and tk_in + tk_create round-trips back to the input.
    s = split_tokens(300, 50, 1000, 200)
    assert s == {"tk_in": 100, "tk_out": 50, "tk_read": 1000, "tk_create": 200}
    assert s["tk_in"] + s["tk_create"] == 300          # == billed fresh input
    assert s["tk_in"] + s["tk_out"] + s["tk_create"] == 350   # == ▪-row tokens


def test_split_tokens_no_cache_activity():
    assert split_tokens(100, 50, 0, 0) == {
        "tk_in": 100, "tk_out": 50, "tk_read": 0, "tk_create": 0}


def test_split_tokens_cache_read_only():
    # Cache READ never touches tk_in (input_tokens excludes it upstream).
    assert split_tokens(7, 3, 5000, 0) == {
        "tk_in": 7, "tk_out": 3, "tk_read": 5000, "tk_create": 0}


def test_split_tokens_codex_shape():
    # codex passes create=0 with an input already net of cache reads — the
    # split must leave its `fresh` untouched as tk_in.
    assert split_tokens(42, 9, 12345, 0) == {
        "tk_in": 42, "tk_out": 9, "tk_read": 12345, "tk_create": 0}


# ---- claude-mirror.py iter_painted / painted_rows — the ONE row walk --------
# frame_bytes, trim_to_budget, measure, locate_viewport, restore_to and
# toggle_repaint all used to hand-roll the same "for op in OPS: for o in
# expanded(op)" walk; any two disagreeing is a model-vs-buffer divergence
# (restores land where the math said, not where the frame is). These pins hold
# the shared helper's row accounting: the leading 1 is the banner line, each
# rendered op contributes newline-count + 1.

def _load_mirror():
    import importlib.util
    import os
    spec = importlib.util.spec_from_file_location(
        "claude_mirror_script", os.path.join(REPO, "claude-mirror.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.FIXED_WIDTH = 80          # deterministic width() under a non-tty pytest
    return m


def test_painted_rows_counts_banner_plus_ops():
    m = _load_mirror()
    m.OPS[:] = [{"t": "line", "s": "one"},            # 1 row
                {"t": "line", "s": "two\nthree"}]     # 2 rows
    m.VIEW_OPEN.clear()
    assert m.painted_rows(80) == 1 + 1 + 2


def test_painted_rows_includes_open_view_blocks():
    m = _load_mirror()
    m.OPS[:] = [{"t": "line", "s": "Read(x.py)", "v": "g1"}]
    m.VIEW_OPEN.clear()
    m._VIEW_OPS["g1"] = [{"t": "line", "s": "body1\nbody2"}]
    closed = m.painted_rows(80)
    m.VIEW_OPEN.add("g1")
    assert m.painted_rows(80) == closed + 2           # the expanded block's rows


def test_painted_rows_agrees_with_frame_bytes_and_measure():
    m = _load_mirror()
    m.OPS[:] = [{"t": "line", "s": "a"},
                {"t": "line", "s": "b\nc", "v": "g2"},
                {"t": "rule"}]
    m.VIEW_OPEN.clear()
    m._VIEW_OPS["g2"] = [{"t": "line", "s": "vv"}]
    m.VIEW_OPEN.add("g2")
    total = m.painted_rows(80)
    # frame_bytes paints banner + every op, one trailing newline each — its
    # newline count IS the painted row total (the scroll math's ground truth).
    assert m.frame_bytes(80).count("\n") == total
    # measure's third return is the same full painted line count.
    pos, idx, acc = m.measure("g2")
    assert acc == total
    assert pos == 1 and idx == 2                      # banner=0, "a"=1, op at 2
