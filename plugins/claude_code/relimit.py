# plugins/claude_code/relimit.py — auto-migrate a rate-limited session to
# another subscription account (docs/relimit.md).
# Entry point: bin/claude-relimit.py (hook mode via the dispatcher; argv mode =
# the detached migrator).
#
# When the ACCOUNT's rate limit blocks a main-session turn, Claude Code fires a
# StopFailure whose payload carries error="rate_limit" (verified 2026-07-19 —
# the synthetic "You've hit your session limit · resets …" assistant message
# lands in the transcript one beat earlier). That event is the whole trigger:
# no transcript sniffing, no polling, no idle timeouts (CLAUDE.md invariant —
# cancellation/limit recovery is EVENTS only).
#
# The migration itself rides machinery that already exists:
#   - every subscription account shares ~/.claude (the switcher's configs/<slug>
#     dirs are symlink farms; only the OAuth token in env/keychain differs), so
#     a session is resumable from ANY account with no file copying — verified by
#     resuming a c1-born session under c2 (docs/relimit.md);
#   - `<alias> claude --resume <sid> "<nudge>"` through account.launch_argv is
#     byte-for-byte the dashboard's resume-&-send web launch;
#   - the resume FORKS the sid and the existing adopt machinery (adopt.py)
#     renames the parked state DB and retags panes, so the mirror/scoreboard
#     history carries over on its own;
#   - the new process's status-line capture stashes its own `account` kv, so
#     the dashboard's account chip is accurate with no extra work.
#
# Split like stream.py/substream.py: the HOOK half (main) decides and spawns;
# the MIGRATOR half (migrate) is a short detached process that closes the old
# tab, waits for the SessionEnd park, and launches the resume tab. The hook
# must exit immediately — closing the tab from inside the dying session's own
# hook would race Claude Code's shutdown against the hook's exit.
import os
import re
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import frontends
from core import ops as O
from core import state as St
from core.noaudit import load_audit
from core.tail import stream_lifecycle
from plugins.claude_code import account as ACC
from plugins.claude_code import hookkit as H
from plugins.claude_code import model as M

A = load_audit()   # always-on audit trail (CLAUDE_AUDIT=0 disables); inert stub if it can't import

# Env overrides exist solely for the test suite (docs/testing.md) — real
# sessions never set them, so the shipped cadence stays the literal defaults.
CLOSE_TIMEOUT_S = float(os.environ.get("CLAUDE_RELIMIT_TIMEOUT_S") or 30)
                            # how long the migrator waits for the closed tab's
                            # SessionEnd to park the state DB before giving up
                            # (launching over a still-live session would have
                            # two processes fighting over one state DB)
POLL_S = float(os.environ.get("CLAUDE_RELIMIT_POLL_S") or 0.3)
                            # the park-wait poll cadence
COOLDOWN_S = float(os.environ.get("CLAUDE_RELIMIT_COOLDOWN_S") or 600)
                            # one migration attempt per session per this window
                            # — a relaunch that instantly re-hits a limit must
                            # not ping-pong tabs forever

# The auto-continue message that rides the --resume argv (the user chose
# fully-transparent migration: the failed turn's prompt is already in the
# transcript, so the resumed session just needs a push to pick it back up).
NUDGE = ("Continue where you left off — the previous turn was cut off by the "
         "account's rate limit and this session was resumed on another account.")


def enabled():
    """The kill switch: CLAUDE_RELIMIT=0 disables the migration (the limit-hit
    kv stamp is still written — the dashboard pill must flag the account either
    way)."""
    return (os.environ.get("CLAUDE_RELIMIT") or "1").strip() != "0"


# Claude Code's limit messages come in two scopes: account-wide ("You've hit
# your session limit · resets …") and MODEL-scoped ("You've reached your
# Fable 5 limit. /model to switch models." — other models still work, observed
# 2026-07-19). The stamp records which, so the dashboard chip can say "fable
# limit hit" and the new-session auto-picker can skip the account only when
# the limited model is actually the one being launched.
_MODEL_LIMIT_RE = re.compile(r"you've reached your (.+?) limit", re.I)


def limit_model(msg):
    """The model FAMILY a limit message is scoped to ('Fable 5' → 'fable',
    matching the model-picker vocabulary), or None for an account-wide limit.
    The ONE parser of the limit message's scope (docs/styleguide.md
    single-owner table)."""
    m = _MODEL_LIMIT_RE.search(msg or "")
    if not m:
        return None
    words = [w for w in m.group(1).lower().split() if w != "claude"]
    return words[0] if words else None


# The account-wide limit message names the reset as a WALL-CLOCK time in an
# explicit tz — "resets 2:40am (Asia/Makassar)" (observed 2026-07-19). The
# status-line snapshot's `five_hour_reset` epoch is the preferred source, but it
# is often absent for a "session limit" (the reported bug: a null resets_at fell
# back to a fixed ts+5h window, so the dashboard pill stayed lit for hours after
# the account had already reset). Parsing the message recovers the true epoch.
# Require minutes OR am/pm so "resets 5 messages" can't false-match an hour.
_RESET_RE = re.compile(
    r"resets?\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*([ap]m)?"
    r"(?:\s*\(([^)]+)\))?", re.I)


def limit_reset(msg, now):
    """The absolute reset epoch a limit message names ('resets 2:40am
    (Asia/Makassar)' → epoch), anchored so it is the next occurrence of that
    wall-clock time at/after `now` (a limit resets within the 5h window, so
    rolling one day forward when the time already passed today is enough).
    None when the message carries no parseable reset time (e.g. the model-scoped
    '/model to switch' message) or its timezone can't be resolved — the caller
    then keeps the conservative window fallback. The ONE parser of the limit
    message's reset time (docs/styleguide.md single-owner table)."""
    m = _RESET_RE.search(msg or "")
    if not m or not (m.group(2) or m.group(3)):
        return None
    hour, minute = int(m.group(1)), int(m.group(2) or 0)
    ap = (m.group(3) or "").lower()
    if ap == "pm" and hour != 12:
        hour += 12
    elif ap == "am" and hour == 12:
        hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    tzname = (m.group(4) or "").strip()
    try:
        tz = ZoneInfo(tzname) if tzname else datetime.now().astimezone().tzinfo
        cand = datetime.fromtimestamp(now, tz).replace(
            hour=hour, minute=minute, second=0, microsecond=0)
    except Exception:
        return None
    if cand.timestamp() <= now:
        cand += timedelta(days=1)
    return cand.timestamp()


# --------------------------------------------------------------- hook half

def main():
    """StopFailure handler (dispatched after stop_fmt's recovery — see
    dispatch._ROUTES): on a MAIN-session rate-limit death, stamp the account's
    `limit-hit` kv and hand off to the detached migrator."""
    d, LOG = H.read_payload()
    if d is None:
        return
    if d.get("hook_event_name") != "StopFailure":
        return H.ignore(d, "not StopFailure (relimit acts on turn death only)")
    if d.get("agent_id"):
        return H.ignore(d, "agent_id (a subagent's API error is stop_fmt's job)")
    if (d.get("error") or "") != "rate_limit":
        return H.ignore(d, "StopFailure error=%r (not rate_limit)"
                        % (d.get("error") or ""))
    sid = d.get("session_id") or ""
    # Write ONLY when the state DB already exists (the mirror created it at
    # SessionStart) — never create it: the DB's file-existence is the
    # session-alive signal, and a headless/daemon session must not gain one
    # from a kv stamp (same rule as statusline.capture).
    if not os.path.isfile(St.db_path(LOG)):
        return A.hook_event(
            d, decision="rate_limit: no live state DB (unhosted session) — skip")
    acc = ACC.current()
    # The limit-hit stamp FIRST, unconditionally: the dashboard's account pill
    # keys on it (sessionapi.limit_hit_active), and the target picker skips
    # accounts still inside one. resets_at's SOURCE must match the limit's own
    # window: an account-wide "session limit" resets on the 5h window
    # (`five_hour_reset`), a MODEL-scoped limit is a WEEKLY per-model quota whose
    # reset is NOT the 5h window (that rolls in ~hours and would expire the stamp
    # while the weekly cap still bites — the reported false-clear). The
    # statusline reports no per-model window today (statusline.parse_usage), so a
    # model-scoped reset stays unknown and limit_hit_active's weekly fallback
    # carries it; the `seven_day_<model>_reset` read is future-proofing for when
    # it appears. Either way limit_reset fills in from the message text (the
    # account-wide "resets 2:40am" naming), so an account-wide pill never falls
    # back to the coarse window while the message knew the real reset.
    usage = St.kv_get(LOG, "usage") or {}
    msg = (d.get("last_assistant_message") or "")[:200]
    now = time.time()
    model = limit_model(msg)
    reset = (usage.get("seven_day_%s_reset" % model) if model
             else usage.get("five_hour_reset")) or limit_reset(msg, now)
    hit = {"slug": acc.get("slug") or "", "ts": now,
           "resets_at": reset, "model": model, "msg": msg}
    St.kv_set(LOG, "limit-hit", hit)
    A.state_file(LOG, "", "limit-hit", hit)
    if not enabled():
        return A.hook_event(
            d, decision="rate_limit: stamped; migration off (CLAUDE_RELIMIT=0)")
    last = St.kv_get(LOG, "relimit-attempt") or {}
    if (last.get("ts") or 0) + COOLDOWN_S > time.time():
        return A.hook_event(
            d, decision="rate_limit: stamped; migration skipped (cooldown, "
                        "last attempt → %s)" % (last.get("to") or "?"))
    fe = frontends.get()
    win = fe.window_for_session(sid)
    if not win:
        return A.hook_event(
            d, decision="rate_limit: stamped; no hosted tab (headless/daemon) "
                        "— not migrating")
    # The model to walk the downgrade ladder from (docs/relimit.md
    # *Model-downgrade ladder*): the limited family for a MODEL-scoped limit, else
    # the session's actually-running model read from its transcript (an
    # account-wide limit names no model). None/unreadable → pick_target keeps the
    # current model (bare resume), today's behavior.
    sess_model = M.session_model(d.get("transcript_path") or "")
    cur_model = model or M.family(sess_model)
    explain = {}
    target = ACC.pick_target(acc.get("slug") or "", cur_model, explain=explain)
    # Record the FULL picker reasoning — which branch ran, cur_model + how it
    # resolved, and every account it weighed (rung / effective 5h / limit-hit
    # scope / why rejected) — so a refusal is pinpointed from the DB instead of
    # re-derived by hand. `branch=fallback` is the tell that the running model
    # was unknown, so the coarse "any active limit-hit disqualifies" rule barred
    # an account a model-scoped stamp would have left usable for another rung
    # (docs/relimit.md *Audit trail*).
    A.state_file(LOG, "", "relimit-pick",
                 {"limit_model": model, "session_model": sess_model, **explain})
    if target is None:
        return A.hook_event(
            d, decision="rate_limit: stamped; no fallback account (cur_model=%s, "
                        "%s branch, ceiling %d%%) — not migrating (see "
                        "relimit-pick)"
                        % (cur_model, explain.get("branch"), ACC.TARGET_MAX_PCT))
    # pick_target returns model="" for a same-model migration (resume bare, the
    # proven path) and a family word only for a real downgrade rung.
    mig_model = target["model"]
    dg = bool(mig_model)
    St.kv_set(LOG, "relimit-attempt", {"ts": time.time(), "to": target["slug"]})
    # The announce line lands in the ops stream BEFORE the park, so the adopted
    # successor replays it — the mirror's own record of why the tab was swapped.
    label = acc.get("label") or "account"
    O.emit(LOG, O.label(
        ("⚠ %s hit its %s limit → resuming as %s on %s"
         % (label, cur_model, target["model"], target["slug"])) if dg else
        ("⚠ %s hit its rate limit → resuming on %s" % (label, target["slug"])),
        O.AMBER))
    proc = H.spawn_streamer(
        "claude-relimit.py",
        [LOG, sid, target["slug"], target["alias"], d.get("cwd") or "", "auto",
         mig_model],
        LOG, purpose="relimit:" + target["slug"])
    if proc is None:
        return A.hook_event(d, decision="rate_limit: migrator spawn FAILED")
    A.hook_event(d, decision="rate_limit: migrating to %s (effective 5h %d%%)%s,"
                             " migrator pid %d"
                             % (target["slug"], target["eff"],
                                " downgrading %s→%s" % (cur_model, mig_model)
                                if dg else "", proc.pid))


def entry():
    H.run(main)


# ------------------------------------------------------------ migrator half

def migrate(log, sid, slug, alias, cwd, mode="auto", model=""):
    """The detached migrator: close the session's tab, wait for its SessionEnd
    to park the state DB, then launch `<alias> claude --resume <sid>` in a new
    tab. When `model` is non-empty (a downgrade rung the picker chose —
    docs/relimit.md *Model-downgrade ladder*) the launch carries `--model
    <model>`, so the resumed session drops to that model instead of the
    transcript's exhausted one. Every exit path closes the audit streams row
    with a distinct end_reason — a `relimit` stream that isn't 'launched' IS the
    triage signal (see the anomalies query). Two modes:
      auto   — the rate-limit hook's hand-off: the failed turn's prompt is in
               the transcript, so the relaunch rides the NUDGE auto-continue
               (the hook half already emitted the announce line).
      manual — the dashboard's ⇆ migrate button (docs/relimit.md *Manual
               migrate*): nothing was cut off, so NO nudge — the resumed
               session opens at the prompt; the announce line is emitted here
               (the hook half never ran), only while the DB is still live —
               a parked session must not be recreated by a paint op."""
    fe = frontends.get()
    with stream_lifecycle(log, "relimit", task_id=slug,
                          ctx={"sid": sid, "to": slug, "mode": mode,
                               "model": model}) as run:
        win = fe.window_for_session(sid)
        if win:
            if mode == "manual":
                # The hook half never ran for a web migrate — announce here,
                # just before the close so the line parks and replays in the
                # successor's mirror. (A parked session skips this branch and
                # must: a paint op would recreate the live DB.)
                O.emit(log, O.label("⇆ migrating to %s (web)" % slug, O.AMBER))
            if not fe.close_tab(win):
                A.error(log, "relimit close_tab", {"win": win})
                run.end("close-failed")
                return
            deadline = time.time() + CLOSE_TIMEOUT_S
            while not St.parked(log):
                if time.time() > deadline:
                    A.error(log, "relimit park-wait timeout",
                            {"sid": sid, "timeout_s": CLOSE_TIMEOUT_S})
                    run.end("close-timeout")
                    return
                time.sleep(POLL_S)
        elif not St.parked(log):
            # No tab yet a live state DB: a state this migrator doesn't
            # understand (the window vanished between the hook's check and
            # now, but the session didn't end) — bail rather than launch a
            # duplicate over a possibly-running session.
            A.error(log, "relimit window gone but session live", {"sid": sid})
            run.end("window-gone")
            return
        # `--model` (a downgrade rung) precedes the positional nudge; the auto
        # nudge names the new model so the resumed turn knows why it changed.
        nudge = NUDGE + (
            " It is now running %s because the previous model's quota was "
            "exhausted." % model if model else "")
        words = (["--resume", sid] + (["--model", model] if model else [])
                 + ([nudge] if mode == "auto" else []))
        argv = ACC.launch_argv(words, alias)
        ok = bool(fe.launch_tab(cwd or os.path.expanduser("~"), argv))
        A.state_file(log, "", "relimit-launch",
                     {"sid": sid, "to": slug, "cwd": cwd, "mode": mode,
                      "model": model, "ok": ok})
        if not ok:
            A.error(log, "relimit launch_tab", {"to": slug, "cwd": cwd})
            run.end("launch-failed")
            return
        run.end("launched")


def migrate_entry(argv):
    """bin/claude-relimit.py's argv mode: LOG SID SLUG ALIAS CWD MODE [MODEL]
    (mode: auto = the rate-limit hand-off, manual = the web button; MODEL is the
    downgrade rung, optional/empty ⇒ keep the current model). The 7th arg is
    accepted as absent for a same-model migrate (migrate()'s `model` defaults to
    "")."""
    if len(argv) not in (6, 7) or argv[5] not in ("auto", "manual"):
        A.error(argv[0] if argv else "", "relimit migrate (bad argv)",
                {"argv": list(argv)})
        return
    migrate(*argv)
