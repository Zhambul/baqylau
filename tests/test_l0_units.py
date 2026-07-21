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
import os
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


def test_fmt_dur_hour_day_tiers():       # two largest units only above an hour
    assert fmt_dur(3599) == "59m59s"      # last second of the minute tier
    assert fmt_dur(3600) == "1h00m"       # hour tier drops seconds
    assert fmt_dur(3753) == "1h02m"       # 1h02m33s truncates to minutes
    assert fmt_dur(27458) == "7h37m"      # the 457m38s complaint case
    assert fmt_dur(86399) == "23h59m"     # last minute of the hour tier
    assert fmt_dur(86400) == "1d00h"      # day tier drops minutes
    assert fmt_dur(198000) == "2d07h"
    # decimals only affects the sub-minute branch — higher tiers identical
    assert fmt_dur(3600, decimals=False) == "1h00m"
    assert fmt_dur(27458, decimals=False) == "7h37m"
    assert fmt_dur(86400, decimals=False) == "1d00h"


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


def test_no_module_bypasses_load_audit():
    """core/noaudit.py is the ONE audit-import-degradation helper: no other
    module may define its own _NoAudit-style stub, and every module gets its
    audit handle via load_audit() rather than importing core.audit directly
    (bin/claude-audit.py, the audit CLI entry over the audit module itself, is
    the sole sanctioned direct import). Grep-style pin, like the semantic
    colour-table test in test_l5_render.py."""
    import os
    import re
    stub_pat = re.compile(r"class\s+_?NoAudit\b")
    imp_pat = re.compile(r"^\s*from core import audit\b|^\s*import core\.audit\b",
                         re.MULTILINE)
    allowed_import = {"core/noaudit.py", "bin/claude-audit.py"}
    offenders = []
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in
                   (".git", "__pycache__", "tests", ".claude")]
        for f in files:
            if not f.endswith(".py"):
                continue
            rel = os.path.relpath(os.path.join(root, f), REPO)
            with open(os.path.join(root, f), encoding="utf-8",
                      errors="replace") as fh:
                text = fh.read()
            if rel != "core/noaudit.py" and stub_pat.search(text):
                offenders.append("%s: defines its own NoAudit stub" % rel)
            if rel not in allowed_import and imp_pat.search(text):
                offenders.append("%s: imports core.audit directly "
                                 "(use core.noaudit.load_audit)" % rel)
    assert not offenders, "\n".join(offenders)


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


# ---- core.hostpane.close_stale_mirrors — the audited ok must be the REAL rc -
# The sweep used to hardcode ok=1 in its pane_events row; a close that FAILED
# (pane lingered) was indistinguishable from a verified one, so the "pane
# operations that failed" anomaly could never see it.

def _audit_env(tmp_path, monkeypatch):
    """Route audit rows into the hermetic tmpdir; returns a query closure."""
    monkeypatch.setenv("CLAUDE_AUDIT", "1")
    monkeypatch.setenv("CLAUDE_AUDIT_DIR", str(tmp_path / "audit"))

    def rows(sql):
        import sqlite3
        db = str(tmp_path / "audit" / "audit.db")
        if not os.path.exists(db):
            return []
        conn = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=5)
        try:
            return conn.execute(sql).fetchall()
        finally:
            conn.close()
    return rows


class _SweepFE(_FakeFE):
    """One stale mirror in the focused tab; close_pane returns a fixed rc."""

    def __init__(self, rc):
        super().__init__([{"is_focused": True, "tabs": [{
            "is_focused": True,
            "windows": [_win(7, claude_mirror="old-sid")]}]}])
        self.rc = rc
        self.closed = []

    def current_window(self):
        return None

    def close_pane(self, var=None, win_id=None):
        self.closed.append(win_id)
        return self.rc


def test_close_stale_records_real_close_result(tmp_path, monkeypatch):
    from core import hostpane as HP
    rows = _audit_env(tmp_path, monkeypatch)
    HP.close_stale_mirrors(_SweepFE(rc=0), keep="new-sid")   # verified close
    HP.close_stale_mirrors(_SweepFE(rc=1), keep="new-sid")   # close FAILED
    got = rows("SELECT ok, detail FROM pane_events WHERE action='close-stale' "
               "ORDER BY id")
    assert [ok for ok, _ in got] == [1, 0], got
    # the detail (the hijack anomaly's join key) is identical for both
    assert all("closed sid=old-sid win=7" in d for _, d in got), got


# ---- core.hostpane.open_mirror — hand inner focus back to the host pane ------
# The mirror/scorebar panes take focus when they're split in (a plain launch
# does, and a background/web launch deliberately can't --keep-focus), so the tab
# ends up titled "▪ session" instead of the host's ai-generated title. open_mirror
# corrects the inner focus to the host — but only when it ACTUALLY opened a pane
# (else a resume/toggle-while-open would yank a mirror the user is reading).

class _OpenFE:
    """Records the terminal ops open_mirror issues. `present` = the user-var
    names of panes ALREADY in the tab (so open_mirror skips creating them)."""

    def __init__(self, present=(), focus_rc=0):
        self.present = set(present)
        self.focus_rc = focus_rc
        self.focused = []
        self.launched = []

    def find_window(self, var, value, tree=None):
        # a present pane reports 5 rows so size_bar's delta is 0 (no resize)
        return {"id": 9, "lines": 5} if var in self.present else None

    def goto_splits_layout(self, win=None):
        return 0

    def launch_pane(self, *a, **k):
        self.launched.append(k.get("var"))
        return 0

    def resize_pane(self, *a, **k):
        return 0

    def focus_first_pane(self, win_id):
        self.focused.append(win_id)
        return self.focus_rc


def test_open_mirror_focuses_host_after_opening(tmp_path, monkeypatch):
    from core import hostpane as HP
    rows = _audit_env(tmp_path, monkeypatch)
    fe = _OpenFE()                                   # nothing exists yet
    HP.open_mirror(fe, "/bin", "sid-1", str(tmp_path / "m.log"), 25, anchor="7")
    assert fe.launched and fe.focused == ["7"]       # both panes + focus to host
    got = rows("SELECT ok, detail FROM pane_events WHERE action='focus-host'")
    assert got == [(1, "win=7")], got


def test_open_mirror_records_failed_focus(tmp_path, monkeypatch):
    from core import hostpane as HP
    rows = _audit_env(tmp_path, monkeypatch)
    HP.open_mirror(_OpenFE(focus_rc=1), "/bin", "sid-1",
                   str(tmp_path / "m.log"), 25, anchor="7")
    got = rows("SELECT ok FROM pane_events WHERE action='focus-host'")
    assert got == [(0,)], got                        # the audited ok is the REAL rc


def test_open_mirror_no_focus_when_nothing_opened(tmp_path, monkeypatch):
    from core import hostpane as HP
    rows = _audit_env(tmp_path, monkeypatch)
    fe = _OpenFE(present={"claude_mirror", "claude_scorebar"})
    HP.open_mirror(fe, "/bin", "sid-1", str(tmp_path / "m.log"), 25, anchor="7")
    assert fe.focused == [] and fe.launched == []    # both existed → don't yank
    assert rows("SELECT 1 FROM pane_events WHERE action='focus-host'") == []


def test_open_mirror_no_focus_without_anchor(tmp_path, monkeypatch):
    from core import hostpane as HP
    rows = _audit_env(tmp_path, monkeypatch)
    fe = _OpenFE()
    HP.open_mirror(fe, "/bin", "sid-1", str(tmp_path / "m.log"), 25, anchor=None)
    assert fe.launched and fe.focused == []          # opened, but no host to target
    assert rows("SELECT 1 FROM pane_events WHERE action='focus-host'") == []


# ---- core.slots.pid_set — a displaced live pid must get its release-pid -----
# The upsert silently REPLACES a live pid (a resumed agent's new tailer taking
# over from the old one) with only a claim-pid row; without the paired
# release-pid for the displaced holder, the claim/release pairing anomaly
# false-flagged every healthy resume as an unbalanced slot.

def test_pid_set_displacement_emits_release_for_old_pid(tmp_path, monkeypatch):
    from core import slots
    rows = _audit_env(tmp_path, monkeypatch)
    log = str(tmp_path / "claude-mirror-pidset.log")
    slots.pid_set(log, "agentA", 111)     # first claim — no incumbent
    slots.pid_set(log, "agentA", 222)     # displaces 111
    slots.pid_set(log, "agentA", 222)     # same pid — idempotent, no release
    got = rows("SELECT action, owner_pid FROM slots WHERE kind='sub' "
               "ORDER BY id")
    assert got == [("claim-pid", 111),
                   ("release-pid", 111), ("claim-pid", 222),
                   ("claim-pid", 222)], got
    # the live row itself holds only the new pid
    assert slots.pid_get(log, "agentA") == 222


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
        "claude_mirror_script", os.path.join(REPO, "bin", "claude-mirror.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.FIXED_WIDTH = 80          # deterministic width() under a non-tty pytest
    # width() is now the module's memoized wrapper over a panescript closure —
    # rebind the underlying probe and drop the memo, keeping the wrapper.
    m._raw_width = m.PS.make_width(80)
    m.width_refresh()
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


def test_render_cache_row_counts_avoid_rescans():
    """Perf pin for the trim_to_budget quadratic fix: the (op, width) cache
    record carries the painted row count, so the row-accounting walks
    (painted_rows / trim_to_budget / measure) render each op AT MOST ONCE per
    width — repeat walks re-render nothing."""
    m = _load_mirror()
    n = 2000
    m.OPS[:] = [{"t": "line", "s": "op-%d\nsecond" % i} for i in range(n)]
    m.VIEW_OPEN.clear()
    calls = []
    real = m._render
    m._render = lambda op, w: (calls.append(1), real(op, w))[1]
    assert m.painted_rows(80) == 1 + 2 * n
    assert len(calls) == n                    # first walk renders every op once
    calls.clear()
    m.painted_rows(80)
    m.trim_to_budget(80)                      # budget not exceeded — no drop
    m.measure("nope")
    assert calls == []                        # repeat walks: zero re-renders
    m.painted_rows(60)                        # width change invalidates
    assert len(calls) == n


def test_stripped_rows_cached_per_render():
    """Perf pin for locate_viewport's rows rebuild: the ANSI-stripped line
    list is cached on the render record (filled lazily, invalidated with it),
    so repeat viewport searches strip each op at most once per width."""
    m = _load_mirror()
    op = {"t": "gut", "s": "aa\nbb", "c": [1, 2, 3]}
    calls = []
    real = m.R.strip_ansi
    m.R.strip_ansi = lambda s: (calls.append(1), real(s))[1]
    try:
        c = m.rendered(op, 80)
        first = m.stripped_rows(c)
        assert first == [r.rstrip() for r in real(c[1]).split("\n")]
        n = len(calls)
        assert n >= 1
        assert m.stripped_rows(c) is first    # second read: the cached list
        assert len(calls) == n
    finally:
        m.R.strip_ansi = real
    assert m.rendered(op, 60)[3] is None      # width change drops it with _c


def test_width_memoized_until_refresh():
    """Perf pin for the per-loop-iteration width memo: repeated width() calls
    cost one size probe until width_refresh() (called once per loop tick and
    from the SIGWINCH handler) drops the memo."""
    m = _load_mirror()
    probes = []
    m._raw_width = lambda: (probes.append(1), 80)[1]
    m.width_refresh()
    assert m.width() == m.width() == m.width() == 80
    assert len(probes) == 1
    m.width_refresh()
    assert m.width() == 80
    assert len(probes) == 2


def test_stream_build_chip_branches():
    """Pin the finish-chip builder extracted from claude-stream.py's tailer
    body (plugins/claude_code/stream.py build_chip) — the override branches:
    precomputed PostToolUse chip (its colour, slot fallback), the subagent
    pass/fail hand-off, and the per-kind generic texts. Run in a fresh
    interpreter (import is side-effect free since _init() took over argv
    parsing — build_chip is a pure function of its arguments)."""
    import os
    import subprocess
    prog = """
import sys
sys.argv = ["claude-stream.py", "bg", "tid", "/tmp/claude-mirror-x.log", "0"]
from plugins.claude_code import stream
from core import ops as O
SLOT = (7, 7, 7)
# 1. fg with a precomputed chip: text verbatim, its own colour...
assert stream.build_chip("fg", {"chip": "chipped", "color": [1, 2, 3]},
                         "9.9s", SLOT) == ("chipped", (1, 2, 3))
# ...falling back to the slot colour when the hand-off carries none,
# and winning over a simultaneous failed flag (chip is checked first).
assert stream.build_chip("fg", {"chip": "chipped", "failed": True},
                         "9.9s", SLOT) == ("chipped", SLOT)
# 2. fg pass/fail-only hand-off (subagent path): red, tailer-owned duration.
assert stream.build_chip("fg", {"failed": True}, "3.0s", SLOT) == \
    ("\\u25a0 failed \\u00b7 3.0s", O.RED)
# 3. defaults per kind (no override / fg override with neither key).
assert stream.build_chip("bg", None, "5.0s", SLOT) == \
    ("\\u25a0 background finished \\u00b7 5.0s", SLOT)
assert stream.build_chip("fg", {}, "5.0s", SLOT) == \
    ("\\u25a0 foreground finished \\u00b7 5.0s", SLOT)
assert stream.build_chip("monitor", None, "5.0s", SLOT) == \
    ("\\u25a0 monitor ended \\u00b7 5.0s", SLOT)
print("OK")
"""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("KITTY_", "CLAUDE_"))}
    r = subprocess.run([sys.executable, "-c", prog], cwd=REPO, env=env,
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and "OK" in r.stdout, r.stderr


# --- is_teammate — the meta.json teammate probe, now a thin wrapper over -----
# --- model.agent_meta (the ONE retry-read of the sidecar) --------------------

def test_is_teammate_marker_present(tmp_path):
    import json
    from plugins.claude_code import subagent_fmt
    tpath = str(tmp_path / "sess.jsonl")
    sub = tmp_path / "sess" / "subagents"
    sub.mkdir(parents=True)
    (sub / "agent-a1.meta.json").write_text(
        json.dumps({"taskKind": "in_process_teammate"}))
    assert subagent_fmt.is_teammate(tpath, "a1") is True


def test_is_teammate_ordinary_subagent(tmp_path):
    import json
    from plugins.claude_code import subagent_fmt
    tpath = str(tmp_path / "sess.jsonl")
    sub = tmp_path / "sess" / "subagents"
    sub.mkdir(parents=True)
    (sub / "agent-a1.meta.json").write_text(json.dumps({"agentType": "task"}))
    assert subagent_fmt.is_teammate(tpath, "a1") is False


def test_is_teammate_missing_meta_false(tmp_path):
    # No meta.json ever appears: agent_meta's brief retry exhausts -> {} -> False.
    from plugins.claude_code import subagent_fmt
    assert subagent_fmt.is_teammate(str(tmp_path / "sess.jsonl"), "gone") is False


# --- core.state.stats() counter typing + internal-key visibility --------------------
# The reader used to hardcode WHICH counters coerce to int ("start", "txpos",
# "commands", ...) — every new counter silently came back float (SQLite REAL).
# Now typing is generic (integral -> int, else float), FLOAT_COUNTERS pin the
# always-float ones (cost/paused), and INTERNAL_COUNTERS (v/txpos/block_seq —
# accounting cursors, not scoreboard state) never leak into the public dict.

def test_stats_generic_counter_typing(tmp_path):
    from core import state as S
    log = str(tmp_path / "claude-mirror-t.log")
    st = S.incr(log, tool="Bash", commands=1, failed=1, added=3, removed=2,
                tokens=165, tk_in=100, cost=2.0, paused=1.0, otel_seen=1)
    # integral counters come back int — including ones the old reader never
    # listed (tokens, tk_in, otel_seen used to arrive as float).
    for k in ("commands", "failed", "added", "removed", "tokens", "tk_in",
              "otel_seen", "start"):
        assert isinstance(st[k], int), k
    # declared floats stay float even when integral.
    assert isinstance(st["cost"], float) and st["cost"] == 2.0
    assert isinstance(st["paused"], float) and st["paused"] == 1.0
    # a non-integral value on an undeclared counter keeps its float.
    st = S.incr(log, weird=0.5)
    assert isinstance(st["weird"], float) and st["weird"] == 0.5
    assert st["tools"] == {"Bash": 1} and isinstance(st["tools"]["Bash"], int)


def test_stats_hides_internal_counters(tmp_path):
    from core import state as S
    log = str(tmp_path / "claude-mirror-t2.log")
    S.incr(log, commands=1)                       # bumps 'v' too
    assert S.next_group(log) == 1                 # creates 'block_seq'
    conn = S.connect(log)
    with S.immediate(conn):
        S.counter_set(conn, "txpos", 4096)        # transcript byte cursor
    st = S.stats(log)
    for k in ("v", "txpos", "block_seq"):
        assert k not in st, k
    # ...but they stay readable through counter_get for the accountant.
    assert int(S.counter_get(conn, "txpos")) == 4096
    assert int(S.counter_get(conn, "v")) >= 1


# ---- claude-scorebar.py fit_parts — the ONE tail-drop shrink-to-fit ---------
# compose() used to repeat this loop four times with subtly different guards
# (`len(parts) > 1` vs `parts and ...`) and a repeated `w - 3` magic number;
# fit_parts(min_keep=, text=) is the single extraction. Pin the exact
# semantics each row relied on.


def _load_scorebar():
    import importlib.util
    import os
    spec = importlib.util.spec_from_file_location(
        "claude_scorebar_script", os.path.join(REPO, "bin", "claude-scorebar.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_fit_parts_tail_drop_and_min_keep():
    m = _load_scorebar()
    assert m.SEP_W == 3 and m.PREFIX_W == 3           # " · " joiner, " ▪ " prefix
    parts = [("a", "aaaa"), ("b", "bbbb"), ("c", "cccc")]
    # 3 segs = 12 chars + 2 seps*3 = 18; avail 17 drops one (11 + 3 = 14 fits).
    got = m.fit_parts(list(parts), 17)
    assert got == parts[:2]
    # fits exactly -> untouched (and returns the same list, mutated in place).
    keep = list(parts)
    assert m.fit_parts(keep, 18) is keep and keep == parts
    # min_keep=1 (the ✉/Σ rows): never empties, even when the survivor overflows.
    assert m.fit_parts(list(parts), 1) == parts[:1]
    # min_keep=0 (the ▪/tools rows): may empty out entirely.
    assert m.fit_parts(list(parts), 1, min_keep=0) == []
    assert m.fit_parts([], 10, min_keep=0) == []      # empty input is a no-op


def test_fit_parts_custom_text():
    m = _load_scorebar()
    # The tools row measures "name count" pairs, not (kind, text) tuples.
    tools = [("Read", 34), ("Edit", 18), ("Write", 4)]
    text = lambda kv: f"{kv[0]} {kv[1]}"              # noqa: E731
    # "Read 34" + "Edit 18" + "Write 4" = 21 chars + 2*3 = 27.
    assert m.fit_parts(list(tools), 27, min_keep=0, text=text) == tools
    assert m.fit_parts(list(tools), 26, min_keep=0, text=text) == tools[:2]


# ---- core.state.tab_state -> tabs.tab_get delegation ------------------------
# tab_state used to hand-roll its own mode=ro connect + SQL against the tab DB;
# it now delegates to tabs.tab_get (tabs.py owns the schema). Pin the contract:
# reads the set state, '' when the DB is missing, and — crucially — a probe on
# a missing DB must NOT create the file (its existence is a liveness signal).


def test_tab_state_reads_set_state(tmp_path, monkeypatch):
    from core import state, tabs
    monkeypatch.setattr(tabs, "TABDB", str(tmp_path / "tab.db"))
    tabs.tab_set("7", "thinking")
    assert state.tab_state(7) == "thinking"           # int win coerced to str
    assert state.tab_state("7") == "thinking"


def test_tab_state_missing_db_default_and_no_create(tmp_path, monkeypatch):
    from core import state, tabs
    db = tmp_path / "absent" / "tab.db"               # parent dir doesn't exist either
    monkeypatch.setattr(tabs, "TABDB", str(db))
    assert state.tab_state("7") == ""
    assert not db.exists() and not db.parent.exists()


# ---- core.slots token format ownership ---------------------------------------
# _token()/_untoken() are the ONE owner of the "<log>::live:<kind>.<key>" claim
# token in BOTH directions — set_owner used to hand-parse it, and pid_set used
# to hand-build the sub.pid sibling form inline.


def test_slots_token_roundtrip():
    from core import slots
    for log, kind, key in [("/tmp/claude-mirror-abc.log", "bg", 3),
                           ("/tmp/x.y.log", "sub.id", 0),
                           ("/tmp/x.log", "sub.pid", "agent-01HXYZ")]:
        tok = slots._token(log, kind, key)
        assert slots._untoken(tok) == (log, kind, str(key))


def test_slots_pid_marker_matches_old_literal():
    from core import slots
    log, ident = "/tmp/claude-mirror-s1.log", "agent-42"
    assert slots._token(log, "sub.pid", ident) == f"{log}::live:sub.pid.{ident}"


def test_set_owner_audits_via_slot_with_slot_n(tmp_path, monkeypatch):
    from core import slots
    log = str(tmp_path / "claude-mirror-sess1.log")
    calls = []

    class Rec:
        def __getattr__(self, name):
            def f(*a, **kw):
                calls.append((name, a, kw))
            return f

    monkeypatch.setattr(slots, "A", Rec())
    idx, token = slots.claim("bg", log)
    assert token == slots._token(log, "bg", idx)
    calls.clear()
    slots.set_owner(token, 12345)
    assert len(calls) == 1
    name, a, kw = calls[0]
    assert name == "slot"                             # A.slot, not raw A.event
    assert a[:3] == (log, "bg", "set-owner")
    assert kw["slot_n"] == idx                        # groups with its claim/release
    assert kw["owner_pid"] == 12345
    assert kw["marker_path"] == token


def test_wait_until_ceiling_scales(monkeypatch):
    """wait_until's timeout ceiling multiplies by conftest.WAIT_SCALE (set from
    CLAUDE_TEST_WAIT_SCALE / CI): slow shared runners get more headroom without
    slowing green runs, which return as soon as the predicate holds."""
    import time as _t

    import pytest

    import conftest as C
    monkeypatch.setattr(C, "WAIT_SCALE", 4.0)
    t0 = _t.time()
    with pytest.raises(AssertionError) as e:
        C.wait_until(lambda: False, timeout=0.1, interval=0.01, desc="never")
    assert _t.time() - t0 >= 0.4                      # ceiling actually scaled
    assert "0.4" in str(e.value)                      # message reports scaled value
    # and a truthy predicate returns immediately regardless of scale
    assert C.wait_until(lambda: 7, timeout=0.1) == 7


def test_pytest_timeout_budget_outlives_scaled_waits(request):
    """The per-test pytest-timeout budget must exceed the suite's largest
    scaled wait_until ceiling, or the CI headroom (80f8615's 6x WAIT_SCALE)
    is unreachable: a slow-but-passing wait is killed at pytest.ini's unscaled
    30s as an opaque pytest-timeout thread dump instead of ever using its 60s
    ceiling (test_f10b on 06efef6, test_f4a on a3d5de8 — macOS runner). The CI
    workflow keeps them in lockstep via PYTEST_TIMEOUT=180 on the test step."""
    import os as _os

    import conftest as C
    budget = float(_os.environ.get("PYTEST_TIMEOUT")
                   or request.config.getini("timeout"))
    longest = 20.0        # largest explicit wait_until timeout= in the suite
    assert budget > longest * C.WAIT_SCALE, (
        "pytest-timeout budget %ss can't outlive a scaled %ss wait — set "
        "PYTEST_TIMEOUT alongside WAIT_SCALE (see .github/workflows/test.yml)"
        % (budget, longest * C.WAIT_SCALE))


# --- model.claude_dirs / model.settings_env (the ONE settings walk) ----------
# split.py's private nearest-.claude walk + env-block layering was consolidated
# onto model.py. Pin both walk modes and the layering order so the consolidation
# can't silently change either caller's precedence.

def _settings_tree(tmp_path, monkeypatch):
    """cfg/.claude (user config dir) + outer/.claude + outer/inner/.claude,
    cwd = outer/inner/sub — a REAL nested-.claude layout."""
    import json as _json
    cfg = tmp_path / "cfg" / ".claude"
    outer = tmp_path / "outer"
    inner = outer / "inner"
    sub = inner / "sub"
    for d in (cfg, outer / ".claude", inner / ".claude", sub):
        d.mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.chdir(sub)

    def write(claude_dir, name, env):
        (claude_dir / name).write_text(_json.dumps({"env": env}))
    return cfg, outer / ".claude", inner / ".claude", write


def test_claude_dirs_all_ancestors_vs_nearest_only(tmp_path, monkeypatch):
    from plugins.claude_code import model as M
    cfg, outer_c, inner_c, _ = _settings_tree(tmp_path, monkeypatch)
    # default: EVERY ancestor .claude, nearest-first, config dir last
    assert M.claude_dirs() == [str(inner_c), str(outer_c), str(cfg)]
    # nearest_only (split.py's walk): stop at the nearest, still config-dir last
    assert M.claude_dirs(nearest_only=True) == [str(inner_c), str(cfg)]


def test_claude_dirs_project_dir_pins(tmp_path, monkeypatch):
    from plugins.claude_code import model as M
    cfg, outer_c, _inner_c, _ = _settings_tree(tmp_path, monkeypatch)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(outer_c.parent))
    assert M.claude_dirs() == [str(outer_c), str(cfg)]
    assert M.claude_dirs(nearest_only=True) == [str(outer_c), str(cfg)]


def test_settings_env_layering_project_over_global(tmp_path, monkeypatch):
    from plugins.claude_code import model as M
    cfg, _outer_c, inner_c, write = _settings_tree(tmp_path, monkeypatch)
    write(cfg, "settings.json", {"CLAUDE_MIRROR_BIAS": 25})
    write(inner_c, "settings.json", {"CLAUDE_MIRROR_BIAS": 40})
    write(inner_c, "settings.local.json", {"CLAUDE_MIRROR_BIAS": 55})
    # local shadows settings within the dir; project beats global — both modes
    assert M.settings_env("CLAUDE_MIRROR_BIAS") == "55"
    assert M.settings_env("CLAUDE_MIRROR_BIAS", nearest_only=True) == "55"
    # absent from the project -> falls through to the global file
    assert M.settings_env("CLAUDE_MIRROR_STEP", nearest_only=True) == ""
    write(cfg, "settings.json", {"CLAUDE_MIRROR_STEP": 6})
    assert M.settings_env("CLAUDE_MIRROR_STEP", nearest_only=True) == "6"


def test_settings_env_walk_depth_is_the_behavioral_difference(tmp_path, monkeypatch):
    """A key defined only in the OUTER .claude of a nested layout: the default
    walk falls through the inner .claude to it; nearest_only (split's mode)
    stops at the inner .claude and goes straight to global — the exact
    difference that made nearest_only a parameter."""
    from plugins.claude_code import model as M
    _cfg, outer_c, _inner_c, write = _settings_tree(tmp_path, monkeypatch)
    write(outer_c, "settings.json", {"CLAUDE_MIRROR_BIAS": 33})
    assert M.settings_env("CLAUDE_MIRROR_BIAS") == "33"
    assert M.settings_env("CLAUDE_MIRROR_BIAS", nearest_only=True) == ""


def test_settings_env_falsy_value_and_global_local_ignored(tmp_path, monkeypatch):
    from plugins.claude_code import model as M
    cfg, _outer_c, inner_c, write = _settings_tree(tmp_path, monkeypatch)
    # a present-but-falsy JSON value still wins (presence is `is not None`)
    write(inner_c, "settings.json", {"CLAUDE_MIRROR_BIAS": 0})
    assert M.settings_env("CLAUDE_MIRROR_BIAS", nearest_only=True) == "0"
    # the user config dir contributes only settings.json, never a local file
    write(cfg, "settings.local.json", {"CLAUDE_MIRROR_STEP": 9})
    assert M.settings_env("CLAUDE_MIRROR_STEP", nearest_only=True) == ""


def test_context_used_is_every_input_token_the_model_saw():
    # The ONE ctx-occupancy arithmetic (styleguide table): fresh + cache-write
    # + cache-read input; output excluded; garbage-tolerant.
    from plugins.claude_code import model as M
    assert M.context_used({"input_tokens": 10, "cache_creation_input_tokens": 5,
                           "cache_read_input_tokens": 85, "output_tokens": 999}) == 100
    assert M.context_used({"input_tokens": 10}) == 10
    assert M.context_used(None) == 0
    assert M.context_used("junk") == 0


# --- plugins/otel/config.port — the ONE port resolver ----------------------------

def test_otel_port_is_single_sited(monkeypatch):
    """launch.py's already-listening pre-check and receiver.py's bind must
    resolve the port through the SAME function (plugins/otel/config.py) — a
    re-encoded copy in either is exactly the drift the single-siting removes."""
    from plugins.otel import config, launch, receiver
    assert launch._port is config.port
    assert receiver._port is config.port


def test_otel_port_env_resolution(monkeypatch):
    from plugins.otel import config
    monkeypatch.delenv("CLAUDE_OTEL_PORT", raising=False)
    assert config.port() == 4319                      # the default
    monkeypatch.setenv("CLAUDE_OTEL_PORT", "5005")
    assert config.port() == 5005
    monkeypatch.setenv("CLAUDE_OTEL_PORT", "")        # empty -> default
    assert config.port() == 4319
    monkeypatch.setenv("CLAUDE_OTEL_PORT", "junk")    # unparsable -> default
    assert config.port() == 4319


# --- hookkit.payload_or_stdin / injected / has_payload ---------------------------
# The shared "dispatcher-injected payload, else stdin once" accessor that
# tabstatus.read_payload and split.sid_from_stdin now delegate to.

def _fresh_hookkit(monkeypatch):
    from plugins.claude_code import hookkit as HK
    monkeypatch.setattr(HK, "_INJECTED", None)
    monkeypatch.setattr(HK, "_STDIN", None)
    return HK


def test_payload_or_stdin_prefers_injected(monkeypatch):
    import io
    HK = _fresh_hookkit(monkeypatch)
    monkeypatch.setattr("sys.stdin", io.StringIO('{"session_id": "stdin-sid"}'))
    HK.set_payload({"session_id": "injected-sid"})
    assert HK.injected() == {"session_id": "injected-sid"}
    assert HK.has_payload()
    assert HK.payload_or_stdin()["session_id"] == "injected-sid"
    HK.clear_payload()
    assert HK.injected() is None


def test_payload_or_stdin_caches_the_one_stdin_read(monkeypatch):
    """stdin can only be read once: the first call parses, the second must
    return the cache instead of re-reading a drained stream and getting {}."""
    import io
    HK = _fresh_hookkit(monkeypatch)
    monkeypatch.setattr("sys.stdin", io.StringIO('{"session_id": "s2"}'))
    assert HK.payload_or_stdin() == {"session_id": "s2"}
    monkeypatch.setattr("sys.stdin", io.StringIO(""))   # drained/replaced
    assert HK.payload_or_stdin() == {"session_id": "s2"}


def test_payload_or_stdin_lenient_on_garbage(monkeypatch):
    import io
    HK = _fresh_hookkit(monkeypatch)
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    assert HK.payload_or_stdin() == {}                  # never raises
    HK2 = _fresh_hookkit(monkeypatch)
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert HK2.payload_or_stdin() == {}                 # empty pipe -> {}


def test_has_payload_without_consuming_stdin(monkeypatch):
    import io
    HK = _fresh_hookkit(monkeypatch)
    fake = io.StringIO('{"session_id": "x"}')           # non-tty pipe
    monkeypatch.setattr("sys.stdin", fake)
    assert HK.has_payload()                             # plausible payload…
    assert fake.tell() == 0                             # …but nothing consumed

    class Tty(io.StringIO):
        def isatty(self):
            return True

    HK2 = _fresh_hookkit(monkeypatch)
    monkeypatch.setattr("sys.stdin", Tty(""))
    assert not HK2.has_payload()                        # manual terminal run
    HK2.set_payload({})                                 # injected {} still counts
    assert HK2.has_payload()
    HK2.clear_payload()


# --- core.locks — the pid-liveness lock trio (moved out of core/state.py) ----------

def test_locks_acquire_holder_release(tmp_path):
    from core import locks as LK
    import os
    db = str(tmp_path / "claims.db")
    me = os.getpid()
    assert LK.lock_acquire(db, "k") == "claim"
    assert LK.lock_holder(db, "k") == me
    assert LK.lock_acquire(db, "k") == "claim"          # re-acquire by holder is fine
    # a live foreign holder is denied; a dead one is stolen
    assert LK.lock_acquire(db, "k", pid=1).startswith("claim-denied:")
    LK.lock_release(db, "k")
    assert LK.lock_holder(db, "k") == 0
    dead = 99999999
    assert LK.lock_acquire(db, "k2", pid=dead) == "claim"
    assert LK.lock_acquire(db, "k2") == "steal-stale"   # dead holder is taken over
    LK.lock_release(db, "k2", pid=me)
    assert LK.lock_holder(db, "k2") == 0
    # release by a non-holder is a no-op
    assert LK.lock_acquire(db, "k3", pid=dead) == "claim"
    LK.lock_release(db, "k3", pid=me)
    assert LK.lock_holder(db, "k3") == dead


def test_monitor_sig_extraction():
    """monitor_sig (plugins/claude_code/stream.py) is the ONE owner of the
    signature-token extraction shared by both monitor launch sites
    (monitor_fmt.py and substream.spawn_tailer) — pin the longest-5+-char-token
    behavior on representative monitor commands so the wire contract with
    find_proc can't drift."""
    from plugins.claude_code.stream import monitor_sig
    assert monitor_sig("tail -f /var/log/build-output.log") == "/var/log/build-output.log"
    assert monitor_sig("kubectl logs -f pod/web-7d9f --namespace=prod") == "--namespace=prod"
    assert monitor_sig("python3 watch.py --url=http://host:8080/api") == "--url=http://host:8080/api"
    assert monitor_sig("ls -l") == ""            # no token reaches 5 chars
    assert monitor_sig("") == ""
    assert monitor_sig(None) == ""


def test_find_proc_matches_ps_line_with_sig(monkeypatch):
    """find_proc greps `ps` argv output for the sig monitor_sig extracted — a
    live end-to-end check that the extraction really matches a process whose
    argv contains the command (unique token, so no full-cmd disambiguation
    needed; CLAUDE_MONITOR_CMD unset exercises the token-only path)."""
    import os
    import subprocess
    from plugins.claude_code import stream as ST
    monkeypatch.delenv("CLAUDE_MONITOR_CMD", raising=False)
    # two statements so sh can't exec-optimize itself away (an exec'd sleep's
    # argv would no longer carry the sig; same trick as the l2 monitor flow)
    cmd = f": monitor-sig-unit-{os.getpid()}; sleep 30"
    sig = ST.monitor_sig(cmd)
    assert sig == f"monitor-sig-unit-{os.getpid()}"
    proc = subprocess.Popen(["/bin/sh", "-c", cmd],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        assert ST.find_proc(sig) == proc.pid
    finally:
        proc.kill()
        proc.wait()


def test_norm_cmd_escape_insensitive():
    """_norm_cmd (plugins/claude_code/stream.py) must normalize a raw multi-line
    command and its ps-escaped renderings to the SAME string, so find_proc's
    full-command match survives however ps escaped the embedded newlines — the
    heredoc-monitor bug (raw \\n never a substring of the escaped argv)."""
    from plugins.claude_code.stream import _norm_cmd
    raw = "cd ~/x && python3 - <<PY\nimport time\ntime.sleep(9)\nPY"
    zsh = r"cd\ ~/x\ &&\ python3\ -\ \<\<PY$'\n'import\ time$'\n'time.sleep\(9\)$'\n'PY"
    octal = "cd ~/x && python3 - <<PY\\012import time\\012time.sleep(9)\\012PY"
    n = _norm_cmd(raw)
    assert n and n == _norm_cmd(zsh) == _norm_cmd(octal)
    # and the normalized raw command is CONTAINED in a realistic wrapper argv
    wrapper = "/bin/zsh -c source snap.sh 2>/dev/null || true && eval " + zsh
    assert n in _norm_cmd(wrapper)


def test_find_proc_multiline_heredoc_disambiguates(monkeypatch):
    """find_proc identifies a MULTI-LINE heredoc monitor's wrapper process even
    when its sig (the command's longest token) is ambiguous — regression for the
    bug where a `python3 - <<PY` monitor whose longest token was the shared
    project path matched many processes AND whose raw newlines could never match
    ps's escaped argv, so the full-command disambiguation silently failed and
    find_proc returned None ('monitor process never found'). Hermetic: `ps` is
    stubbed with a canned rendering (no real zsh — CI has no /bin/zsh — and no
    dependence on how a given platform's ps escapes newlines)."""
    import types
    from plugins.claude_code import stream as ST
    proj = "/code/proj/aggregator-adapters"          # the shared project path
    cmd = f"cd ~{proj} && python3 - <<PY\nimport time\ntime.sleep(9)\nPY"
    sig = ST.monitor_sig(cmd)                          # longest token = that path
    assert sig == proj
    monkeypatch.setenv("CLAUDE_MONITOR_CMD", cmd)
    # The real wrapper's argv carries the full command with ps-ESCAPED newlines
    # ($'\n' from zsh quoting) + backslash-escaped specials — a raw substring of
    # CLAUDE_MONITOR_CMD (real \n) could never match it, so only _norm_cmd does.
    esc = (r"cd\ ~/code/proj/aggregator-adapters\ &&\ python3\ -\ \<\<PY"
           r"$'\n'import\ time$'\n'time.sleep\(9\)$'\n'PY")
    ps_out = "\n".join([
        f" 111 -zsh cd ~{proj}",                       # decoy: a shell in the dir
        f" 222 /bin/zsh -c source snap.sh && eval {esc}",  # the real monitor wrapper
        f" 333 tail -f ~{proj}/build.log",             # decoy: unrelated, same path
    ])
    monkeypatch.setattr(ST.subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(stdout=ps_out))
    # All three carry the ambiguous sig; only the wrapper matches the full command.
    assert ST.find_proc(sig) == 222


# ---- core.errwatch — the audit warning light (pure shapes) ------------------
# The ⚠ chip / mirror-block vocabulary is single-owned by core/errwatch.py
# (styleguide ownership table); pin the shapes the scorebar and the e2e flow
# (test_l4_scoreboard.py) rely on.


def test_errwatch_summary_last_traceback_line():
    from core import errwatch as EW
    tb = ("Traceback (most recent call last):\n"
          '  File "x.py", line 1, in f\n'
          "ValueError: boom\n")
    assert EW._summary(tb) == "ValueError: boom"
    assert EW._summary("just a message") == "just a message"
    assert EW._summary("") == "?"
    assert EW._summary(None) == "?"


def test_errwatch_err_ops_per_row_and_flood_collapse():
    from core import errwatch as EW
    rows = [(1, "claude-cmd-fmt.py", "on_post", "ValueError: boom"),
            (2, "claude-split.py", "", "OSError: nope")]
    ops = EW.err_ops(rows, "sid-1")
    assert [o["t"] for o in ops] == ["label", "label"]
    assert ops[0]["s"] == "⚠ audit: claude-cmd-fmt.py: ValueError: boom"
    assert ops[1]["s"] == "⚠ audit: claude-split.py: OSError: nope"
    from core import ops as O
    assert ops[0]["c"] == list(O.AMBER)          # amber warning, not red failure
    # Past FLOOD_N new rows in one poll: ONE collapsed line pointing at the CLI.
    flood = [(i, "s.py", "f", "E: x") for i in range(EW.FLOOD_N + 1)]
    ops = EW.err_ops(flood, "sid-1")
    assert len(ops) == 1
    assert ops[0]["s"] == ("⚠ audit: %d new errors (bin/claude-audit.py errors "
                           "sid-1)" % (EW.FLOOD_N + 1))
    # Per-line char cap: a huge exception message truncates to TEXT_MAX.
    ops = EW.err_ops([(9, "s.py", "f", "E: " + "x" * 500)], "sid-1")
    assert len(ops[0]["s"]) == EW.TEXT_MAX


def test_errwatch_no_exception_row_shows_func():
    # A deliberate degrade row (A.error outside an except block) stores the
    # 'NoneType: None' format_exc sentinel — the mirror line must show the func
    # string ('spawn nope.py (script missing)'), not that noise. A row with
    # neither a real traceback nor a func keeps the sentinel (nothing better).
    from core import errwatch as EW
    ops = EW.err_ops([(1, "-c", "spawn nope.py (script missing)",
                       "NoneType: None\n")], "sid-1")
    assert ops[0]["s"] == "⚠ audit: -c: spawn nope.py (script missing)"
    ops = EW.err_ops([(2, "s.py", "", "NoneType: None\n")], "sid-1")
    assert ops[0]["s"] == "⚠ audit: s.py: NoneType: None"


def test_errwatch_chip_part_shape():
    from core import errwatch as EW
    assert EW.chip_part(3) == ("warn", "⚠ 3")


def test_scorebar_compose_warn_chip():
    """The ▪ row leads with the ⚠ chip when nerr > 0 (so tail-drop never sheds
    the warning) and shows no trace of it when nerr == 0."""
    import re
    m = _load_scorebar()
    strip = lambda s: re.sub(r"\x1b\[[0-9;]*m", "", s)        # noqa: E731
    st = {"commands": 2, "start": 1000.0}
    with_chip = strip(m.compose(80, [], dict(st), 2)[2])
    without = strip(m.compose(80, [], dict(st), 0)[2])
    assert "⚠ 2" in with_chip and "2 cmds" in with_chip
    assert with_chip.index("⚠ 2") < with_chip.index("2 cmds")   # chip leads
    assert "⚠" not in without


# --- single-owner delegation pins (styleguide ownership table) ----------------
# 1. split.py's sizes-DB paths delegate to model.config_dir() at CALL time — the
#    one owner of the $CLAUDE_CONFIG_DIR/~/.claude default (it used to re-encode
#    the expanduser fallback in a module-level CONFIG_DIR).
# 2. The mirror-width default is core/hostpane.DEFAULT_BIAS, shared by BOTH
#    hosts (split.py's settings-layered read and codex's env-only read).
# 3. codex's lenient payload reader must AUDIT a malformed stdin before
#    degrading to {} (every swallow audits first).

def test_split_sizedb_delegates_to_model_config_dir(tmp_path, monkeypatch):
    from plugins.claude_code import split
    cfg = tmp_path / "cfg-a"
    cfg.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    assert split._sizedb() == str(cfg / "kitty-mirror.db")
    assert split._size_dir() == str(cfg / "kitty-mirror-sizes")
    # Call-time resolution: a changed env is honoured without re-import.
    cfg2 = tmp_path / "cfg-b"
    cfg2.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg2))
    assert split._sizedb() == str(cfg2 / "kitty-mirror.db")
    # Grep pin: split.py no longer re-encodes model.config_dir()'s default.
    import inspect
    assert 'expanduser("~/.claude")' not in inspect.getsource(split)


def test_mirror_bias_default_single_owner(monkeypatch):
    import inspect
    from core import hostpane as HP
    from plugins.codex import session as CS
    monkeypatch.delenv("CLAUDE_MIRROR_BIAS", raising=False)
    assert CS.bias() == HP.DEFAULT_BIAS          # codex shares the core default
    monkeypatch.setenv("CLAUDE_MIRROR_BIAS", "40")
    assert CS.bias() == 40                       # env-only read still honoured
    sig = inspect.signature(HP.open_mirror)
    assert sig.parameters["default_bias"].default == HP.DEFAULT_BIAS


def test_codex_malformed_payload_audits(monkeypatch):
    import io
    from plugins.codex import session as CS
    errors = []

    class _Rec:
        def error(self, log, where, *a, **k):
            errors.append(where)

    monkeypatch.setattr(CS, "A", _Rec())
    monkeypatch.setattr(sys, "stdin", io.StringIO("{not json"))
    assert CS.read_payload() == {}               # still degrades, never raises
    assert errors == ["codex payload parse (stdin not valid JSON)"]


def test_session_model_honors_tail_scan_bytes(tmp_path, monkeypatch):
    # The seek window must come from TAIL_SCAN_BYTES, not a re-encoded literal:
    # shrink the constant and verify an assistant turn OUTSIDE the window is
    # invisible while one inside it is found.
    from plugins.claude_code import model as cm
    old = '{"type":"assistant","message":{"model":"claude-old-9"}}\n'
    pad = '{"type":"pad","x":"' + "p" * 400 + '"}\n'
    new = '{"type":"assistant","message":{"model":"claude-new-9"}}\n'
    t = tmp_path / "s.jsonl"
    t.write_text(old + pad + new)
    monkeypatch.setattr(cm, "TAIL_SCAN_BYTES", len(new) + 10)
    assert cm.session_model(str(t)) == "claude-new-9"   # old line outside window
    monkeypatch.setattr(cm, "TAIL_SCAN_BYTES", 1 << 20)
    assert cm.session_model(str(t)) == "claude-new-9"   # full window: LAST wins
    t.write_text(old + pad)                              # only the old turn left
    monkeypatch.setattr(cm, "TAIL_SCAN_BYTES", 50)
    assert cm.session_model(str(t)) is None              # window misses it


def test_tabstatus_watcher_ceilings_and_reason_strings():
    # The named ceilings must reproduce the historical loop counts for the
    # shipped poll cadences, and the derived audit reason strings must stay
    # byte-identical to the old hardcoded vocabulary (SKILL.md cites
    # "no-interrupt-within-30m" literally).
    from plugins.claude_code import tabstatus as TS
    assert TS.BGWATCH_MAX_S == 3600 and TS.INTERRUPT_MAX_S == 1800
    assert TS.BG_MISS_GRACE_N == 4
    assert int(TS.BGWATCH_MAX_S / 2) == 1800          # bg-watch @ 2s poll
    assert int(TS.INTERRUPT_MAX_S / 0.5) == 3600      # interrupt-watch @ 0.5s
    lbl = TS._dur_label
    assert f"gave-up-after-{lbl(TS.BGWATCH_MAX_S)} (markers still live)" \
        == "gave-up-after-1h (markers still live)"
    assert f"no-interrupt-within-{lbl(TS.INTERRUPT_MAX_S)}" \
        == "no-interrupt-within-30m"
    assert f"~{lbl(TS.BG_MISS_GRACE_N * 2)} of checks" == "~8s of checks"
    assert lbl(45) == "45s" and lbl(90) == "90s" and lbl(120) == "2m"


# --- core.spawn.spawn_detached ---------------------------------------------------

def test_spawn_detached_missing_script_returns_none(tmp_path):
    """A renamed/deleted script must return None (audited), never raise.
    The degrade row must land in the per-test audit sandbox (conftest's
    _fresh_audit_conn redirects CLAUDE_AUDIT_DIR for in-process calls) — this
    test once wrote a GLOBAL row into the REAL ~/.claude/baqylau-audit DB, which
    every live session's ⚠ warning light then surfaced."""
    import sqlite3
    from core import audit as A
    from core.spawn import spawn_detached
    assert spawn_detached(str(tmp_path / "nope.py"), [], "") is None
    assert os.path.expanduser("~/.claude") not in A.db_path(), \
        "in-process audit writes must never target the real DB"
    conn = sqlite3.connect(A.db_path())
    try:
        funcs = [r[0] for r in conn.execute("SELECT func FROM errors")]
    finally:
        conn.close()
    assert any("script missing" in f for f in funcs), \
        "the degrade row must be audited (in the sandbox): %s" % funcs


def test_spawn_detached_detaches_into_own_session(tmp_path, reaper):
    """The child must run in its OWN session (start_new_session=True is the
    load-bearing half of the pattern — a same-group child hung SessionStart)."""
    import os
    from conftest import wait_until
    from core.spawn import spawn_detached
    script = tmp_path / "sid.py"
    out = tmp_path / "sid.txt"
    script.write_text("import os,sys\n"
                      "open(sys.argv[1],'w').write(str(os.getsid(0)))\n")
    proc = spawn_detached(str(script), [str(out)], "")
    assert proc is not None
    reaper.append(proc)
    wait_until(lambda: out.exists() and out.read_text(), desc="child sid file")
    assert int(out.read_text()) != os.getsid(0)


# --- opt-in web-dashboard auto-start (split._maybe_autostart_dashboard) ----------
# CLAUDE_DASHBOARD_AUTOSTART=1 makes a hosted SessionStart spawn-if-not-running
# the per-machine dashboard, DETACHED via the audited spawn — after a cheap
# lock_holder+pid_alive liveness check (never a port bind from a hook). Auto-start
# ONLY (docs/dashboard.md's explicit-lifecycle decision: no idle-exit, no
# auto-stop). OFF by default: with the env unset nothing spawns and nothing is
# audited (the OTLP receiver's telemetry-gate precedent).

def _autostart_harness(monkeypatch):
    from core import spawn as CS
    from plugins.claude_code import split
    spawns, panes = [], []
    monkeypatch.setattr(
        CS, "spawn_detached",
        lambda path, argv, log, **k: spawns.append((path, list(argv), k)) or "proc")
    monkeypatch.setattr(
        split, "audit_pane",
        lambda sid, action, ok, detail: panes.append((action, ok, detail)))
    return split, spawns, panes


def test_dashboard_autostart_disabled_no_spawn(monkeypatch):
    monkeypatch.delenv("CLAUDE_DASHBOARD_AUTOSTART", raising=False)
    split, spawns, panes = _autostart_harness(monkeypatch)
    split._maybe_autostart_dashboard("sid-1", "/tmp/x.log")
    assert spawns == [] and panes == []      # gate returns before touching anything


def test_dashboard_autostart_skips_when_already_running(monkeypatch):
    monkeypatch.setenv("CLAUDE_DASHBOARD_AUTOSTART", "1")
    split, spawns, panes = _autostart_harness(monkeypatch)
    from core import locks, state
    monkeypatch.setattr(locks, "lock_holder", lambda db, key: 4321)
    monkeypatch.setattr(state, "pid_alive", lambda pid: True)
    split._maybe_autostart_dashboard("sid-1", "/tmp/x.log")
    assert spawns == []                      # a live holder means no second server
    assert panes == [("dash-autostart", 1, "already running (pid 4321)")]


def test_dashboard_autostart_spawns_when_no_holder(monkeypatch):
    monkeypatch.setenv("CLAUDE_DASHBOARD_AUTOSTART", "1")
    split, spawns, panes = _autostart_harness(monkeypatch)
    from core import locks, paths as P, state
    monkeypatch.setattr(locks, "lock_holder", lambda db, key: 0)   # nothing running
    monkeypatch.setattr(state, "pid_alive", lambda pid: False)
    split._maybe_autostart_dashboard("sid-1", "/tmp/x.log")
    assert len(spawns) == 1, spawns
    path, argv, _ = spawns[0]
    assert path == os.path.join(P.BIN, "claude-dashboard.py")
    assert argv == ["serve"]                 # `serve`, not `start` (no port bind from a hook)
    assert panes == [("dash-autostart", 1, "spawned")]


# --- core.hostpane.park_db / decide_log_fate — failure-path fates ---------------
# A silent park failure used to report "keep-history" while the live DB stayed
# put (orphaned pollers, reuse-live-db on resume), and the three independent
# moves could tear the WAL away from the parked main file. Pins: the WAL is
# checkpointed before parking, the MAIN move failing returns a distinct audited
# fate with the live DB untouched, a sidecar-only failure still parks (audited),
# and the restore direction audits its failures + drops stale live sidecars.

def _park_env(tmp_path, monkeypatch):
    """Route audit rows + the durable park into the hermetic tmpdir and hand
    back (hostpane, paths, log, state_db, parked_db, audit_errors)."""
    import core.audit
    from core import hostpane as HP
    from core import paths as P
    monkeypatch.setenv("CLAUDE_AUDIT", "1")
    monkeypatch.setenv("CLAUDE_AUDIT_DIR", str(tmp_path / "audit"))
    # audit caches its connection per process — drop it so rows land in THIS
    # test's hermetic audit dir, not a previous test's.
    monkeypatch.setattr(core.audit, "_CONN", None)
    monkeypatch.setattr(core.audit, "_FAILED", False)
    monkeypatch.setattr(P, "HISTORY_DIR", str(tmp_path / "park"))
    log = str(tmp_path / "claude-mirror-parkunit.log")

    def errors():
        import sqlite3
        db = str(tmp_path / "audit" / "audit.db")
        if not os.path.exists(db):
            return []
        conn = sqlite3.connect(db)
        try:
            return [r[0] for r in conn.execute("SELECT func FROM errors")]
        finally:
            conn.close()
    return HP, P, log, P.state_db(log), P.parked_db(log), errors


def _seed_state_db(db, wal=False):
    """A real SQLite DB with one recognizable row (WAL mode keeps the -wal
    sidecar alive while `hold` stays open)."""
    import sqlite3
    conn = sqlite3.connect(db)
    if wal:
        conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t(x)")
    conn.execute("INSERT INTO t VALUES ('parked-row')")
    conn.commit()
    return conn


def test_park_db_checkpoints_wal_before_moving(tmp_path, monkeypatch):
    """Rows committed only to the WAL must survive the park in the MAIN file:
    park_db checkpoints (TRUNCATE) first, so the parked DB is self-contained
    even if the sidecar moves were to fail."""
    import sqlite3
    HP, P, log, db, pk, errors = _park_env(tmp_path, monkeypatch)
    hold = _seed_state_db(db, wal=True)          # open conn keeps the -wal alive
    assert os.path.getsize(db + "-wal") > 0      # frames really live in the WAL
    assert HP.park_db("parkunit", log) == "keep-history"
    hold.close()
    assert not os.path.exists(db)
    conn = sqlite3.connect("file:%s?mode=ro" % pk, uri=True)
    try:
        assert conn.execute("SELECT x FROM t").fetchone()[0] == "parked-row"
    finally:
        conn.close()
    # the checkpoint emptied the WAL — no frame-bearing sidecar in the park
    assert not os.path.exists(pk + "-wal") or os.path.getsize(pk + "-wal") == 0
    assert errors() == []


def test_park_db_main_move_failure_keeps_db_live_and_audits(tmp_path, monkeypatch):
    """ENOSPC/EPERM on the MAIN move: distinct fate, errors row, live DB (and
    its sidecars) untouched — never a false keep-history."""
    HP, P, log, db, pk, errors = _park_env(tmp_path, monkeypatch)
    _seed_state_db(db).close()
    open(db + "-wal", "w").write("fake frames")
    # The DB is rollback-mode with a hand-planted stray -wal: whether the
    # checkpoint's short-lived connection deletes such a stray is
    # sqlite-VERSION-dependent (CI's bundled sqlite does; 3.51 doesn't).
    # This test pins the MOVE failure path, not the checkpoint (which has its
    # own test above) — bypass it so the sidecar deterministically survives.
    monkeypatch.setattr(HP, "_checkpoint_wal", lambda db, log: None)

    def boom(src, dst):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(HP.shutil, "move", boom)
    assert HP.park_db("parkunit", log) == "park-failed (kept live)"
    assert os.path.exists(db), "live DB must be untouched"
    assert os.path.exists(db + "-wal"), "sidecars must not be torn off a live DB"
    assert not os.path.exists(pk)
    assert "park_db (main move — DB kept live)" in errors()


def test_park_db_sidecar_move_failure_still_parks_and_audits(tmp_path, monkeypatch):
    """Main parked, only the -wal move fails: still keep-history (the WAL was
    checkpointed into the main file), the failure is audited, and the stale
    live sidecar is removed so it can't corrupt the next restore."""
    HP, P, log, db, pk, errors = _park_env(tmp_path, monkeypatch)
    _seed_state_db(db).close()
    open(db + "-wal", "w").write("leftover")
    # Bypass the checkpoint for the same sqlite-version reason as above: some
    # sqlite builds delete a stray -wal on connect, which would leave the
    # sidecar-move path (the thing under test) with nothing to move.
    monkeypatch.setattr(HP, "_checkpoint_wal", lambda db, log: None)
    real_move = HP.shutil.move

    def flaky(src, dst):
        if src.endswith("-wal"):
            raise OSError(1, "Operation not permitted")
        return real_move(src, dst)
    monkeypatch.setattr(HP.shutil, "move", flaky)
    assert HP.park_db("parkunit", log) == "keep-history"
    assert os.path.exists(pk) and not os.path.exists(db)
    assert not os.path.exists(db + "-wal"), "stale live sidecar must be removed"
    assert "park_db (sidecar move -wal)" in errors()


def test_decide_log_fate_restore_main_failure_keeps_park(tmp_path, monkeypatch):
    """The restore direction of the same bug: a failed MAIN move back returns a
    distinct audited fate and leaves the park intact for a later resume."""
    HP, P, log, db, pk, errors = _park_env(tmp_path, monkeypatch)
    os.makedirs(os.path.dirname(pk), exist_ok=True)
    _seed_state_db(pk).close()

    def boom(src, dst):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(HP.shutil, "move", boom)
    assert HP.decide_log_fate("parkunit", log) == "restore-failed (park kept)"
    assert os.path.exists(pk) and not os.path.exists(db)
    assert "decide_log_fate (restore move main)" in errors()


def test_decide_log_fate_restore_drops_stale_live_sidecar(tmp_path, monkeypatch):
    """A stale live -wal with no parked counterpart would be replayed into the
    freshly restored main file — the restore must remove it."""
    import sqlite3
    HP, P, log, db, pk, errors = _park_env(tmp_path, monkeypatch)
    os.makedirs(os.path.dirname(pk), exist_ok=True)
    _seed_state_db(pk).close()                    # parked main only, no sidecars
    open(db + "-wal", "w").write("foreign frames")
    assert HP.decide_log_fate("parkunit", log) == "restore-history"
    assert not os.path.exists(db + "-wal"), "stale live sidecar survived restore"
    conn = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    try:
        assert conn.execute("SELECT x FROM t").fetchone()[0] == "parked-row"
    finally:
        conn.close()
    assert errors() == []


# --- core.state.evict — the multi-session cached-conn release --------------------
# The OTLP receiver (the one long-lived MULTI-session state-DB writer) needs to
# drop a parked session's cached connection: _connect only swaps on an inode
# CHANGE at the path, so without evict every ended session pinned a conn +
# WAL/SHM fds for the receiver's lifetime. Per-session processes must NOT call
# it (their stale conn after a park is deliberate) — see the docstring.

def test_state_evict_closes_and_drops_cached_conn(tmp_path):
    import sqlite3
    import pytest
    from core import state as S
    log = str(tmp_path / "claude-mirror-evictunit.log")
    conn = S.connect(log)
    assert conn is not None and S.db_path(log) in S._CONNS
    assert S.evict(log) is True
    assert S.db_path(log) not in S._CONNS, "evict left the cache entry"
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")                  # the fd is really closed
    assert S.evict(log) is False                  # nothing cached -> no-op
    # a later connect still works (evict is a cache op, not a session op)
    assert S.connect(log) is not None
    S.evict(log)          # don't leak the tmp conn into other in-process tests


# --- kfmt boundary rounding ------------------------------------------------------
# 999_500..999_999 rounds to 1.0M, so it must take the M branch — the old raw-value
# check rendered "1000k". Boundary pins table-driven around both unit edges.

def test_kfmt_boundaries():
    from core.ops import kfmt
    for n, want in [
        (0, "0"), (999, "999"), (1000, "1k"),           # k edge: no int gap
        (124_000, "124k"), (999_499, "999k"),           # rounds down: stays k
        (999_500, "1M"), (999_999, "1M"),               # rounds to 1.0M -> M branch
        (1_000_000, "1M"), (1_200_000, "1.2M"),
    ]:
        assert kfmt(n) == want, f"kfmt({n}) = {kfmt(n)!r}, want {want!r}"


# --- diff_rows: "\ No newline at end of file" ------------------------------------
# The diff library emits that literal entry inside a hunk's `lines` when a file
# lacks a trailing newline. It's metadata, not a file line: it must be skipped,
# never numbered as context (which also shifted every later lineno by one).

def test_diff_rows_skips_no_newline_marker():
    from plugins.claude_code import tools as T
    resp = {"structuredPatch": [{
        "oldStart": 10, "newStart": 10,
        "lines": [" ctx", "-old", "\\ No newline at end of file", "+new", " tail"],
    }]}
    assert T.diff_rows("Edit", {}, resp) == [
        (" ", 10, "ctx"),                 # old 10 / new 10
        ("-", 11, "old"),                 # removals number in the OLD file
        ("+", 11, "new"),                 # additions in the NEW file
        (" ", 12, "tail"),                # not 13: the marker consumed nothing
    ]


# --- read_extent: offset-read that reaches EOF -----------------------------------
# Read(offset=100) on a 149-line file IS partial (lines 1-99 unread), so the
# honest render is the range — pinned as correct-as-is: the whole-file '' short
# circuit stays start<=1 only.

def test_read_extent_offset_to_eof_stays_ranged():
    from plugins.claude_code import tools as T
    fi = {"startLine": 100, "numLines": 50, "totalLines": 149}
    assert T.read_extent(fi) == "100-149/149"
    # and a genuinely whole read stays bare
    assert T.read_extent({"startLine": 1, "numLines": 149, "totalLines": 149}) == ""


# --- parse_redirect: statement scoping + static cd tracking ----------------------
# The repro_summary.txt shape (2026-07-16): last-redirect-wins across the WHOLE
# command latched onto a mid-command bookkeeping redirect (`… >> summary.txt ) &
# done↵wait↵sort summary.txt`) while the visible output went to stdout, AND a
# relative target resolved against the hook cwd even though the command cd'd
# elsewhere first — the fg tailer waited on a path that never existed and the
# mirror painted "output not found". Now: only the FINAL statement's redirect
# counts (statements after it print to stdout → the tee captures everything),
# and a relative target follows statically resolvable top-level cds.

def test_parse_redirect_final_statement_only():
    from plugins.claude_code import tools as T
    # redirect in the final statement: still tailed (single- and multi-statement)
    assert T.parse_redirect("make > log", "/w") == ("/w/log", False)
    assert T.parse_redirect("sleep 45; adapters mr > /t/out 2>&1", "/w") == \
        ("/t/out", False)
    # mid-command redirect, final statement prints to stdout: tee fallback
    assert T.parse_redirect("cmd >> sum.txt; sort sum.txt", "/w") is None
    assert T.parse_redirect("cmd > a.log && echo done", "/w") is None
    # the shape from session cf514935: loop-body append + trailing sort
    repro = ("cat data.json\ncd /scratch &&\nrm -f sum.txt &&\nfor i in 1 2\n"
             "do ( cmd > out_$i 2>&1\necho $i >> sum.txt ) & done\nwait\n"
             "sort sum.txt")
    assert T.parse_redirect(repro, "/w") is None


def test_parse_redirect_follows_static_cd():
    import os
    from plugins.claude_code import tools as T
    assert T.parse_redirect("cd build && make > log", "/w") == \
        ("/w/build/log", False)
    assert T.parse_redirect("cd /abs/dir && make >> log", "/w") == \
        ("/abs/dir/log", True)
    assert T.parse_redirect("cd a && cd b && make > log", "/w") == \
        ("/w/a/b/log", False)
    assert T.parse_redirect("cd ..; make > log", "/w/sub") == ("/w/log", False)
    assert T.parse_redirect("cd 'my dir' && make > log", "/w") == \
        ("/w/my dir/log", False)
    assert T.parse_redirect("cd && make > log", "/w") == \
        (os.path.expanduser("~") + "/log", False)
    # an absolute target never needs the tracking, even past a dynamic cd
    assert T.parse_redirect('cd "$DIR" && make > /t/log', "/w") == \
        ("/t/log", False)


def test_parse_redirect_untrackable_cd_bails_to_tee():
    from plugins.claude_code import tools as T
    for cmd in [
        'cd "$DIR" && make > log',      # dynamic target
        "cd ~/x && make > log",         # ~ expansion
        "cd - && make > log",           # previous dir
        "cd -P x && make > log",        # flags
        "(cd x); make > log",           # paren-scoped: conservative bail
        "(cd x; make > rel)",           # glued `(cd` token — must still poison
        "( cd x; make > rel )",
    ]:
        assert T.parse_redirect(cmd, "/w") is None, cmd
    # a cd inside $(…) can't change the outer cwd — correctly ignored
    assert T.parse_redirect("x=$(cd foo; pwd); make > log", "/w") == \
        ("/w/log", False)


# --- streamfmt.file_display: location-aware file-op names ------------------------
# A bare basename hid WHERE a Read/Update/Write landed — scratchpad, wiki, and
# repo ops all looked alike in the mirror. Under the session cwd the quiet
# basename stays; a session-scratchpad file gets the ✎ icon; anything else
# outside the project gets a dim abbreviated directory prefix.

def test_file_display_locations():
    import os
    from core import render as R
    from core import streamfmt as SF
    cwd = "/w/project"
    # under the cwd: unchanged bare basename
    assert SF.file_display("/w/project/src/app.py", cwd) == ("app.py", "")
    assert SF.file_display("/w/project/top.md", cwd) == ("top.md", "")
    # scratchpad (both /tmp and macOS /private/tmp spellings): icon + basename
    for root in ("/tmp", "/private/tmp"):
        p = root + "/claude-503/-w-project/some-sid-uuid/scratchpad/repro_1.out"
        disp, kind = SF.file_display(p, cwd)
        assert (disp, kind) == (SF.SCRATCH_ICON + " repro_1.out", "scratch"), p
    # outside the project: dim abbreviated dir + basename
    disp, kind = SF.file_display("/etc/hosts", cwd)
    assert kind == "out" and disp.endswith(R.COL["def"] + "hosts")
    assert R.strip_ansi(disp) == "/etc/hosts"
    # home abbreviates to ~, long chains middle-elide to first + last two
    home = os.path.expanduser("~")
    disp, kind = SF.file_display(home + "/wiki/01/providers/zenith/concepts/x.md", cwd)
    assert kind == "out"
    assert R.strip_ansi(disp) == "~/wiki/…/zenith/concepts/x.md"


def test_file_display_default_cwd_is_process_cwd():
    import os
    from core import streamfmt as SF
    here = os.getcwd()
    assert SF.file_display(os.path.join(here, "x.py")) == ("x.py", "")


# --- FileTailer worst-case bounds (core/tail.py PUMP_MAX_B / line_max) -----------
# A 100MB burst used to be ONE unbounded read into `pending` (memory) and one
# giant emit (renderer latency); a newline-free multi-MB line grew `pending`
# forever. The caps bound both — pinned here so the `capped` re-pump contract
# and the `consumed` checkpoint semantics can't silently regress.

def _tail_mod():
    from core import tail as T
    return T


def test_pump_cap_drains_backlog_across_pumps(tmp_path, monkeypatch):
    T = _tail_mod()
    monkeypatch.setattr(T, "PUMP_MAX_B", 4096)
    p = tmp_path / "burst.out"
    n = 500
    p.write_bytes(b"".join(b"line %03d " % i + b"x" * 90 + b"\n" for i in range(n)))
    t = T.FileTailer(str(p))
    total, pumps = 0, 0
    while True:
        lines = t.pump()
        assert lines is not None
        total += len(lines)
        pumps += 1
        if not t.capped:
            break
    assert total == n                      # nothing lost, nothing duplicated
    assert pumps > 1                       # the cap actually chunked the read
    assert t.consumed == p.stat().st_size  # checkpoint lands exactly at EOF
    assert t.pending == b""


def test_pump_capped_flag_clears_when_caught_up(tmp_path):
    T = _tail_mod()
    p = tmp_path / "f.out"
    p.write_bytes(b"one\ntwo\n")
    t = T.FileTailer(str(p))
    assert t.pump() == [b"one", b"two"]
    assert t.capped is False
    assert t.pump() == []                  # idle pump: still not capped
    assert t.capped is False


def test_line_cap_bounds_memory_and_elides_with_marker(tmp_path, monkeypatch):
    T = _tail_mod()
    monkeypatch.setattr(T, "PUMP_MAX_B", 8192)
    p = tmp_path / "giant.out"
    p.write_bytes(b"a" * 100_000)          # newline-free so far
    t = T.FileTailer(str(p), line_max=1000)
    while t.pump() is not None and t.capped:
        pass
    # memory bound holds even BEFORE the newline arrives
    assert len(t.pending) <= 1000
    with open(p, "ab") as fh:
        fh.write(b"zz\nnext\n")
    lines = []
    while True:
        got = t.pump()
        lines += got
        if not t.capped:
            break
    assert len(lines) == 2
    head, marker = lines[0][:1000], lines[0][1000:]
    assert head == b"a" * 1000
    elided = 100_000 + 2 - 1000            # full line minus the surfaced head
    assert marker.decode() == " … (%d bytes elided)" % elided
    assert lines[1] == b"next"             # later lines untouched
    assert t.consumed == p.stat().st_size  # checkpoint intact past the elision


def test_line_cap_short_lines_pass_byte_identical(tmp_path):
    T = _tail_mod()
    p = tmp_path / "ok.out"
    p.write_bytes(b"short\n" + b"x" * 999 + b"\n")
    t = T.FileTailer(str(p), line_max=1000)
    assert t.pump() == [b"short", b"x" * 999]


def test_truncation_resets_line_cap_dropped_state(tmp_path):
    T = _tail_mod()
    p = tmp_path / "t.out"
    p.write_bytes(b"y" * 5000)             # over-cap partial: drops bytes
    t = T.FileTailer(str(p), line_max=1000)
    t.pump()
    assert t.dropped > 0
    p.write_bytes(b"fresh\n")              # file SHRANK: content is fresh
    assert t.pump() == [b"fresh"]          # no stale elision folded in
    assert t.dropped == 0


# --- verbatim_batches: a burst splits into bounded ops ---------------------------
# One pump's lines become MULTIPLE ≤OP_MAX_B gut ops, each still a multi-line
# batch (never a per-line-op regression), with every line surfaced exactly once.

def test_verbatim_batches_bounds_each_op():
    from plugins.claude_code.stream import verbatim_batches
    parts = ["line-%03d-%s" % (i, "x" * 60) for i in range(100)]
    batches = list(verbatim_batches(parts, op_max=1024))
    assert len(batches) > 1                            # the burst actually split
    assert [p for b in batches for p in b] == parts    # order + completeness
    assert all(sum(len(p) for p in b) <= 1024 for b in batches)
    assert all(len(b) > 1 for b in batches[:-1])       # batched, not per-line


def test_verbatim_batches_single_overcap_line_is_own_op():
    from plugins.claude_code.stream import verbatim_batches
    parts = ["small", "B" * 5000, "small2"]
    batches = list(verbatim_batches(parts, op_max=1024))
    assert ["B" * 5000] in batches                     # never split, never dropped
    assert [p for b in batches for p in b] == parts


# ---- core.tabs.sqc — the cached read-only tab-DB reader ----------------------
# The long-lived pollers (scorebar tab_state tick, bg-watch, interrupt-watch)
# read the fixed-path tab DB through ONE cached ro conn per process instead of
# a fresh connect per poll. Pin the contract: a write committed through a
# separate connection is visible to the cached reader (WAL), a missing-DB
# probe neither creates the file nor caches the failure, and the reader
# connects once the DB appears later.


def test_tabs_sqc_cached_reader_sees_committed_writes(tmp_path, monkeypatch):
    from core import tabs
    db = str(tmp_path / "tab.db")
    monkeypatch.setattr(tabs, "TABDB", db)
    tabs.tab_set("7", "thinking")                     # separate write conn (tw)
    assert tabs.tab_get("7") == "thinking"            # populates the cache
    conn = tabs._RO_CONNS.get(db)
    assert conn is not None
    tabs.tab_set("7", "executing")                    # committed write, new conn
    assert tabs.tab_get("7") == "executing"           # cached reader sees it
    assert tabs._RO_CONNS.get(db) is conn             # ...through the SAME conn


def test_tabs_sqc_missing_db_no_create_then_succeeds(tmp_path, monkeypatch):
    from core import tabs
    db = tmp_path / "sub" / "tab.db"                  # parent dir absent too
    monkeypatch.setattr(tabs, "TABDB", str(db))
    assert tabs.tab_get("9") == ""                    # silent miss
    assert not db.exists() and not db.parent.exists()  # probe never creates
    assert str(db) not in tabs._RO_CONNS              # absence NOT cached
    db.parent.mkdir()
    tabs.tab_set("9", "idle")                         # DB appears later
    assert tabs.tab_get("9") == "idle"                # retry-able: now connects
    assert str(db) in tabs._RO_CONNS


# ---- core.errwatch — cached audit-DB connection ------------------------------
# poll() keeps ONE cached mode=ro conn to the fixed-path audit DB instead of a
# fresh connect every POLL_S: absent DB stays a non-creating, non-cached miss
# (a DB that appears later connects then), and committed writes from another
# connection are visible through the cached one.


def test_errwatch_cached_conn_and_db_appears_later(tmp_path, monkeypatch):
    import sqlite3

    from core import errwatch as EW
    db = tmp_path / "audit.db"
    log = str(tmp_path / "claude-mirror-sid1.log")

    class StubA:
        def enabled(self):
            return True

        def db_path(self):
            return str(db)

        def error(self, *a, **k):
            pass

        def state_file(self, *a, **k):
            pass
    monkeypatch.setattr(EW, "A", StubA())
    monkeypatch.setattr(EW, "_conn", None)
    # Absent DB: None (caller keeps memoized count), no file created, no
    # failure cached — the conn slot stays empty for a later retry.
    assert EW.poll(log, "sid1") is None
    assert not db.exists()
    assert EW._conn is None
    w = sqlite3.connect(str(db))                      # DB appears later
    w.execute("CREATE TABLE errors(id INTEGER PRIMARY KEY, session_id TEXT,"
              " script TEXT, func TEXT DEFAULT '', traceback TEXT)")
    w.execute("INSERT INTO errors(session_id, script, traceback)"
              " VALUES('sid1', 's.py', 'E: x')")
    w.commit()
    assert EW.poll(log, "sid1") == 1                  # now connects + counts
    conn1 = EW._conn
    assert conn1 is not None
    w.execute("INSERT INTO errors(session_id, script, traceback)"
              " VALUES('sid1', 't.py', 'E: y')")
    w.commit()                                        # committed elsewhere...
    w.close()
    assert EW.poll(log, "sid1") == 2                  # ...seen by the cache
    assert EW._conn is conn1                          # same conn reused


# ---- substream.cancelled_by_user — the stat-gated meta.json poll -------------
# The 0.4s completion loop used to re-open + json.loads the whole meta.json
# every tick to read one monotonic boolean. Now the parse is gated on the
# (mtime_ns, size) stat signature (Claude Code REWRITES the file, so every
# real update moves it) and True short-circuits permanently.


def _reset_cancel_state(monkeypatch, meta_path):
    from plugins.claude_code import substream as SS
    monkeypatch.setattr(SS, "META_PATH", str(meta_path))
    monkeypatch.setattr(SS, "_META_SIG", None)
    monkeypatch.setattr(SS, "_CANCELLED", False)
    return SS


def _count_json_loads(monkeypatch):
    import json
    calls = {"n": 0}
    real = json.load

    def counting(fh, *a, **k):
        calls["n"] += 1
        return real(fh, *a, **k)
    monkeypatch.setattr(json, "load", counting)
    return calls


def test_cancelled_by_user_stat_gate_and_short_circuit(tmp_path, monkeypatch):
    meta = tmp_path / "agent-x.meta.json"
    SS = _reset_cancel_state(monkeypatch, meta)
    calls = _count_json_loads(monkeypatch)
    meta.write_text('{"other": 1}')
    assert SS.cancelled_by_user() is False            # parses once
    assert calls["n"] == 1
    for _ in range(5):                                # unchanged file: stat only
        assert SS.cancelled_by_user() is False
    assert calls["n"] == 1
    meta.write_text('{"other": 1, "stoppedByUser": true}')  # rewrite bumps sig
    assert SS.cancelled_by_user() is True             # flip detected -> reparse
    assert calls["n"] == 2
    meta.unlink()                                     # once True, stays True —
    for _ in range(3):                                # no stat, no parse, ever
        assert SS.cancelled_by_user() is True
    assert calls["n"] == 2


def test_cancelled_by_user_absent_file_and_mtime_only_change(tmp_path, monkeypatch):
    import os as _os
    meta = tmp_path / "agent-y.meta.json"
    SS = _reset_cancel_state(monkeypatch, meta)
    calls = _count_json_loads(monkeypatch)
    assert SS.cancelled_by_user() is False            # absent: False, no parse,
    assert calls["n"] == 0                            # retried next poll
    meta.write_text('{"stoppedByUser": false}')
    assert SS.cancelled_by_user() is False
    assert calls["n"] == 1
    # Same-size rewrite: only mtime moves — the gate must still reparse.
    meta.write_text('{"stoppedByUser": true }')       # same byte length (24)
    _os.utime(meta, ns=(_os.stat(meta).st_mtime_ns + 10**7,) * 2)
    assert SS.cancelled_by_user() is True
    assert calls["n"] == 2


# --- core.state.ops_after: the -1 reset contract + the gated MAX(id) probe ----
# The renderer detects a recreated DB via its per-iteration inode stat
# (sync_inode), so its idle poll opts out of the empty-path MAX(id) probe
# (check_reset=False -> ONE query per idle tick, and never a -1). Every other
# caller keeps the reset contract byte-identical.

class _CountingConn:
    def __init__(self, conn):
        self._c, self.sqls = conn, []

    def execute(self, sql, *a):
        self.sqls.append(sql)
        return self._c.execute(sql, *a)

    def __getattr__(self, k):
        return getattr(self._c, k)


def test_ops_after_reset_contract_and_gated_probe(tmp_path, monkeypatch):
    from core import state as S
    log = str(tmp_path / "claude-mirror-oa.log")
    assert S.ops_append(log, [{"t": "line", "s": "a"}])
    cc = _CountingConn(S.connect(log))
    monkeypatch.setattr(S, "connect", lambda l: cc)
    # default: the empty path runs the MAX(id) probe, and a max BELOW last_id
    # is the recreated-DB signal (-1) — two queries.
    assert S.ops_after(log, 99) == (-1, [])
    assert len(cc.sqls) == 2
    # check_reset=False: ONE query on the idle path, never a reset signal.
    cc.sqls.clear()
    assert S.ops_after(log, 99, check_reset=False) == (99, [])
    assert len(cc.sqls) == 1
    # the non-empty path is identical either way (each op carries its row's
    # batch timestamp under the reserved _ts key).
    last, ops = S.ops_after(log, 0, check_reset=False)
    assert last == 1 and len(ops) == 1
    assert ops[0]["t"] == "line" and ops[0]["s"] == "a"
    assert isinstance(ops[0]["_ts"], float)
    assert S.ops_after(log, 0) == (last, ops)
