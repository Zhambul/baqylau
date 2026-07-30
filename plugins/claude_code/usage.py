# plugins/claude_code/usage.py — Claude Code's LIMITS / ACCOUNTS / COSTS model.
#
# Everything the dashboard shows about "how much of your subscription is left"
# and "what did this session cost", for the ONE host that has subscription
# accounts at all. It is the read model over three Claude-shaped sources:
#
#   · the `usage` / `account` kv rows the status-line shim stashes per session
#     (statusline.py owns those shapes — the ONLY tokenless channel carrying
#     Anthropic's 5h/7d rate-limit windows),
#   · the `limit-hit` / `logged-out` stamps relimit.py writes when a session
#     dies on a rate limit or a revoked login,
#   · the audit `otel` table's Claude Code telemetry (query_source
#     main/subagent/auxiliary — that taxonomy is Claude Code's).
#
# ALL OF IT USED TO LIVE IN core/sessionapi.py, which calls itself tool-agnostic
# while spelling Anthropic's window LENGTHS (5h / 7d), Anthropic's model family
# ladder, and Claude Code's own status-line re-render timing as core constants
# (host-parity survey E). Core keeps the generic machinery these still stand on
# — sessions(), session_db(), db_cached(), the audit query helpers — and the
# facts moved here, behind four registry providers:
#
#   usage_strip()        the list page's usage strip rows (one per ACCOUNT)
#   session_usage(sid)   one session's rate-limit snapshot
#   session_account(sid) one session's subscription account
#   session_costs(sid)   one session's token/cost totals
#
# The arithmetic itself is unchanged — moved verbatim, because the rate-limit
# migration's target picker (account.pick_target, docs/relimit.md) runs on the
# same numbers and a behavioural drift here silently migrates onto a blocked
# account. This module is the single owner of that arithmetic (docs/styleguide.md
# single-owner table); the dashboard serves the computed values and the page
# never re-derives them.
#
# Read-only: no audit rows (the ctx/goal precedent). Nothing here may raise into
# a hook — the only hook-side caller is relimit's migrator, which already runs
# under the audit-then-swallow harness.
import time

from core import sessionapi as API
from core import state as S

# --- Anthropic's rate-limit windows -------------------------------------------
# The window LENGTHS are the vendor's, which is why they are here and not in
# core: another host's windows are its own (codex reports a duration per window
# and needs no table at all — plugins/codex/usage.py).

FIVE_HOUR_S = 5 * 3600      # the 5h window length — the rolled-over fallback
                            # when a snapshot has no resets_at
SEVEN_DAY_S = 7 * 86400     # the 7d window length — same fallback role

LOGGED_OUT_GRACE_S = 60     # how much NEWER than a `logged-out` stamp a usage
                            # snapshot must be to count as a re-login and clear
                            # it (logged_out_active). Not a window length — a
                            # margin against the dying session's OWN post-turn
                            # status-line render, measured ~0.3s after the stamp
                            # (see the function); anything in the tens of
                            # seconds separates that from a real re-login

# Scheduling knobs for the new-session default-account picker (sched_score,
# docs/dashboard.md *Default account*). Objective (b): maximise total work
# extracted across accounts per week, so we BURN perishable weekly quota first.
SCHED_5H_GATE = 90          # effective 5h use at/above this bars an account from
                            # the PREFERRED pool — a session-safety gate so the
                            # picker doesn't open onto an account already at its
                            # 5h wall (mirrors account.TARGET_MAX_PCT); the burn-
                            # perishable ordering runs among the survivors
SCHED_MIN_HORIZON_H = 0.5   # floor on hours-to-reset in the perishability ratio,
                            # so a window resetting in seconds can't produce an
                            # unbounded score (div-by-~0)

# The two ACCOUNT-WIDE windows, in display order. Everything else a snapshot
# carries is a model-scoped weekly window (seven_day_<family>, merged in from
# the OAuth /usage endpoint — model_usage.py), which is what `scope` names in
# the strip vocabulary below.
ACCOUNT_WINDOWS = ("five_hour", "seven_day")

HOST = "claude_code"        # this plugin's own name, stamped on its strip rows
#                             so ONE painter can group the list-page strip by
#                             host without knowing any host's name itself


def account_usage(limit=50, cache=None):
    """{slug: {"usage": …, "limit_hit": …, "logged_out": …}} — per account, the
    FRESHEST status-line usage snapshot, the freshest rate-limit-hit stamp, and
    the freshest logged-out stamp across the recent sessions (newest `ts` wins;
    each snapshot came from a session running under that account's own token, so
    this is per-account by construction — no API call, no token). Slugs are
    whatever the sessions recorded ('' = the plain-claude default account); the
    caller joins its own registry. `cache` is an optional API.db_cached() memo
    dict."""
    def read(p):
        return (S.kv_at(p, "account") or {}, S.kv_at(p, "usage"),
                S.kv_at(p, "limit-hit"), S.kv_at(p, "logged-out"))
    def file_under(best, slug, key, val):
        ent = best.setdefault(slug, {"usage": None, "limit_hit": None,
                                     "logged_out": None})
        if val and (ent[key] is None
                    or (val.get("ts") or 0) > (ent[key].get("ts") or 0)):
            ent[key] = val
    best = {}
    for row in API.sessions(limit):
        sdb = API.session_db(row)
        acc, usage, hit, lo = (API.db_cached(cache, sdb, read)
                               if cache is not None else read(sdb))
        slug = acc.get("slug") or ""
        file_under(best, slug, "usage", usage)
        # The hit is filed under ITS OWN slug (relimit stamps it), not the
        # session's: after a rate-limit migration the adopted session's
        # `account` kv is the NEW account while the stamp in the same state DB
        # still describes the OLD one — grouping by the session's account
        # pinned the blocked account's chip on the healthy one AND hid the
        # block from the target picker (which could then migrate BACK onto it).
        file_under(best, hit.get("slug", slug) if hit else slug,
                   "limit_hit", hit)
        # logged-out is filed under its own stamped slug for the same reason.
        file_under(best, lo.get("slug", slug) if lo else slug,
                   "logged_out", lo)
    return best


def _window_rolled(usage, key, span, now):
    """True when a snapshot's `key` rate-limit window has rolled over: its
    reset time has passed, or, when the reset is unknown, the snapshot is
    older than the window itself."""
    reset = usage.get(key + "_reset")
    return (reset <= now if isinstance(reset, (int, float)) and reset > 0
            else (usage.get("ts") or 0) + span < now)


def effective_five_hour(usage, now=None):
    """The effective 5h-used percentage of a usage snapshot, for load
    balancing: a rolled-over window (_window_rolled) counts as 0 used; no
    snapshot at all means no recent traffic → also 0."""
    if not usage:
        return 0
    pct = usage.get("five_hour")
    if not isinstance(pct, (int, float)):
        return 0
    now = time.time() if now is None else now
    return 0 if _window_rolled(usage, "five_hour", FIVE_HOUR_S, now) else int(pct)


def usage_windows(usage):
    """The window keys present in a usage snapshot, in display order: the
    account-wide pair first (five_hour, seven_day), then any model-scoped
    window (e.g. `seven_day_fable`) sorted by key. A window is a numeric
    used-% that isn't the `ts` stamp or a `*_reset` sibling. The dict itself
    is already built in this order (statusline.parse_usage) and json/JS
    preserve it, but consumers that ENUMERATE go through here — the one owner
    of what counts as a window (docs/styleguide.md single-owner table)."""
    keys = [k for k, v in (usage or {}).items()
            if isinstance(v, (int, float)) and k != "ts"
            and not k.endswith("_reset")]
    known = [k for k in ACCOUNT_WINDOWS if k in keys]
    return known + sorted(k for k in keys if k not in ACCOUNT_WINDOWS)


def window_span(key):
    """A window key's length in seconds: 5h for the five_hour* family, 7d for
    everything else — model-scoped windows are weekly, like the seven_day pair
    they extend. Only the rolled-over fallback arithmetic uses this (a
    snapshot with a resets_at never needs it)."""
    return FIVE_HOUR_S if key.startswith("five_hour") else SEVEN_DAY_S


def window_label(key):
    """A window key's SHORT display label: five_hour → "5h", seven_day → "7d",
    seven_day_fable → "7d fable". Claude Code's own vocabulary, and the reason
    the label is a per-host field of the strip row rather than something derived
    from the duration: codex calls the same 10080-minute window "1w"."""
    for prefix, short in (("five_hour", "5h"), ("seven_day", "7d")):
        if key.startswith(prefix):
            key = short + key[len(prefix):]
            break
    return key.replace("_", " ").strip()


def window_rows(usage):
    """A usage snapshot → the shared usage-window vocabulary: a LIST of
    {key, label, used_pct, resets_at, window_mins, scope}, account-wide windows
    first (usage_windows' order). [] for an empty snapshot.

    The ONE place a Claude rate-limit window is mapped into the host-neutral
    shape both the list-page strip and the session header read (the vocabulary
    itself is owned by plugins.usage_strip's docstring). `scope` is "account"
    for the 5h/7d pair and "model" for a per-model weekly cap — that is what
    tells the painter whether the window owns a reset column, since a model
    window resets on the same clock as the account-wide `seven_day` bar above
    it and repeating it was pure duplication."""
    out = []
    for key in usage_windows(usage):
        out.append({
            "key": key,
            "label": window_label(key),
            "used_pct": int(usage[key]),
            "resets_at": usage.get(key + "_reset"),
            "window_mins": window_span(key) // 60,
            "scope": "account" if key in ACCOUNT_WINDOWS else "model",
        })
    return out


def effective_usage(usage, now=None):
    """A display-ready copy of a usage snapshot: each window (the 5h/7d pair
    AND any model-scoped window — usage_windows) that rolled over
    (_window_rolled) has its used% zeroed and its reset dropped. Without
    this, an account with no recent session serves its last-known
    percentages with an already-past reset epoch, which the dashboard pill
    renders as 'resets now' — forever. Same single-owner arithmetic as
    effective_five_hour; the page reads the served values, never re-derives
    (docs/styleguide.md single-owner table)."""
    if not usage:
        return usage
    now = time.time() if now is None else now
    out = dict(usage)
    for key in usage_windows(out):
        if _window_rolled(out, key, window_span(key), now):
            out[key] = 0
            out.pop(key + "_reset", None)
    return out


def sched_score(usage, now=None):
    """The PERISHABILITY of an account's weekly (7d) quota, for the new-session
    default-account picker (docs/dashboard.md *Default account*). Objective (b) —
    maximise total work extracted across accounts per week — means BURNING quota
    that will otherwise be wiped soon: score = remaining% / hours-to-7d-reset, so
    an account with quota still left AND a near reset scores HIGH (spend it before
    it resets), while the same headroom with a distant reset scores low (conserve
    it — it survives to next week). The picker prefers the highest score among
    accounts under the 5h session-safety gate (SCHED_5H_GATE); the automigrate
    safety net (docs/relimit.md) catches the higher per-session wall risk this
    accepts. The single owner of the scheduling arithmetic — the dashboard serves
    this number and never re-derives it (docs/styleguide.md single-owner table).

    A rolled-over / unknown-reset 7d window (or no snapshot at all) counts as full
    quota over a full-week horizon — a baseline, non-urgent score, never a spike.
    An exhausted window (0 remaining) scores 0. Only the account-wide `seven_day`
    window is scored: per-MODEL weekly caps still HARD-block via limit_hit, but a
    soft per-model perishability tie-break is a deliberate non-goal for now (the
    tokenless snapshot the migration picker shares carries no per-model window)."""
    now = time.time() if now is None else now
    used = (usage or {}).get("seven_day")
    if (not isinstance(used, (int, float))
            or _window_rolled(usage, "seven_day", SEVEN_DAY_S, now)):
        remaining, horizon_h = 100.0, SEVEN_DAY_S / 3600.0
    else:
        remaining = max(0.0, 100.0 - used)
        reset = usage.get("seven_day_reset")
        horizon_h = ((reset - now) / 3600.0
                     if isinstance(reset, (int, float)) and reset > now
                     else SEVEN_DAY_S / 3600.0)
    return remaining / max(horizon_h, SCHED_MIN_HORIZON_H)


def sched_ok(usage, now=None):
    """Whether an account clears the 5h session-safety gate (SCHED_5H_GATE) —
    i.e. it belongs in the PREFERRED pool the new-session picker ranks by
    sched_score. False = near its 5h wall, kept as a fallback only. The gate owner
    (docs/dashboard.md *Default account*); the dashboard serves this boolean."""
    return effective_five_hour(usage, now) < SCHED_5H_GATE


def limit_hit_active(hit, now=None):
    """True while a `limit-hit` stamp still BLOCKS its account: its reset time
    hasn't passed (or, with no reset known, it is younger than the limit's OWN
    window — a model-scoped stamp caps a WEEKLY per-model quota, so its fallback
    span is one week, not the 5h of an account-wide session limit). Without the
    scope-aware span a Fable ('model'-scoped) stamp inherited the 5h fallback
    (its snapshot carries no per-model reset — statusline.parse_usage), so the
    chip vanished ~5h in while the weekly limit was still in force (reported
    2026-07-19). The dashboard pill gates purely on this (a limited account is
    flagged regardless of which model was capped); the migration target-picker
    layers per-model scope on top via model_available."""
    if not hit:
        return False
    now = time.time() if now is None else now
    reset = hit.get("resets_at")
    if isinstance(reset, (int, float)) and reset > 0:
        return reset > now
    span = SEVEN_DAY_S if hit.get("model") else FIVE_HOUR_S
    return (hit.get("ts") or 0) + span > now


def logged_out_active(stamp, usage):
    """True while a `logged-out` stamp still describes the account's current
    state — i.e. no SUCCESSFUL session has run under it since. Unlike a
    rate-limit, being logged out has no reset epoch; the clear signal is a
    re-login, which (being a `/login` session) captures a fresh status-line
    `usage` snapshot. So the stamp is active while no usage snapshot is more
    than LOGGED_OUT_GRACE_S newer than it. No snapshot at all (never captured)
    → the stamp stands. relimit stamps it on a StopFailure
    error='authentication_failed'; account._rank and the dashboard pill gate on
    this (docs/relimit.md *Logged-out accounts*).

    Why the GRACE margin and not a plain `stamp.ts >= usage.ts`: the original
    predicate assumed the dead session's own status line was captured at the
    prompt BEFORE the turn died on auth (older ts). It isn't — Claude Code
    re-renders the status line at the END of every turn, INCLUDING a failed one,
    so the dying session stashed a `usage` snapshot ~0.3s AFTER its own stamp and
    the badge self-cleared instantly (session 518b6f4d, 2026-07-26: stamp
    ts=…534.026, usage ts=…534.328). That snapshot's CONTENT can't be screened
    either — it carries the account's last-known percentages, which look
    perfectly healthy. The margin also survives repeated failed turns (each
    restamps, and its post-mortem render is again only ~0.3s later); the cost is
    that a re-login inside the SAME session clears the flag only on the first
    status-line render past the margin. A stricter "proof of a successful turn"
    signal (a clean Stop / an OTEL datapoint under that account) was considered
    and rejected as stickier: a bare `/login` produces no turn to prove."""
    if not stamp:
        return False
    return ((stamp.get("ts") or 0) + LOGGED_OUT_GRACE_S
            >= ((usage or {}).get("ts") or 0))


def model_available(hit, model, now=None):
    """Whether `model` (a family word — model.family / relimit.limit_model
    vocabulary: 'fable'/'opus'/'sonnet') is still runnable on an account, given
    that account's freshest `limit-hit` stamp. True unless an ACTIVE stamp
    (limit_hit_active) bars it: an ACCOUNT-WIDE stamp (no `model` scope — nothing
    on the account works) bars every model; a MODEL-scoped stamp bars ONLY its
    own family (a Fable weekly cap leaves Opus/Sonnet on that same account fully
    usable). This is the per-model successor to the old coarse limit_hit_blocks
    — the migration ladder (account.pick_target, docs/relimit.md *Model-downgrade
    ladder*) asks it once per rung. The ONE owner of 'does this stamp bar this
    model on this account' (docs/styleguide.md single-owner table)."""
    if not limit_hit_active(hit, now):
        return True
    scope = (hit or {}).get("model")
    return bool(scope) and scope != model


# --- the registry providers ---------------------------------------------------

def strip_rows(per, model_win):
    """The usage-strip rows for every registry account — the body behind
    usage_strip(), taking its two inputs so a caller (and a test) can hand it
    fixtures instead of the machine's real sessions.

    `per` is account_usage()'s mapping; `model_win` the per-MODEL weekly windows
    (model_usage.windows_by_slug — the caps NO tokenless channel carries, fetched
    from the OAuth /usage endpoint and MERGED into `usage`, after which the
    generic renderer paints them like any other window; five_hour_eff/limit_hit
    stay on the tokenless snapshot, so a missing/failed fetch simply omits them).

    Everything the page shows is server-computed (single-owner rule): `usage` is
    the EFFECTIVE snapshot (effective_usage — a rolled-over window is zeroed and
    its reset dropped, so a stale snapshot can't render 'resets now' forever),
    `windows` that same snapshot in the shared strip vocabulary, `five_hour_eff`
    the load-balancing figure the new-session form preselects by, and `limit_hit`
    the still-active stamp (else None).

    Two live-data OVERRIDES on the pill, both presentation-only — core's
    tokenless numbers (and so the relimit target picker) stay untouched:

      · a MODEL-scoped limit_hit stamp carries no reset epoch (the CLI message
        doesn't state one), so limit_hit_active falls back to 'blocked for a week
        from the hit'. When the fetched live window for that very model reads
        BELOW 100%, the cap has demonstrably cleared (Anthropic resets limits
        mid-week sometimes — reported 2026-07-20), so the stale stamp is dropped.
      · an ACCOUNT-WIDE stamp means the 5h window is MAXED right now, but the
        tokenless snapshot froze BELOW 100: it lags the block (~13s), and once the
        session MIGRATED away its state DB was re-stamped to the NEW account
        (adopt.py), so this account's freshest snapshot is whatever an older
        session captured (measured: 98 min old / 25% for a migrated c2 sitting at
        its cap — a 25% bar under a "limit hit" chip). The account-wide session
        limit resets on the 5h window (relimit sources the stamp's resets_at from
        five_hour_reset), so peg the 5h bar to 100% + the limit's own reset."""
    from plugins.claude_code import account as ACC

    out = []
    for a in ACC.registry():
        ent = per.get(a["slug"]) or {}
        usage, hit = ent.get("usage"), ent.get("limit_hit")
        mw = model_win.get(a["slug"])
        if mw:                                   # per-model windows the tokenless
            usage = dict(usage or {}, **mw)      # snapshot can't carry
        active = limit_hit_active(hit)
        if active and (hit or {}).get("model"):
            pct = (mw or {}).get("seven_day_%s" % hit["model"])
            if isinstance(pct, (int, float)) and pct < 100:
                active = False                   # live window says the cap cleared
        eff_usage = effective_usage(usage)
        if active and not (hit or {}).get("model"):
            eff_usage = dict(eff_usage or {}, five_hour=100)
            if hit.get("resets_at"):
                eff_usage["five_hour_reset"] = hit["resets_at"]
        # LOGGED OUT (the account's OAuth login was revoked/expired — a session
        # on it died on error='authentication_failed', relimit's `logged-out`
        # stamp). Server-computed via logged_out_active, which clears it the
        # moment a fresher usage snapshot for the slug appears (a re-login
        # `/login` session) — docs/dashboard.md *Logged-out accounts*.
        lo = ent.get("logged_out")
        logged_out = logged_out_active(lo, ent.get("usage"))
        out.append(dict(
            a, host=HOST, switchable=True, plan="",
            usage=eff_usage, windows=window_rows(eff_usage),
            ts=(eff_usage or {}).get("ts"),
            five_hour_eff=effective_five_hour(ent.get("usage")),
            # the new-session picker's load-balancing signals: sched_score is the
            # weekly-quota perishability it ranks by, sched_ok the 5h session-
            # safety gate it filters on (docs/dashboard.md *Default account*).
            # Server-computed; the page never re-derives them.
            sched_score=sched_score(usage),
            sched_ok=sched_ok(ent.get("usage")),
            limit_hit=hit if active else None,
            logged_out=logged_out,
            logged_out_msg=(lo or {}).get("msg") if logged_out else None))
    return out


def usage_strip(cache=None, limit=50):
    """The usage-strip provider (plugins.usage_strip fan-out) — one row per
    SUBSCRIPTION ACCOUNT, since that is the unit Claude Code's limits are
    per (unlike codex, which has one host-wide reading). `cache` is the caller's
    db_cached() memo dict (shared by both reads) and `limit` how many recent
    sessions the per-account aggregation scans. See strip_rows."""
    from plugins.claude_code import model_usage
    return strip_rows(account_usage(limit, cache=cache) or {},
                      model_usage.windows_by_slug(cache=cache) or {})


def session_usage(sid):
    """The per-session usage provider (plugins.session_usage fan-out) — the
    session's last-seen rate-limit snapshot as {five_hour, five_hour_reset,
    seven_day, seven_day_reset, ts, windows}, captured from the status-line
    stdin by the shim. None when none has been captured (no shim, a fresh
    account before its first API response, an old session). Per-account by
    construction: the number came from THIS session's own token. Reads the
    RESOLVED state DB path (live or parked), so a parked session keeps it.

    The flat window keys ARE the kv row, served verbatim as they always were;
    `windows` is the same snapshot in the shared strip vocabulary, which is what
    the session header renders (so a host whose windows are not 5h/7d still
    lights the chip)."""
    sdb = API.state_db_for(sid)
    snap = (S.kv_at(sdb, "usage") or None) if sdb else None
    if not snap:
        return None
    return dict(snap, windows=window_rows(snap))


def session_account(sid):
    """The per-session account provider (plugins.session_account fan-out) — the
    subscription account a session runs under, {slug, label}, stamped into the
    state DB at SessionStart and refreshed by the status-line shim (account.py /
    statusline.py). {} when unknown (an old session, or the plain default account
    with no slug)."""
    sdb = API.state_db_for(sid)
    return (S.kv_at(sdb, "account") or {}) if sdb else {}


def session_costs(sid):
    """The per-session cost provider (plugins.session_costs fan-out) — OTEL
    cost/token totals, chain-aware (pre-fork datapoints live under the OLD sid).
    Same ground truth as the audit CLI's otel breakdown: SUM(value) over the raw
    datapoints — {"tokens": {query_source: {type: n}}, "cost": {query_source:
    usd}, "total_usd": x}.

    In the plugin rather than core because `query_source` (main / subagent /
    auxiliary) is Claude Code's OWN telemetry taxonomy, and the `otel` table it
    reads is filled by a receiver only Claude Code sessions spawn — a codex
    session summing it reads 0 for work it really did (it prices itself into its
    scoreboard instead: plugins/codex/usage.session_costs)."""
    chain = API.sid_chain(sid)
    db = API.audit_db()
    ins = API.in_clause(len(chain))
    tokens = {}
    for qs, typ, n in API.db_rows(
            db, "SELECT query_source, type, SUM(value) FROM otel"
                " WHERE session_id IN (%s) AND metric='token'"
                " GROUP BY query_source, type" % ins, tuple(chain)):
        tokens.setdefault(qs or "?", {})[typ or "?"] = n or 0
    cost = {}
    for qs, usd in API.db_rows(
            db, "SELECT query_source, SUM(value) FROM otel"
                " WHERE session_id IN (%s) AND metric='cost'"
                " GROUP BY query_source" % ins, tuple(chain)):
        cost[qs or "?"] = usd or 0.0
    return {"tokens": tokens, "cost": cost, "total_usd": sum(cost.values())}
