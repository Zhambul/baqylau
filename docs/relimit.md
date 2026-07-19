# Rate-limit account migration ("relimit")

`plugins/claude_code/relimit.py` · entry `bin/claude-relimit.py` · dispatched on
`StopFailure` (docs/wiring.md) · tests `tests/test_l2_relimit.py`

When the subscription account a session runs under hits its rate limit
(claude.ai's "You've hit your session limit" — the 5-hour window), the session
is dead in the water until the window resets, even though the machine's OTHER
account (the `claude-subscription` switcher's c1/c2) may be nearly idle. This
feature migrates the session transparently: close the old tab, relaunch the
same conversation under the other account in a new tab, auto-continue the
interrupted turn. The mirror history follows (adopt machinery), the dashboard's
account chip flips to the new account on its own, and the account strip pill
says `limit hit · resets …` on the exhausted account.

## The trigger — StopFailure error="rate_limit"

A main-session turn blocked by the account limit fires a **`StopFailure` whose
payload carries `"error": "rate_limit"`** and `last_assistant_message` = the
synthetic limit message (measured 2026-07-19, session a6cc25a4: the transcript
gains `You've hit your session limit · resets 2:40am (Asia/Makassar)` and the
StopFailure lands one second later). That single event is the whole trigger —
consistent with the tab-colour invariant that every cancellation/failure
recovery is an EVENT, never an idle timeout.

Why not the status line / usage percentage: Claude Code's status-line JSON
reports `used_percentage` from the API's utilization headers
(`anthropic-ratelimit-unified-5h-utilization`), and the block decision travels
in a SEPARATE header (`…-status: allowed/allowed_warning/rejected`) that the
status line never exposes. Empirically the status line stamped **95% thirteen
seconds AFTER the block** — the number physically cannot reach 100 while
requests bounce, so any threshold rule ("migrate at ≥99%") would either
false-fire or never fire. The event says exactly what happened; the percentage
does not.

The dispatcher routes `StopFailure` to Stop's steps + `claude-relimit.py`
LAST — the tab dispatch and stop_fmt's subagent recovery see the session
before the migrator can close its tab.

## The hook half (relimit.main) — decide and hand off

Ordered guards, every skip audited as a decision row:

1. **Not StopFailure / carries `agent_id` / `error != "rate_limit"`** →
   ignored. A subagent's API-error StopFailure is stop_fmt's recovery job;
   relimit is main-session only.
2. **No live state DB** → skip ("unhosted session"). The kv writes below would
   otherwise CREATE the DB — whose file-existence is the session-alive signal —
   for a headless/daemon session (same guard as `statusline.capture`).
3. **Stamp `limit-hit`** into the state DB kv (slug, ts, `resets_at` from the
   freshest usage snapshot's `five_hour_reset`, the limit message) + an audit
   `state_files` row (`action='limit-hit'`). Unconditional from here on —
   the dashboard pill must flag the account even when migration is off.
4. **Kill switch** `CLAUDE_RELIMIT=0` → stamped, not migrated.
5. **Cooldown** (`relimit-attempt` kv younger than `COOLDOWN_S`, 600s) → skip.
   A relaunch that instantly re-hits a limit must not ping-pong tabs forever.
6. **No hosted tab** (`window_for_session`) → skip (headless / daemon).
7. **No target** (`account.pick_target`) → skip. The picker takes the OTHER
   registry accounts, drops any inside an active `limit-hit` stamp, ranks by
   `sessionapi.effective_five_hour` (freshest per-account snapshots via
   `sessionapi.account_usage` — the same numbers the dashboard strip shows),
   and refuses candidates at/above `TARGET_MAX_PCT` (90) — migrating to an
   almost-exhausted account would hit the wall again.
8. Otherwise: stamp `relimit-attempt`, emit the AMBER announce op
   (`⚠ <label> hit its rate limit → resuming on <slug>` — it parks with the
   DB and REPLAYS in the successor's mirror, the visible record of the swap),
   and spawn the detached migrator (`hookkit.spawn_streamer`, purpose
   `relimit:<slug>`). The hook exits immediately — closing the tab from inside
   the dying session's own hook would race Claude Code's shutdown.

## The migrator half (relimit.migrate) — close, wait, relaunch

A short detached process under `core.tail.stream_lifecycle(kind="relimit")`;
every exit path is a distinct `end_reason` (the anomalies query keys on them):

1. Re-find the session's window. **Close the tab** (`Frontend.close_tab`) —
   Claude Code gets SIGHUP, exits gracefully, fires SessionEnd, and the normal
   lifecycle parks the state DB (`close-failed` when the terminal refuses).
2. **Wait for the park** (`state.parked`, poll `POLL_S` up to
   `CLOSE_TIMEOUT_S`=30s) — launching before the old process exited would have
   two sessions fighting over one state DB (`close-timeout` on giving up,
   and crucially NO launch happens then). A window already gone with the DB
   already parked skips straight to launch; gone-but-live is `window-gone`
   (bail — something else owns that session's fate).
3. **Launch the resume tab**: `Frontend.launch_tab(cwd,
   account.launch_argv(["--resume", <sid>, NUDGE], <alias>))` — byte-for-byte
   the dashboard's resume-&-send web launch (same `$SHELL -lic '<alias> "$@"'`
   wrapper, same registry-vetted command word). `NUDGE` is the auto-continue
   message: the failed turn's prompt is already in the transcript, so the
   resumed session just needs a push to pick the work back up
   (`launch-failed` / `launched`, plus a `relimit-launch` state_files row
   recording sid/slug/cwd/ok).

## Manual migrate (the dashboard's ⇆ button)

The session header's action row carries **`⇆ migrate`** right after `✎ rename`
(same style, and like rename it works live AND parked). `POST
/api/session/<sid>/migrate` spawns the SAME detached migrator in
**`mode=manual`**, which differs from the automatic hand-off in exactly the
ways manual intent implies:

- **No % ceiling on the target** (`plugins.migration_target(manual=True)` →
  `account.pick_target(ceiling=None)`): an explicit click outranks the 90%
  refuge rule. An ACTIVE `limit-hit` stamp still disqualifies — a blocked
  account is useless however deliberate the click. No qualifying account →
  `409`.
- **No auto-continue nudge**: nothing was cut off, so the relaunch is a bare
  `--resume <sid>` and the session opens at the prompt.
- **The announce line moves into the migrator** (`⇆ migrating to <slug>
  (web)`, emitted just before the tab close): the hook half never ran for a
  web migrate. Emitted only on the live-window path — a parked session's DB
  must not be recreated by a paint op.
- **Immediate, no confirmation** (user decision — like `■ stop`): the click
  IS the intent, and the worst case is a tab swap you watch happen.

Everything else is shared: same close→park-wait→launch legs, same `relimit`
stream end_reasons (the `relimit-launch` row carries `mode`), same adopt/
status-line continuity. The endpoint audits every attempt as a `web-migrate`
state_files row (`from`/`to`/`eff`/`ok`, or the `no target`/`no terminal`/
`unknown sid` reject), and the migrator spawn carries purpose
`relimit:<slug> (web)`.

One guard the endpoint owns: a sid this machine has never seen (no audit
sessions row, no live/parked state DB) is a `404`, never a spawn. The
migrator's park check is a bare "state DB absent" (`state.parked`), which
cannot tell *parked* from *never existed* — an unknown sid sailed through it
and launched a doomed `--resume` tab (caught live 2026-07-19, the probe's
tab error-exited and self-closed). Validation is the CALLER's job; the
migrator stays trusting because its two callers (the hook, this endpoint)
both verify existence first.

## Why there is no file-migration step

Every switcher account's `configs/<slug>` is a SYMLINK FARM over the shared
`~/.claude` — `projects/` (transcripts), `history.jsonl`, `sessions/`,
`file-history/`, `tasks/`, all of it. Only the OAuth token (keychain, injected
by the alias env) differs per account. So any session is already resumable
from any account — verified end-to-end by resuming a c1-born session headless
under c2 (`zsh -lic 'c2 claude -p --resume <sid> …'` → answered normally,
2026-07-19). If the switcher ever stops symlinking `projects/`, the migrator
gains a copy step; today one would be dead code.

## What makes it seamless

- **Mirror/scoreboard continuity is FREE**: the `--resume` forks the sid and
  the existing adopt machinery (`adopt.py`, driven by `split.cmd_open`'s
  `adopt_pending` note) renames the parked state DB to the new sid, retags
  panes, and replays history — including the announce line.
- **The account chip is accurate for FREE**: the new process's status-line
  capture stashes its own `account` kv on the first API response.
- **The dashboard pill**: `/api/accounts` serves each account's `limit_hit`
  stamp while `sessionapi.limit_hit_active` says it still blocks (reset not
  passed; or younger than one 5h window when the reset is unknown) — rendered
  as a red `limit hit` chip + reset countdown. The frozen ~95% usage bar alone
  is misleading at exactly the moment it matters (see The trigger above).
- **The stamp survives the migration in the SUCCESSOR's state DB** (the adopt
  renames the DB), whose `account` kv now names the NEW account. So
  `sessionapi.account_usage` files each `limit-hit` under the stamp's own
  `slug` field, never the session's account — grouping by the session pinned
  the blocked account's chip on the healthy one and hid the block from
  `pick_target` (which could then migrate a later limit-hit straight back
  onto the still-blocked account).

## Audit trail & triage

- hook decisions: `hook_events` handler `claude-relimit.py` — every skip path
  names itself (`stamped; no hosted tab …`, `cooldown`, `migration off`,
  `no fallback account`), the go path records target + effective % + migrator
  pid.
- spawn: a `spawns` row (purpose `relimit:<slug>`); stream: a `streams` row
  kind `relimit` whose `end_reason` is the migrator's outcome; launch: a
  `state_files` row `relimit-launch` with `ok`.
- canned anomaly ("rate-limit migration incomplete"): any relimit stream that
  ended ≠ `launched`, or `launched` with no later SessionStart under the sid
  (the `--resume` fires SessionStart under the OLD sid, so its absence means
  the relaunch died in the shell — bad alias, keychain prompt, claude not on
  PATH).

## Rejected designs

- **Percentage-threshold trigger** (migrate at ≥N%): the status line's number
  lags reality and never reaches 100 while blocked (above) — false-fires or
  never fires, and it's a poll where an event exists.
- **Transcript sniffing for the limit message**: the StopFailure payload
  already carries `error="rate_limit"` — parsing prose out of the transcript
  duplicates a structured fact and breaks on wording/locale changes.
- **Migrating inside the hook**: hooks must exit fast and never fail;
  close-tab kills the very process running the hook's parent. Detached
  migrator, same reasoning as every streamer.
- **Reusing the same window via send-text**: a user-typed session returns to a
  shell prompt on exit, but a web-launched tab's `zsh -lic` exits WITH claude
  (tab gone), and a half-typed command in the surviving shell would corrupt
  the send. New-tab-then-close-old is uniform.
- **`/api/oauth/usage` for target picking**: needs each account's token from
  the keychain — the whole usage pipeline is deliberately tokenless
  (docs/dashboard.md, *Accounts & usage*).
