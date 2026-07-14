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
        "claude_scorebar_script", os.path.join(REPO, "claude-scorebar.py"))
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
