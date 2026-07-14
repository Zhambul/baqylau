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
    m.width = m.PS.make_width(80)   # width is a panescript closure — rebind it too
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
    rows = [(1, "claude-cmd-fmt.py", "ValueError: boom"),
            (2, "claude-split.py", "OSError: nope")]
    ops = EW.err_ops(rows, "sid-1")
    assert [o["t"] for o in ops] == ["label", "label"]
    assert ops[0]["s"] == "⚠ audit: claude-cmd-fmt.py: ValueError: boom"
    assert ops[1]["s"] == "⚠ audit: claude-split.py: OSError: nope"
    from core import ops as O
    assert ops[0]["c"] == list(O.AMBER)          # amber warning, not red failure
    # Past FLOOD_N new rows in one poll: ONE collapsed line pointing at the CLI.
    flood = [(i, "s.py", "E: x") for i in range(EW.FLOOD_N + 1)]
    ops = EW.err_ops(flood, "sid-1")
    assert len(ops) == 1
    assert ops[0]["s"] == ("⚠ audit: %d new errors (bin/claude-audit.py errors "
                           "sid-1)" % (EW.FLOOD_N + 1))
    # Per-line char cap: a huge exception message truncates to TEXT_MAX.
    ops = EW.err_ops([(9, "s.py", "E: " + "x" * 500)], "sid-1")
    assert len(ops[0]["s"]) == EW.TEXT_MAX


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
    """A renamed/deleted script must return None (audited), never raise."""
    from core.spawn import spawn_detached
    assert spawn_detached(str(tmp_path / "nope.py"), [], "") is None


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
