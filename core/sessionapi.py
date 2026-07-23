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


def _session_db(row):
    """A sessions() row's state DB path — the live /tmp file when present,
    else its durable park."""
    sdb = P.state_db(row["log"])
    return sdb if os.path.isfile(sdb) else P.parked_db(row["log"])


def account_usage(limit=50, cache=None):
    """{slug: {"usage": …, "limit_hit": …}} — per account, the FRESHEST
    status-line usage snapshot and the freshest rate-limit-hit stamp across
    the recent sessions (newest `ts` wins; each snapshot came from a session
    running under that account's own token, so this is per-account by
    construction — no API call, no token). Slugs are whatever the sessions
    recorded ('' = the plain-claude default account); the caller joins its own
    registry. `cache` is an optional db_cached() memo dict."""
    def read(p):
        return (S.kv_at(p, "account") or {}, S.kv_at(p, "usage"),
                S.kv_at(p, "limit-hit"))
    def file_under(best, slug, key, val):
        ent = best.setdefault(slug, {"usage": None, "limit_hit": None})
        if val and (ent[key] is None
                    or (val.get("ts") or 0) > (ent[key].get("ts") or 0)):
            ent[key] = val
    best = {}
    for row in sessions(limit):
        sdb = _session_db(row)
        acc, usage, hit = (db_cached(cache, sdb, read) if cache is not None
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
    chain = sid_chain(sid)
    out = {}
    q = ("SELECT agent_id, kind, src_path, started_at, ended_at, end_reason,"
         " lines_emitted FROM streams WHERE kind IN ('subagent','teammate')"
         " AND session_id IN (%s) ORDER BY started_at" % _in_clause(len(chain)))
    for aid, kind, src, st, en, er, lines in _rows(audit_db(), q, tuple(chain)):
        if not aid:
            continue
        rec = out.setdefault(aid, {"agent_id": aid, "kind": kind,
                                   "transcript": src or "", "started_at": st})
        # A restarted (idle-teammate) agent has several stream rows: keep the
        # first start and the newest end/status/transcript.
        rec["ended_at"], rec["end_reason"] = en, er or ""
        rec["tools"] = lines
        if src:
            rec["transcript"] = src
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
    chain = sid_chain(sid)
    out = {}
    q = ("SELECT src_path, task_id, started_at, ended_at, end_reason,"
         " lines_emitted FROM streams WHERE kind='codex'"
         " AND session_id IN (%s) ORDER BY started_at" % _in_clause(len(chain)))
    for src, task, st, en, er, lines in _rows(audit_db(), q, tuple(chain)):
        aid = codex_aid(src)
        if not aid:
            continue
        rec = out.setdefault(aid, {"agent_id": aid, "kind": "codex",
                                   "transcript": src or "", "started_at": st,
                                   "desc": task or ""})
        rec["ended_at"], rec["end_reason"] = en, er or ""
        rec["tools"] = lines
    return sorted(out.values(), key=lambda r: r.get("started_at") or 0)


def monitor_streams(sid):
    """The audit `streams` lifecycle rows for a session's MONITORS (kind
    'monitor'), chain-aware, keyed by task_id: {task: {started_at, ended_at,
    end_reason, lines, pid, agent_id, live}}. Several rows per task (a re-latched
    tailer / a resumed session) merge like agents(): keep the FIRST start and the
    NEWEST end/status. `live` is the newest row's `ended_at` being None (still
    tailing, or the streamer died uncleanly). This is the STATE half of the
    monitors read-model — the transcript (plugins.monitors) owns command/events;
    streams own start/end/liveness (the same keystone agents() reads)."""
    chain = sid_chain(sid)
    q = ("SELECT task_id, agent_id, pid, started_at, ended_at, end_reason,"
         " lines_emitted FROM streams WHERE kind='monitor'"
         " AND session_id IN (%s) ORDER BY started_at" % _in_clause(len(chain)))
    out = {}
    for task, aid, pid, st, en, er, lines in _rows(audit_db(), q, tuple(chain)):
        if not task:
            continue
        rec = out.setdefault(task, {"started_at": st, "agent_id": aid or ""})
        rec["ended_at"], rec["end_reason"] = en, er or ""
        rec["lines"], rec["pid"] = lines, pid
        rec["live"] = en is None
    return out


def monitor_count(sid):
    """The distinct-monitor COUNT for a session (chain-aware) — the cheap twin of
    plugins.monitors() for the monitors tab's badge, from the audit `streams`
    keystone alone (no transcript parse), so the per-session overview/SSE can show
    it without reading the whole transcript on every tick."""
    chain = sid_chain(sid)
    q = ("SELECT COUNT(DISTINCT task_id) FROM streams WHERE kind='monitor'"
         " AND session_id IN (%s)" % _in_clause(len(chain)))
    rows = _rows(audit_db(), q, tuple(chain))
    return int(rows[0][0]) if rows else 0


def jobs(sid):
    """Background Bash jobs of a session (run_in_background launches + Ctrl+B
    conversions), chain-aware, from the audit `streams` keystone (kind='bg',
    task_id=backgroundTaskId) merged with the mirror OPS: each job's COMMAND is
    the `code` op of its copy-group (core.copy.group_commands — the job's taskId
    IS its op group `g`). Row shape mirrors agents()/the monitors read model:
    {task, command, started_at, ended_at, end_reason, live, lines}. The full
    OUTPUT is deliberately NOT carried here (a build log can be huge) — the
    drill-down reads it on demand from the same ops via the /copy/<task>/out
    endpoint (core.copy.collect). `live` is the newest streams row's ended_at
    being None. Sorted by first start. A converted (Ctrl+B) job's command op
    lives in its foreground group, so `command` may be blank there — the card
    falls back to the taskId."""
    chain = sid_chain(sid)
    q = ("SELECT task_id, pid, started_at, ended_at, end_reason, lines_emitted"
         " FROM streams WHERE kind='bg' AND session_id IN (%s) ORDER BY started_at"
         % _in_clause(len(chain)))
    out = {}
    for task, pid, st, en, er, lines in _rows(audit_db(), q, tuple(chain)):
        if not task:
            continue
        rec = out.setdefault(task, {"task": task, "started_at": st, "command": ""})
        rec["ended_at"], rec["end_reason"] = en, er or ""
        rec["lines"], rec["pid"] = lines, pid
        rec["live"] = en is None
    sdb = state_db_for(sid)
    if sdb and out:
        from core import copy as CP
        cmds = CP.group_commands(sdb, set(out))
        for task, rec in out.items():
            rec["command"] = cmds.get(task, "")
    return sorted(out.values(), key=lambda r: r.get("started_at") or 0)


def job_count(sid):
    """The distinct background-job COUNT for a session (chain-aware) — the cheap
    twin of jobs() for the jobs tab's badge (audit `streams` kind='bg', no ops
    read), so the per-session overview/SSE can show it per-tick."""
    chain = sid_chain(sid)
    q = ("SELECT COUNT(DISTINCT task_id) FROM streams WHERE kind='bg'"
         " AND session_id IN (%s)" % _in_clause(len(chain)))
    rows = _rows(audit_db(), q, tuple(chain))
    return int(rows[0][0]) if rows else 0


def memory(sid):
    """The memory-wiki notes a session touched — the `memory` kv the file
    formatter / substream stash on every op under ~/wiki/01 (plugins.claude_code
    .memory.record), which survives park. A list of {path, name, verb, agent,
    count, ts} (verb ∈ Read/Update/Write, agent None = main), newest-touch first;
    [] when the session touched no memory. Team-wide (main agent AND subagents,
    unlike the main-agent-only mirror). Read-only (kv_at, live-or-parked path)."""
    sdb = state_db_for(sid)
    if not sdb:
        return []
    stash = kv_at(sdb, "memory")
    files = stash.get("files") if isinstance(stash, dict) else None
    if not isinstance(files, list):
        return []
    return sorted((f for f in files if isinstance(f, dict)),
                  key=lambda f: f.get("ts") or 0, reverse=True)


def memory_count(sid):
    """The distinct memory-note COUNT for a session — the cheap twin of memory()
    for the Memory tab's badge (the kv is small, so this just len()s it, but the
    separate entry keeps the per-tick SSE symmetric with jobs/errors)."""
    return len(memory(sid))


def agent_transcript(sid, agent_id):
    """The transcript path for one agent — the newest streams row's src_path
    ('' when the audit never saw a streamer for it; plugins.activity() then
    falls back to the layout derivation)."""
    chain = sid_chain(sid)
    q = ("SELECT src_path FROM streams WHERE agent_id=? AND session_id IN (%s)"
         " ORDER BY started_at DESC LIMIT 1" % _in_clause(len(chain)))
    rows = _rows(audit_db(), q, (agent_id, *chain))
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
