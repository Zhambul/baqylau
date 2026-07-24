# L2 — the rate-limit account migration (plugins/claude_code/relimit.py,
# docs/relimit.md).
#
# A main-session StopFailure carrying error="rate_limit" must: stamp the
# `limit-hit` kv (the dashboard pill), pick the least-used fallback account,
# and hand off to the detached migrator — which closes the session's tab,
# waits for the SessionEnd park, and launches `<alias> claude --resume <sid>
# <nudge>` in a new tab. Every skip path must leave a decision row; every
# migrator exit path must close its `relimit` stream with a distinct
# end_reason (the anomalies query keys on them).
import json
import os
import time

import pytest

import oracle
import payloads as P
from conftest import wait_until

RL = "claude-relimit.py"
LIMIT_MSG = "You've hit your session limit · resets 2:40am (Asia/Makassar)"


def rate_limit_payload(s):
    return dict(P.stop(s, failure=True), error="rate_limit",
                last_assistant_message=LIMIT_MSG)


@pytest.fixture
def rl_env(test_env, fake_kitten):
    """test_env + the switcher registry (c1/c2 in the hermetic HOME) and the
    c1 account identity — the shape of a session running under `c1` when its
    account hits the limit. Depends on fake_kitten so the terminal env vars it
    injects into test_env land in the snapshot (fixture-order matters: a plain
    dict(test_env) taken first would miss them and every window lookup would
    come back empty)."""
    reg = os.path.join(test_env["HOME"], ".config", "claude-subscriptions")
    os.makedirs(reg, exist_ok=True)
    with open(os.path.join(reg, "accounts.tsv"), "w") as f:
        f.write("c1\toboard\tsvc-1\nc2\tclaude-01\tsvc-2\n")
    env = dict(test_env)
    env.update({"CLAUDE_SUBSCRIPTION_SLUG": "c1",
                "CLAUDE_SUBSCRIPTION_LABEL": "oboard",
                "CLAUDE_RELIMIT_POLL_S": "0.05"})
    return env


@pytest.fixture
def hosted(session, seed):
    """A session with a live state DB (what SessionStart leaves behind) and a
    fresh 95% usage snapshot — seeded via the product's own writers."""
    def _make(usage=True):
        s = session.make()
        code = "from core import hostpane as HP\nHP.ensure_db(%r)\n" % s.log
        if usage:
            code += ("from core import state as S\n"
                     "S.kv_set(%r, 'usage', {'five_hour': 95, "
                     "'five_hour_reset': %r, 'ts': %r})\n"
                     % (s.log, time.time() + 8000, time.time()))
        seed.py(code)
        return s
    return _make


def kv(s, key):
    rows = s.query_state("SELECT val FROM kv WHERE key=?", (key,))
    return json.loads(rows[0][0]) if rows else None


def relimit_streams(env, sid):
    return [r for r in oracle.streams(env, sid) if r[0] == "relimit"]


# ------------------------------------------------------------- the full arc

def test_rate_limit_migrates_to_fallback_account(run_hook, rl_env, hosted,
                                                 fake_kitten, session):
    """StopFailure(rate_limit) → limit-hit stamp + announce line + migrator:
    close the old tab, wait for the park, launch the c2 resume tab."""
    s = hosted()
    fake_kitten.set_ls_for_session(s.sid)
    run_hook(RL, rate_limit_payload(s), env=rl_env)

    hit = kv(s, "limit-hit")
    assert hit["slug"] == "c1" and hit["msg"] == LIMIT_MSG
    assert hit["model"] is None                      # account-wide, not model-scoped
    assert hit["resets_at"] == pytest.approx(time.time() + 8000, abs=60)
    assert kv(s, "relimit-attempt")["to"] == "c2"
    assert "resuming on c2" in s.ops_text()          # replayed after adoption
    decs = oracle.decisions(rl_env, s.sid, handler=RL)
    assert any(d.startswith("rate_limit: migrating to c2") for d in decs), decs

    # the detached migrator closes the session's tab...
    wait_until(lambda: fake_kitten.calls("close-tab"), desc="old tab closed")
    close = fake_kitten.calls("close-tab")[-1]
    assert "window_id:%s" % fake_kitten.window_id in " ".join(close)
    # ...whereupon (in prod) Claude Code fires SessionEnd; run its park here
    run_hook("claude-split.py", P.session_end(s), argv=("close",), env=rl_env)
    assert os.path.exists(s.parked_db)

    # the resume tab launches under the fallback alias, in the session's cwd
    def launched():
        for c in fake_kitten.calls("launch"):
            if "--type=tab" in c:
                return c
        return None
    argv = wait_until(launched, desc="resume tab launched")
    assert argv[argv.index("--cwd") + 1] == s.cwd
    assert 'c2 "$@"' in argv and "--resume" in argv
    assert argv[argv.index("--resume") + 1] == s.sid
    from plugins.claude_code import relimit as RLM
    assert argv[-1] == RLM.NUDGE                     # the auto-continue nudge

    wait_until(lambda: any(r[1] == "launched" for r in relimit_streams(rl_env, s.sid)),
               desc="relimit stream closed 'launched'")
    launch_rows = [r for r in oracle.state_files(rl_env, s.sid)
                   if r[1] == "relimit-launch"]
    assert launch_rows and json.loads(launch_rows[-1][2])["ok"] is True


def test_dispatcher_routes_stopfailure_to_relimit(run_hook, rl_env, session):
    """The single-dispatcher path reaches relimit with its own handler
    identity (no state DB here → the earliest skip decision)."""
    s = session.make()
    run_hook("claude-hook.py", rate_limit_payload(s), env=rl_env)
    decs = oracle.decisions(rl_env, s.sid, handler=RL)
    assert any("no live state DB" in d for d in decs), decs
    assert not os.path.exists(s.state_db)            # never created by a probe


# ------------------------------------------------------------- skip paths

def test_ignores_non_rate_limit_failures(run_hook, rl_env, hosted):
    s = hosted()
    run_hook(RL, dict(P.stop(s, failure=True), error="server_error"), env=rl_env)
    run_hook(RL, P.stop(s), env=rl_env)                       # plain Stop
    run_hook(RL, dict(rate_limit_payload(s), agent_id="a1"), env=rl_env)
    decs = oracle.decisions(rl_env, s.sid, handler=RL)
    assert any("not rate_limit" in d for d in decs)
    assert any("not StopFailure" in d for d in decs)
    assert any("agent_id" in d for d in decs)
    assert kv(s, "limit-hit") is None
    assert oracle.spawns(rl_env, s.sid) == []


def test_kill_switch_stamps_but_never_migrates(run_hook, rl_env, hosted,
                                               fake_kitten):
    s = hosted()
    fake_kitten.set_ls_for_session(s.sid)
    env = dict(rl_env, CLAUDE_RELIMIT="0")
    run_hook(RL, rate_limit_payload(s), env=env)
    assert kv(s, "limit-hit")["slug"] == "c1"        # the pill still flags
    assert any("migration off" in d
               for d in oracle.decisions(rl_env, s.sid, handler=RL))
    assert oracle.spawns(rl_env, s.sid) == []
    assert fake_kitten.calls("close-tab") == []


FABLE_MSG = "You've reached your Fable 5 limit. /model to switch models."


def test_model_scoped_limit_stamps_its_model(run_hook, rl_env, hosted,
                                             fake_kitten):
    """A model-scoped limit message stamps model='fable' — the dashboard chip
    reads "fable limit hit" and the new-session auto-picker skips the account
    only when launching that model (account-wide stamps carry model=None)."""
    s = hosted()
    fake_kitten.set_ls_for_session(s.sid)
    env = dict(rl_env, CLAUDE_RELIMIT="0")           # stamp only — no tab churn
    run_hook(RL, dict(rate_limit_payload(s), last_assistant_message=FABLE_MSG),
             env=env)
    hit = kv(s, "limit-hit")
    assert hit["model"] == "fable" and hit["msg"] == FABLE_MSG
    # a model-scoped limit must NOT inherit the account-wide 5h reset (that
    # rolls in ~hours and would clear the weekly cap early — the reported bug);
    # with no per-model window in the snapshot the reset stays unknown and the
    # weekly fallback in limit_hit_active carries it.
    assert hit["resets_at"] is None


def test_limit_model_parses_scope():
    from plugins.claude_code import relimit
    assert relimit.limit_model(FABLE_MSG) == "fable"
    assert relimit.limit_model("You've reached your Claude Opus 4.8 limit.") == "opus"
    assert relimit.limit_model(LIMIT_MSG) is None    # account-wide
    assert relimit.limit_model("") is None
    assert relimit.limit_model(None) is None


def test_limit_reset_parses_message_epoch():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from plugins.claude_code import relimit
    tz = ZoneInfo("Asia/Makassar")
    now = datetime(2026, 7, 19, 12, 14, tzinfo=tz).timestamp()
    # named wall-clock time later today → that instant, same day
    got = relimit.limit_reset("resets 1:20pm (Asia/Makassar)", now)
    assert datetime.fromtimestamp(got, tz) == datetime(2026, 7, 19, 13, 20, tzinfo=tz)
    # a time already past today rolls forward one day (limits reset within 5h)
    late = datetime(2026, 7, 18, 23, 0, tzinfo=tz).timestamp()
    got2 = relimit.limit_reset(LIMIT_MSG, late)              # resets 2:40am
    assert datetime.fromtimestamp(got2, tz) == datetime(2026, 7, 19, 2, 40, tzinfo=tz)
    # no reset time, empty, false-match, and unknown tz all decline
    assert relimit.limit_reset(FABLE_MSG, now) is None       # "/model to switch"
    assert relimit.limit_reset("", now) is None
    assert relimit.limit_reset(None, now) is None
    assert relimit.limit_reset("resets 5 messages later", now) is None
    assert relimit.limit_reset("resets 1:20pm (Not/AZone)", now) is None


def test_resets_at_falls_back_to_message_when_snapshot_lacks_it(
        run_hook, rl_env, hosted, fake_kitten):
    """No `five_hour_reset` in the usage snapshot (the reported bug) → the stamp
    recovers the reset epoch from the message text instead of leaving a null
    that lights the pill for a fixed ts+5h window."""
    from plugins.claude_code import relimit as RLM
    s = hosted(usage=False)                          # no usage kv → no reset epoch
    fake_kitten.set_ls_for_session(s.sid)
    env = dict(rl_env, CLAUDE_RELIMIT="0")           # stamp only — no tab churn
    run_hook(RL, rate_limit_payload(s), env=env)     # LIMIT_MSG = "resets 2:40am …"
    hit = kv(s, "limit-hit")
    assert hit["resets_at"] == pytest.approx(RLM.limit_reset(LIMIT_MSG, time.time()))
    assert hit["resets_at"] is not None


def test_cooldown_blocks_a_second_attempt(run_hook, rl_env, hosted, seed,
                                          fake_kitten):
    s = hosted()
    fake_kitten.set_ls_for_session(s.sid)
    seed.py("from core import state as S\n"
            "S.kv_set(%r, 'relimit-attempt', {'ts': %r, 'to': 'c2'})\n"
            % (s.log, time.time()))
    run_hook(RL, rate_limit_payload(s), env=rl_env)
    assert any("cooldown" in d
               for d in oracle.decisions(rl_env, s.sid, handler=RL))
    assert oracle.spawns(rl_env, s.sid) == []


def test_headless_session_stamps_but_never_migrates(run_hook, rl_env, hosted):
    s = hosted()                                     # no tab in the fake ls
    run_hook(RL, rate_limit_payload(s), env=rl_env)
    assert kv(s, "limit-hit")["slug"] == "c1"
    assert any("no hosted tab" in d
               for d in oracle.decisions(rl_env, s.sid, handler=RL))
    assert oracle.spawns(rl_env, s.sid) == []


def test_no_fallback_account_never_migrates(run_hook, rl_env, hosted,
                                            fake_kitten):
    """Registry holds only the CURRENT account → nowhere to go."""
    s = hosted()
    fake_kitten.set_ls_for_session(s.sid)
    reg = os.path.join(rl_env["HOME"], ".config", "claude-subscriptions")
    with open(os.path.join(reg, "accounts.tsv"), "w") as f:
        f.write("c1\toboard\tsvc-1\n")
    run_hook(RL, rate_limit_payload(s), env=rl_env)
    assert any("no fallback account" in d
               for d in oracle.decisions(rl_env, s.sid, handler=RL))
    assert oracle.spawns(rl_env, s.sid) == []
    # The refusal must leave a reconstructible trace: a relimit-pick row naming
    # the branch + every account weighed and why it was rejected (docs/relimit.md
    # *Audit trail*) — so a future triage never has to re-derive it by hand.
    picks = [json.loads(r[2]) for r in oracle.state_files(rl_env, s.sid)
             if r[1] == "relimit-pick"]
    assert len(picks) == 1
    pick = picks[0]
    assert pick["chosen"] is None
    assert pick["cur_slug"] == "c1"
    assert any(c["slug"] == "c1" and "current account" in (c["reject"] or "")
               for c in pick["candidates"])


# ------------------------------------------------------- migrator exit paths

def test_migrator_times_out_when_park_never_comes(run_hook, rl_env, hosted,
                                                  fake_kitten):
    """The tab closes but no SessionEnd parks the DB → 'close-timeout', and
    crucially NO resume tab is launched over the still-live session."""
    s = hosted()
    fake_kitten.set_ls_for_session(s.sid)
    env = dict(rl_env, CLAUDE_RELIMIT_TIMEOUT_S="0.3")
    run_hook(RL, rate_limit_payload(s), argv=(s.log, s.sid, "c2", "c2", s.cwd, "auto"),
             env=env)
    assert [r[1] for r in relimit_streams(rl_env, s.sid)] == ["close-timeout"]
    assert all("--type=tab" not in c for c in fake_kitten.calls("launch"))


def test_migrator_bails_when_window_gone_but_session_live(run_hook, rl_env,
                                                          hosted):
    s = hosted()                                     # live DB, no tab in ls
    run_hook(RL, {}, argv=(s.log, s.sid, "c2", "c2", s.cwd, "auto"), env=rl_env)
    assert [r[1] for r in relimit_streams(rl_env, s.sid)] == ["window-gone"]


def test_manual_migrate_launches_when_window_gone_but_session_live(
        run_hook, rl_env, hosted, fake_kitten):
    """A MANUAL ⇆ over a stranded-live session (no tab, DB never parked — a
    logged-out account dies on authentication_failed with NO SessionEnd) does
    NOT bail like the auto race-guard: explicit intent, no tab to fight over, so
    it announces and launches straight over the live DB (the --resume reuses it
    and the fork adopts it)."""
    s = hosted()                                     # live DB, no tab in ls
    run_hook(RL, {}, argv=(s.log, s.sid, "c2", "c2", s.cwd, "manual"), env=rl_env)
    assert [r[1] for r in relimit_streams(rl_env, s.sid)] == ["launched"]
    assert any("--type=tab" in c for c in fake_kitten.calls("launch"))
    assert "migrating to c2 (web)" in s.ops_text()


def test_migrator_launches_straight_from_a_parked_session(run_hook, rl_env,
                                                          hosted, fake_kitten,
                                                          session):
    """Tab already gone AND the DB already parked (the close raced ahead) —
    the migrator skips the close leg and just launches."""
    s = hosted()
    run_hook("claude-split.py", P.session_end(s), argv=("close",), env=rl_env)
    assert os.path.exists(s.parked_db)
    run_hook(RL, {}, argv=(s.log, s.sid, "c2", "c2", s.cwd, "auto"), env=rl_env)
    assert [r[1] for r in relimit_streams(rl_env, s.sid)] == ["launched"]
    assert any("--type=tab" in c for c in fake_kitten.calls("launch"))


def test_migrator_records_launch_failure(run_hook, rl_env, hosted, fake_kitten):
    s = hosted()
    run_hook("claude-split.py", P.session_end(s), argv=("close",), env=rl_env)
    fake_kitten.set_rc("launch", 1)
    run_hook(RL, {}, argv=(s.log, s.sid, "c2", "c2", s.cwd, "auto"), env=rl_env)
    assert [r[1] for r in relimit_streams(rl_env, s.sid)] == ["launch-failed"]
    launch_rows = [r for r in oracle.state_files(rl_env, s.sid)
                   if r[1] == "relimit-launch"]
    assert launch_rows and json.loads(launch_rows[-1][2])["ok"] is False


# ----------------------------------------------------- manual (web) migrate

def test_manual_migrate_launches_without_nudge(run_hook, rl_env, hosted,
                                               fake_kitten):
    """mode=manual (the dashboard's ⇆ button): nothing was cut off, so the
    resume rides NO positional nudge — the session opens at the prompt."""
    s = hosted()
    run_hook("claude-split.py", P.session_end(s), argv=("close",), env=rl_env)
    run_hook(RL, {}, argv=(s.log, s.sid, "c2", "c2", s.cwd, "manual"),
             env=rl_env)
    assert [r[1] for r in relimit_streams(rl_env, s.sid)] == ["launched"]
    argv = next(c for c in fake_kitten.calls("launch") if "--type=tab" in c)
    assert argv[-1] == s.sid and argv[-2] == "--resume"  # no trailing nudge
    launch_rows = [r for r in oracle.state_files(rl_env, s.sid)
                   if r[1] == "relimit-launch"]
    assert json.loads(launch_rows[-1][2])["mode"] == "manual"


def test_migrator_downgrades_model_in_launch(run_hook, rl_env, hosted,
                                             fake_kitten):
    """A 7th argv element (the downgrade rung the ladder chose) rides through to
    `--model <rung>` on the resume launch, before the positional nudge, and the
    auto nudge names the new model so the resumed turn knows why it changed."""
    s = hosted()
    run_hook("claude-split.py", P.session_end(s), argv=("close",), env=rl_env)
    run_hook(RL, {}, argv=(s.log, s.sid, "c2", "c2", s.cwd, "auto", "opus"),
             env=rl_env)
    assert [r[1] for r in relimit_streams(rl_env, s.sid)] == ["launched"]
    argv = next(c for c in fake_kitten.calls("launch") if "--type=tab" in c)
    assert argv[argv.index("--model") + 1] == "opus"
    assert argv.index("--model") > argv.index("--resume")
    assert "opus" in argv[-1]                        # the nudge names the model
    launch_rows = [r for r in oracle.state_files(rl_env, s.sid)
                   if r[1] == "relimit-launch"]
    assert json.loads(launch_rows[-1][2])["model"] == "opus"


def test_manual_migrate_downgrades_without_nudge(run_hook, rl_env, hosted,
                                                 fake_kitten):
    """A manual downgrade carries `--model` but still no positional nudge — the
    session opens at the prompt, already on the downgraded model."""
    s = hosted()
    run_hook("claude-split.py", P.session_end(s), argv=("close",), env=rl_env)
    run_hook(RL, {}, argv=(s.log, s.sid, "c2", "c2", s.cwd, "manual", "opus"),
             env=rl_env)
    argv = next(c for c in fake_kitten.calls("launch") if "--type=tab" in c)
    assert argv[-2] == "--model" and argv[-1] == "opus"   # no trailing nudge


def test_manual_migrate_announces_in_the_live_mirror(run_hook, rl_env, hosted,
                                                     fake_kitten):
    """A manual migrate of a LIVE session paints its own announce line (the
    hook half never ran) before closing the tab — it parks with the DB and
    replays in the successor's mirror."""
    s = hosted()
    fake_kitten.set_ls_for_session(s.sid)
    env = dict(rl_env, CLAUDE_RELIMIT_TIMEOUT_S="0.3")   # park never comes
    run_hook(RL, {}, argv=(s.log, s.sid, "c2", "c2", s.cwd, "manual"), env=env)
    assert "migrating to c2 (web)" in s.ops_text()
    assert fake_kitten.calls("close-tab")


def test_migrator_rejects_a_bad_mode(run_hook, rl_env, hosted):
    s = hosted()
    run_hook(RL, {}, argv=(s.log, s.sid, "c2", "c2", s.cwd, "yolo"), env=rl_env)
    assert relimit_streams(rl_env, s.sid) == []          # never started
    assert any("bad argv" in r[2] for r in oracle.errors(rl_env, s.sid))


# ------------------------------------------------------------ target picking

def test_pick_target_prefers_least_used_and_skips_limited(monkeypatch,
                                                          tmp_path):
    """The keep-model fallback (cur_model=None — an account-wide limit whose
    model couldn't be read): migrate to the least-used OTHER account, skipping
    any with an active limit-hit and any at/above the ceiling. The direct
    successor of the pre-ladder picker."""
    from core import sessionapi as API
    from plugins.claude_code import account as ACC
    tsv = tmp_path / "accounts.tsv"
    tsv.write_text("c1\toboard\tsvc-1\nc2\tclaude-01\tsvc-2\nc3\tspare\tsvc-3\n")
    monkeypatch.setattr(ACC, "ACCOUNTS_TSV", str(tsv))
    now = time.time()
    fresh = {"c2": {"usage": {"five_hour": 60, "five_hour_reset": now + 1000,
                              "ts": now}, "limit_hit": None},
             "c3": {"usage": {"five_hour": 20, "five_hour_reset": now + 1000,
                              "ts": now}, "limit_hit": None}}
    monkeypatch.setattr(API, "account_usage", lambda limit=50, cache=None: fresh)
    assert ACC.pick_target("c1", None) == {"slug": "c3", "alias": "c3",
                                           "model": "", "eff": 20}
    # the current account is never its own target; an account with NO snapshot
    # (c1 — no recent traffic) counts as effective 0 and wins
    assert ACC.pick_target("c3", None)["slug"] == "c1"
    assert ACC.pick_target("c2", None)["slug"] == "c1"
    # an account inside an active limit-hit stamp is skipped even at low usage
    fresh["c3"]["limit_hit"] = {"ts": now, "resets_at": now + 500}
    assert ACC.pick_target("c1", None)["slug"] == "c2"
    # ... but an EXPIRED stamp is no bar
    fresh["c3"]["limit_hit"] = {"ts": now - 9000, "resets_at": now - 10}
    assert ACC.pick_target("c1", None)["slug"] == "c3"
    # nobody under the ceiling → no target (never ping-pong exhausted accounts)
    fresh["c2"]["usage"]["five_hour"] = 95
    fresh["c3"]["usage"]["five_hour"] = 92
    assert ACC.pick_target("c1", None) is None
    # ...but a MANUAL migrate drops the ceiling (an explicit click outranks
    # the refuge rule) — the limit-hit skip still applies
    assert ACC.pick_target("c1", None, ceiling=None)["slug"] == "c3"
    fresh["c3"]["limit_hit"] = {"ts": now, "resets_at": now + 500}
    assert ACC.pick_target("c1", None, ceiling=None)["slug"] == "c2"
    fresh["c3"]["limit_hit"] = None
    # the plugins registry fan-out routes manual through the same owner
    import plugins
    assert plugins.migration_target("c1", None) is None
    assert plugins.migration_target("c1", None, manual=True)["slug"] == "c3"
    # rolled-over snapshots count as 0 → eligible again
    fresh["c3"]["usage"]["five_hour_reset"] = now - 10
    assert ACC.pick_target("c1", None) == {"slug": "c3", "alias": "c3",
                                           "model": "", "eff": 0}


def test_pick_target_walks_the_model_ladder(monkeypatch, tmp_path):
    """The fable→opus→sonnet downgrade ladder (docs/relimit.md): keep the model
    as high as possible (same model on another account before any downgrade),
    rank each rung by most headroom, never skip a rung, and rejoin the current
    account at downgrade rungs (its Fable cap doesn't bar Opus)."""
    from core import sessionapi as API
    from plugins.claude_code import account as ACC
    tsv = tmp_path / "accounts.tsv"
    tsv.write_text("c1\toboard\tsvc-1\nc2\tclaude-01\tsvc-2\nc3\tspare\tsvc-3\n")
    monkeypatch.setattr(ACC, "ACCOUNTS_TSV", str(tsv))
    now = time.time()

    def u(pct):
        return {"five_hour": pct, "five_hour_reset": now + 1000, "ts": now}

    def fable():
        return {"ts": now, "resets_at": now + 500, "model": "fable"}

    # (a) Fable limited on c1; c2 & c3 have Fable free → SAME model, most-headroom
    # OTHER account (c3=20 < c2=40), and model="" (no downgrade — resume bare).
    fresh = {"c1": {"usage": u(50), "limit_hit": fable()},
             "c2": {"usage": u(40), "limit_hit": None},
             "c3": {"usage": u(20), "limit_hit": None}}
    monkeypatch.setattr(API, "account_usage", lambda limit=50, cache=None: fresh)
    assert ACC.pick_target("c1", "fable") == {"slug": "c3", "alias": "c3",
                                              "model": "", "eff": 20}

    # (b) Fable limited on EVERY account, but Opus free everywhere → downgrade to
    # Opus on the most-headroom account, current account (c1=10) INCLUDED.
    fresh = {"c1": {"usage": u(10), "limit_hit": fable()},
             "c2": {"usage": u(40), "limit_hit": fable()},
             "c3": {"usage": u(20), "limit_hit": fable()}}
    monkeypatch.setattr(API, "account_usage", lambda limit=50, cache=None: fresh)
    t = ACC.pick_target("c1", "fable")
    assert t == {"slug": "c1", "alias": "c1", "model": "opus", "eff": 10}, t

    # (c) never-skip: on Opus, blocked everywhere, Sonnet free → Sonnet (not
    # skipped), most-headroom account (c1=10, rejoined at the downgrade rung).
    def opus():
        return {"ts": now, "resets_at": now + 500, "model": "opus"}
    fresh = {"c1": {"usage": u(10), "limit_hit": opus()},
             "c2": {"usage": u(40), "limit_hit": opus()},
             "c3": {"usage": u(30), "limit_hit": opus()}}
    monkeypatch.setattr(API, "account_usage", lambda limit=50, cache=None: fresh)
    assert ACC.pick_target("c1", "opus") == {"slug": "c1", "alias": "c1",
                                             "model": "sonnet", "eff": 10}
    # ...and a raw model id normalizes to its family (the dashboard passes the id)
    assert ACC.pick_target("c1", "claude-opus-4-8")["model"] == "sonnet"

    # (d) manual (ceiling=None) runs the SAME ladder — no special model-scoped
    # wave-through, just the % bar dropped.
    fresh = {"c1": {"usage": u(95), "limit_hit": fable()},
             "c2": {"usage": u(96), "limit_hit": fable()},
             "c3": {"usage": u(97), "limit_hit": fable()}}
    monkeypatch.setattr(API, "account_usage", lambda limit=50, cache=None: fresh)
    assert ACC.pick_target("c1", "fable") is None            # every rung over ceiling
    assert ACC.pick_target("c1", "fable", ceiling=None)["model"] == "opus"

    # (e) all accounts account-wide blocked → nothing, at any rung.
    def wide():
        return {"ts": now, "resets_at": now + 500, "model": None}
    fresh = {"c1": {"usage": u(10), "limit_hit": wide()},
             "c2": {"usage": u(10), "limit_hit": wide()},
             "c3": {"usage": u(10), "limit_hit": wide()}}
    monkeypatch.setattr(API, "account_usage", lambda limit=50, cache=None: fresh)
    assert ACC.pick_target("c1", "fable", ceiling=None) is None


def test_pick_target_explain_records_the_reasoning(monkeypatch, tmp_path):
    """The `explain` trace makes a refusal reconstructible from the audit DB —
    the reported no-migrate-with-idle-c2 bug (docs/relimit.md *Audit trail*): an
    account-wide limit whose running model couldn't be read falls into the
    FALLBACK branch, whose coarse "any active limit-hit disqualifies" rule bars
    an account whose stamp is scoped to a DIFFERENT model — one the LADDER would
    happily use. The trace must show both."""
    from core import sessionapi as API
    from plugins.claude_code import account as ACC
    tsv = tmp_path / "accounts.tsv"
    tsv.write_text("c1\toboard\tsvc-1\nc2\tclaude-01\tsvc-2\n")
    monkeypatch.setattr(ACC, "ACCOUNTS_TSV", str(tsv))
    now = time.time()
    # c2 is nearly idle but carries a still-active FABLE-scoped stamp.
    fresh = {"c1": {"usage": {"five_hour": 100, "five_hour_reset": now + 1000,
                              "ts": now}, "limit_hit": None},
             "c2": {"usage": {"five_hour": 4, "five_hour_reset": now + 1000,
                              "ts": now},
                    "limit_hit": {"ts": now, "resets_at": None, "model": "fable",
                                  "slug": "c2"}}}
    monkeypatch.setattr(API, "account_usage", lambda limit=50, cache=None: fresh)

    # cur_model unknown → fallback branch → c2 refused over its fable stamp.
    e = {}
    assert ACC.pick_target("c1", None, explain=e) is None
    assert e["branch"] == "fallback"
    assert e["cur_model"] is None
    assert e["chosen"] is None
    c2 = next(c for c in e["candidates"] if c["slug"] == "c2")
    assert "limit-hit" in c2["reject"]
    assert c2["limit_hit"]["model"] == "fable"
    assert c2["eff5h"] == 4          # the trace proves c2 had headroom

    # ...but had the model resolved (ladder branch), c2 IS a valid Opus rung —
    # the fable cap doesn't bar Opus — which is exactly what the fallback missed.
    e2 = {}
    t = ACC.pick_target("c1", "fable", explain=e2)
    assert e2["branch"] == "ladder"
    assert t["slug"] == "c2" and t["model"] == "opus"
    assert e2["chosen"] == {"slug": "c2", "eff5h": 4, "model": "opus"}


def test_model_available_single_owner():
    """core.sessionapi.model_available is the ONE per-model bar rule: an
    account-wide stamp blocks every model, a model-scoped stamp only its own,
    an expired/absent stamp blocks nothing."""
    from core import sessionapi as API
    now = time.time()
    scoped = {"ts": now, "resets_at": now + 500, "model": "fable"}
    wide = {"ts": now, "resets_at": now + 500, "model": None}
    expired = {"ts": now - 9000, "resets_at": now - 10, "model": "fable"}
    assert API.model_available(scoped, "fable", now) is False
    assert API.model_available(scoped, "opus", now) is True
    assert API.model_available(wide, "opus", now) is False
    assert API.model_available(expired, "fable", now) is True
    assert API.model_available(None, "fable", now) is True
