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
import time

import frontends
from core import ops as O
from core import state as St
from core.noaudit import load_audit
from core.tail import stream_lifecycle
from plugins.claude_code import account as ACC
from plugins.claude_code import hookkit as H

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
    # accounts still inside one. resets_at comes from the status-line capture's
    # freshest snapshot — the 5h reset epoch the blocked account reported.
    usage = St.kv_get(LOG, "usage") or {}
    hit = {"slug": acc.get("slug") or "", "ts": time.time(),
           "resets_at": usage.get("five_hour_reset"),
           "msg": (d.get("last_assistant_message") or "")[:200]}
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
    target = ACC.pick_target(acc.get("slug") or "")
    if target is None:
        return A.hook_event(
            d, decision="rate_limit: stamped; no fallback account under %d%% "
                        "effective 5h — not migrating" % ACC.TARGET_MAX_PCT)
    St.kv_set(LOG, "relimit-attempt", {"ts": time.time(), "to": target["slug"]})
    # The announce line lands in the ops stream BEFORE the park, so the adopted
    # successor replays it — the mirror's own record of why the tab was swapped.
    O.emit(LOG, O.label("⚠ %s hit its rate limit → resuming on %s"
                        % (acc.get("label") or "account", target["slug"]),
                        O.AMBER))
    proc = H.spawn_streamer(
        "claude-relimit.py",
        [LOG, sid, target["slug"], target["alias"], d.get("cwd") or ""],
        LOG, purpose="relimit:" + target["slug"])
    if proc is None:
        return A.hook_event(d, decision="rate_limit: migrator spawn FAILED")
    A.hook_event(d, decision="rate_limit: migrating to %s (effective 5h %d%%),"
                             " migrator pid %d"
                             % (target["slug"], target["eff"], proc.pid))


def entry():
    H.run(main)


# ------------------------------------------------------------ migrator half

def migrate(log, sid, slug, alias, cwd):
    """The detached migrator: close the session's tab, wait for its SessionEnd
    to park the state DB, then launch `<alias> claude --resume <sid> <NUDGE>`
    in a new tab. Every exit path closes the audit streams row with a distinct
    end_reason — a `relimit` stream that isn't 'launched' IS the triage signal
    (see the anomalies query)."""
    fe = frontends.get()
    with stream_lifecycle(log, "relimit", task_id=slug,
                          ctx={"sid": sid, "to": slug}) as run:
        win = fe.window_for_session(sid)
        if win:
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
        argv = ACC.launch_argv(["--resume", sid, NUDGE], alias)
        ok = bool(fe.launch_tab(cwd or os.path.expanduser("~"), argv))
        A.state_file(log, "", "relimit-launch",
                     {"sid": sid, "to": slug, "cwd": cwd, "ok": ok})
        if not ok:
            A.error(log, "relimit launch_tab", {"to": slug, "cwd": cwd})
            run.end("launch-failed")
            return
        run.end("launched")


def migrate_entry(argv):
    """bin/claude-relimit.py's argv mode: LOG SID SLUG ALIAS CWD."""
    if len(argv) != 5:
        A.error(argv[0] if argv else "", "relimit migrate (bad argv)",
                {"argv": list(argv)})
        return
    migrate(*argv)
