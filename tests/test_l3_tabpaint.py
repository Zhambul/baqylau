# L3 — core/tabpaint.py, the tool-AGNOSTIC tab PAINT engine, in isolation.
#
# test_l3_tab.py pins the whole Claude-Code dispatch END TO END (through the real
# hook subprocess); these pin the ENGINE it now delegates its paint to — the piece
# a SECOND producer (standalone codex, a future hookless polled producer) reuses.
# The reuse contract is exactly the two hard-won rules: the DEDUP against the
# persisted tab row, and PERSIST-ONLY-ON-rc==0 (persisting a failed paint stranded
# a colour the tab never showed, and the dedup then suppressed every retry). Driven
# in-process with a fake frontend + the per-test hermetic tab/audit DBs — no plugin,
# no hook: whatever drives the engine (any plugin's decision table) gets these.
import os
import sqlite3

import pytest

from core import tabpaint
from core import tabs
from core.tabs import COLORS


class _FE:
    """The minimal Frontend surface tabpaint.paint touches, recording paints."""

    def __init__(self, rc=0, available=True, usable=True):
        self.rc = rc
        self._available = available
        self._usable = usable
        self.calls = []          # ("set", win, (rgb...)) / ("clear", win)

    def available(self):
        return self._available

    def usable(self):
        return self._usable

    def set_tab_color(self, win, *rgb):
        self.calls.append(("set", win, rgb))
        return self.rc

    def clear_tab_color(self, win):
        self.calls.append(("clear", win))
        return self.rc


@pytest.fixture
def tabdb(monkeypatch, tmp_path):
    """Hermetic tab DB — tabs.TABDB is import-time-captured from /tmp."""
    monkeypatch.setattr(tabs, "TABDB", str(tmp_path / "tab.db"))
    return str(tmp_path / "tab.db")


def _transitions():
    """(prev, new, applied, reason) rows the engine wrote this test — the audit
    dir is the autouse _fresh_audit_conn fixture's per-test CLAUDE_AUDIT_DIR."""
    db = os.path.join(os.environ["CLAUDE_AUDIT_DIR"], "audit.db")
    if not os.path.exists(db):
        return []
    conn = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=5)
    try:
        return conn.execute("SELECT prev_state, new_state, applied, reason "
                            "FROM tab_transitions ORDER BY id").fetchall()
    finally:
        conn.close()


WIN = "77"


def test_paint_persists_then_dedups(tabdb):
    """A landed paint sets the tab colour AND persists the row; a second paint of
    the SAME state skips the socket entirely (dedup) and audits `already shown`."""
    fe = _FE(rc=0)
    tabpaint.paint(fe, WIN, "executing", "pretool: Bash", sid="s1", dispatch="pretool")
    assert fe.calls == [("set", WIN, COLORS["executing"])]
    assert tabs.tab_get(WIN) == "executing"

    tabpaint.paint(fe, WIN, "executing", "pretool: Bash", sid="s1", dispatch="pretool")
    assert len(fe.calls) == 1, "identical state must not re-hit the socket"
    rows = _transitions()
    assert (rows[0][1], rows[0][2]) == ("executing", 1)          # applied
    assert rows[-1][2] == 0 and "already shown" in (rows[-1][3] or "")


def test_paint_failed_not_persisted_then_retried(tabdb):
    """rc!=0 must leave the row unchanged (so the next same-state event RETRIES)
    and audit the failure — the stranded-colour contract the engine exists for."""
    fe = _FE(rc=1)
    tabpaint.paint(fe, WIN, "idle", "start", sid="s1", dispatch="idle")
    assert tabs.tab_get(WIN) == ""                    # NOT persisted
    rows = _transitions()
    assert rows[-1][2] == 0 and "failed rc=1" in (rows[-1][3] or "")

    fe.rc = 0
    tabpaint.paint(fe, WIN, "idle", "start", sid="s1", dispatch="idle")   # not deduped
    assert tabs.tab_get(WIN) == "idle"
    assert fe.calls == [("set", WIN, COLORS["idle"]), ("set", WIN, COLORS["idle"])]


def test_paint_clear_drops_row_and_noops_when_clear(tabdb):
    """clear/reset paints the theme default, drops the row, and a second clear of
    an already-clear tab is a silent no-op (no socket call)."""
    fe = _FE(rc=0)
    tabpaint.paint(fe, WIN, "idle", sid="s1", dispatch="idle")
    tabpaint.paint(fe, WIN, "clear", sid="s1", dispatch="clear")
    assert fe.calls[-1] == ("clear", WIN)
    assert tabs.tab_get(WIN) == ""

    n = len(fe.calls)
    tabpaint.paint(fe, WIN, "clear", sid="s1", dispatch="clear")
    assert len(fe.calls) == n, "clearing an already-clear tab must not paint"


def test_paint_no_window_audits_skip_and_no_paint(tabdb):
    """No resolved window (a daemon-origin/headless session): no paint, and an
    audited skip so the trail still shows the producer fired."""
    fe = _FE(rc=0)
    tabpaint.paint(fe, "", "working", "posttool", sid="s1", dispatch="posttool")
    assert fe.calls == []
    rows = _transitions()
    assert rows and rows[-1][2] == 0 and "not inside kitty" in (rows[-1][3] or "")


def test_paint_unavailable_frontend_audits_skip(tabdb):
    """available() False (no remote-control socket) is the same skip path."""
    fe = _FE(rc=0, available=False)
    tabpaint.paint(fe, WIN, "working", sid="s1", dispatch="posttool")
    assert fe.calls == []
    assert tabs.tab_get(WIN) == ""
