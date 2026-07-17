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
    DBs the audit never saw (audit disabled at the time) as minimal rows."""
    out, seen = [], set()
    for sid, cwd, tpath, mlog, st, en, er, win in _rows(
            audit_db(),
            "SELECT session_id, cwd, transcript_path, mirror_log, started_at,"
            " ended_at, end_reason, kitty_window_id FROM sessions"
            " ORDER BY started_at DESC LIMIT ?",
            (limit,)):
        log = mlog or P.mirror_log(sid)
        seen.add(P.sid_from_log(log))
        out.append({"sid": sid, "cwd": cwd, "transcript_path": tpath, "log": log,
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
        out.append({"sid": key, "cwd": "", "transcript_path": "", "log": log,
                    "started_at": None, "ended_at": None, "end_reason": "",
                    "kitty_window_id": "",
                    "live": os.path.isfile(P.state_db(log)), "parked": True})
    return out


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


def session(sid):
    """One session's overview: identity + fork chain + liveness + scoreboard
    stats (live or parked) + agents + tab state + cost totals."""
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
            "costs": costs(sid)}
