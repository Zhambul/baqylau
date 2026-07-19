# plugins/claude_code/model_usage.py — per-MODEL weekly usage windows, the ONE
# source that needs a real API call (docs/dashboard.md "Per-model usage bars").
#
# Claude Code's account-wide 5h/7d windows reach the dashboard tokenlessly (the
# status-line capture, statusline.parse_usage). The PER-MODEL weekly caps (e.g.
# "Fable" — the /usage screen's third bar) are exposed by NO tokenless channel:
# they live only behind the undocumented OAuth endpoint /api/oauth/usage, which
# requires a `user:profile`-scoped token. The switcher's setup-tokens are
# inference-only (no user:profile — verified 2026-07-19, github
# leegunwoo98/claude-code-account-switcher), so this module PIGGYBACKS on the
# full-scope OAuth logins Claude Code stores in the macOS keychain
# ("Claude Code-credentials[-<hash>]"): read the access token, refresh it when
# expired, call the endpoint, and shape the model caps into the SAME
# `seven_day_<model>` window kv the dashboard's generic bar renderer already
# paints (the fable-ready pipeline — sessionapi.usage_windows / app.js
# windowLabel). Layer: this is the CONSUMER-tier read model's helper — the
# dashboard calls it (via plugins.model_windows); core and hooks never do (core
# stays tokenless; account_usage/the relimit picker must not depend on an API).
#
# Refresh ownership (avoids a two-writer fight over one refresh token):
#   - the ACTIVELY-used login (plain `claude`, e.g. the personal account) is kept
#     fresh by Claude Code itself — we only READ its keychain token, never refresh.
#   - a SWITCHER-only account's OAuth login is never exercised by Claude Code (it
#     runs on the setup-token), so its keychain access token expires in ~8h with
#     nobody to refresh it; baqylau becomes its SOLE refresher, minting new tokens
#     from the refresh token and persisting the rotation in its OWN keychain entry
#     ("baqylau-model-usage: <service>"), never overwriting Claude Code's copy.
# The per-cycle token pick is just "whichever copy (Claude Code's or ours) is
# fresher", so the personal path never self-refreshes and the work path does.
#
# Fail-silent + audited: an undocumented endpoint or a keychain ACL change must
# NEVER break the dashboard — every failure degrades to "no model windows".

import getpass
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime

from core import sessionapi as API
from core.noaudit import load_audit

A = load_audit()

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
TOKEN_URL = "https://platform.claude.com/v1/oauth/token"   # OAuth refresh (form-encoded)
CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"          # Claude Code's public OAuth client
BETA_HEADER = "oauth-2025-04-20"
# A non-empty claude-code User-Agent — the endpoint aggressively throttles a
# missing UA (observed via the usage-monitor projects, 2026-07-19).
USER_AGENT = "claude-code/baqylau"

CRED_PREFIX = "Claude Code-credentials"      # the full-scope OAuth login entries
STORE_PREFIX = "baqylau-model-usage: "       # baqylau's own rotated-token entries
DEFAULT_EXPIRES_S = 8 * 3600                 # OAuth access-token life when unstated

TTL_S = 60             # cache the whole fan-out — the endpoint/keychain work runs
                       # at most once a minute however often the page polls
RESET_TOL_S = 600      # window-reset epochs count as "the same account" within this
SKEW_S = 120           # treat a token expiring within this as already expired

_CACHE = {"ts": 0.0, "val": {}}
_AUDITED = set()   # funcs already error-audited this process (flood guard, below)


def _audit_once(func, context):
    """Audit a swallowed failure AT MOST ONCE per (func) per process. This is a
    60s-polled consumer read; auditing every cycle of a persistently-down
    endpoint would trickle a new `errors` row each minute, which errwatch then
    surfaces as a ⚠ in EVERY session's mirror. Once per process keeps the
    failure debuggable (core/audit errors table) without the flood."""
    if func in _AUDITED:
        return
    _AUDITED.add(func)
    A.error("", func=func, context=context)


def enabled():
    """macOS + kill-switch (CLAUDE_MODEL_USAGE=0 disables). Off on non-darwin —
    credential discovery is keychain-only, so there is nothing to read."""
    return (sys.platform == "darwin"
            and (os.environ.get("CLAUDE_MODEL_USAGE") or "1").strip() != "0")


# --------------------------------------------------------------- keychain I/O

def _account():
    try:
        return getpass.getuser()
    except Exception:
        return os.environ.get("USER") or ""


def _sec_read(service):
    """The `claudeAiOauth` blob stored under a keychain service, or None. Both
    Claude Code's login entries and baqylau's own rotated entries wrap the token
    set the same way ({"claudeAiOauth": {...}}), so one reader serves both."""
    try:
        raw = subprocess.run(
            ["/usr/bin/security", "find-generic-password", "-s", service, "-w"],
            capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        return None
    if not raw:
        return None
    try:
        return (json.loads(raw) or {}).get("claudeAiOauth") or None
    except Exception:
        return None


def _sec_write(service, blob):
    """Persist a token blob to a baqylau-owned keychain entry (-U = update in
    place). Best-effort — a write failure just means we re-refresh next cycle."""
    try:
        subprocess.run(
            ["/usr/bin/security", "add-generic-password", "-U",
             "-s", service, "-a", _account(),
             "-w", json.dumps({"claudeAiOauth": blob})],
            capture_output=True, timeout=5)
    except Exception as e:
        _audit_once("model_usage._sec_write",
                    {"service": service, "err": str(e)})


def _login_services():
    """Every Claude Code OAuth-login keychain service ("Claude Code-credentials"
    and per-config-dir "…-<hash>" siblings). Metadata-only dump — no secret
    values, so it never prompts. Excludes the switcher's inference-only
    "Claude Code Subscription: …" setup-token entries by prefix."""
    try:
        out = subprocess.run(["/usr/bin/security", "dump-keychain"],
                             capture_output=True, text=True, timeout=15).stdout
    except Exception:
        return []
    return sorted(set(re.findall(r'"(%s[^"]*)"' % re.escape(CRED_PREFIX), out)))


# --------------------------------------------------------------- token refresh

def _expires_at(blob):
    """A blob's expiry as epoch SECONDS (Claude Code stores ms), or 0."""
    v = (blob or {}).get("expiresAt")
    if not isinstance(v, (int, float)):
        return 0.0
    return v / 1000.0 if v > 1e12 else float(v)


def _fresher(a, b):
    """The blob with the later expiry (a wins ties); None only if both None."""
    if not a:
        return b
    if not b:
        return a
    return a if _expires_at(a) >= _expires_at(b) else b


def _refresh(refresh_token):
    """Exchange a refresh token for a fresh access token (OAuth refresh grant,
    form-encoded — the endpoint 400s on JSON). Returns a camelCase blob matching
    the keychain shape, carrying the ROTATED refresh token forward (falling back
    to the presented one if the response omits it). None on any failure."""
    body = urllib.parse.urlencode({"grant_type": "refresh_token",
                                   "refresh_token": refresh_token,
                                   "client_id": CLIENT_ID}).encode()
    req = urllib.request.Request(
        TOKEN_URL, data=body, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=8) as r:
        j = json.loads(r.read())
    at = j.get("access_token")
    if not at:
        return None
    exp = j.get("expires_in")
    exp = exp if isinstance(exp, (int, float)) else DEFAULT_EXPIRES_S
    return {"accessToken": at,
            "refreshToken": j.get("refresh_token") or refresh_token,
            "expiresAt": int((time.time() + exp) * 1000),
            "scopes": (j.get("scope") or "").split() or None}


def _access_token(service):
    """A usable access token for one login service, refreshing if needed. Picks
    the fresher of Claude Code's copy and baqylau's own rotated copy; if that is
    (near-)expired and carries a refresh token, mints a new one and persists it
    to baqylau's entry. None when no credential/refresh is available."""
    cred = _fresher(_sec_read(service), _sec_read(STORE_PREFIX + service))
    if not cred:
        return None
    if _expires_at(cred) > time.time() + SKEW_S:
        return cred.get("accessToken")
    rt = cred.get("refreshToken")
    if not rt:
        return None
    try:
        new = _refresh(rt)
    except Exception as e:
        _audit_once("model_usage._refresh", {"service": service, "err": str(e)})
        return None
    if not new:
        return None
    _sec_write(STORE_PREFIX + service, new)
    return new.get("accessToken")


# --------------------------------------------------------------- endpoint read

def _get(url, token):
    req = urllib.request.Request(url, headers={
        "Authorization": "Bearer %s" % token,
        "anthropic-beta": BETA_HEADER,
        "User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.loads(r.read())


def _iso_epoch(s):
    """An ISO-8601 reset string ('2026-07-21T16:59:59.840+00:00') → epoch
    seconds, or None."""
    try:
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return None


def _model_slug(display_name):
    """A limit's model display_name → the window slug matching the model-picker
    vocabulary and app.js windowLabel ('Fable' → 'fable', 'Claude Opus' →
    'opus'; 'claude' stripped)."""
    words = [w for w in re.sub(r"[^a-z0-9 ]+", " ", (display_name or "").lower()).split()
             if w != "claude"]
    return words[0] if words else ""


def model_windows(usage_json):
    """The endpoint's `weekly_scoped` limits shaped into the generic window kv the
    dashboard already renders: {'seven_day_<model>': used%, '…_reset': epoch}.
    The ONE reader of the /usage `limits` array (docs/styleguide single-owner)."""
    out = {}
    for lim in (usage_json.get("limits") or []):
        if lim.get("kind") != "weekly_scoped":
            continue
        name = (((lim.get("scope") or {}).get("model") or {}).get("display_name"))
        pct = lim.get("percent")
        slug = _model_slug(name)
        if not slug or not isinstance(pct, (int, float)):
            continue
        key = "seven_day_%s" % slug
        out[key] = max(0, min(100, int(round(pct))))
        ep = _iso_epoch(lim.get("resets_at"))
        if ep:
            out[key + "_reset"] = ep
    return out


def _slug_for(usage_json, cache=None):
    """Map the endpoint account to a switcher slug by matching its account-wide
    5h AND 7d reset epochs against each slug's freshest captured usage
    (core.sessionapi.account_usage — the tokenless status-line snapshots).
    BOTH resets must match (the 5h epoch disambiguates accounts that share a 7d
    boundary — a single-signal match mis-mapped once, 2026-07-19). None when no
    slug matches (then the bar simply doesn't attach)."""
    e5 = _iso_epoch(((usage_json.get("five_hour") or {}).get("resets_at")))
    e7 = _iso_epoch(((usage_json.get("seven_day") or {}).get("resets_at")))
    if e5 is None or e7 is None:
        return None
    for slug, ent in (API.account_usage(cache=cache) or {}).items():
        cu = ent.get("usage") or {}
        c5, c7 = cu.get("five_hour_reset"), cu.get("seven_day_reset")
        if (isinstance(c5, (int, float)) and abs(c5 - e5) < RESET_TOL_S
                and isinstance(c7, (int, float)) and abs(c7 - e7) < RESET_TOL_S):
            return slug
    return None


def windows_by_slug(cache=None):
    """{slug: {model-window kv}} across every readable OAuth login, TTL-cached.
    Fail-silent + audited per service — one dead credential (or the whole
    endpoint) never blocks the rest or the dashboard. {} when disabled."""
    if not enabled():
        return {}
    now = time.time()
    if now - _CACHE["ts"] < TTL_S:
        return _CACHE["val"]
    out = {}
    for service in _login_services():
        try:
            token = _access_token(service)
            if not token:
                continue
            usage = _get(USAGE_URL, token)
            mw = model_windows(usage)
            if not mw:
                continue
            slug = _slug_for(usage, cache)
            if slug is None:
                continue
            out.setdefault(slug, {}).update(mw)
        except Exception as e:
            _audit_once("model_usage.windows_by_slug",
                        {"service": service, "err": str(e)})
    _CACHE.update(ts=now, val=out)
    return out
