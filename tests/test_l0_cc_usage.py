# L0 — plugins/claude_code/usage.py, Claude Code's limits / accounts / costs
# read model. The ARITHMETIC half of these tests moved here verbatim from
# test_l0_sessionapi.py when the Anthropic window constants and the snapshot
# maths were evicted from tool-agnostic core (host-parity P3): the numbers are
# unchanged and must stay so — the rate-limit migration's target picker
# (plugins/claude_code/account.pick_target) runs on exactly these functions, and
# a drift here silently migrates a session onto a blocked account. The rest
# covers what P3 added: the strip vocabulary and the per-session facets.
#
# In-process, same conventions as test_l0_sessionapi.py: audit rows through the
# REAL core.audit write API under the autouse hermetic CLAUDE_AUDIT_DIR, state
# DBs through core.state's product functions on tmp-path mirror-log keys.
import sys
import time

import pytest
from conftest import REPO

if REPO not in sys.path:
    sys.path.insert(0, REPO)

import core.audit as A
from core import paths as P
from core import state as S
from plugins.claude_code import usage as U


def test_effective_five_hour_arithmetic():
    now = 10_000_000.0
    live = {"five_hour": 95, "five_hour_reset": now + 100, "ts": now - 60}
    assert U.effective_five_hour(live, now) == 95
    # reset passed → the window rolled over → 0
    assert U.effective_five_hour(dict(live, five_hour_reset=now - 1), now) == 0
    # no reset known: older than the window itself → 0, younger → face value
    assert U.effective_five_hour(
        {"five_hour": 40, "ts": now - U.FIVE_HOUR_S - 1}, now) == 0
    assert U.effective_five_hour({"five_hour": 40, "ts": now - 60}, now) == 40
    # absent/garbage snapshots read as 0 (no recent traffic)
    assert U.effective_five_hour(None, now) == 0
    assert U.effective_five_hour({"five_hour": "n/a"}, now) == 0


def test_effective_usage_rolls_stale_windows():
    now = 10_000_000.0
    live = {"five_hour": 29, "five_hour_reset": now + 100,
            "seven_day": 55, "seven_day_reset": now + 3 * 86400, "ts": now - 60}
    # nothing rolled → served as-is
    assert U.effective_usage(live, now) == live
    # 5h reset passed → that window zeroed and its reset DROPPED (the pill
    # rendered a past epoch as 'resets now' forever); 7d untouched
    stale = dict(live, five_hour_reset=now - 10)
    eff = U.effective_usage(stale, now)
    assert eff["five_hour"] == 0 and "five_hour_reset" not in eff
    assert eff["seven_day"] == 55 and eff["seven_day_reset"] == now + 3 * 86400
    # both rolled (no resets known, snapshot older than each window)
    old = {"five_hour": 40, "seven_day": 60,
           "ts": now - U.SEVEN_DAY_S - 1}
    assert U.effective_usage(old, now) == {
        "five_hour": 0, "seven_day": 0, "ts": now - U.SEVEN_DAY_S - 1}
    # non-numeric pct stays untouched; absent snapshot passes through
    assert U.effective_usage({"five_hour": "n/a", "ts": 0}, now) == {
        "five_hour": "n/a", "ts": 0}
    assert U.effective_usage(None, now) is None
    # a MODEL-SCOPED window (generic capture — e.g. seven_day_fable) rolls
    # exactly like the account-wide pair: live reset → as-is, passed reset →
    # zeroed + reset dropped, no reset → the 7d span fallback (window_span)
    fable = dict(live, seven_day_fable=80, seven_day_fable_reset=now + 100)
    assert U.effective_usage(fable, now) == fable
    eff = U.effective_usage(dict(fable, seven_day_fable_reset=now - 10), now)
    assert eff["seven_day_fable"] == 0 and "seven_day_fable_reset" not in eff
    assert eff["five_hour"] == 29                     # others untouched
    assert U.effective_usage(
        {"seven_day_fable": 80, "ts": now - U.SEVEN_DAY_S - 1},
        now)["seven_day_fable"] == 0


def test_sched_score_perishability():
    now = 10_000_000.0
    week_h = U.SEVEN_DAY_S / 3600.0
    # same remaining, SOONER reset scores higher (burn perishable quota first)
    soon = {"seven_day": 40, "seven_day_reset": now + 7 * 3600, "ts": now}
    far = {"seven_day": 40, "seven_day_reset": now + 5 * 86400, "ts": now}
    assert U.sched_score(soon, now) > U.sched_score(far, now)
    # exactly remaining / hours-to-reset
    assert U.sched_score(soon, now) == 60.0 / 7
    # more remaining, same reset scores higher
    a = {"seven_day": 20, "seven_day_reset": now + 3600, "ts": now}
    b = {"seven_day": 70, "seven_day_reset": now + 3600, "ts": now}
    assert U.sched_score(a, now) > U.sched_score(b, now)
    # exhausted window → 0 (never preferred, even with a near reset)
    assert U.sched_score(
        {"seven_day": 100, "seven_day_reset": now + 60, "ts": now}, now) == 0
    # rolled-over / unknown-reset / no snapshot → the full-week baseline
    # (100% remaining over a 7d horizon), NOT a spike
    base = 100.0 / week_h
    assert U.sched_score({"seven_day": 55, "ts": now - U.SEVEN_DAY_S - 1},
                           now) == base            # snapshot older than the window
    assert U.sched_score({"seven_day": 55, "seven_day_reset": now - 10,
                            "ts": now}, now) == base                  # reset past
    assert U.sched_score(None, now) == base                     # no snapshot
    assert U.sched_score({"five_hour": 10, "ts": now}, now) == base  # no 7d win
    # a reset seconds away can't blow the score up (horizon floored)
    spike = U.sched_score(
        {"seven_day": 50, "seven_day_reset": now + 1, "ts": now}, now)
    assert spike == 50.0 / U.SCHED_MIN_HORIZON_H


def test_sched_ok_gate():
    now = 10_000_000.0
    # under the gate → in the preferred pool; at/above → out
    assert U.sched_ok({"five_hour": 40, "five_hour_reset": now + 100,
                         "ts": now}, now) is True
    assert U.sched_ok({"five_hour": U.SCHED_5H_GATE,
                         "five_hour_reset": now + 100, "ts": now}, now) is False
    # a ROLLED 5h window is effective 0 → passes (no recent 5h load)
    assert U.sched_ok({"five_hour": 99, "five_hour_reset": now - 10,
                         "ts": now}, now) is True
    assert U.sched_ok(None, now) is True


def test_usage_windows_order_and_span():
    # windows enumerate account-wide pair first, then model windows by key;
    # ts and *_reset siblings and non-numerics are never windows
    u = {"seven_day_fable": 80, "five_hour": 10, "seven_day": 20,
         "five_hour_reset": 1.0, "ts": 5.0, "junk": "x"}
    assert U.usage_windows(u) == ["five_hour", "seven_day", "seven_day_fable"]
    assert U.usage_windows(None) == []
    assert U.window_span("five_hour") == U.FIVE_HOUR_S
    assert U.window_span("seven_day") == U.SEVEN_DAY_S
    assert U.window_span("seven_day_fable") == U.SEVEN_DAY_S


def test_limit_hit_active_window():
    now = 10_000_000.0
    assert U.limit_hit_active({"ts": now, "resets_at": now + 5}, now) is True
    assert U.limit_hit_active({"ts": now, "resets_at": now - 5}, now) is False
    # no reset known, account-wide: active for the length of one 5h window
    assert U.limit_hit_active({"ts": now - 60}, now) is True
    assert U.limit_hit_active({"ts": now - U.FIVE_HOUR_S - 1}, now) is False
    assert U.limit_hit_active(None, now) is False
    # no reset known, MODEL-scoped: a weekly per-model cap, so the fallback span
    # is a week — a Fable stamp stays active well past 5h (the reported bug: it
    # cleared at 5h while the weekly limit still bit).
    fable = {"ts": now - U.FIVE_HOUR_S - 1, "model": "fable"}
    assert U.limit_hit_active(fable, now) is True
    assert U.limit_hit_active({"ts": now - U.SEVEN_DAY_S - 1,
                                 "model": "fable"}, now) is False
    # an explicit resets_at still wins over the scope-derived span
    assert U.limit_hit_active({"ts": now, "model": "fable",
                                 "resets_at": now - 5}, now) is False


def test_account_usage_keeps_freshest_per_slug(monkeypatch, tmp_path):
    monkeypatch.setattr(P, "PREFIX", str(tmp_path) + "/claude-mirror-")
    monkeypatch.setattr(P, "HISTORY_DIR", str(tmp_path / "park"))
    for sid, ts, pct in (("au1", 100, 10), ("au2", 200, 20)):
        A.session_start({"session_id": sid, "cwd": "/w", "transcript_path": ""})
        log = P.mirror_log(sid)
        S.kv_set(log, "account", {"slug": "c9", "label": "nine"})
        S.kv_set(log, "usage", {"five_hour": pct, "ts": ts})
    S.kv_set(P.mirror_log("au1"), "limit-hit", {"slug": "c9", "ts": 150})
    per = U.account_usage()
    assert per["c9"]["usage"]["five_hour"] == 20        # newest ts wins
    assert per["c9"]["limit_hit"]["ts"] == 150          # tracked independently
    # the db_cached memo path returns the same picture
    cache = {}
    assert U.account_usage(cache=cache)["c9"]["usage"]["five_hour"] == 20
    assert U.account_usage(cache=cache)["c9"]["usage"]["five_hour"] == 20


# ------------------------------------------- the shared usage-window vocabulary

def test_window_rows_speak_the_shared_vocabulary():
    """A Claude usage snapshot → the strip vocabulary (plugins.usage_strip owns
    the shape). The account-wide pair first, then model windows; each row
    carries CLAUDE's own label ("7d" where codex says "1w" for the same 10080
    minutes — the label is per host, deliberately) and its `scope`, which is
    what tells the painter whether the window owns a reset column: a per-model
    weekly cap resets on the same clock as the `seven_day` bar above it, so
    repeating it was pure duplication."""
    now = 10_000_000.0
    u = {"five_hour": 60, "five_hour_reset": now + 3600,
         "seven_day": 97, "seven_day_reset": now + 10222,
         "seven_day_fable": 32, "ts": now}
    rows = U.window_rows(u)
    assert [r["key"] for r in rows] == ["five_hour", "seven_day",
                                        "seven_day_fable"]
    assert [r["label"] for r in rows] == ["5h", "7d", "7d fable"]
    assert [r["scope"] for r in rows] == ["account", "account", "model"]
    assert [r["window_mins"] for r in rows] == [300, 10080, 10080]
    assert [r["used_pct"] for r in rows] == [60, 97, 32]
    assert rows[0]["resets_at"] == now + 3600
    assert rows[2]["resets_at"] is None       # the fetch carried no reset
    assert U.window_rows(None) == [] and U.window_rows({}) == []
    assert U.window_label("five_hour") == "5h"
    assert U.window_label("seven_day_opus") == "7d opus"


def test_strip_rows_are_the_accounts_payload_the_page_reads(monkeypatch):
    """One row per ACCOUNT, carrying everything the strip AND the new-session
    picker read. This is the wire contract: the fields below are exactly what
    app.01-attention/app.09-newsession consume, and the strip merge must not
    have moved any of them.

    Every number is server-computed (the page never re-derives): `usage` is the
    EFFECTIVE snapshot, `windows` the same reading in the shared vocabulary,
    `five_hour_eff`/`sched_score`/`sched_ok` the picker's load-balancing signals,
    `limit_hit` only while still active. `switchable` marks it as a real account
    — which is what the picker filters on, and what a host with no switcher
    (codex) says False to."""
    from plugins.claude_code import account as ACC
    monkeypatch.setattr(ACC, "registry", lambda: [
        {"slug": "c1", "label": "oboard", "alias": "c1"},
        {"slug": "c2", "label": "claude-01", "alias": "c2"}])
    now = time.time()
    per = {"c1": {"usage": {"five_hour": 60, "five_hour_reset": now + 3600,
                            "seven_day": 97, "seven_day_reset": now + 10222,
                            "ts": now},
                  "limit_hit": None, "logged_out": None},
           "c2": {"usage": {"five_hour": 10, "five_hour_reset": now + 900,
                            "seven_day": 20, "seven_day_reset": now + 86400,
                            "ts": now},
                  "limit_hit": None, "logged_out": None}}
    rows = U.strip_rows(per, {"c1": {"seven_day_fable": 91}})
    by = {r["slug"]: r for r in rows}
    assert set(by) == {"c1", "c2"}
    for r in rows:
        assert r["host"] == "claude_code" and r["switchable"] is True
        assert r["plan"] == "" and r["ts"] == pytest.approx(now)
    # the per-MODEL window is merged in and becomes a third bar on c1 only
    assert [w["label"] for w in by["c1"]["windows"]] == ["5h", "7d", "7d fable"]
    assert [w["label"] for w in by["c2"]["windows"]] == ["5h", "7d"]
    assert by["c1"]["usage"]["seven_day_fable"] == 91      # the flat kv too
    # …but five_hour_eff keys off the TOKENLESS snapshot, never the merged window
    assert by["c1"]["five_hour_eff"] == 60
    assert by["c2"]["sched_ok"] is True
    # perishability is remaining% / hours-to-reset: c1 has almost nothing left
    # (3% over ~2.8h) where c2 has 80% over a day, so c2 is the one worth
    # burning — a near reset only scores high with quota still on it
    assert by["c2"]["sched_score"] > by["c1"]["sched_score"]
    assert by["c1"]["limit_hit"] is None and by["c1"]["logged_out"] is False


def test_strip_rows_peg_and_clear_the_limit_hit_overrides(monkeypatch):
    """The two presentation-only overrides survive the move out of core: an
    ACCOUNT-WIDE stamp pegs the 5h bar to 100% (the frozen snapshot lags the
    block, and after a migration it describes a different account entirely),
    and a MODEL-scoped stamp is DROPPED when the live per-model window reads
    below 100 (Anthropic resets mid-week sometimes). Neither touches the
    tokenless numbers the relimit picker runs on."""
    from plugins.claude_code import account as ACC
    monkeypatch.setattr(ACC, "registry",
                        lambda: [{"slug": "c2", "label": "x", "alias": "c2"}])
    now = time.time()
    stale = {"five_hour": 25, "five_hour_reset": now + 8000,
             "seven_day": 25, "seven_day_reset": now + 400000, "ts": now - 5000}
    wide = {"c2": {"usage": dict(stale), "limit_hit": {
        "slug": "c2", "ts": now, "model": None, "resets_at": now + 8000},
        "logged_out": None}}
    row = U.strip_rows(wide, {})[0]
    assert row["usage"]["five_hour"] == 100                  # pegged to the truth
    assert row["usage"]["seven_day"] == 25                   # 7d untouched
    assert row["windows"][0]["used_pct"] == 100              # …in both readings
    assert row["limit_hit"] is not None
    # a MODEL-scoped stamp whose live window says the cap cleared
    scoped = {"c2": {"usage": dict(stale), "limit_hit": {
        "slug": "c2", "ts": now, "model": "fable"}, "logged_out": None}}
    assert U.strip_rows(scoped, {"c2": {"seven_day_fable": 40}})[0]["limit_hit"] \
        is None
    assert U.strip_rows(scoped, {"c2": {"seven_day_fable": 100}})[0]["limit_hit"] \
        is not None


# ------------------------------------------------------- the per-session facets

def test_session_usage_serves_the_kv_verbatim_plus_the_windows(monkeypatch,
                                                               tmp_path):
    """The flat kv shape is served UNCHANGED (it is the wire contract the
    account picker and every older reader know) and `windows` is added beside
    it — the same reading in the shared vocabulary, which is what the session
    header renders so that a host whose windows are not 5h/7d still lights the
    chip. No snapshot at all → None, not an empty reading."""
    monkeypatch.setattr(P, "PREFIX", str(tmp_path) + "/claude-mirror-")
    monkeypatch.setattr(P, "HISTORY_DIR", str(tmp_path / "park"))
    A.session_start({"session_id": "su1", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("su1")
    assert U.session_usage("su1") is None            # nothing captured yet
    snap = {"five_hour": 12, "five_hour_reset": 5.0, "seven_day": 40, "ts": 1.0}
    S.kv_set(log, "usage", snap)
    got = U.session_usage("su1")
    assert {k: v for k, v in got.items() if k != "windows"} == snap
    assert [w["label"] for w in got["windows"]] == ["5h", "7d"]
    assert U.session_account("su1") == {}            # no account stamped
    S.kv_set(log, "account", {"slug": "c1", "label": "oboard"})
    assert U.session_account("su1") == {"slug": "c1", "label": "oboard"}


def test_session_costs_reads_the_otel_taxonomy(monkeypatch, tmp_path):
    """Claude Code's costs are the OTEL datapoints, grouped by its OWN
    query_source taxonomy (main/subagent/auxiliary — which is exactly why the
    query belongs to this plugin and not to core, where every host's session was
    summed through it and a codex run read a truthful-looking zero)."""
    monkeypatch.setattr(P, "PREFIX", str(tmp_path) + "/claude-mirror-")
    A.session_start({"session_id": "sc1", "cwd": "/w", "transcript_path": ""})
    A.otel("sc1", [
        {"metric": "token", "query_source": "main", "type": "input",
         "value": 120},
        {"metric": "token", "query_source": "subagent", "type": "output",
         "value": 30},
        {"metric": "cost", "query_source": "main", "value": 0.25},
        {"metric": "cost", "query_source": "auxiliary", "value": 0.05},
    ])
    got = U.session_costs("sc1")
    assert got["tokens"] == {"main": {"input": 120}, "subagent": {"output": 30}}
    assert got["cost"] == {"main": 0.25, "auxiliary": 0.05}
    assert got["total_usd"] == pytest.approx(0.30)
    # a session with no datapoints reports the empty envelope, not a fake zero
    # attributed to some source
    assert U.session_costs("nope") == {"tokens": {}, "cost": {}, "total_usd": 0}
