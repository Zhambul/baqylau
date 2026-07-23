# L0 — plugins/claude_code/model_usage.py, the per-model weekly-usage fetch.
# In-process unit tests: the I/O boundaries (keychain read/write, the OAuth
# endpoint, the token refresh) are the seams we monkeypatch; the account→slug
# mapping is driven through the REAL core.sessionapi.account_usage by
# monkeypatching only its return (the mapping arithmetic is what's under test).
# No network, no keychain — every external call is stubbed.
import sys
import time
import urllib.error

import pytest

from conftest import REPO

if REPO not in sys.path:
    sys.path.insert(0, REPO)

from plugins.claude_code import model_usage as MU


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    """Enable the feature (tests run on Linux CI too) and clear the module TTL
    cache before each test so cached values never leak across cases."""
    monkeypatch.setattr(MU, "enabled", lambda: True)
    MU._CACHE.update(ts=0.0, val={})
    yield
    MU._CACHE.update(ts=0.0, val={})


def _usage(five_reset, seven_reset, fable=91, fable_reset="2026-07-21T16:59:59+00:00"):
    """A minimal /usage response with account-wide windows + one weekly_scoped
    Fable cap (ISO resets, like the real endpoint)."""
    from datetime import datetime, timezone
    def iso(e):
        return datetime.fromtimestamp(e, timezone.utc).isoformat()
    lims = [{"kind": "session", "percent": 4},
            {"kind": "weekly_all", "percent": 60}]
    if fable is not None:
        lims.append({"kind": "weekly_scoped", "percent": fable,
                     "resets_at": fable_reset,
                     "scope": {"model": {"display_name": "Fable"}}})
    return {"five_hour": {"utilization": 9, "resets_at": iso(five_reset)},
            "seven_day": {"utilization": 62, "resets_at": iso(seven_reset)},
            "limits": lims}


# ------------------------------------------------------------ pure extractors

def test_model_windows_extracts_weekly_scoped():
    u = _usage(1, 2, fable=100, fable_reset="2026-07-21T16:59:59+00:00")
    mw = MU.model_windows(u)
    assert mw["seven_day_fable"] == 100
    assert mw["seven_day_fable_reset"] == pytest.approx(
        MU._iso_epoch("2026-07-21T16:59:59+00:00"))


def test_model_windows_ignores_non_scoped_and_garbage():
    u = {"limits": [
        {"kind": "weekly_all", "percent": 50},                       # not model-scoped
        {"kind": "weekly_scoped", "percent": None,                   # bad pct
         "scope": {"model": {"display_name": "Opus"}}},
        {"kind": "weekly_scoped", "percent": 30, "scope": {}},       # no model name
        {"kind": "weekly_scoped", "percent": 80,                     # good, no reset
         "scope": {"model": {"display_name": "Sonnet"}}}]}
    mw = MU.model_windows(u)
    assert mw == {"seven_day_sonnet": 80}                            # only the valid one
    assert "seven_day_opus" not in mw


def test_model_slug_vocabulary():
    assert MU._model_slug("Fable") == "fable"
    assert MU._model_slug("Claude Opus 4.8") == "opus"               # 'claude' stripped
    assert MU._model_slug("") == ""
    assert MU._model_slug(None) == ""


def test_pct_clamped():
    u = {"limits": [{"kind": "weekly_scoped", "percent": 140,
                     "scope": {"model": {"display_name": "Fable"}}}]}
    assert MU.model_windows(u)["seven_day_fable"] == 100


# ------------------------------------------------------------ slug mapping

def test_slug_for_7d_tie_broken_by_5h(monkeypatch):
    # two accounts share the 7d boundary; only the 5h reset disambiguates them
    monkeypatch.setattr(MU.API, "account_usage", lambda cache=None: {
        "c1": {"usage": {"five_hour_reset": 2000, "seven_day_reset": 9000}},
        "": {"usage": {"five_hour_reset": 1000, "seven_day_reset": 9000}}})
    u = _usage(2000, 9000)
    assert MU._slug_for(u) == "c1"                     # 5h reset breaks the 7d tie
    u2 = _usage(1000, 9000)
    assert MU._slug_for(u2) == ""


def test_slug_for_unique_7d_survives_rolled_5h(monkeypatch):
    # the 2026-07-20 bug: the captured 5h epoch is from an OLD window (no
    # session ran recently), but the 7d epoch matches uniquely — must attach
    monkeypatch.setattr(MU.API, "account_usage", lambda cache=None: {
        "c1": {"usage": {"five_hour_reset": 1111, "seven_day_reset": 9000}},
        "c2": {"usage": {"five_hour_reset": 2000, "seven_day_reset": 5000}}})
    assert MU._slug_for(_usage(2000, 9000)) == "c1"    # stale 5h ignored
    # …and even with NO captured 5h epoch at all
    monkeypatch.setattr(MU.API, "account_usage", lambda cache=None: {
        "c1": {"usage": {"seven_day_reset": 9000}}})
    assert MU._slug_for(_usage(2000, 9000)) == "c1"


def test_slug_for_ambiguous_7d_stale_5h_returns_none(monkeypatch):
    # a 7d tie the 5h epoch CANNOT break (both captured 5h are stale) must
    # refuse to guess — a wrong attach is worse than a missing bar
    monkeypatch.setattr(MU.API, "account_usage", lambda cache=None: {
        "c1": {"usage": {"five_hour_reset": 1111, "seven_day_reset": 9000}},
        "": {"usage": {"five_hour_reset": 2222, "seven_day_reset": 9000}}})
    assert MU._slug_for(_usage(7777, 9000)) is None


def test_slug_for_no_match_returns_none(monkeypatch):
    monkeypatch.setattr(MU.API, "account_usage", lambda cache=None: {
        "c1": {"usage": {"five_hour_reset": 2000, "seven_day_reset": 9000}}})
    assert MU._slug_for(_usage(5555, 6666)) is None


# ------------------------------------------------------------ token freshness

def test_expires_at_ms_and_s():
    assert MU._expires_at({"expiresAt": 1_700_000_000_000}) == 1_700_000_000
    assert MU._expires_at({"expiresAt": 1_700_000_000}) == 1_700_000_000
    assert MU._expires_at({}) == 0.0


def test_fresher_picks_later_expiry():
    a = {"expiresAt": 100_000}
    b = {"expiresAt": 200_000}
    assert MU._fresher(a, b) is b
    assert MU._fresher(None, b) is b
    assert MU._fresher(a, None) is a


def test_access_token_fresh_never_refreshes(monkeypatch):
    fresh = {"accessToken": "AT-fresh",
             "expiresAt": int((time.time() + 7200) * 1000)}
    monkeypatch.setattr(MU, "_sec_read",
                        lambda svc: fresh if svc == "svc" else None)
    monkeypatch.setattr(MU, "_refresh",
                        lambda rt: pytest.fail("must not refresh a fresh token"))
    assert MU._access_token("svc") == "AT-fresh"


def test_access_token_expired_refreshes_and_persists(monkeypatch):
    expired = {"accessToken": "AT-old", "refreshToken": "RT-1",
               "expiresAt": int((time.time() - 10) * 1000)}
    reads = {"svc": expired}                              # store copy absent
    monkeypatch.setattr(MU, "_sec_read", lambda svc: reads.get(svc))
    minted = {"accessToken": "AT-new", "refreshToken": "RT-2",
              "expiresAt": int((time.time() + 7200) * 1000)}
    monkeypatch.setattr(MU, "_refresh", lambda rt: minted if rt == "RT-1" else None)
    written = {}
    monkeypatch.setattr(MU, "_sec_write",
                        lambda svc, blob: written.update({svc: blob}))
    assert MU._access_token("svc") == "AT-new"
    assert written == {MU.STORE_PREFIX + "svc": minted}  # rotation persisted, ours only


def test_access_token_expired_no_refresh_token(monkeypatch):
    monkeypatch.setattr(MU, "_sec_read", lambda svc: {
        "accessToken": "x", "expiresAt": int((time.time() - 10) * 1000)})
    assert MU._access_token("svc") is None               # nothing to refresh with


def test_access_token_refresh_rejected_not_audited(monkeypatch):
    """A rotated/stale refresh token 4xx (HTTPError, a URLError subclass) is an
    expected outcome: degrade to None WITHOUT lighting the ⚠ warning light."""
    monkeypatch.setattr(MU, "_sec_read", lambda svc: {
        "accessToken": "x", "refreshToken": "RT-stale",
        "expiresAt": int((time.time() - 10) * 1000)})
    def rejected(rt):
        raise urllib.error.HTTPError(MU.TOKEN_URL, 400, "Bad Request", {}, None)
    monkeypatch.setattr(MU, "_refresh", rejected)
    audited = []
    monkeypatch.setattr(MU, "_audit_once", lambda f, c: audited.append(f))
    assert MU._access_token("svc") is None
    assert audited == []                                 # expected — no ⚠


def test_access_token_refresh_unexpected_audited(monkeypatch):
    """A non-network exception in the refresh path STILL audits."""
    monkeypatch.setattr(MU, "_sec_read", lambda svc: {
        "accessToken": "x", "refreshToken": "RT",
        "expiresAt": int((time.time() - 10) * 1000)})
    monkeypatch.setattr(MU, "_refresh",
                        lambda rt: (_ for _ in ()).throw(ValueError("bug")))
    audited = []
    monkeypatch.setattr(MU, "_audit_once", lambda f, c: audited.append(f))
    assert MU._access_token("svc") is None
    assert audited == ["model_usage._refresh"]


# ------------------------------------------------------------ orchestration

def test_windows_by_slug_end_to_end(monkeypatch):
    monkeypatch.setattr(MU, "_login_services", lambda: ["svc-personal", "svc-work"])
    monkeypatch.setattr(MU, "_access_token",
                        lambda svc: "T-" + svc)          # both readable
    responses = {"T-svc-personal": _usage(2000, 9000, fable=91),
                 "T-svc-work": _usage(1000, 8000, fable=100)}
    monkeypatch.setattr(MU, "_get", lambda url, tok: responses[tok])
    monkeypatch.setattr(MU.API, "account_usage", lambda cache=None: {
        "c1": {"usage": {"five_hour_reset": 2000, "seven_day_reset": 9000}},
        "c2": {"usage": {"five_hour_reset": 1000, "seven_day_reset": 8000}}})
    out = MU.windows_by_slug()
    assert out["c1"]["seven_day_fable"] == 91
    assert out["c2"]["seven_day_fable"] == 100


def test_windows_by_slug_one_bad_credential_is_isolated(monkeypatch):
    monkeypatch.setattr(MU, "_login_services", lambda: ["good", "dead"])
    monkeypatch.setattr(MU, "_access_token",
                        lambda svc: None if svc == "dead" else "T")
    monkeypatch.setattr(MU, "_get", lambda url, tok: _usage(2000, 9000, fable=77))
    monkeypatch.setattr(MU.API, "account_usage", lambda cache=None: {
        "c1": {"usage": {"five_hour_reset": 2000, "seven_day_reset": 9000}}})
    out = MU.windows_by_slug()
    assert out == {"c1": {"seven_day_fable": 77,
                          "seven_day_fable_reset": pytest.approx(
                              MU._iso_epoch("2026-07-21T16:59:59+00:00"))}}


def test_windows_by_slug_fetch_error_degrades(monkeypatch):
    monkeypatch.setattr(MU, "_login_services", lambda: ["svc"])
    monkeypatch.setattr(MU, "_access_token", lambda svc: "T")
    def boom(url, tok):
        raise urllib_error()
    monkeypatch.setattr(MU, "_get", boom)
    assert MU.windows_by_slug() == {}                    # fail-silent, no raise


def test_windows_by_slug_expected_net_error_not_audited(monkeypatch):
    """An offline / dead endpoint (URLError) degrades silently AND does NOT
    write an audit `errors` row — it must not light the ⚠ warning light in
    every session forever (global-errors skill, 2026-07-22)."""
    audited = []
    monkeypatch.setattr(MU, "_audit_once", lambda f, c: audited.append(f))
    monkeypatch.setattr(MU, "_login_services", lambda: ["svc"])
    monkeypatch.setattr(MU, "_access_token", lambda svc: "T")
    monkeypatch.setattr(MU, "_get",
                        lambda url, tok: (_ for _ in ()).throw(urllib_error()))
    assert MU.windows_by_slug() == {}
    assert audited == []                                 # expected — no ⚠


def test_windows_by_slug_remote_disconnect_not_audited(monkeypatch):
    """A peer that drops the connection mid-request (http.client.
    RemoteDisconnected — a ConnectionResetError, NOT wrapped in URLError) is an
    environmental transient: degrade silently, no ⚠ (global-errors, 2026-07-23)."""
    import http.client
    audited = []
    monkeypatch.setattr(MU, "_audit_once", lambda f, c: audited.append(f))
    monkeypatch.setattr(MU, "_login_services", lambda: ["svc"])
    monkeypatch.setattr(MU, "_access_token", lambda svc: "T")
    monkeypatch.setattr(MU, "_get", lambda url, tok: (_ for _ in ()).throw(
        http.client.RemoteDisconnected("Remote end closed connection")))
    assert MU.windows_by_slug() == {}
    assert audited == []                                 # expected — no ⚠


def test_windows_by_slug_unexpected_error_audited(monkeypatch):
    """A genuinely unexpected exception (our JSON-shape handling, not the
    network) STILL audits — the warning light stays meaningful."""
    audited = []
    monkeypatch.setattr(MU, "_audit_once", lambda f, c: audited.append(f))
    monkeypatch.setattr(MU, "_login_services", lambda: ["svc"])
    monkeypatch.setattr(MU, "_access_token", lambda svc: "T")
    monkeypatch.setattr(MU, "_get",
                        lambda url, tok: (_ for _ in ()).throw(KeyError("shape")))
    assert MU.windows_by_slug() == {}
    assert audited == ["model_usage.windows_by_slug"]


def test_windows_by_slug_cached(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(MU, "_login_services",
                        lambda: (calls.update(n=calls["n"] + 1), ["svc"])[1])
    monkeypatch.setattr(MU, "_access_token", lambda svc: "T")
    monkeypatch.setattr(MU, "_get", lambda url, tok: _usage(2000, 9000))
    monkeypatch.setattr(MU.API, "account_usage", lambda cache=None: {
        "c1": {"usage": {"five_hour_reset": 2000, "seven_day_reset": 9000}}})
    MU.windows_by_slug()
    MU.windows_by_slug()
    assert calls["n"] == 1                               # second call served from TTL cache


def test_disabled_returns_empty(monkeypatch):
    monkeypatch.setattr(MU, "enabled", lambda: False)
    monkeypatch.setattr(MU, "_login_services",
                        lambda: pytest.fail("must not touch keychain when disabled"))
    assert MU.windows_by_slug() == {}


def urllib_error():
    import urllib.error
    return urllib.error.URLError("boom")
