# plugins/codex/usage.py — codex account rate-limit windows (the read surface).
#
# codex's per-account 5h/weekly usage, the codex analogue of Claude's
# model_usage.windows_by_slug — but codex exposes it over its APP SERVER, not a
# status line: `codex app-server` speaks JSON-RPC on stdio, and
# `account/rateLimits/read` returns the rate limits with NO live session needed
# (the stable source — the per-session token_count.rate_limits is nullable). This
# module spawns the app server, does the minimal handshake, reads the one reply,
# and terminates it — a BOUNDED, TTL-cached call that degrades to None on any
# failure and NEVER raises into the read-side dashboard (P6 renders the result;
# this provides it, behind plugins.usage_windows).
#
# Read-only like ctx/goal: it adds no audit rows on SUCCESS. A FAILURE is audited
# once (A.error) — the task's "audit a degrade" rule — so a persistently
# unreachable app server is diagnosable rather than silent.
import json
import os
import subprocess
import time

from core.noaudit import load_audit

A = load_audit()

APPSERVER_TIMEOUT_S = 6.0     # hard cap on the whole spawn+RPC round-trip
USAGE_TTL_S = 120.0           # cache the windows this long (a poller must not
#                               spawn a codex app-server per tick)

_CACHE = None                 # (expires_at, result-or-None)

# codex is a node script (`#!/usr/bin/env node`), so a STRIPPED env — the
# launchd dashboard runs with PATH=/usr/bin:/bin:/usr/sbin:/sbin — finds neither
# `codex` NOR the `node` it shebangs, and the app-server spawn failed silently
# (the usage strip then hid, "codex missing from the accounts list"). Prepend
# the common node/codex install dirs to PATH so BOTH resolve — the find_kitten
# candidate-list idiom (frontends/kitty.py), not a hard single path. Verified:
# a launch from a shell (login-shell PATH) already worked; only server-side
# DIRECT spawns like this one were blind. $CODEX_BIN_DIR overrides.
CODEX_BIN_DIRS = ("~/.hermes/node/bin", "/opt/homebrew/bin",
                  "/usr/local/bin", "~/.local/bin")


def codex_spawn_env():
    """os.environ with the codex/node bin dirs PREPENDED to PATH — the one owner
    of "how a server-side codex subprocess finds its binary under a stripped
    env". Reused by every direct codex spawn (the usage app-server here; the P4
    error channel next). A dir is added only if it exists, so a machine without
    one is unaffected; $CODEX_BIN_DIR wins for an unusual install."""
    env = dict(os.environ)
    dirs = []
    override = env.get("CODEX_BIN_DIR")
    if override and os.path.isdir(override):
        dirs.append(override)
    for d in CODEX_BIN_DIRS:
        p = os.path.expanduser(d)
        if os.path.isdir(p) and p not in dirs:
            dirs.append(p)
    if dirs:
        env["PATH"] = os.pathsep.join(dirs + [env.get("PATH", "")])
    return env


def _rpc_read_ratelimits():
    """Spawn `codex app-server`, initialize, call account/rateLimits/read, return
    the raw `result` dict — or None on any failure (missing binary, protocol
    drift, timeout). All errors are swallowed here; the ONE audit row is the
    caller's, so a success path stays row-free."""
    try:
        proc = subprocess.Popen(
            ["codex", "app-server"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, env=codex_spawn_env())
    except Exception:
        return None
    try:
        init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"clientInfo": {"name": "baqylau", "version": "1"}}}
        req = {"jsonrpc": "2.0", "id": 2,
               "method": "account/rateLimits/read", "params": {}}
        proc.stdin.write(json.dumps(init) + "\n")
        proc.stdin.write(json.dumps(req) + "\n")
        proc.stdin.flush()
        deadline = time.time() + APPSERVER_TIMEOUT_S
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            try:
                msg = json.loads(line)
            except Exception:
                continue
            if msg.get("id") == 2:
                res = msg.get("result")
                return res if isinstance(res, dict) else None
        return None
    except Exception:
        return None
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.terminate()
        except Exception:
            pass


def _normalize(res):
    """codex's {rateLimits:{planType, primary{usedPercent,windowDurationMins,
    resetsAt}, secondary{…}}} -> {planType, windows:[{used_pct, window_mins,
    resets_at}, …]}. None when the shape is unusable.

    `primary`/`secondary` are SLOTS, not durations — do not read `primary` as
    "the 5h one". On the current plus plan codex sends ONE window: `secondary` is
    literal JSON null and `primary` IS the weekly (10080m) one, which is why the
    strip shows a single codex bar and no 5h bar. That is codex's shape, not a
    dropped window — verified against both raw sources (this reply and the
    rollouts' token_count.rate_limits) and against 75 rollouts of history, in
    which codex changed the shape four times, including a real two-window plus
    period (2026-06-26 → 07-07, primary 300m + secondary 10080m) that this same
    code parsed into two windows. docs/codex.md *One window, not two*.

    Skipping a null slot is therefore correct: a null is not a window. A window
    with `usedPercent` 0 or null, or a null duration, is KEPT (window_rows
    carries it as a ghost) — only BOTH slots being non-dicts yields None."""
    rl = (res or {}).get("rateLimits") if isinstance(res, dict) else None
    if not isinstance(rl, dict):
        return None
    wins = []
    for key in ("primary", "secondary"):
        w = rl.get(key)
        if not isinstance(w, dict):
            continue
        wins.append({"used_pct": w.get("usedPercent"),
                     "window_mins": w.get("windowDurationMins"),
                     "resets_at": w.get("resetsAt")})
    if not wins:
        return None
    return {"planType": rl.get("planType") or "", "windows": wins}


def usage_windows():
    """codex account rate-limit windows: {planType, windows:[{used_pct,
    window_mins, resets_at}]}, or None (app server unreachable / unconfigured /
    protocol drift). TTL-cached; a failure is audited once and cached too (so a
    poller doesn't respawn the app server every tick against a broken setup).
    The ACCOUNT-level source behind usage_strip(); the per-SESSION twin is the
    rollout probe (plugins/codex/read.usage), which produces the same shape."""
    global _CACHE
    now = time.time()
    if _CACHE and _CACHE[0] > now:
        return _CACHE[1]
    try:
        out = _normalize(_rpc_read_ratelimits())
    except Exception:
        out = None
    if out is None:
        try:
            A.error("codex-usage", "codex app-server account/rateLimits/read",
                    {"note": "degraded to None"})
        except Exception:
            pass
    _CACHE = (now + USAGE_TTL_S, out)
    return out


# --- the shared usage-window vocabulary (plugins.usage_strip) -----------------
# codex names a window by its DURATION, because that is all it reports: there is
# no key like Claude's `five_hour`, just `primary`/`secondary` and a length in
# minutes. The label is NOT codex's own: the shared duration table
# (`plugins.window_label`) spells 300 → "5h" and 10080 → "7d" for every host,
# because the strip lays its columns out BY DURATION and this row's weekly bar
# sits directly under Claude's. It used to read "1w" here and "7d" there, each
# "the way its own UI does" — which is one column with two names, not two
# vocabularies. The duration ladder below survives only as the FALLBACK for a
# duration that table does not name.

HOST = "codex"              # this plugin's name, stamped on its strip row so
#                             ONE painter can group the strip by host

MINS_WEEK = 60 * 24 * 7
MINS_DAY = 60 * 24


def _derived_label(mins):
    """codex's OWN duration ladder — 20160 → "2w", 1440 → "1d", 90 → "90m". Only
    reachable for a duration the shared table does not name (it names 300 and
    10080), which is why 10080 can no longer come out of here as "1w"."""
    if mins % MINS_WEEK == 0:
        return "%dw" % (mins // MINS_WEEK)
    if mins % MINS_DAY == 0:
        return "%dd" % (mins // MINS_DAY)
    if mins % 60 == 0:
        return "%dh" % (mins // 60)
    return "%dm" % mins


def window_label(mins, i=0):
    """A codex window's short label from its duration: 300 → "5h", 10080 → "7d"
    (the SHARED table, `plugins.window_label` — the strip's columns are keyed by
    duration and this row stacks under Claude's), 1440 → "1d" (codex's own
    ladder, for a duration that table does not name). Falls back to
    primary/secondary by position when the duration is missing — the same rule
    the browser used to apply client-side, moved here so the server owns every
    string the strip shows (docs/styleguide.md)."""
    import plugins as P

    if isinstance(mins, (int, float)) and mins > 0:
        mins = int(mins)
        return P.window_label(mins, fallback=_derived_label(mins))
    return "primary" if i == 0 else "secondary"


def window_rows(windows):
    """codex's [{used_pct, window_mins, resets_at}] → the shared usage-window
    vocabulary [{key, label, used_pct, resets_at, window_mins, scope}] (owned by
    plugins.usage_strip's docstring). `key` is the duration, which is the only
    stable identity a codex window has — it must only be unique WITHIN this
    host's rows, since the painter unions columns per host. Every codex window is
    account-wide (`scope`), so each carries a reset column; there is no per-MODEL
    cap in codex's reporting. A window with no readable percentage is kept with
    used_pct None — the painter ghosts it rather than dropping the column."""
    out = []
    for i, w in enumerate(windows or []):
        mins = w.get("window_mins")
        pct = w.get("used_pct")
        out.append({
            "key": ("w%d" % int(mins)) if isinstance(mins, (int, float)) and mins
                   else ("primary" if i == 0 else "secondary"),
            "label": window_label(mins, i),
            "used_pct": int(round(pct)) if isinstance(pct, (int, float)) else None,
            "resets_at": w.get("resets_at"),
            "window_mins": mins,
            "scope": "account",
        })
    return out


def strip_row(windows, ts=None):
    """codex's usage-strip row from a windows payload, or None when it names no
    window. ONE row, not one per account: codex has no subscription SWITCHER, so
    its limits are a single host-wide reading — which is why the row carries
    `switchable: False` (the new-session account picker offers only rows that are
    an account you can launch under) and no slug.

    The row's name is LOWERCASE `codex` — the tool's own spelling of itself (its
    binary, its config dir, this plugin's `HOST`), and the one the strip should
    use, since a Claude row beside it is named by its account slug rather than
    by a title-cased product name."""
    rows = window_rows((windows or {}).get("windows"))
    if not rows:
        return None
    plan = ((windows or {}).get("planType") or "").strip()
    return {"host": HOST, "switchable": False, "slug": "", "plan": plan,
            "label": HOST + " · " + plan if plan else HOST,
            "windows": rows, "ts": ts,
            # the account-switcher fields a Claude row carries. Served as the
            # honest empty so ONE painter can read every row the same way
            # without asking which host wrote it.
            "usage": None, "limit_hit": None, "logged_out": False}


def usage_strip(cache=None, limit=50):
    """The usage-strip provider (plugins.usage_strip fan-out) — codex's single
    host-wide row, from the app server's account rate limits. [] when codex is
    unconfigured / unreachable / not installed (the strip then simply has no
    codex row). `cache`/`limit` are the fan-out's per-host arguments and are not
    used here: the app-server read has its own TTL cache and scans no sessions."""
    row = strip_row(usage_windows())
    return [row] if row else []


def _rollout_usage(sid):
    """The session's own rollout rate-limit reading (read.usage over the
    standalone host's rollout), or None. Lazy import: read.py imports this
    module's sibling parse half, and the providers below are read-side only."""
    from plugins.codex import read
    path = read._rollout_for(sid, "")
    return read.usage(path) if path else None


def session_usage(sid):
    """The per-session usage provider (plugins.session_usage fan-out) — this
    codex session's last rate-limit reading, as {plan, ts, windows:[…]} in the
    shared vocabulary; None when its rollout carries none.

    Read from the ROLLOUT rather than the app server on purpose: the app server
    answers for the account as it stands NOW, which is the right answer for the
    list-page strip and the wrong one for a session you are looking back at — a
    parked run's header should say where its limits stood, and a machine with no
    codex installed can still open it. Unlike Claude's, the payload carries no
    flat window keys: there is no kv row behind it, and `windows` is the whole
    vocabulary (dashboard/read/session serves it, the header renders it)."""
    got = _rollout_usage(sid)
    if not got:
        return None
    return {"plan": (got.get("planType") or "").strip(),
            "windows": window_rows(got.get("windows")), "ts": None}


def session_account(sid):
    """The per-session account provider (plugins.session_account fan-out) — the
    minimal honest shape for a host with NO account switcher: no slug (there is
    nothing to switch to, and a slug is what the migrate/launch paths key on),
    just the plan the rollout reported, so the header chip reads "◈ codex · plus"
    instead of blanking. {} when the rollout names no plan — the chip is then
    absent, which is the honest answer rather than a bare "codex" that claims a
    subscription reading we do not have.

    Lowercase, and the SAME string strip_row builds, for the same reason: it is
    the tool's own spelling of itself, and the header chip and the strip row name
    one thing — two spellings of it would read as two facts."""
    got = _rollout_usage(sid)
    plan = ((got or {}).get("planType") or "").strip()
    return {"slug": "", "label": HOST + " · " + plan} if plan else {}


def session_costs(sid):
    """The per-session cost provider (plugins.session_costs fan-out) — a codex
    session's token/cost totals from its OWN scoreboard counters.

    codex never reaches the `otel` table (that receiver is Claude Code's
    telemetry), so the OTEL sum that answers for a Claude session reads a
    truthful-looking 0 for a codex one. The real numbers are already in the state
    DB: plugins/codex/stream.py folds each turn's usage delta and prices it with
    CODEX_PRICES at the moment it reads it. This just reports what is banked
    there, in the same envelope the OTEL side returns — one `query_source`, named
    for the host, since codex has no main/subagent/auxiliary split to report."""
    from core import sessionapi as API
    from core import state as S
    sdb = API.state_db_for(sid)
    st = (S.stats_at(sdb) or {}) if sdb else {}
    usd = st.get("cost") or 0.0
    toks = {k: int(st.get(k) or 0)
            for k in ("tk_in", "tk_out", "tk_read", "tk_create")
            if st.get(k)}
    return {"tokens": {HOST: toks} if toks else {},
            "cost": {HOST: usd} if usd else {},
            "total_usd": usd}


def corpus_costs():
    """EVERY codex session's totals — {sid: {"tokens": n, "cost": usd}}, the
    bulk twin of session_costs (plugins.corpus_costs).

    Without it the cross-session views read codex as FREE: they sum the audit
    `otel` table, which only Claude Code's telemetry receiver fills, so a codex
    session showed real spend on its own page and 0 in the corpus fold.

    codex's spend is banked in each session's own SCOREBOARD (the stream folds
    the turn delta and prices it with CODEX_PRICES as it reads it), so this is
    a scan rather than a query — but only over sessions this plugin OWNS: the
    audit `sessions` rows whose transcript is a codex rollout. That is what
    keeps it cheap (a handful of state DBs, not the whole corpus), and it is
    the same ownership question `owns` answers everywhere else. A session with
    nothing banked is omitted, exactly as a zero-cost row would read."""
    from core import sessionapi as API
    from core import state as S
    from plugins.codex import rollout
    out = {}
    db = API.audit_db()
    for sid, tpath in API.db_rows(
            db, "SELECT session_id, transcript_path FROM sessions"
                " WHERE transcript_path IS NOT NULL AND transcript_path != ''"):
        if not sid or not rollout.owns(tpath or ""):
            continue
        try:
            sdb = API.state_db_for(sid)
            st = (S.stats_at(sdb) or {}) if sdb else {}
        except Exception:
            continue                    # an unreadable session reports nothing
        toks, usd = int(st.get("tokens") or 0), float(st.get("cost") or 0.0)
        if toks or usd:
            out[sid] = {"tokens": toks, "cost": usd}
    return out
