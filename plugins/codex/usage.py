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
    resets_at}, …]}. None when the shape is unusable."""
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
    Behind plugins.usage_windows."""
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
