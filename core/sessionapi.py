# core/sessionapi.py — the READ-SIDE session-data API (docs/sessionapi.md).
#
# The single sanctioned door for CONSUMERS of session data — the pane renderers
# (claude-mirror.py, claude-scorebar.py), claude-copy.py's toggles, tooling, and
# any future dashboard. It unifies access, not storage: there is deliberately NO
# new write path and NO event table here (the transcripts + audit DB already
# ARE the event record, written by more reliable hands than hooks — a hook-time
# event log would go blank exactly on the no-hook-on-cancel cases; see
# docs/sessionapi.md "why not an events table"). Two kinds of surface:
#
#   PRESENTATION channel — thin delegations to core.state (ops_after, stats,
#     kv, version, parked, tab_state, db_path, evict). The mirror/scorebar
#     consume ONLY this channel; the delegations are the same function objects,
#     so behavior is identical by construction.
#
#   READ MODEL — queries composed over the four existing stores: the per-session
#     state DB (live in /tmp, parked in paths.HISTORY_DIR), the audit DB
#     (sessions/streams/otel/errors — the `streams` table is the keystone: its
#     src_path IS an agent's transcript and its end_reason IS the agent's final
#     status, already carrying every cancellation-recovery outcome), the global
#     tab DB, and the transcripts (parsed plugin-side — plugins.activity(); core
#     imports no plugin, so the tool-specific parsing stays out of here).
#
# Every audit-backed query is FORK-AWARE: adopt.py renames the state DB at a
# sid fork but pre-fork audit rows stay under the OLD sid, so sid-keyed queries
# must resolve the adopt chain first (sid_chain) or costs/errors/agents
# silently truncate at the fork.
#
# All reads are mode=ro / bare-exists probes — this module can never create a
# DB whose existence is a liveness signal (styleguide SQL rules). Failures are
# silent-with-default like tabs.sq(): for a read-only probe a missing store is
# a normal state, not an error worth an audit row.
import json
import os
import sqlite3
import time
from collections import OrderedDict

from core import paths as P
from core import state as S
from core.noaudit import load_audit

A = load_audit()   # only for db_path()/enabled() — this module writes nothing

# --- presentation channel (delegations — the mirror/scorebar's whole diet) --------
ops_after = S.ops_after
ops_at    = S.ops_at
stats     = S.stats
stats_at  = S.stats_at
kv_at     = S.kv_at
version   = S.version
parked    = S.parked
kv_get    = S.kv_get
kv_set    = S.kv_set
db_path   = S.db_path
evict     = S.evict
tab_state = S.tab_state
tab_states = S.tab_states


def _rows(db, sql, params=()):
    """Full-row read-only query; [] when the DB file is missing/unreadable.
    Fresh conn per call — these are one-shot CLI/dashboard queries, not tick
    pollers (the styleguide's cached-ro-conn rule does not apply)."""
    if not db or not os.path.isfile(db):
        return []
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=1.0)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()
    except Exception:
        return []


def audit_db():
    """The audit DB path, or '' when auditing is off/degraded (NoAudit stub)."""
    try:
        return A.db_path() if A.enabled() else ""
    except Exception:
        return ""


_HAS_START_CWD = False


def _sessions_has_start_cwd(db):
    """Whether the audit `sessions` table has the `start_cwd` column yet.
    It's a late-added column (core.audit._migrate ALTERs it in): a WRITER adds
    it the first time it opens the DB after an upgrade, but this read-only
    module can't ALTER — so for the brief window right after an upgrade the
    column may still be absent. Probe once and cache the True result (a column
    never disappears once added), so the sessions list degrades to the old
    group-by-live-cwd for that window instead of a _rows() error blanking it."""
    global _HAS_START_CWD
    if not _HAS_START_CWD:
        _HAS_START_CWD = any(r[1] == "start_cwd"
                             for r in _rows(db, "PRAGMA table_info(sessions)"))
    return _HAS_START_CWD


# --- sid-fork chain ---------------------------------------------------------------

def sid_chain(sid):
    """Every sid this session has been known under, oldest → newest. Resolves
    plugins/claude_code/adopt.py forks from the audit trail: each adoption
    leaves a state_files row (session_id = the NEW sid, action='adopt',
    content JSON carrying {"from": <old sid>}). [sid] when the audit is
    unavailable or the sid never forked."""
    rows = _rows(audit_db(),
                 "SELECT session_id, content FROM state_files WHERE action='adopt'")
    fwd, back = {}, {}
    for new_sid, content in rows:
        try:
            old = (json.loads(content or "{}") or {}).get("from")
        except Exception:
            continue
        if old and new_sid:
            fwd[old] = new_sid
            back[new_sid] = old
    if sid not in fwd and sid not in back:
        return [sid]
    cur, seen = sid, {sid}
    while back.get(cur) and back[cur] not in seen:   # walk to the oldest sid
        cur = back[cur]
        seen.add(cur)
    chain = [cur]
    while fwd.get(chain[-1]) and fwd[chain[-1]] not in set(chain):
        chain.append(fwd[chain[-1]])
    return chain


def _in_clause(n):
    # "?,?,?" — placeholder list only, values are always bound (styleguide).
    return ",".join("?" * n)


def _stream_count(sid, kind):
    """Distinct-`task_id` COUNT over the audit `streams` keystone for ONE kind,
    chain-aware — the shared skeleton behind job_count()/monitor_count(). Kind
    is bound (styleguide), so `kind='bg'` becomes an equivalent `kind=?`."""
    chain = sid_chain(sid)
    q = ("SELECT COUNT(DISTINCT task_id) FROM streams WHERE kind=?"
         " AND session_id IN (%s)" % _in_clause(len(chain)))
    rows = _rows(audit_db(), q, (kind,) + tuple(chain))
    return int(rows[0][0]) if rows else 0


def _streams_by(sid, kinds, cols, key, fold):
    """The shared chain→in-clause→select→merge skeleton over the audit `streams`
    keystone, behind agents()/codex_runs()/monitor_streams()/jobs():
    `SELECT <cols> FROM streams WHERE kind IN (<kinds>) AND session_id IN
    (<chain>) ORDER BY started_at`, then merge the rows into a dict.

    `key(row)` extracts the merge key (a falsy key SKIPS the row). `fold(out,
    k, row)` owns the row-SHAPING — the per-reader `out.setdefault(k, {...})`
    plus field assignment — and is called for every row in started_at order, so
    the setdefault keeps the FIRST start while later assignments carry the
    NEWEST end/status (the merge semantics each reader relies on). Returns the
    merged dict `out`; readers do their own final shaping (sort / join). Kinds
    are bound (styleguide): `kind='codex'` becomes an equivalent `kind IN (?)`."""
    chain = sid_chain(sid)
    q = ("SELECT %s FROM streams WHERE kind IN (%s) AND session_id IN (%s)"
         " ORDER BY started_at"
         % (cols, _in_clause(len(kinds)), _in_clause(len(chain))))
    out = {}
    for row in _rows(audit_db(), q, tuple(kinds) + tuple(chain)):
        k = key(row)
        if not k:
            continue
        fold(out, k, row)
    return out


# --- discovery ----------------------------------------------------------------------

def sessions(limit=25):
    """Recent sessions, newest first: the audit `sessions` rows joined with
    on-disk liveness (live state DB in /tmp vs parked history), plus any parked
    DBs the audit never saw (audit disabled at the time) as minimal rows. Each
    row carries both `cwd` (live — session_paths re-stamps it as the session
    moves) and `start_cwd` (the frozen ORIGINAL cwd, for stable grouping — the
    dashboard groups on it so a mid-session cd never moves a card's group)."""
    out, seen = [], set()
    db = audit_db()
    # A controlled 2-value column choice, never user input: fall back to `cwd`
    # (the pre-migration behaviour) when start_cwd isn't in the table yet.
    scwd_col = "start_cwd" if _sessions_has_start_cwd(db) else "cwd"
    for sid, cwd, tpath, mlog, st, en, er, win, scwd in _rows(
            db,
            "SELECT session_id, cwd, transcript_path, mirror_log, started_at,"
            " ended_at, end_reason, kitty_window_id, " + scwd_col + " FROM sessions"
            " ORDER BY started_at DESC LIMIT ?",
            (limit,)):
        log = mlog or P.mirror_log(sid)
        seen.add(P.sid_from_log(log))
        out.append({"sid": sid, "cwd": cwd, "start_cwd": scwd or cwd or "",
                    "transcript_path": tpath, "log": log,
                    "started_at": st, "ended_at": en, "end_reason": er,
                    "kitty_window_id": win or "",
                    "live": os.path.isfile(P.state_db(log)),
                    "parked": os.path.isfile(P.parked_db(log))})
    try:
        parked_keys = sorted(f[:-len(".state.db")]
                             for f in os.listdir(P.HISTORY_DIR)
                             if f.endswith(".state.db"))
    except OSError:
        parked_keys = []
    for key in parked_keys:
        if key in seen:
            continue
        log = P.log_for_key(key)
        out.append({"sid": key, "cwd": "", "start_cwd": "",
                    "transcript_path": "", "log": log,
                    "started_at": None, "ended_at": None, "end_reason": "",
                    "kitty_window_id": "",
                    "live": os.path.isfile(P.state_db(log)), "parked": True})
    return out


# --- account usage read model --------------------------------------------------------
# The per-ACCOUNT rate-limit picture, composed from what each session's status-
# line capture stashed into its state DB (plugins/claude_code/statusline.py owns
# the `usage`/`account` kv shapes; relimit.py owns `limit-hit`). Consumers: the
# dashboard's accounts strip / new-session picker AND the rate-limit migration's
# target picker (plugins/claude_code/relimit.py) — this module is the ONE owner
# of both the freshest-per-slug aggregation and the effective-5h arithmetic
# (docs/styleguide.md single-owner table); the dashboard's JS reads the served
# number, never re-derives it.

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


def db_sig(path):
    """A change fingerprint for a sqlite state DB: (mtime_ns, size) of the DB
    file plus its -wal sidecar when present (a live writer appends to the WAL
    without touching the main file until checkpoint — the main file's stat
    alone serves stale numbers for exactly the sessions that move). None when
    the file is missing — callers delegate to the uncached read."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    try:
        wal = os.stat(path + "-wal")
        return (st.st_mtime_ns, st.st_size, wal.st_mtime_ns, wal.st_size)
    except OSError:
        return (st.st_mtime_ns, st.st_size)


class BoundedLRU(OrderedDict):
    """An insertion-order LRU that evicts its OLDEST entry once it exceeds
    `cap` — the fix for the process-lifetime memo leak. A long-lived singleton
    (the dashboard server, a receiver) that memoizes per session/transcript/cwd
    path grows without bound otherwise: the value side is freshness-checked
    (db_sig / file size / TTL), but the KEY set only ever grows as new sessions
    are seen over a days-long life. Every value cached this way is re-derivable,
    so an evicted key just re-reads once — eviction is always safe. Size `cap`
    well ABOVE the live working set (the top-N discovery window plus its agents)
    so active sessions never thrash; only paths that scrolled out of discovery
    (and so aren't read anymore) age out. Recency is refreshed on WRITE, not
    read (db_cached returns a fresh-sig hit without re-storing), which is enough
    because a still-in-window path is re-read — and re-stored — the moment its
    sig changes; a parked path whose sig is frozen is exactly the one safe to
    evict. A plain dict everywhere it's used, so no call site changes."""

    def __init__(self, cap, *a, **k):
        self._cap = cap
        super().__init__(*a, **k)

    def __setitem__(self, key, value):
        if key in self:
            super().__delitem__(key)       # move existing key to the newest end
        super().__setitem__(key, value)
        while len(self) > self._cap:
            self.popitem(last=False)       # drop the oldest


def db_cached(cache, path, read):
    """(path, db_sig) memo over a state-DB read — a poller must not open 50
    sqlite connections per tick when nearly all the DBs are parked. The sig is
    taken BEFORE the read, so a write racing the read can only make the cached
    value newer than its sig — the next poll re-reads; never the stale
    direction. `cache` is the CALLER's dict (each poller keeps its own)."""
    sig = db_sig(path)
    if sig is None:
        return read(path)
    hit = cache.get(path)
    if hit and hit[0] == sig:
        return hit[1]
    val = read(path)
    cache[path] = (sig, val)
    return val


def session_db(row):
    """A sessions() row's state DB path — the live /tmp file when present, else
    its durable park. The ONE owner of that choice: distinct from
    state_db_for(sid), which resolves the same pair from a SID and returns falsy
    when NEITHER exists — a caller that already holds a row wants a usable path
    unconditionally (the list/resume payloads stat it for last_active and read
    stats off it, both of which tolerate an absent file)."""
    sdb = P.state_db(row["log"])
    return sdb if os.path.isfile(sdb) else P.parked_db(row["log"])


def account_usage(limit=50, cache=None):
    """{slug: {"usage": …, "limit_hit": …, "logged_out": …}} — per account, the
    FRESHEST status-line usage snapshot, the freshest rate-limit-hit stamp, and
    the freshest logged-out stamp across the recent sessions (newest `ts` wins;
    each snapshot came from a session running under that account's own token, so
    this is per-account by construction — no API call, no token). Slugs are
    whatever the sessions recorded ('' = the plain-claude default account); the
    caller joins its own registry. `cache` is an optional db_cached() memo
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
    for row in sessions(limit):
        sdb = session_db(row)
        acc, usage, hit, lo = (db_cached(cache, sdb, read) if cache is not None
                               else read(sdb))
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
    known = [k for k in ("five_hour", "seven_day") if k in keys]
    return known + sorted(k for k in keys if k not in ("five_hour", "seven_day"))


def window_span(key):
    """A window key's length in seconds: 5h for the five_hour* family, 7d for
    everything else — model-scoped windows are weekly, like the seven_day pair
    they extend. Only the rolled-over fallback arithmetic uses this (a
    snapshot with a resets_at never needs it)."""
    return FIVE_HOUR_S if key.startswith("five_hour") else SEVEN_DAY_S


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


def session_row(sid):
    """The audit sessions row for a sid (walking the fork chain newest→oldest
    for the first sid that has one), as a dict; None when absent."""
    db = audit_db()
    for s in reversed(sid_chain(sid)):
        rows = _rows(db,
                     "SELECT session_id, cwd, transcript_path, mirror_log,"
                     " started_at, ended_at, end_reason, kitty_window_id"
                     " FROM sessions WHERE session_id=?", (s,))
        if rows:
            sid_, cwd, tpath, mlog, st, en, er, win = rows[0]
            return {"sid": sid_, "cwd": cwd, "transcript_path": tpath,
                    "log": mlog or P.mirror_log(sid_), "started_at": st,
                    "ended_at": en, "end_reason": er, "kitty_window_id": win}
    return None


def state_db_for(sid):
    """The session's state-DB path — live if present, else the parked history
    copy, else ''. Walks the fork chain newest→oldest (after an adoption the
    unified DB lives under the NEWEST sid; old paths are symlinks/dangling).
    Bare exists checks only — never creates either file."""
    for s in reversed(sid_chain(sid)):
        log = P.mirror_log(s)
        live = P.state_db(log)
        if os.path.isfile(live):
            return live
        parked_path = P.parked_db(log)
        if os.path.isfile(parked_path):
            return parked_path
    return ""


# --- the read model -----------------------------------------------------------------

def agents(sid):
    """All subagents/teammates of a session, chain-aware, plus its codex runs
    (codex_runs() below — same row shape, kind 'codex'). The audit `streams`
    rows are the keystone (src_path IS the transcript, end_reason IS the final
    status — 'stop-sentinel', 'stoppedByUser (manual cancel)',
    'parent-task-resolved (rejected)', …; an ended_at of None on the newest row
    means the streamer is still live or died uncleanly), merged with the state
    DB's agents table (desc, done, slot). Sorted by first start."""
    def fold(out, aid, row):
        _, kind, src, st, en, er, lines = row
        rec = out.setdefault(aid, {"agent_id": aid, "kind": kind,
                                   "transcript": src or "", "started_at": st})
        # A restarted (idle-teammate) agent has several stream rows: keep the
        # first start and the newest end/status/transcript.
        rec["ended_at"], rec["end_reason"] = en, er or ""
        rec["tools"] = lines
        if src:
            rec["transcript"] = src
    out = _streams_by(sid, ("subagent", "teammate"),
                      "agent_id, kind, src_path, started_at, ended_at,"
                      " end_reason, lines_emitted",
                      lambda r: r[0], fold)
    sdb = state_db_for(sid)
    if sdb:
        for aid, arec in S.agents_at(sdb).items():
            rec = out.setdefault(aid, {"agent_id": aid, "kind": "",
                                       "transcript": "", "started_at":
                                       arec.get("start_ts")})
            rec["desc"] = arec.get("desc") or ""
            rec["done"] = bool(arec.get("done"))
            rec["slot"] = arec.get("slot")
    for rec in codex_runs(sid):
        out[rec["agent_id"]] = rec   # synthesized ids — can't collide with hook agent_ids
    return sorted(out.values(), key=lambda r: r.get("started_at") or 0)


def codex_aid(src_path):
    """The synthesized agent identity of a codex run — codex tailers record
    no hook agent_id (a run is session/cwd-attributed, docs/codex.md), so the
    read model names one by its stream src_path basename, extension stripped:
    'rollout-<ts>-<uuid>' for a native rollout, the job id for a companion
    log. One owner (here, styleguide table); the codex activity provider
    resolves the same ids back through codex_runs()."""
    return os.path.splitext(os.path.basename(src_path or ""))[0]


def codex_runs(sid):
    """The session's codex runs, chain-aware, from the audit streams keystone
    (kind='codex' — written by the codex tailer's stream_lifecycle) in the
    agents() row shape: agent_id is codex_aid(src_path), desc is the run
    label (the streams task_id: 'cli', 'Review', …), transcript is the run's
    SOURCE file — a native rollout .jsonl (parseable by the codex activity
    provider) or a companion job .log (activity log only; no drill-down).
    A restarted run (several stream rows, one src) merges like a restarted
    teammate: first start, newest end/status."""
    def fold(out, aid, row):
        src, task, st, en, er, lines = row
        rec = out.setdefault(aid, {"agent_id": aid, "kind": "codex",
                                   "transcript": src or "", "started_at": st,
                                   "desc": task or ""})
        rec["ended_at"], rec["end_reason"] = en, er or ""
        rec["tools"] = lines
    out = _streams_by(sid, ("codex",),
                      "src_path, task_id, started_at, ended_at, end_reason,"
                      " lines_emitted",
                      lambda r: codex_aid(r[0]), fold)
    # Drop the STANDALONE host's OWN run. A codex running on its own writes its
    # session transcript AS a rollout (uuid == sid), and the standalone watcher
    # streams that very rollout under kind='codex' — so it lands here as a "run".
    # But it is the SESSION itself, not a nested sidecar: a standalone run's ops
    # are UNSTAMPED (codex is the main agent there), so listing it as an agent
    # mints a clickable card whose scope — {codex:<label>} — matches no op, and
    # clicking it yields an EMPTY mirror (the self-run empty-scope bug,
    # docs/codex.md). Its rollout IS the session's own transcript, which is the
    # tell. A SIDECAR codex run (inside a Claude host) has a different transcript
    # from the Claude session and is kept.
    own = (session_row(sid) or {}).get("transcript_path") or ""
    if own:
        out = {aid: r for aid, r in out.items()
               if (r.get("transcript") or "") != own}
    return sorted(out.values(), key=lambda r: r.get("started_at") or 0)


# --- nested-job ownership -----------------------------------------------------------
# How long a session's owner map is trusted before it is rebuilt. It only changes
# when a NEW job/monitor launches, and every reader below is a per-tick badge or a
# tab fetch, so a few seconds of staleness costs at most one late row while saving a
# hook_events scan on every SSE tick.
OWNERS_TTL_S = 5.0
_OWNERS = BoundedLRU(512)            # sid -> (expires_at, map)


def nested_owners(sid):
    """Who launched each of a session's background jobs and monitors:
    `{task_id: {"agent_id", "tool_use_id", "command", "description"}}`,
    chain-aware. The ONE owner of that fact for the read model — jobs(),
    monitor_streams() and the counts all resolve through here.

    The authoritative source is the tailer's own audit `streams.agent_id`
    (hookkit.stream_env's CLAUDE_STREAM_AGENT). This map is the SECOND source,
    and it exists for two reasons the stream row cannot serve:

      * HISTORY. Every stream row written before the stamp carries agent_id ''
        whoever launched it, so a parked session could never be partitioned.
      * The missing COMMAND. A subagent's bg job paints its `code` op under the
        tool_use_id while its stream row is keyed by the backgroundTaskId, so
        core.copy.group_commands misses and the job rendered with a blank
        command; a subagent's MONITOR is absent from the main transcript
        entirely, so plugins.monitors had no command for it either.

    Both are recoverable from `hook_events`, whose PostToolUse payload carries
    agent_id, the task id, the tool_use_id and the command TOGETHER. Extraction
    runs in SQLite (json_extract) rather than Python so a busy session's large
    payloads are never pulled across — only the six small columns."""
    now = time.time()
    hit = _OWNERS.get(sid)
    if hit and hit[0] > now:
        return hit[1]
    chain = sid_chain(sid)
    q = ("SELECT agent_id,"
         " json_extract(payload,'$.tool_response.backgroundTaskId'),"
         " json_extract(payload,'$.tool_response.taskId'),"
         " json_extract(payload,'$.tool_use_id'),"
         " json_extract(payload,'$.tool_input.command'),"
         " json_extract(payload,'$.tool_input.description')"
         " FROM hook_events WHERE hook='PostToolUse' AND tool_name IN ('Bash','Monitor')"
         " AND session_id IN (%s) ORDER BY id" % _in_clause(len(chain)))
    out = {}
    for aid, btid, mtid, tuid, cmd, desc in _rows(audit_db(), q, tuple(chain)):
        task = btid or mtid
        if not task:
            continue                      # a plain foreground call — no nested stream
        out[task] = {"agent_id": aid or "", "tool_use_id": tuid or "",
                     "command": cmd or "", "description": desc or ""}
    _OWNERS[sid] = (now + OWNERS_TTL_S, out)
    return out


def _owner_of(task, stream_aid, owners):
    """The owning agent id of one nested task ("" = the lead agent's own).
    The stream row wins when it is stamped; the owner map answers for the
    history that predates the stamp (see nested_owners)."""
    if stream_aid:
        return stream_aid
    return (owners.get(task) or {}).get("agent_id", "")


def _agent_match(owner, agent):
    """Does a row owned by `owner` belong in a view scoped to `agent`?
    `agent` None = no scoping (everything); "" = LEAD ONLY, the session-level
    view (docs/dashboard.md *Agent scope*); an id = exactly that agent, so a
    teammate's jobs never show under a subagent."""
    return agent is None or owner == agent


def _nested_count(sid, kind, agent):
    """Distinct nested tasks of one `kind` owned by `agent` — the shared body of
    job_count()/monitor_count() once a scope is asked for. `agent is None` (count
    everything) is the caller's fast path: it needs no ownership at all and stays
    on the pure-SQL _stream_count."""
    def fold(out, task, row):
        out[task] = row[1] or ""         # newest row wins, as everywhere else
    rows = _streams_by(sid, (kind,), "task_id, agent_id", lambda r: r[0], fold)
    owners = nested_owners(sid) if rows else {}
    return sum(1 for task, aid in rows.items()
               if _agent_match(_owner_of(task, aid, owners), agent))


def monitor_streams(sid):
    """The audit `streams` lifecycle rows for a session's MONITORS (kind
    'monitor'), chain-aware, keyed by task_id: {task: {started_at, ended_at,
    end_reason, lines, pid, agent_id, live}}. Several rows per task (a re-latched
    tailer / a resumed session) merge like agents(): keep the FIRST start and the
    NEWEST end/status. `live` is the newest row's `ended_at` being None (still
    tailing, or the streamer died uncleanly). This is the STATE half of the
    monitors read-model — the transcript (plugins.monitors) owns command/events;
    streams own start/end/liveness (the same keystone agents() reads).

    `agent_id` is resolved through nested_owners, so a monitor an AGENT launched
    is attributed even on a session whose stream rows predate the stamp; the
    owner map's `command`/`description` ride along too, because the main
    transcript plugins.monitors parses never saw that launch."""
    def fold(out, task, row):
        _, aid, pid, st, en, er, lines = row
        rec = out.setdefault(task, {"started_at": st, "agent_id": aid or ""})
        rec["ended_at"], rec["end_reason"] = en, er or ""
        rec["lines"], rec["pid"] = lines, pid
        rec["live"] = en is None
    out = _streams_by(sid, ("monitor",),
                      "task_id, agent_id, pid, started_at, ended_at,"
                      " end_reason, lines_emitted",
                      lambda r: r[0], fold)
    if out:
        owners = nested_owners(sid)
        for task, rec in out.items():
            own = owners.get(task) or {}
            rec["agent_id"] = _owner_of(task, rec.get("agent_id") or "", owners)
            rec["command"] = own.get("command", "")
            rec["description"] = own.get("description", "")
    return out


def monitor_count(sid, agent=""):
    """The distinct-monitor COUNT for a session (chain-aware) — the cheap twin of
    plugins.monitors() for the monitors tab's badge, so the per-session overview/SSE
    can show it without reading the whole transcript on every tick. `agent` scopes
    it exactly as monitors are listed: "" (the default) counts the LEAD's monitors
    only, an id counts that agent's, None counts every one."""
    if agent is None:
        return _stream_count(sid, "monitor")
    return _nested_count(sid, "monitor", agent)


def jobs(sid, agent=None):
    """Background Bash jobs of a session (run_in_background launches + Ctrl+B
    conversions), chain-aware, from the audit `streams` keystone (kind='bg',
    task_id=backgroundTaskId) merged with the mirror OPS: each job's COMMAND is
    the `code` op of its copy-group (core.copy.group_commands — the job's taskId
    IS its op group `g`). Row shape mirrors agents()/the monitors read model:
    {task, command, group, agent_id, started_at, ended_at, end_reason, live,
    lines}. The full OUTPUT is deliberately NOT carried here (a build log can be
    huge) — the drill-down reads it on demand from the same ops via the
    /copy/<group>/out endpoint (core.copy.collect). `live` is the newest streams
    row's ended_at being None. Sorted by first start.

    `agent` scopes the list: None = every job, "" = the LEAD's own only (what the
    session-level Jobs tab shows), an id = exactly that agent's.

    An AGENT's job needs both fallbacks below. Its command op is painted under the
    tool_use_id (the substream's block group), not the backgroundTaskId this row is
    keyed by, so group_commands misses and nested_owners supplies the command; and
    `group` carries that tool_use_id so the drill-down's output fetch has a group
    that actually exists. A converted (Ctrl+B) job's command op likewise lives in
    its foreground group."""
    def fold(out, task, row):
        _, aid, pid, st, en, er, lines = row
        rec = out.setdefault(task, {"task": task, "started_at": st, "command": "",
                                    "agent_id": aid or "", "group": task})
        rec["ended_at"], rec["end_reason"] = en, er or ""
        rec["lines"], rec["pid"] = lines, pid
        rec["live"] = en is None
    out = _streams_by(sid, ("bg",),
                      "task_id, agent_id, pid, started_at, ended_at, end_reason,"
                      " lines_emitted",
                      lambda r: r[0], fold)
    owners = nested_owners(sid) if out else {}
    for task, rec in out.items():
        rec["agent_id"] = _owner_of(task, rec.get("agent_id") or "", owners)
    out = {t: r for t, r in out.items() if _agent_match(r["agent_id"], agent)}
    sdb = state_db_for(sid)
    if sdb and out:
        from core import copy as CP
        cmds = CP.group_commands(sdb, set(out))
        for task, rec in out.items():
            rec["command"] = cmds.get(task, "")
    for task, rec in out.items():
        own = owners.get(task) or {}
        if not rec["command"]:
            rec["command"] = own.get("command", "")
            if own.get("tool_use_id"):
                rec["group"] = own["tool_use_id"]     # where the ops actually live
    return sorted(out.values(), key=lambda r: r.get("started_at") or 0)


def job_count(sid, agent=""):
    """The distinct background-job COUNT for a session (chain-aware) — the cheap
    twin of jobs() for the jobs tab's badge (audit `streams` kind='bg', no ops
    read), so the per-session overview/SSE can show it per-tick. `agent` scopes it
    exactly as jobs() lists: "" (the default) counts the LEAD's jobs only, an id
    counts that agent's, None counts every one."""
    if agent is None:
        return _stream_count(sid, "bg")
    return _nested_count(sid, "bg", agent)


def _memory_list(sid, key):
    """One list of the `memory` kv, newest-first — the shared body of memory() and
    memory_searches() (the kv holds both halves under one key, so reading it twice
    would be two loads of the same json for one payload)."""
    sdb = state_db_for(sid)
    if not sdb:
        return []
    stash = kv_at(sdb, "memory")
    rows = stash.get(key) if isinstance(stash, dict) else None
    if not isinstance(rows, list):
        return []
    return sorted((r for r in rows if isinstance(r, dict)),
                  key=lambda r: r.get("ts") or 0, reverse=True)


def memory(sid):
    """The memory-wiki notes a session touched — the `memory` kv's `files` list,
    stashed on every op under ~/wiki/01 by the file formatters (a Read/Write/Edit
    tool call) and by the Bash formatters (a `cat`/`find -exec cat`/`qmd get` of a
    note — plugins.claude_code.memory.record via memcmd), and surviving park. A
    list of {path, name, verb, agent, count, ts} (verb ∈ Read/Update/Write, agent
    None = main), newest-touch first; [] when the session touched no memory.
    Team-wide (main agent AND subagents, unlike the main-agent-only mirror).
    Read-only (kv_at, live-or-parked path)."""
    return _memory_list(sid, "files")


def memory_searches(sid):
    """The vault SEARCHES a session ran — the `memory` kv's `searches` list
    (plugins.claude_code.memory.record_search, fed by memcmd's qmd parsing), newest
    first. A list of {kind, sub, query, cmd, expanded, hits, agent, count, ts},
    where `hits` are the parsed result rows ({path, rel, name, line, title, score,
    snippet}) — the Memory tab's search cards. The OTHER half of recall: a `qmd
    query` opens no note, so memory() above can't see it, yet it is exactly the
    moment the session asked memory a question."""
    return _memory_list(sid, "searches")


def memory_count(sid):
    """The Memory tab's badge number — notes touched PLUS searches run. Both, and
    not just the notes, because a session that only SEARCHED the vault (a very
    ordinary shape: ask qmd, read the ranked passages, act) would otherwise wear a
    0 badge over a tab with content in it. The cheap twin of memory() +
    memory_searches() (the kv is small, so this just len()s them; the separate
    entry keeps the per-tick SSE symmetric with jobs/errors)."""
    return len(memory(sid)) + len(memory_searches(sid))


# The stream KINDS whose `src_path` is an agent's TRANSCRIPT. Every other kind
# an agent owns (its bg jobs, its monitors, its foreground commands) now carries
# that agent's id too — `hookkit.stream_env(agent=)`, the attribution agent scope
# is built on — and an `fg` row's src_path is the command's tee file. So "the
# agent's newest stream" stopped meaning "the agent's transcript" the moment
# nested tailers were attributed, and the query has to say which kinds it means.
TRANSCRIPT_KINDS = ("subagent", "teammate")


def agent_transcript(sid, agent_id):
    """The transcript path for one agent — the newest TRANSCRIPT-bearing streams
    row's src_path ('' when the audit never saw a streamer for it; the caller
    then falls back to the subagents/ layout derivation).

    Kind-filtered, and that is load-bearing: unfiltered, the newest row for a
    busy agent is one of its own foreground commands, whose src_path is a
    `.subfg.<tid>.out` tee file. `transcript.agent_path` then failed its isfile
    test and answered None — so an agent that had run a shell command lost its
    WHOLE conversation in agent scope (no brief, no messages, no result: ops
    only), while one that had only run background jobs was fine, because a bg
    row's src_path is empty and the layout fallback caught it."""
    chain = sid_chain(sid)
    q = ("SELECT src_path FROM streams WHERE agent_id=? AND session_id IN (%s)"
         " AND kind IN (%s) ORDER BY started_at DESC LIMIT 1"
         % (_in_clause(len(chain)), _in_clause(len(TRANSCRIPT_KINDS))))
    rows = _rows(audit_db(), q, (agent_id, *chain, *TRANSCRIPT_KINDS))
    return (rows[0][0] or "") if rows else ""


def costs(sid):
    """OTEL cost/token totals, chain-aware (pre-fork datapoints live under the
    OLD sid). Same ground truth as the audit CLI's otel breakdown: SUM(value)
    over the raw datapoints — {"tokens": {query_source: {type: n}},
    "cost": {query_source: usd}, "total_usd": x}."""
    chain = sid_chain(sid)
    db = audit_db()
    ins = _in_clause(len(chain))
    tokens = {}
    for qs, typ, n in _rows(
            db, "SELECT query_source, type, SUM(value) FROM otel"
                " WHERE session_id IN (%s) AND metric='token'"
                " GROUP BY query_source, type" % ins, tuple(chain)):
        tokens.setdefault(qs or "?", {})[typ or "?"] = n or 0
    cost = {}
    for qs, usd in _rows(
            db, "SELECT query_source, SUM(value) FROM otel"
                " WHERE session_id IN (%s) AND metric='cost'"
                " GROUP BY query_source" % ins, tuple(chain)):
        cost[qs or "?"] = usd or 0.0
    return {"tokens": tokens, "cost": cost,
            "total_usd": sum(cost.values())}


def running(sid):
    """What is EXECUTING under a session right now, grouped by kind — the read
    model over the state DB's `live` slot table (core/slots.py, S.live_at). It
    resolves state_db_for(sid) (live or parked — a parked session's rows are all
    dead) and keeps only rows whose owning pid is still alive, grouped by kind:
    {kind: [row, ...]} (kinds 'fg'/'bg'/'monitor'/'sub.pid'; the pid-less
    colour-mapping '<k>.id' rows never survive the alive filter). Empty dict when
    nothing is live. The dashboard's "running now" ribbon renders one chip per
    row; a parked session yields {}. A pure reader — never steals a stale slot."""
    sdb = state_db_for(sid)
    out = {}
    if not sdb:
        return out
    for row in S.live_at(sdb):
        if row.get("alive"):
            out.setdefault(row["kind"], []).append(row)
    return out


def fg_running(sid):
    """The main session's IN-FLIGHT foreground command as {"g", "start_ts"}, or
    None when nothing is executing — the block-level twin of running() (which
    answers "is an fg slot alive" but, being slot-keyed, can't say WHICH mirror
    block it belongs to). The source is the take-once `fg-live` hand-off record
    claude-cmd-pre.py writes when it spawns the live tailer: its `tid` IS the
    block's copy-group id (the tool_use_id stamped on the ▶ foreground header
    ops) and its `ts` the command's start. PostToolUse consumes the record, so
    its mere presence is the "still running" signal — with the owning tailer's
    pid_alive() as the backstop for the cancel case that fires no hook at all
    (an abandoned record whose tailer died reads as not-running, exactly as
    cmd_pre's own staleness check treats it). The dashboard ticks a live elapsed
    chip on that block from start_ts; a parked session yields None. A pure
    reader — it PEEKS, never takes (consuming here would strand the real
    consumer's finish chip)."""
    sdb = state_db_for(sid)
    if not sdb:
        return None
    rec = S.hand_peek_at(sdb, "fg-live")
    if not isinstance(rec, dict):
        return None
    gid, ts, pid = rec.get("tid"), rec.get("ts"), rec.get("pid")
    if not gid or not ts:
        return None                 # a pre-`ts` producer's record — nothing to tick
    if not (pid and S.pid_alive(pid)):
        return None                 # abandoned record (cancelled command, dead tailer)
    return {"g": gid, "start_ts": ts}


def errors(sid):
    """Swallowed-exception rows for a session (chain-aware), oldest first —
    the same evidence errors-CLI/errwatch surface, as dicts."""
    chain = sid_chain(sid)
    q = ("SELECT id, ts, script, func, traceback, context FROM errors"
         " WHERE session_id IN (%s) ORDER BY id" % _in_clause(len(chain)))
    return [{"id": i, "ts": ts, "script": sc, "func": fn,
             "traceback": tb, "context": ctx}
            for i, ts, sc, fn, tb, ctx in _rows(audit_db(), q, tuple(chain))]


def error_count(sid):
    """The swallowed-exception COUNT for a session (chain-aware) — the cheap
    twin of errors() for the dashboard's ⚠ badge, which polls it every few
    seconds and must not haul every full traceback just to show a number. Same
    scope as errors() (the fork chain; NOT errwatch's global-rows-included chip
    — the badge is the web sibling of that chip but tracks this session)."""
    chain = sid_chain(sid)
    q = ("SELECT COUNT(*) FROM errors WHERE session_id IN (%s)"
         % _in_clause(len(chain)))
    rows = _rows(audit_db(), q, tuple(chain))
    return int(rows[0][0]) if rows else 0


def account(sid):
    """The subscription account a session runs under — {slug, label} stamped
    into the state DB at SessionStart / refreshed by the status-line shim
    (plugins.claude_code.account / statusline). {} when unknown (an old
    session, or the plain default account with no slug). Reads the RESOLVED
    path (live or parked), so a parked session keeps its label."""
    sdb = state_db_for(sid)
    return (S.kv_at(sdb, "account") or {}) if sdb else {}


def usage(sid):
    """The session's last-seen rate-limit snapshot — {five_hour, five_hour_reset,
    seven_day, seven_day_reset, ts} — captured from the status-line stdin by the
    shim (docs/dashboard.md). None when none has been captured (no shim, a fresh
    account before its first API response, an old session). Per-account by
    construction: the number came from THIS session's own token."""
    sdb = state_db_for(sid)
    return (S.kv_at(sdb, "usage") or None) if sdb else None


def session(sid):
    """One session's overview: identity + fork chain + liveness + scoreboard
    stats (live or parked) + agents + tab state + cost totals + account +
    usage."""
    row = session_row(sid) or {}
    chain = sid_chain(sid)
    log = row.get("log") or P.mirror_log(chain[-1])
    sdb = state_db_for(sid)
    win = row.get("kitty_window_id") or ""
    return {"sid": sid, "chain": chain, "cwd": row.get("cwd") or "",
            "transcript_path": row.get("transcript_path") or "",
            "log": log, "started_at": row.get("started_at"),
            "ended_at": row.get("ended_at"),
            "end_reason": row.get("end_reason") or "",
            "live": os.path.isfile(P.state_db(log)),
            "state_db": sdb,
            "stats": S.stats_at(sdb) if sdb else {},
            "agents": agents(sid),
            "tab": tab_state(win) if win else "",
            "account": account(sid), "usage": usage(sid),
            "costs": costs(sid)}


# --- cross-session aggregates (the dashboard Stats / Insights page) ---------------

def activity_stats(heatmap_days=371):
    """Whole-corpus activity aggregates for the dashboard's GitHub-Insights-style
    Stats page (dashboard.stats_payload → GET /api/stats). Computed with a handful
    of GROUP BYs over the audit `sessions`/`otel`/`errors` tables — the DURABLE
    cross-session record (per-session state DBs get parked; the audit is the only
    all-history source). The unit is a session (the "commit" analog).

    Returns raw arrays; the client does the heatmap bucketing and per-window prose,
    and the server composer (stats_payload) does the per-project grouping. Unlike
    the sid-keyed reads here, this deliberately does NO sid_chain resolution: these
    are whole-corpus SUM/COUNTs where each row/datapoint is already counted exactly
    once — a forked sid's tokens are attributed under whichever `sessions` row
    adopt.py wrote (the fork's own or its predecessor's), which is correct for a
    corpus total. All reads are mode=ro; a missing audit DB yields empty arrays.

      {
        "generated_at": epoch,
        "total_sessions": n,
        "daily":   [["YYYY-MM-DD", count], ...],   # last heatmap_days, localtime
        "punch":   [[dow, hour, count], ...],        # dow 0=Sun; <=168 triples
        "sessions": [{sid, start_cwd, started_at, ended_at, tokens, cost, errors}, ...],
      }
    """
    db = audit_db()
    now = time.time()
    scwd = "start_cwd" if _sessions_has_start_cwd(db) else "cwd"
    # per-session token + cost totals in TWO grouped passes folded in Python —
    # one query each instead of one per session (the otel table is indexed on
    # (session_id, ts), so these are cheap).
    tok, cost = {}, {}
    for sid, n in _rows(db, "SELECT session_id, SUM(value) FROM otel"
                            " WHERE metric='token' GROUP BY session_id"):
        tok[sid] = n or 0
    for sid, usd in _rows(db, "SELECT session_id, SUM(value) FROM otel"
                              " WHERE metric='cost' GROUP BY session_id"):
        cost[sid] = usd or 0.0
    err = {}
    for sid, n in _rows(db, "SELECT session_id, COUNT(*) FROM errors"
                            " GROUP BY session_id"):
        err[sid] = n or 0
    rows = [{"sid": sid, "start_cwd": sc or "", "started_at": st, "ended_at": en,
             "tokens": tok.get(sid, 0), "cost": cost.get(sid, 0.0),
             "errors": err.get(sid, 0)}
            for sid, sc, st, en in _rows(
                db, "SELECT session_id, " + scwd + ", started_at, ended_at"
                    " FROM sessions ORDER BY started_at DESC")]
    cut = now - heatmap_days * 86400
    daily = [[d, c] for d, c in _rows(
        db, "SELECT date(started_at,'unixepoch','localtime') d, COUNT(*)"
            " FROM sessions WHERE started_at >= ? GROUP BY d ORDER BY d", (cut,))]
    punch = [[int(dow), int(hr), c] for dow, hr, c in _rows(
        db, "SELECT strftime('%w', started_at, 'unixepoch', 'localtime') dow,"
            " strftime('%H', started_at, 'unixepoch', 'localtime') hr, COUNT(*)"
            " FROM sessions WHERE started_at IS NOT NULL GROUP BY dow, hr")]
    return {"generated_at": now, "total_sessions": len(rows),
            "daily": daily, "punch": punch, "sessions": rows}
