# dashboard/read/lists.py — the OVERVIEW read payloads.
#
# What the list page and its strips show ACROSS sessions: the sessions list
# (enriched per row), the resume picker, the accounts strip, and the Stats page
# aggregates. All read-only + memoized (read/cache.py); the per-row metadata
# comes from read/meta.py and the live-window truth from control/launch.py.
import json
import os
import time

import plugins
from core import sessionapi as API
from dashboard import config
from dashboard.config import (RESUMABLE_SCAN, SESSIONS_LIMIT, STATS_TOP_PROJECTS)
from dashboard.control import launch
from dashboard.read.cache import MEMO_CAP, _db_cached, ttl_cached
from dashboard.read.meta import (canon_cwd, git_info, session_ctx,
                                 session_effort, session_title, group_dir,
                                 session_slug)


def _last_active(row, sdb):
    """The session's last-activity timestamp, for the list card's recency
    chip: the transcript's mtime (the file grows on every turn — the same
    activity signal interrupt-watch and escape-recheck trust), else the audit
    ended_at, else the state DB's mtime (the audit-less minimal parked rows
    carry no transcript path), else started_at. Why not started_at directly:
    an unlabeled "1h ago" reads as staleness, and a live session an hour into
    its work showed exactly that while actively streaming. Why not the audit
    hook_events MAX(ts): a per-row query against the big audit DB per tick vs
    one stat on a path the row already carries — and the audit can be off."""
    tpath = row.get("transcript_path") or ""
    if tpath:
        try:
            return os.path.getmtime(tpath)
        except OSError:
            pass
    if row.get("ended_at"):
        return row["ended_at"]
    try:
        return os.path.getmtime(sdb)
    except OSError:
        return row.get("started_at")


_STATS = API.BoundedLRU(MEMO_CAP)  # state-db path -> (sig, stats): the list poll
#                   must not open
#                   50 sqlite connections per tick — parked DBs never change
#                   and idle live ones change rarely. The sig is _db_sig (DB
#                   file AND -wal stat), not (path, size): a live writer
#                   appends to the WAL without touching the main file until
#                   checkpoint, so the main file's stat alone would serve
#                   stale numbers for exactly the sessions that move.

_ACCT = API.BoundedLRU(MEMO_CAP)  # state-db path -> (sig, (account kv, usage
#                   kv)): same
#                   _db_sig idea — the accounts strip re-scans the same 50
#                   session DBs per fetch, nearly all parked.



def sessions_payload():
    """The sessions list, enriched with what the list view shows per row:
    scoreboard stats (read-only, live or parked), the tab state, the
    display title (plugins.session_title over the transcript), and
    `last_active` (the recency chip / group order / archive boundary —
    _last_active). `live` is
    corrected to require an OPEN tab (see live_windows): a session whose state
    DB lingers but whose tab is gone (closed without a SessionEnd — crash/kill,
    or a leaked DB) is demoted to not-live so it can't masquerade as running."""
    tabstates = API.tab_states()
    live_wins = launch.live_windows()
    out = []
    for row in API.sessions(SESSIONS_LIMIT):
        sdb = API.session_db(row)      # the live DB, else its durable park
        # demote a state-DB-live session whose window is gone (launch.demote_if_dead
        # is the single owner of the check; docs/dashboard.md *Liveness*).
        launch.demote_if_dead(row, live_wins)
        # Reconcile the window id to the SAME live-RESOLVED value session_payload
        # serves (from live_windows), so the two endpoints never disagree. The
        # list used to return the RAW start-time audit id while /api/session
        # returns the resolved id (blank until the pane is tagged claude_session=
        # <sid>) — two different id-spaces. The client's updateHeadFromList
        # compared them, so during a launch's tag-race the mismatch read as a
        # spurious "window moved" and rebuilt the header every list tick,
        # FLICKERING the action-buttons row on/off 2–3× (reported 2026-07-24).
        # For a live row the resolved id (or "" mid-tag-race) IS the current
        # window — it also gates the card ✕ close correctly (only once tagged).
        # A not-live row keeps its raw id (the client never compares a parked
        # window, and the demotion check above needed the raw id to run first).
        if row.get("live"):
            row["kitty_window_id"] = (live_wins or {}).get(row["sid"], "")
        st = _db_cached(_STATS, sdb, API.stats_at)
        row["stats"] = st
        row["tab"] = tabstates.get(str(row.get("kitty_window_id") or "")) or ""
        row["title"] = session_title(row.get("transcript_path") or "")
        row["ctx"] = session_ctx(row.get("transcript_path") or "", main=True)
        row["cwd"] = canon_cwd(row.get("cwd") or "")   # collapse the /kitty symlink
        row["git"] = git_info(row["cwd"])              # chips: the LIVE location
        # what the list GROUPS on: the frozen ORIGINAL cwd (start_cwd) resolved
        # to its linked-worktree owner. Distinct from row.git (live cwd) on
        # purpose — so worktrees still aggregate under their main checkout AND a
        # mid-session cd never moves a card between groups.
        row["group_dir"] = group_dir(
            canon_cwd(row.get("start_cwd") or "") or row["cwd"])
        row["last_active"] = _last_active(row, sdb)
        out.append(row)
    return out


def dir_live_sessions(key):
    """The live sessions whose list-page group key equals `key` — the SAME
    grouping app.js groupSessions does (group_dir || cwd || ""), over the SAME
    window-corrected `live` sessions_payload computes (a state-DB-live session
    whose tab is gone is already demoted to not-live, so it doesn't count). This
    is the hide guard's truth: a directory with an active session can't be
    hidden (docs/dashboard.md *Hidden directories*). sessions_payload is not the
    cheapest call, but a hide is a rare user action, and reusing it keeps the
    'is this dir active' answer byte-identical to what the page shows."""
    return [r for r in sessions_payload()
            if r.get("live")
            and (r.get("group_dir") or r.get("cwd") or "") == key]


def resumable_payload(cwd, limit, q=""):
    """The directory's resumable sessions for the new-session form's resume
    picker (GET /api/resumable) — newest-first (API.sessions order), capped at
    `limit` (≤ RESUMABLE_MAX), each enriched with what a picker row shows: title,
    last_active, live, the transcript-tail model, the SAVED effort, and the
    account (slug + label). Directory-scoped (canon-compared).

    `q` searches the directory's WHOLE history (title + sid, case-insensitive),
    not just the newest rows — the client can't (it only holds ≤limit rows), so
    an old session is only findable through the server. Discovery therefore scans
    up to RESUMABLE_SCAN sessions (cheap — one audit query + a canon cache);
    enrichment (the per-row transcript/settings reads) is the real cost, so we
    stop after `limit` matches. A stale directory (not in the newest
    SESSIONS_LIMIT globally) is still found for the same reason.

    Read-only — resolves effort per the session's OWN account config dir (each
    account keeps its own settings.json), exactly like session_payload; no state
    writes, so no audit rows (a read endpoint, like /api/session/<sid>). The
    browser side is audited via the clientlog channel instead."""
    want = canon_cwd(cwd or "")
    if not want:
        return []
    ql = (q or "").strip().lower()
    labels = {a.get("slug"): a.get("label") for a in plugins.accounts()}
    live_wins = launch.live_windows()
    canon = {}                                   # memo: realpath is a syscall/row
    out = []
    for row in API.sessions(RESUMABLE_SCAN):
        rc = row.get("cwd") or ""
        if canon.setdefault(rc, canon_cwd(rc)) != want:
            continue
        sid = row["sid"]
        tpath = row.get("transcript_path") or ""
        title = session_title(tpath)
        if ql and ql not in (title or "").lower() and ql not in sid.lower():
            continue
        sdb = API.session_db(row)      # the live DB, else its durable park
        # demote a state-DB-live session whose window is gone (same correction
        # sessions_payload applies, launch.demote_if_dead) — a resume of a truly-
        # live session 409s anyway, but the row marks it so the picker can flag it.
        launch.demote_if_dead(row, live_wins, sid)
        ctx = session_ctx(tpath, main=True)
        slug = session_slug(sid)
        out.append({
            "sid": sid,
            "title": title,
            "last_active": _last_active(row, sdb),
            "live": bool(row.get("live")),
            "model": (ctx or {}).get("model") or "",
            # …and its OWNING host's display spelling of that id (session_ctx
            # stamps it — HostControl.model_short). The picker row shows this
            # and the form's model prefill matches the host's menu against it;
            # the page used to shorten the raw id itself, through a grammar that
            # knew exactly two hosts.
            "model_short": (ctx or {}).get("model_short") or "",
            # the SAME host-branched resolution the session payload uses
            # (read/meta.session_effort, the one owner): this row used to serve
            # the DEFAULT host's cwd-keyed saved level for every session in the
            # directory, so a codex resume row showed Claude's effort.
            "effort": session_effort(tpath, want, slug, ctx),
            # WHICH host owns this conversation — so the resume picker can switch
            # the new-session form's tool (a codex rollout resumes with `codex
            # resume`, a claude transcript with `claude --resume`). The launch is
            # OWNER-routed server-side regardless (post_new_session), so this is a
            # UI convenience, not the source of truth. Defaults to the registry's
            # DEFAULT host when unclaimed (owns_by is None for a file no plugin
            # speaks) — plugins.default_host is the ONE owner of that name.
            "tool": plugins.owns_by(tpath) or plugins.default_host(),
            "account": {"slug": slug,
                        "label": labels.get(slug) or (slug or "default")},
        })
        if len(out) >= limit:
            break
    return out


def wire_row(r):
    """A sessions row as the PAGE consumes it: minus `transcript_path`, `log`
    and `start_cwd` — server-side values the client never reads (start_cwd only
    feeds group_dir server-side; the page groups on group_dir), ~20% of the
    snapshot's bytes at 50 rows. sessions_payload keeps them internally (the
    notifier's winmap and the title/ctx caches resolve through them); only the
    two wire exits (`/api/sessions`, the global SSE) strip."""
    return {k: v for k, v in r.items()
            if k not in ("transcript_path", "log", "start_cwd")}


def row_key(wire_row):
    """ONE wire row's change-detection key: the row minus stats['paused'].
    The scorebar accrues that float ~once per second for every session
    sitting at a prompt (its awaiting-pause accumulator), which would make
    the row differ on EVERY tick. Only the diff is paused-blind: a pushed
    row still carries the exact value, and the card's ⏱ (elapsed MINUS
    paused) is constant while paused accrues, so the frozen card a
    suppressed push leaves behind is already showing the right number."""
    st = wire_row.get("stats") or {}
    return json.dumps(
        dict(wire_row, stats={k: v for k, v in st.items() if k != "paused"}),
        default=str, sort_keys=True)


def accounts_payload():
    """The list page's USAGE STRIP: every host's rate-limit rows, in the one
    usage-window vocabulary (plugins.usage_strip — that fan-out's docstring owns
    the shape). Concatenated across hosts, so ONE painter renders the whole strip
    and a new host appears in it without a line of dashboard code.

    Claude Code contributes one row per SUBSCRIPTION ACCOUNT (the unit its limits
    are per), which is why the route is still `/api/accounts` and why those rows
    still carry the new-session picker's fields (`slug`, `five_hour_eff`,
    `sched_score`, `sched_ok`, `limit_hit`, `logged_out`) — the picker reads the
    same payload and offers the rows marked `switchable`. codex contributes ONE
    host-wide row: it has no account switcher, so there is nothing to have a row
    per, and it is `switchable: False`. That asymmetry used to be a whole second
    endpoint (`/api/codex-usage`), a second DOM node and a second painter; it is
    now two fields.

    All server-computed (single-owner rule): the page renders the served numbers
    and labels — including each window's own SHORT LABEL, since the same 10080
    minutes is "7d" to one host and "1w" to another and only the host knows which.
    Read-only, adds NO audit rows (a codex app-server degrade is audited once in
    plugins/codex/usage.py, never here)."""
    return plugins.usage_strip(cache=_ACCT, limit=SESSIONS_LIMIT)


def accounts_key(payload):
    """The accounts strip's change-detection key: the payload minus each row's
    `sched_score`. That score is remaining%/hours-to-reset — it moves
    continuously with the clock, so a full-payload diff would push an
    `accounts` SSE event on EVERY tick of an idle dashboard (row_key's
    paused-blind diff, applied to the strip). A pushed payload still carries
    the exact score; only the DIFF is score-blind — and every other field is
    step-valued (integer percentages, reset epochs, booleans), moving only
    when a snapshot, stamp, or window boundary actually moves."""
    return json.dumps([{k: v for k, v in a.items() if k != "sched_score"}
                       for a in payload], default=str, sort_keys=True)


# The stats aggregate's TTL memo (read/cache.ttl_cached, keyed by the single
# key "" — the payload takes no arguments). A whole-corpus scan has no cheap
# fingerprint, so bounded staleness is the only freshness rule available; the
# bound is config.STATS_TTL_S. `.clear()` is the documented bypass (the tests
# use it).
_STATS_AGG = {}


def _stats_compute():
    """stats_payload's body — the whole-corpus fold, run on a memo MISS. Split
    from the public entry so the caching rule is one line there instead of a
    hand-rolled deadline check around a 60-line function."""
    now = time.time()
    agg = API.activity_stats()
    rows = agg["sessions"]
    # "active" is GENUINE liveness, not `ended_at IS NULL`. Claude Code fires no
    # hook on cancel/kill/crash and a reboot wipes /tmp, so a session that died
    # without a clean SessionEnd keeps ended_at=NULL in the audit corpus forever
    # — counting those as active inflated the tally far past the list page's
    # count (docs/dashboard.md *Stats / Insights*). Reuse the list page's OWN
    # window-corrected liveness (sessions_payload, exactly as dir_live_sessions
    # does) so the two views can't disagree. A live session is always recent, so
    # the SESSIONS_LIMIT discovery depth always covers it.
    live_sids = {r["sid"] for r in sessions_payload() if r.get("live")}
    # resolve each row's grouping key ONCE (canon_cwd is a realpath syscall,
    # group_dir a cached .git walk) and reuse it for both the project cards and
    # every pulse window.
    for r in rows:
        r["_key"] = group_dir(canon_cwd(r.get("start_cwd") or "")) or ""
    projects = {}
    for r in rows:
        key = r["_key"]
        p = projects.get(key)
        if p is None:
            p = projects[key] = {"dir": key, "name": os.path.basename(key) or key,
                                 "sessions": 0, "tokens": 0, "cost": 0.0,
                                 "errors": 0, "last_active": 0, "_daily": {}}
        p["sessions"] += 1
        p["tokens"] += r.get("tokens") or 0
        p["cost"] += r.get("cost") or 0.0
        p["errors"] += r.get("errors") or 0
        st = r.get("started_at") or 0
        p["last_active"] = max(p["last_active"], st)
        if st:
            d = time.strftime("%Y-%m-%d", time.localtime(st))
            p["_daily"][d] = p["_daily"].get(d, 0) + 1
    proj_list = sorted(projects.values(),
                       key=lambda p: p["sessions"], reverse=True)
    for p in proj_list:
        p["spark"] = sorted([d, n] for d, n in p.pop("_daily").items())
    windows = {}
    for name, days in (("7d", 7), ("30d", 30), ("all", None)):
        cut = 0 if days is None else now - days * 86400
        wr = [r for r in rows if (r.get("started_at") or 0) >= cut]
        by_proj = {}
        for r in wr:
            by_proj[r["_key"]] = by_proj.get(r["_key"], 0) + 1
        top = sorted(by_proj.items(), key=lambda kv: kv[1],
                     reverse=True)[:STATS_TOP_PROJECTS]
        windows[name] = {
            "sessions": len(wr),
            "ended": sum(1 for r in wr if r.get("ended_at")),
            "active": sum(1 for r in wr if r["sid"] in live_sids),
            "tokens": sum(r.get("tokens") or 0 for r in wr),
            "cost": sum(r.get("cost") or 0.0 for r in wr),
            "errors": sum(r.get("errors") or 0 for r in wr),
            "projects": [{"dir": k, "name": os.path.basename(k) or k,
                          "sessions": n} for k, n in top],
        }
    out = {"generated_at": now, "total_sessions": agg["total_sessions"],
           "daily": agg["daily"], "punch": agg["punch"],
           "windows": windows, "projects": proj_list}
    return out


def stats_payload():
    """The GitHub-Insights-style cross-session Stats page (GET /api/stats): the
    contribution heatmap (sessions/day), a per-window Pulse summary, the day×hour
    punch card, and per-project cards. Everything is computed SERVER-side (single-
    owner rule; the JS only renders) from core.sessionapi.activity_stats (the audit
    tables). A read-only aggregate — no writes, no audit rows (like accounts_payload,
    ctx saturation, and the goal probe). Memo-cached for config.STATS_TTL_S so
    re-opening the page doesn't re-scan the whole history.

    Sessions group under the SAME key the list page uses — start_cwd (the frozen
    original cwd), symlink-canonicalised and resolved to its linked-worktree owner
    (group_dir) — so worktrees fold under their main checkout. Pulse windows
    (7d/30d/all) and per-project sparkline series are folded from the same rows in
    one pass; the heatmap buckets are left client-side so the scale self-normalises
    without a round-trip."""
    return ttl_cached(_STATS_AGG, "", config.STATS_TTL_S, _stats_compute)
