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

When the limit is MODEL-scoped (e.g. a Fable weekly cap) and no OTHER account
has that model free either, the migration additionally **downgrades the model**
one rung — fable→opus→sonnet, never skipping a rung — on whichever account
(current or other) has the most headroom, so the session keeps working on a
lesser model instead of stalling (*Model-downgrade ladder* below). This is why
the recovery has to live here and not in Claude Code config: the in-TUI *"Fable
now uses usage credits → Switch to Sonnet 5"* dialog is a **billing** event, and
Claude Code's own `fallbackModel`/`--fallback-model` chain explicitly never
fires on authentication/**billing**/rate-limit/request-size/transport errors
(code.claude.com/docs/en/model-config). An external, event-driven migration is
the only lever — and it can reach the Opus rung the TUI dialog skips.

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

## Limit scope — account-wide vs model-scoped

The limit message comes in two scopes, and the same `error="rate_limit"`
StopFailure carries both (observed 2026-07-19): account-wide (*"You've hit
your session limit · resets …"* — nothing on the account works) and
MODEL-scoped (*"You've reached your Fable 5 limit. /model to switch
models."* — only that model is blocked; others still run). The stamp records
which as `model` (`limit_model()` — *"Fable 5"* → the family word `fable`,
matching the dashboard's model-picker vocabulary; `None` = account-wide; the
ONE parser of the message's scope, docs/styleguide.md). Consumers:

- the dashboard chip says `fable limit hit` instead of the overstated bare
  `limit hit` (docs/dashboard.md *The "limit hit" pill*);
- the new-session auto-picker skips the account only when the limited model
  is the one being launched (docs/dashboard.md);
- **`pick_target` walks the model-downgrade ladder** (below), asking
  `core.sessionapi.model_available(hit, model)` — the single owner of the
  per-model bar rule — once per rung: a model-scoped stamp bars ONLY its own
  family (a Fable cap leaves Opus/Sonnet on that same account usable), an
  account-wide stamp (no `model` scope) bars every model on it. This replaces
  the old coarse `limit_hit_blocks(model_scoped_ok=…)` fudge: the automatic
  path no longer gives up when the current model is capped everywhere — it
  drops a rung; and the manual ⇆ migrate no longer waves a scoped stamp through
  blindly — it too runs the ladder (an account whose Fable is capped is a fine
  target *for Opus*, chosen explicitly). This also subsumes the reported bug
  where a Fable-only-limited account with Opus quota was refused as a target
  (2026-07-19): now it is a valid Opus rung.

## The hook half (relimit.main) — decide and hand off

Ordered guards, every skip audited as a decision row:

1. **Not StopFailure / carries `agent_id` / `error != "rate_limit"`** →
   ignored. A subagent's API-error StopFailure is stop_fmt's recovery job;
   relimit is main-session only.
2. **No live state DB** → skip ("unhosted session"). The kv writes below would
   otherwise CREATE the DB — whose file-existence is the session-alive signal —
   for a headless/daemon session (same guard as `statusline.capture`).
3. **Stamp `limit-hit`** into the state DB kv (slug, ts, `resets_at`, the limit
   message, and the message's model scope — see *Limit scope* below) + an audit
   `state_files` row (`action='limit-hit'`). Unconditional from here on — the
   dashboard pill must flag the account even when migration is off.
   `resets_at`'s SOURCE matches the limit's own window: an account-wide
   "session limit" resets on the 5h window (`five_hour_reset`), while a
   MODEL-scoped limit is a WEEKLY per-model quota whose reset is emphatically
   NOT the 5h window (`seven_day_<model>_reset`, currently never present — the
   statusline reports no per-model window, statusline.parse_usage — so it reads
   as unknown). Taking the model-scoped reset from `five_hour_reset` was the
   reported FALSE-CLEAR: the 5h epoch passes in ~hours, so `limit_hit_active`
   declared a still-biting Fable weekly cap expired and the chip vanished until
   a fresh chat re-hit the limit (2026-07-19).
   For the account-wide case the reset wall-clock time named in the message
   itself fills a missing epoch (`limit_reset()` — *"resets 2:40am
   (Asia/Makassar)"* → the next occurrence of that instant, the ONE parser of
   the message's reset time, docs/styleguide.md); without it a null `resets_at`
   sent `limit_hit_active` to its coarse fallback window while the message knew
   the real reset. A model-scoped message ("/model to switch") names no reset,
   so its `resets_at` stays null and `limit_hit_active` uses the WEEKLY fallback
   span (not 5h) — matching the cap's real cadence.
4. **Kill switch** `CLAUDE_RELIMIT=0` → stamped, not migrated.
5. **Cooldown** (`relimit-attempt` kv younger than `COOLDOWN_S`, 600s) → skip.
   A relaunch that instantly re-hits a limit must not ping-pong tabs forever.
6. **No hosted tab** (`window_for_session`) → skip (headless / daemon).
7. **No target** (`account.pick_target(cur_slug, cur_model)`) → skip. `cur_model`
   is the ladder start: the limited family for a model-scoped limit
   (`limit_model`), else the session's running model read from its transcript
   (`model.session_model` → `model.family`), else None. The picker walks the
   downgrade ladder (below) and returns the best-headroom account + the model to
   run there (or None → skip: ping-ponging between exhausted accounts helps
   nobody). Ranking is `sessionapi.effective_five_hour` over the freshest
   per-account snapshots (`sessionapi.account_usage` — the same numbers the
   dashboard strip shows); candidates at/above `TARGET_MAX_PCT` (90) are refused.
8. Otherwise: stamp `relimit-attempt`, emit the AMBER announce op — `⚠ <label>
   hit its rate limit → resuming on <slug>` for a same-model migrate, or
   `⚠ <label> hit its <cur_model> limit → resuming as <model> on <slug>` for a
   downgrade — it parks with the DB and REPLAYS in the successor's mirror, the
   visible record of the swap. Then spawn the detached migrator
   (`hookkit.spawn_streamer`, purpose `relimit:<slug>`), passing the chosen
   model as the 7th argv element (empty ⇒ same model). The hook exits
   immediately — closing the tab from inside the dying session's own hook would
   race Claude Code's shutdown.

## The model-downgrade ladder (`account.pick_target`)

The one algorithm behind both the automatic path and the manual ⇆ button. Given
`cur_model` (a `model.family` word) and the per-account usage + `limit-hit`
snapshots, walk `model.ladder_from(cur_model)` — `fable→opus→sonnet`, best model
first (`model.MODEL_LADDER` is the single owner of the order; Haiku is
deliberately NOT a rung — the floor is Sonnet):

    for rung in ladder:                 # e.g. [fable, opus, sonnet]
        candidates = accounts where model_available(hit, rung) and eff5h < ceiling
        if candidates: return the lowest-eff5h one, model=rung   # FIRST rung wins

Three properties fall out for free:

- **Keep the model as high as possible.** The `cur_model` rung is tried across
  every account before any downgrade — *same model on another account* beats
  *downgrade in place*, which is what you want (Fable on c2 beats Opus on c1).
- **Never skip a rung.** Opus is explored across all accounts before Sonnet is
  considered — so a Fable limit can never jump straight to Sonnet while any
  account has Opus.
- **Most headroom within a rung.** Candidates are ranked by
  `effective_five_hour`, so the least-loaded account wins that rung.

The current account needs no special-casing: at the top (`cur_model`) rung it is
skipped (`skip_cur` — you never migrate a model to the account that just ran out
of it; its fresh stamp would exclude it anyway); at downgrade rungs it rejoins as
a normal candidate, because a Fable-scoped stamp doesn't bar Opus
(`model_available`). An account-wide stamp excludes it at every rung.

`pick_target` returns `model=""` when the chosen rung IS `cur_model` (a same-model
migration, or the keep-model fallback) so callers resume **bare** (`--resume`
only — the proven path), and a family word only for a real downgrade (→ `--model
<rung>`). When `cur_model` is unknown / not a rung (an account-wide limit whose
transcript model couldn't be read, or a Haiku session) the ladder is empty and
it falls back to the pre-ladder behavior: keep the current model, migrate to the
least-used OTHER account with no active `limit-hit` at all (any active stamp
disqualifies — we can't prove the kept model survives a scoped one). The `ceiling`
is `TARGET_MAX_PCT` for the automatic path and `None` for a manual click (which
outranks the % refuge rule but still runs the same ladder).

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
   (bail — something else owns that session's fate). **AUTO only:** a
   `mode=manual` migrate does NOT bail on gone-but-live — see *Manual migrate*.
3. **Launch the resume tab**: `Frontend.launch_tab(cwd,
   account.launch_argv(["--resume", <sid>] + ["--model", <model>]? + [NUDGE]?,
   <alias>))` — byte-for-byte the dashboard's resume-&-send web launch (same
   `$SHELL -lic '<alias> "$@"'` wrapper, same registry-vetted command word).
   `--model <model>` is present only on a downgrade (the 7th argv element the
   picker chose — empty ⇒ same model, resume bare); it overrides the model the
   transcript would otherwise restore (code.claude.com/docs/en/model-config).
   `NUDGE` is the auto-continue message (auto mode only): the failed turn's
   prompt is already in the transcript, so the resumed session just needs a
   push to pick the work back up — on a downgrade it also names the new model so
   the turn knows why it changed (`launch-failed` / `launched`, plus a
   `relimit-launch` state_files row recording sid/slug/cwd/**model**/ok).

## Manual migrate (the dashboard's ⇆ button)

The session header's action row carries **`⇆ migrate`** right after `✎ rename`
(same style, and like rename it works live AND parked). `POST
/api/session/<sid>/migrate` spawns the SAME detached migrator in
**`mode=manual`**, which differs from the automatic hand-off in exactly the
ways manual intent implies:

- **The % refuge ceiling dropped** (`plugins.migration_target(cur, cur_model,
  manual=True)` → `account.pick_target(ceiling=None)`): an explicit click
  outranks the headroom bar. It runs the SAME `fable→opus→sonnet` ladder as the
  automatic path (`cur_model` read off the transcript via `plugins.context`), so
  a manual migrate now ALSO downgrades the model when no account has the current
  one free — a downgrade rung rides through to `--model`, just like the
  automatic path. No qualifying account at any rung → `409`.
- **No auto-continue nudge**: nothing was cut off, so the relaunch is a bare
  `--resume <sid>` (plus `--model <rung>` on a downgrade) and the session opens
  at the prompt, already on the chosen model.
- **The announce line moves into the migrator** (`⇆ migrating to <slug>
  (web)`, emitted just before the tab close): the hook half never ran for a
  web migrate. Emitted only on the live-window path — a parked session's DB
  must not be recreated by a paint op.
- **Immediate, no confirmation** (user decision — like `■ stop`): the click
  IS the intent, and the worst case is a tab swap you watch happen.
- **Migrates a stranded-live session** (no tab, DB never parked): the auto
  path bails here (`window-gone`) — that gone-but-live state is a race after
  its own moment-ago window check, so "something else owns its fate". A manual
  ⇆ does the opposite: it announces and **launches straight over the live DB**.
  This is the logged-out-account recovery — an account that is logged out dies
  on an `authentication_failed` StopFailure that relimit ignores (not a rate
  limit), so NO SessionEnd fires and the state DB is left LIVE forever with the
  tab gone; the manual ⇆ is the only way out, and bailing made it impossible
  (reported 2026-07-24). Safe because the `--resume` reuses the live DB
  (`decide_log_fate → reuse-live-db`) and the fork adopts it, exactly as the
  parked path's `restore-history` would.

Everything else is shared: same close→park-wait→launch legs, same `relimit`
stream end_reasons (the `relimit-launch` row carries `mode` and `model`), same
adopt/status-line continuity. The endpoint audits every attempt as a
`web-migrate` state_files row (`from`/`to`/`model`/`eff`/`ok`, or the `no
target`/`no terminal`/`unknown sid` reject), and the migrator spawn carries
purpose `relimit:<slug> (web)`.

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
  `no fallback account (cur_model=… <branch> branch …)`), the go path records
  target + effective % + `migrating to <slug> … downgrading <cur>→<rung>` when a
  rung was dropped + migrator pid.
- **pick reasoning: a `state_files` row `relimit-pick`** — the FULL `pick_target`
  trace (its `explain` out-param), emitted on EVERY decided migration (go or
  refuse) so a refusal is reconstructible from the DB instead of re-derived by
  hand. It carries `limit_model` (the message's scope, null=account-wide),
  `session_model` (the raw model read off the transcript — **null here is the
  tell** that the running model couldn't be resolved), `cur_model` (the resolved
  ladder start), `branch` (`ladder` when `cur_model` is a known rung, else
  `fallback` — and the fallback branch is deliberately COARSER: it disqualifies
  ANY account with an active limit-hit, even one scoped to a DIFFERENT model,
  because it can't prove the kept model survives), `ceiling`, `chosen` (the
  target or null), and `candidates` — one record per account weighed at each
  rung (`rung` / `slug` / `eff5h` / `limit_hit` scope / `reject` reason or null).
  This is what pinpoints the reported *"idle account, still didn't migrate"*
  case: a `fallback` branch (session_model null) rejecting a near-idle account
  over a stale model-scoped stamp the ladder branch would have used for a lower
  rung.
- spawn: a `spawns` row (purpose `relimit:<slug>`); stream: a `streams` row
  kind `relimit` whose `end_reason` is the migrator's outcome and whose ctx
  carries the chosen `model`; launch: a `state_files` row `relimit-launch` with
  `model` + `ok`.
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
- **Driving Claude Code's native "switch model" dialog** (the in-TUI *"Fable
  now uses usage credits → Switch to Sonnet 5"* prompt) instead of a relaunch:
  it would have to be screen-scraped and key-driven (fragile, like the rewind
  menu), it can't cross accounts, and it only ever offers **Sonnet** — skipping
  the Opus rung the ladder insists on. Relaunching with `--model` reaches any
  rung on any account deterministically.
- **A same-account model downgrade WITHOUT a tab relaunch** (inject `/model
  opus` into the live session): there is no hook or control channel to change a
  running session's model — the only lever is relaunch. So even a same-account
  downgrade goes through the full close→park→`--resume --model` cycle, uniform
  with an account switch (the tab swap is the same one the user already sees for
  a plain migration).
- **Auto-UPGRADING back to Fable when its weekly cap resets**: out of scope —
  the migration is a one-way recovery. The session stays on the downgraded model
  until the user switches back with `/model`; re-upgrading would need polling the
  (tokenless-unavailable) per-model reset and would surprise a mid-task session.
