# tests/test_l0_dash_server.py — L0 dashboard: the HTTP server: endpoints, payloads, routing, caching.
#
# One subject out of the former 8468-line L0 dashboard monolith; the
# shared HTTP/audit helpers live in tests/dashkit.py and the in-process
# server fixture (`dash`) in tests/conftest.py.
import gzip
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

import pytest
from conftest import REPO, wait_until

if REPO not in sys.path:
    sys.path.insert(0, REPO)

import plugins
import core.audit as A
from core import ops as O
from core import paths as P
from core import state as S
from dashboard import prefs
from dashboard import server as DS


# ------------------------------------------------------------------ opshtml
from dashkit import (_get, _get_json, _post, _sf_rows_full, _state_rows)


def test_http_root_and_static_whitelist(dash):
    code, body = _get(dash + "/")
    assert code == 200 and body.lstrip().startswith("<!doctype html>")
    # cache-bust: the index's sub-resource URLs carry ?v=<BOOT_ID> so a restart
    # forces remote browsers/CDNs off a stale app.js/style.css
    assert ("/static/app.00-core.js?v=" + DS.config.BOOT_ID) in body
    assert ("/static/style.css?v=" + DS.config.BOOT_ID) in body
    code, _ = _get(dash + "/static/app.00-core.js")
    assert code == 200
    # the ?v= is a cache key only — the file still serves with the query present
    code, _ = _get(dash + "/static/app.00-core.js?v=" + DS.config.BOOT_ID)
    assert code == 200
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(dash + "/static/secret.txt")          # not on the whitelist
    assert e.value.code == 404


def test_versioned_static_cache_headers(dash):
    """An asset fetched under the CURRENT boot's ?v=<BOOT_ID> stamp is
    immutable-cacheable at that URL (config.CACHE_STATIC) — the tunnel-502 fix:
    every refresh used to re-pull all 14 no-store SPA parts in one parallel
    burst, which overflowed the accept queue and cloudflared turned the resets
    into 502s / half-loaded pages (docs/dashboard.md *Cache-busting*). Anything
    un-stamped or stale-stamped (index.html, sw.js, an old boot's ?v=) stays
    no-store."""
    v = "?v=" + DS.config.BOOT_ID
    with urllib.request.urlopen(dash + "/static/app.00-core.js" + v,
                                timeout=10) as r:
        assert r.headers["Cache-Control"] == DS.config.CACHE_STATIC
    for path in ("/", "/sw.js", "/static/app.00-core.js",
                 "/static/app.00-core.js?v=stale"):
        with urllib.request.urlopen(dash + path, timeout=10) as r:
            assert r.headers["Cache-Control"] == "no-store", path


def test_server_accept_backlog_raised():
    # the socketserver default backlog of 5 resets a tunnel refresh burst (~16
    # parallel connections); the real server class must keep it raised
    assert DS.Server.request_queue_size == DS.config.BACKLOG
    assert DS.config.BACKLOG >= 64


def test_favicon_ico_served_at_root_and_undeclared(dash):
    # the RASTER fallback favicon lives at the root path clients auto-probe when
    # the declared data-URI SVG icon is unusable (iOS Safari supports SVG
    # favicons in no version), docs/dashboard.md *Favicon fallback*
    with urllib.request.urlopen(dash + "/favicon.ico", timeout=10) as r:
        assert r.status == 200
        data = r.read()
        assert r.headers["Content-Type"] == "image/vnd.microsoft.icon"
    assert data[:4] == b"\x00\x00\x01\x00"          # a real ICO header
    code, index = _get(dash + "/")
    # deliberately NOT declared: a raster <link rel="icon"> would out-rank the
    # SVG, which is the one carrying the dynamic red asking-you badge
    assert "favicon.ico" not in index
    assert 'rel="icon"' in index and "data:image/svg+xml" in index


def test_icon_urls_are_cache_busted(dash):
    # regenerating an icon is new bytes at an unchanged URL, and an icon cache is
    # stickier than a resource cache (a hard reload does not evict Safari's), so
    # the icon URLs carry ?v=<BOOT_ID> too — index.html AND the manifest's own
    # icon list, which is where the installed-app glyph is read from
    v = "?v=" + DS.config.BOOT_ID
    code, index = _get(dash + "/")
    assert ("/static/apple-touch-icon.png" + v) in index
    assert ("/static/manifest.webmanifest" + v) in index
    code, man = _get(dash + "/static/manifest.webmanifest")
    assert code == 200
    assert ("/static/icon-192.png" + v) in man
    assert ("/static/icon-512.png" + v) in man
    # still a valid manifest, and the stamped URL still serves the file
    assert json.loads(man)["icons"]
    code, _ = _get(dash + "/static/icon-192.png" + v)
    assert code == 200


def test_get_routing_registry_resolves(dash):
    """The read plane routes through a REGISTRY, the twin of the POST control
    plane's (_FIXED_GET / _SESSION_GET, dashboard/http/get.py) — so every entry
    must name a real handler with the table's uniform signature. A typo'd entry
    is a 404 that looks like an empty tab, not a crash, so the table gets its own
    guard; each fixed endpoint is also fetched for real, since a handler that
    exists but raises is the same empty tab."""
    for table, argc in ((DS.Handler._FIXED_GET, 2),      # (self, url)
                        (DS.Handler._SESSION_GET, 3)):   # (self, sid, url)
        for key, name in table.items():
            fn = getattr(DS.Handler, name, None)
            assert callable(fn), "%s → no handler named %s" % (key, name)
            assert fn.__code__.co_argcount == argc, \
                "%s takes %d args, the table calls it with %d" % (
                    name, fn.__code__.co_argcount, argc)
    for key in DS.Handler._FIXED_GET:
        code, _ = _get(dash + "/api/" + "/".join(key))
        assert code == 200, key
    # an unrouted path is 404 on both shapes (fixed and session-scoped), not a 500
    for miss in ("/api/nope", "/api/session/" + "s" * 8 + "/nope", "/events/nope"):
        with pytest.raises(urllib.error.HTTPError) as e:
            _get(dash + miss)
        assert e.value.code == 404, miss


def test_page_session_verbs_are_all_routed(dash):
    """Every `/api/session/<sid>/<verb>` the PAGE builds is routed by the server
    — the GET registry, the POST registry, or one of the explicitly-matched
    multi-segment reads whose tail is a NAME (agent/view/copy). Cross-tier, in
    the direction that breaks: an endpoint the page fetches but nothing routes is
    a silent 404 (an empty tab, a control gesture that never lands), invisible in
    the server-side audit because no handler ever ran."""
    code, index = _get(dash + "/")
    assert code == 200
    verbs = set()
    for p in sorted(set(re.findall(r"/static/(app\.\d\d-[a-z]+\.js)", index))):
        code, body = _get(dash + "/static/" + p)
        assert code == 200
        verbs |= set(re.findall(r'"/api/session/"\s*\+\s*[^+]+\+\s*"/([a-z-]+)',
                                body))
    assert len(verbs) > 20, "the URL shape changed — this guard stopped seeing it"
    routed = (set(DS.Handler._SESSION_GET) | set(DS.Handler._SESSION_POST)
              | {"agent", "view", "copy"})
    assert not (verbs - routed), "the page fetches unrouted verbs: %s" % (
        sorted(verbs - routed),)


def test_close_in_flight_state_has_one_owner(dash):
    """The two halves of an optimistic close — S.closing (greyed card) and
    S.closePend (the optPending web-hint handle) — are MUTATED only by
    closeBegin/closeSettle in app.00-core.js. The card ✕, the header ✕ and
    reconcileCloses hand-rolled the pairing in two files; the settle half also
    had to fire exactly once, since a leaked handle beacons a bogus `stale` row
    (the stuck-greyed-state bug signal) 20s after a close that did resolve.
    Static check on the served parts: reads (`.has`, `Object.keys`, `.t0`) stay
    open to every site, writes don't."""
    for part in ("04-list", "11-chrome", "05-session", "07-dialogs"):
        code, body = _get(dash + "/static/app.%s.js" % part)
        assert code == 200
        for write in ("S.closing.add", "S.closing.delete", "S.closePend[sid] =",
                      "delete S.closePend"):
            assert write not in body, "%s writes close state: %s" % (part, write)
    code, core = _get(dash + "/static/app.00-core.js")
    assert "function closeBegin(" in core and "function closeSettle(" in core


def test_two_step_confirm_has_one_implementation(dash):
    """The arm-then-fire confirm is one rule — "a misclick here costs you the
    conversation, so ask once" — with one implementation: `armConfirm`
    (app.00-core.js). The header's ✕ close and ⊜ compact each hand-rolled it,
    the same timer handle and label swap 60 lines apart in one function, which is
    how one of them ends up with a fix the other misses.

    The list card's ✕ is the sanctioned exception and says so at the site: its
    arm must survive the per-tick card REBUILD, so it holds a deadline in `S`
    instead of a closure. Static check over the served parts — the arm styling is
    the tell, since that is what any hand-rolled copy has to do."""
    code, index = _get(dash + "/")
    assert code == 200
    bodies = {}
    for p in sorted(set(re.findall(r"/static/(app\.\d\d-[a-z]+\.js)", index))):
        code, bodies[p] = _get(dash + "/static/" + p)
        assert code == 200
    assert "function armConfirm(" in bodies["app.00-core.js"]
    owners = ("app.00-core.js", "app.04-list.js")     # the helper + cardClose
    for p, body in bodies.items():
        if p in owners:
            continue
        assert 'classList.add("arm")' not in body, \
            "%s hand-rolls the two-step confirm — use armConfirm()" % p
    # …and the two sites that used to own copies now go through it
    assert bodies["app.11-chrome.js"].count("armConfirm(") == 2


def test_no_dead_page_functions(dash):
    """Every function declared by the SPA is called by someone. The parts are
    classic scripts sharing one global scope (app.NN-*.js, ordered), which makes
    an orphan invisible: nothing errors, nothing lints, the code just sits there
    reading as live — `fiveHourUsed` outlived the client-side account picker that
    way (the ranking moved server-side to `sched_score`/`sched_ok`, and the
    function kept its explanatory comment about a job it no longer had).

    The part list comes from index.html so a NEW part is covered the moment it is
    wired, and index.html is part of the haystack (an inline handler counts as a
    call)."""
    code, index = _get(dash + "/")
    assert code == 200
    parts = sorted(set(re.findall(r"/static/(app\.\d\d-[a-z]+\.js)", index)))
    assert len(parts) > 5, parts
    bodies = {}
    for p in parts:
        code, bodies[p] = _get(dash + "/static/" + p)
        assert code == 200
    hay = index + "".join(bodies.values())
    dead = []
    for p, body in bodies.items():
        for name in re.findall(r"^function (\w+)\(", body, re.M):
            # one occurrence IS the declaration; a call adds another
            if len(re.findall(r"\b%s\b" % re.escape(name), hay)) < 2:
                dead.append("%s: %s()" % (p, name))
    assert not dead, "dead page functions: " + ", ".join(dead)


def test_app_js_initializes_close_state(dash):
    """Regression guard for THE "still not closing" bug: the ✕ handler does
    `S.closePend[sid] = optPending(...)` and reconcileCloses does
    `Object.keys(S.closePend)` on every sessions tick — if `closePend` is not
    initialized in the `S` state object it is `undefined`, and BOTH throw a
    TypeError ("Cannot convert undefined or null to object" / "set property of
    undefined"), the second BEFORE `closeSession` runs, so /stop never fires and
    the close silently does nothing. It shipped uninitialized once (found only
    once the js.error frontend-audit row pointed at app.js:878). A pure static
    check on the served bundle — no JS engine needed."""
    code, body = _get(dash + "/static/app.00-core.js")
    assert code == 200
    # the S state literal must declare closePend (and closing) as containers
    assert "closePend: {}" in body, "S.closePend must be initialized (see the bug)"
    assert "closing: new Set()" in body
    A.session_start({"session_id": "dash1", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("dash1")
    O.emit(log, O.label("▶ foreground", (170, 185, 210), g="g1"),
           O.code("echo hi", g="g1"), O.gut("hi", (170, 185, 210), g="g1"))
    rows = _get_json(dash + "/api/sessions")
    row = next(r for r in rows if r["sid"] == "dash1")
    assert row["live"] is True
    d = _get_json(dash + "/api/session/dash1/ops?after=0")
    assert d["last"] >= 3 and len(d["items"]) >= 3
    # …the command block's own header, in the quiet register (opshtml.cmd_note)
    assert any(it.get("quiet") == "open" for it in d["items"])
    # grouped items carry their copy-group id so the app can fold the block
    assert all(it["g"] == "g1" for it in d["items"])
    # the overview composes without error even for a minimal session
    ov = _get_json(dash + "/api/session/dash1")
    assert ov["sid"] == "dash1" and ov["live"] is True


def test_sessions_stats_cache_by_db_sig(dash, monkeypatch):
    """The list poll memoizes stats_at by _db_sig (DB file + -wal stat):
    repeat polls with no writes must not re-open the DB, and a product-API
    write — which may land only in the WAL, never touching the main file's
    stat — must invalidate on the next poll."""
    A.session_start({"session_id": "dashc", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("dashc")
    S.incr(log, commands=1)
    calls = []
    real = DS.API.stats_at
    monkeypatch.setattr(DS.API, "stats_at",
                        lambda p: calls.append(p) or real(p))
    row = next(r for r in _get_json(dash + "/api/sessions")
               if r["sid"] == "dashc")
    assert row["stats"].get("commands") == 1
    n = len(calls)
    assert n >= 1
    _get_json(dash + "/api/sessions")
    assert len(calls) == n             # unchanged DB → served from the memo
    S.incr(log, commands=1)            # a WAL-only write must still invalidate
    row = next(r for r in _get_json(dash + "/api/sessions")
               if r["sid"] == "dashc")
    assert row["stats"].get("commands") == 2 and len(calls) > n


def test_sessions_last_active_fallback_chain(dash, tmp_path):
    """The list card's recency chip: `last_active` is the transcript's mtime
    (the file grows on every turn), else the audit ended_at, else the state
    DB's mtime, else started_at — started_at alone read as staleness on a
    live session an hour into its work."""
    # transcript present → its mtime wins
    tr = tmp_path / "tr.jsonl"
    tr.write_text("{}\n")
    os.utime(tr, (1_000_000, 1_000_000))
    A.session_start({"session_id": "dla1", "cwd": "/w",
                     "transcript_path": str(tr)})
    # transcript gone + ended → the audit ended_at
    A.session_start({"session_id": "dla2", "cwd": "/w",
                     "transcript_path": str(tmp_path / "gone.jsonl")})
    A.session_end({"session_id": "dla2"}, "other")
    # no transcript, still open, state DB on disk → the state DB's mtime
    A.session_start({"session_id": "dla3", "cwd": "/w", "transcript_path": ""})
    S.incr(P.mirror_log("dla3"), commands=1)
    os.utime(P.state_db(P.mirror_log("dla3")), (2_000_000, 2_000_000))
    # nothing at all → started_at
    A.session_start({"session_id": "dla4", "cwd": "/w", "transcript_path": ""})

    rows = {r["sid"]: r for r in _get_json(dash + "/api/sessions")}
    assert rows["dla1"]["last_active"] == 1_000_000
    assert rows["dla2"]["last_active"] == rows["dla2"]["ended_at"] > 0
    assert rows["dla3"]["last_active"] == 2_000_000
    assert rows["dla4"]["last_active"] == rows["dla4"]["started_at"] > 0


def test_stats_active_counts_only_live_sessions(dash):
    """Stats Pulse `active` is GENUINE liveness (sessions_payload's live), NOT
    `ended_at IS NULL`. A session that died without a clean SessionEnd keeps
    ended_at=NULL in the audit corpus forever (Claude Code fires no hook on
    cancel/kill/crash, and a reboot wipes /tmp), and must NOT inflate the active
    tally past what the list page shows (docs/dashboard.md *Stats / Insights*).
    active + ended therefore no longer partitions sessions — a stranded row is
    neither."""
    # two genuinely-live sessions: an audit row + a live /tmp state DB
    for sid in ("sa1", "sa2"):
        A.session_start({"session_id": sid, "cwd": "/w", "transcript_path": ""})
        S.incr(P.mirror_log(sid), commands=1)      # creates the live state DB
    # a stranded session: audit row, ended_at NULL, but NO live state DB
    A.session_start({"session_id": "sast", "cwd": "/w", "transcript_path": ""})
    # a cleanly-ended session (SessionEnd sets ended_at; no live state DB)
    A.session_start({"session_id": "sadone", "cwd": "/w", "transcript_path": ""})
    A.session_end({"session_id": "sadone"}, "other")

    DS.lists._STATS_AGG.clear()                 # bypass the wall-clock memo
    win = _get_json(dash + "/api/stats")["windows"]["all"]
    assert win["sessions"] == 4
    assert win["active"] == 2      # only the two live ones, NOT the stranded row
    assert win["ended"] == 1       # only the clean SessionEnd


def test_resumable_endpoint_dir_scoped_enriched(dash, monkeypatch):
    """GET /api/resumable is the new-session resume picker's source: the
    directory's recent sessions, each enriched with the model/effort/account it
    ran under (docs/dashboard.md *Resume picker*). Directory-scoped (canon
    cwd), capped at RESUMABLE_MAX, `limit` clamped, blank cwd → []."""
    # a known account registry so the label resolves without the real
    # accounts.tsv (plugins.accounts reads ~/.config otherwise)
    monkeypatch.setattr(DS.plugins, "accounts",
                        lambda: [{"slug": "acc1", "label": "Account One",
                                  "alias": "acc1"}])
    A.session_start({"session_id": "rz1", "cwd": "/proj", "transcript_path": ""})
    A.session_start({"session_id": "rz2", "cwd": "/proj", "transcript_path": ""})
    # rz2 ran under acc1 — the account kv the statusline stashes; writing it also
    # creates the state DB session_slug reads
    S.kv_set(P.mirror_log("rz2"), "account", {"slug": "acc1"})
    A.session_start({"session_id": "rz3", "cwd": "/other", "transcript_path": ""})

    rows = _get_json(dash + "/api/resumable?cwd=/proj")
    sids = [r["sid"] for r in rows]
    assert set(sids) == {"rz1", "rz2"}              # /other excluded (dir-scoped)
    for r in rows:
        assert set(r) >= {"sid", "title", "last_active", "live",
                          "model", "effort", "account"}
        assert set(r["account"]) == {"slug", "label"}
    by = {r["sid"]: r for r in rows}
    assert by["rz2"]["account"] == {"slug": "acc1", "label": "Account One"}
    # no stashed account → the empty-slug default
    assert by["rz1"]["account"] == {"slug": "", "label": "default"}

    # limit is clamped to [1, RESUMABLE_MAX]
    assert len(_get_json(dash + "/api/resumable?cwd=/proj&limit=1")) == 1
    assert len(_get_json(dash + "/api/resumable?cwd=/proj&limit=999")) == 2
    # a blank/unknown dir has nothing to resume
    assert _get_json(dash + "/api/resumable") == []
    assert _get_json(dash + "/api/resumable?cwd=/nope") == []


def test_resumable_search_across_history(dash):
    """?q= searches the directory's WHOLE history (title + sid), not just the
    loaded rows — the fix for 'search does not search all history'. Here we match
    on the sid substring (a title needs a transcript); a miss returns []."""
    A.session_start({"session_id": "srch-alpha-1", "cwd": "/s",
                     "transcript_path": ""})
    A.session_start({"session_id": "srch-beta-2", "cwd": "/s",
                     "transcript_path": ""})
    A.session_start({"session_id": "other-gamma", "cwd": "/s",
                     "transcript_path": ""})
    got = {r["sid"] for r in _get_json(dash + "/api/resumable?cwd=/s&q=srch")}
    assert got == {"srch-alpha-1", "srch-beta-2"}          # both srch-* match
    one = [r["sid"] for r in _get_json(dash + "/api/resumable?cwd=/s&q=beta")]
    assert one == ["srch-beta-2"]                          # narrowed to one
    assert _get_json(dash + "/api/resumable?cwd=/s&q=nomatch") == []  # a miss


def test_http_backlog_endpoint(dash):
    """/backlog is the gzip-able GET twin of the SSE fresh-connect payload —
    the same merged_backlog output ({last, mpos, oldest, items}); the page
    fetches it first and hands the cursors to the SSE, which then only
    streams increments (SSE frames are never compressed — docs/dashboard.md,
    *Lazy backlog*)."""
    A.session_start({"session_id": "dashbl", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("dashbl")
    O.emit(log, O.label("▶ one", (1, 2, 3), g="b1"),
           O.code("echo hi", g="b1"), O.gut("hi", (1, 2, 3), g="b1"))
    d = _get_json(dash + "/api/session/dashbl/backlog")
    assert d["last"] >= 3 and d["oldest"] == 0
    assert d["items"] and all("html" in it for it in d["items"])
    # the cursor contract: an SSE connected with these cursors has nothing
    # left to replay — the ops tail past `last` is empty
    tail = _get_json(dash + "/api/session/dashbl/ops?after=%d" % d["last"])
    assert tail["items"] == []


def test_live_windows_memoized_by_ttl(monkeypatch):
    """live_windows runs ONE `kitten @ ls` per _LIVE_TTL window and serves
    the memo in between (the ~21ms subprocess was the server's largest
    recurring cost when the TTL sat under the 1s tick). Read-side only by
    design — control-plane POSTs never touch this map, they re-scan via
    fe.window_for_session at action time."""
    calls = []
    win = {"id": 7, "user_vars": {"claude_session": "sX"}}
    class FE:
        def ls(self):
            calls.append(1)
            return [{"tabs": [{"windows": [win]}]}]
        def iter_windows(self, tree=None):
            for osw in tree or self.ls():
                for t in osw.get("tabs", []):
                    for w in t.get("windows", []):
                        yield osw, t, w
    monkeypatch.setattr(DS.launch, "frontend", lambda: FE())
    monkeypatch.setattr(DS.launch, "_LIVE_WINS", {"ts": -1e9, "val": None})
    assert DS.launch.live_windows() == {"sX": "7"}
    assert DS.launch.live_windows() == {"sX": "7"}      # within TTL → memo, no scan
    assert len(calls) == 1                        # ONE ls per TTL (tree reused)
    DS.launch._LIVE_WINS["ts"] -= DS.launch._LIVE_TTL + 1       # age the memo past the TTL
    assert DS.launch.live_windows() == {"sX": "7"}
    assert len(calls) == 2


def test_live_windows_empty_ls_is_cant_tell(monkeypatch):
    """A transient `kitten @ ls` failure surfaces as an EMPTY tree (kitten_ls
    swallows every failure into [] and never raises), which must be treated as
    can't-tell (None), NOT as an authoritative 'no live tabs'. Trusting {} on a
    hiccup demoted every running session to not-live, flashing its dashboard
    card to 'gone' while it was working."""
    class FE:
        def ls(self):
            return []                     # the swallowed-failure signature
        def iter_windows(self, tree=None):
            raise AssertionError("must not iterate an empty tree")
    monkeypatch.setattr(DS.launch, "frontend", lambda: FE())
    monkeypatch.setattr(DS.launch, "_LIVE_WINS", {"ts": -1e9, "val": None})
    assert DS.launch.live_windows() is None            # not {} → no wrongful demotion


def _notifier_for_asking(monkeypatch, screen, delay=999):
    """A Notifier wired hermetically to one red 'asking' tab on window '9':
    controllable tab states, a fake frontend returning `screen["txt"]`, and
    every home-touching dependency (session-end / composer / mute / audit /
    payload) stubbed. Returns (n, cur, asking, sent, audited)."""
    win = "9"
    asking = next(s for s, k in DS.config.NOTIFY_STATES.items() if k == "asking")
    cur = {"states": {}}
    monkeypatch.setattr(DS.API, "tab_states", lambda: dict(cur["states"]))
    monkeypatch.setattr(DS.presence, "session_ended", lambda sid: False)
    monkeypatch.setattr(DS.presence, "composing", lambda sid: False)
    monkeypatch.setattr(DS.prefs, "notify_muted", lambda sid: False)
    monkeypatch.setattr(DS.config, "NOTIFY_TELEGRAM", True)
    monkeypatch.setattr(DS.config, "NOTIFY_DELAY_S", delay)
    audited = []
    monkeypatch.setattr(DS.A, "state_file",
                        lambda *a, **k: audited.append(a))

    class FE:
        def get_text(self, w, extent="screen"):
            return screen["txt"]

    n = DS.Notifier()
    n.fe = FE()
    n.winmap = {win: {"sid": "sX"}}
    n._payload = lambda kind, state, row: {
        "kind": kind, "state": state, "sid": row["sid"]}
    sent = []
    n._telegram = lambda entry, *a: sent.append(entry)
    n._webpush = lambda entry: False   # no push subscribed → Telegram is the path
    n.push = lambda ev, pl: None
    return n, cur, asking, sent, audited


def test_notify_suppressed_when_answering_dialog_at_terminal(monkeypatch):
    """A red 'asking' tab whose TERMINAL dialog region CHANGES (you typed a
    free-text answer / toggled a selection) drops the armed Telegram alert:
    answering at the keyboard moves neither the tab off red nor the transcript,
    so the dialog-region diff is the only 'I'm on it' signal."""
    screen = {"txt": "☒ Q\n❯ 1. Yes\n  2. No\nEnter to select"}
    n, cur, asking, sent, audited = _notifier_for_asking(monkeypatch, screen)
    n.scan()                                 # baseline (prev is None)
    cur["states"] = {"9": asking}
    n.scan()                                 # arm + region baseline
    assert n.pending.get("9", {}).get("ask_region")
    screen["txt"] = "☒ Q\n  1. Yes\n❯ 2. No\nEnter to select"
    n.scan()                                 # region moved → suppressed
    assert "9" not in n.pending and sent == []
    assert any(a[2] == "notify-suppress" for a in audited)
    monkeypatch.setattr(DS.config, "NOTIFY_DELAY_S", 0)
    n.scan()                                 # nothing left to fire
    assert sent == []


def test_notify_fires_when_dialog_untouched(monkeypatch):
    """The guard is precise: a STABLE dialog region (you walked away) still
    fires after the grace window — the baseline sighting alone never suppresses."""
    screen = {"txt": "☒ Q\n❯ 1. Yes\n  2. No\nEnter to select"}
    n, cur, asking, sent, _ = _notifier_for_asking(monkeypatch, screen, delay=0)
    n.scan()                                 # baseline
    cur["states"] = {"9": asking}
    n.scan()                                 # arm, baseline, fire (delay 0)
    assert sent and sent[0]["sid"] == "sX"


def test_notify_suppressed_when_global_toggle_off(monkeypatch):
    """The GLOBAL alerts toggle OFF (docs/dashboard.md *Global alerts toggle*)
    suppresses EVERYTHING at the transition site: a red 'asking' tab neither
    pushes the in-page toast NOR arms the deferred alert, and the scan stamps a
    `notify-suppress` `reason: global-off` row — the machine-wide off switch,
    which fires BEFORE (and thus overrides) any per-session mute."""
    screen = {"txt": "☒ Q\n❯ 1. Yes\n  2. No\nEnter to select"}
    n, cur, asking, sent, audited = _notifier_for_asking(monkeypatch, screen,
                                                         delay=0)
    monkeypatch.setattr(DS.prefs, "notify_enabled", lambda: False)
    pushed = []
    n.push = lambda ev, pl: pushed.append((ev, pl))
    n.scan()                                 # baseline (prev is None)
    cur["states"] = {"9": asking}
    n.scan()                                 # transition — globally suppressed
    assert n.pending == {} and pushed == [] and sent == []
    supp = [a for a in audited if a[2] == "notify-suppress"]
    assert supp and supp[0][3].get("reason") == "global-off"


def _escalation_notifier(monkeypatch, clock):
    """A bare Notifier wired for device-first/escalation timing tests: a
    controllable monotonic `clock`, one 'done' tab on window '7', _watching off,
    _telegram/_webpush recorded by the caller (returned as (sent, pushed))."""
    monkeypatch.setattr(DS.config, "NOTIFY_DELAY_S", 0.0)
    monkeypatch.setattr(DS.config, "ESCALATE_S", 300.0)
    monkeypatch.setattr(DS.config, "NOTIFY_TELEGRAM", True)
    monkeypatch.setattr(DS.config, "NOTIFY_WEBPUSH", True)
    monkeypatch.setattr(DS.config, "NOTIFY_TELEGRAM_ALWAYS", False)
    monkeypatch.setattr(DS.prefs, "notify_muted", lambda sid: False)
    monkeypatch.setattr(DS.time, "monotonic", lambda: clock[0])
    n = DS.Notifier()
    monkeypatch.setattr(n, "_watching", lambda *a: None)
    n._payload = lambda kind, state, row: {
        "kind": kind, "state": state, "sid": row["sid"]}
    n.push = lambda ev, pl: None
    n.winmap = {"7": {"sid": "s7", "cwd": "/w", "transcript_path": ""}}
    return n


def test_device_push_first_then_telegram_escalation(monkeypatch):
    """Device-first, Telegram-if-ignored: after the grace the ON-DEVICE push
    fires and Telegram is held back; only if ESCALATE_S later you STILL did
    nothing with the session does the Telegram nudge fire."""
    clock = [0.0]
    n = _escalation_notifier(monkeypatch, clock)
    sent, pushed = [], []
    monkeypatch.setattr(n, "_telegram", lambda e, *a: sent.append(e))
    monkeypatch.setattr(n, "_webpush", lambda e: (pushed.append(e), True)[1])
    states = {"7": "working"}
    monkeypatch.setattr(DS.API, "tab_states", lambda: dict(states))
    n.scan()                                     # baseline
    states["7"] = "awaiting-response"
    n.scan()                                     # arm + stage1 device push
    assert len(pushed) == 1 and sent == []       # pushed to device, no Telegram yet
    assert n.pending["7"].get("notified") is not None
    clock[0] = 299
    n.scan()                                     # before escalate_at → still quiet
    assert sent == []
    clock[0] = 301
    n.scan()                                     # escalation window passed → Telegram
    assert len(sent) == 1 and "7" not in n.pending


def test_escalation_cancelled_when_you_act(monkeypatch):
    """If you act on the session (here: the tab leaves done) after the device
    push but before the escalation, the Telegram nudge NEVER fires."""
    clock = [0.0]
    n = _escalation_notifier(monkeypatch, clock)
    sent, pushed = [], []
    monkeypatch.setattr(n, "_telegram", lambda e, *a: sent.append(e))
    monkeypatch.setattr(n, "_webpush", lambda e: (pushed.append(e), True)[1])
    states = {"7": "working"}
    monkeypatch.setattr(DS.API, "tab_states", lambda: dict(states))
    n.scan()
    states["7"] = "awaiting-response"
    n.scan()                                     # stage1 device push
    assert len(pushed) == 1 and "7" in n.pending
    states["7"] = "working"                      # you answered / it resumed
    clock[0] = 500
    n.scan()                                     # cancel loop drops it, no escalation
    assert "7" not in n.pending and sent == []


def test_no_device_falls_back_to_telegram_immediately(monkeypatch):
    """With nothing to push to (_webpush → False), Telegram is the IMMEDIATE
    fallback at stage 1 — no on-device channel to escalate from."""
    clock = [0.0]
    n = _escalation_notifier(monkeypatch, clock)
    sent = []
    monkeypatch.setattr(n, "_telegram", lambda e, reason=None: sent.append(reason))
    monkeypatch.setattr(n, "_webpush", lambda e: False)
    states = {"7": "working"}
    monkeypatch.setattr(DS.API, "tab_states", lambda: dict(states))
    n.scan()
    states["7"] = "awaiting-response"
    n.scan()                                     # no device → Telegram now
    assert sent == ["no-device"] and "7" not in n.pending   # reason audited


def test_telegram_always_sends_both_at_stage1(monkeypatch):
    """CLAUDE_DASH_NOTIFY_TELEGRAM_ALWAYS forces BOTH channels at the first send
    (no escalation wait) — the opt-out of device-first/escalate."""
    clock = [0.0]
    n = _escalation_notifier(monkeypatch, clock)
    monkeypatch.setattr(DS.config, "NOTIFY_TELEGRAM_ALWAYS", True)
    sent, pushed = [], []
    monkeypatch.setattr(n, "_telegram", lambda e, reason=None: sent.append(reason))
    monkeypatch.setattr(n, "_webpush", lambda e: (pushed.append(e), True)[1])
    states = {"7": "working"}
    monkeypatch.setattr(DS.API, "tab_states", lambda: dict(states))
    n.scan()
    states["7"] = "awaiting-response"
    n.scan()                                     # both fire at once, no escalation
    assert len(pushed) == 1 and sent == ["always"] and "7" not in n.pending


def test_mru_push_targets_picks_most_recent_device(monkeypatch):
    """The on-device push goes to the subscriptions of the device with the
    newest presence beat — not every subscription (the whole point: one device,
    the one you're working on)."""
    subs = [{"endpoint": "https://push/mac", "keys": {}, "device": "mac"},
            {"endpoint": "https://push/ipad", "keys": {}, "device": "ipad"}]
    monkeypatch.setattr(DS.prefs, "push_subscriptions", lambda: subs)
    clock = [100.0]
    monkeypatch.setattr(DS.time, "monotonic", lambda: clock[0])
    DS.presence._DEVICE_SEEN.clear()
    DS.mark_device("ipad")
    clock[0] = 200
    DS.mark_device("mac")                       # mac is now most-recent
    targets, decision = DS.mru_push_targets()
    assert [s["endpoint"] for s in targets] == ["https://push/mac"]
    # the decision dict feeds the notify-route audit: chosen device + every
    # candidate's presence age, so "wrong device buzzed" is answerable
    assert decision["target"] == "mac" and decision["legacy"] is False
    ages = {c["device"]: c["age_s"] for c in decision["candidates"]}
    assert ages["mac"] == 0.0 and ages["ipad"] == 100.0
    clock[0] = 300
    DS.mark_device("ipad")                      # ...and now the iPad
    assert [s["endpoint"] for s in DS.mru_push_targets()[0]] == ["https://push/ipad"]
    DS.presence._DEVICE_SEEN.clear()


def test_mru_push_targets_legacy_untagged_sends_all(monkeypatch):
    """A subscription with no device tag (a client from before device routing)
    can't be routed, so it degrades to send-all (decision `legacy:True`) —
    nothing silently lost."""
    subs = [{"endpoint": "https://push/x", "keys": {}},
            {"endpoint": "https://push/y", "keys": {}}]
    monkeypatch.setattr(DS.prefs, "push_subscriptions", lambda: subs)
    targets, decision = DS.mru_push_targets()
    assert targets == subs and decision["legacy"] is True and decision["target"] is None


def test_webpush_audits_route_decision(monkeypatch):
    """_webpush emits a `notify-route` row naming the chosen device + every
    candidate's presence age — so a 'wrong device buzzed' is answerable from the
    DB (the whole point of the audit-coverage pass)."""
    monkeypatch.setattr(DS.webpush, "enabled", lambda: True)
    subs = [{"endpoint": "https://p/mac", "keys": {}, "device": "mac", "label": "macOS"},
            {"endpoint": "https://p/ipad", "keys": {}, "device": "ipad", "label": "iPad"}]
    monkeypatch.setattr(DS.prefs, "push_subscriptions", lambda: subs)
    DS.presence._DEVICE_SEEN.clear()
    DS.mark_device("mac")                       # mac is the MRU device
    audited = []
    monkeypatch.setattr(DS.A, "state_file", lambda *a, **k: audited.append(a))
    n = DS.Notifier()
    # don't actually hit the network: stub the fan-out at its owner (the send
    # itself now lives in notify/channels.py — the notifier only routes + tracks)
    monkeypatch.setattr(DS.channels, "_webpush_fanout", lambda *a: None)
    ok = n._webpush({"sid": "s7", "kind": "done", "title": "t", "project": "p"})
    assert ok is True
    routes = [a[3] for a in audited if a[2] == "notify-route"]
    assert len(routes) == 1
    assert routes[0]["target"] == "mac" and routes[0]["sid"] == "s7"
    ages = {c["device"]: c["age_s"] for c in routes[0]["candidates"]}
    assert set(ages) == {"mac", "ipad"} and ages["mac"] == 0.0
    DS.presence._DEVICE_SEEN.clear()


def test_webpush_send_row_carries_device(monkeypatch):
    """Each `web-push` `send` row names the target `device`, the on-device analog
    of the route decision — so a delivery is attributable to a device."""
    class R:
        ok, gone, status = True, False, 201
    monkeypatch.setattr(DS.webpush, "send", lambda sub, payload: R())
    audited = []
    monkeypatch.setattr(DS.A, "state_file", lambda *a, **k: audited.append(a))
    DS.channels._webpush_fanout(
        [{"endpoint": "https://p/mac", "keys": {}, "device": "mac"}],
        {"sid": "s7", "kind": "done", "badge": 1}, "send")
    sends = [a[3] for a in audited if a[2] == "web-push" and a[3].get("action") == "send"]
    assert len(sends) == 1 and sends[0]["device"] == "mac"
    assert sends[0]["ok"] is True and sends[0]["endpoint"] == "https://p/mac"


def test_notify_lifecycle_audit_rows(monkeypatch):
    """The deferred lifecycle is fully audited: `notify-arm` (phase arm) on the
    transition, `notify-arm` (phase escalate) when the on-device push arms the
    Telegram nudge, and `telegram-notify` with the `reason` that explains WHY
    Telegram fired (escalation)."""
    clock = [0.0]
    n = _escalation_notifier(monkeypatch, clock)
    sent, pushed = [], []
    monkeypatch.setattr(n, "_telegram",
                        lambda e, reason=None: sent.append((e, reason)))
    monkeypatch.setattr(n, "_webpush", lambda e: (pushed.append(e), True)[1])
    audited = []
    monkeypatch.setattr(DS.A, "state_file", lambda *a, **k: audited.append((a[2], a[3])))
    states = {"7": "working"}
    monkeypatch.setattr(DS.API, "tab_states", lambda: dict(states))
    n.scan()                                     # baseline
    states["7"] = "awaiting-response"
    n.scan()                                     # arm + stage1 push + escalate-armed
    arms = [c for act, c in audited if act == "notify-arm"]
    assert any(c.get("phase") == "arm" for c in arms)
    assert any(c.get("phase") == "escalate" for c in arms)
    clock[0] = 301
    n.scan()                                     # escalation → telegram
    assert sent and sent[-1][1] == "escalation"


def _notifier_for_done(monkeypatch, screen, delay=999):
    """A Notifier wired hermetically to one green 'done' tab on window '9': a
    fake ANSI-capable frontend returning `screen["txt"]` and every home-touching
    dependency stubbed. Returns (n, cur, done, sent, audited)."""
    win = "9"
    done = next(s for s, k in DS.config.NOTIFY_STATES.items() if k == "done")
    cur = {"states": {}}
    monkeypatch.setattr(DS.API, "tab_states", lambda: dict(cur["states"]))
    monkeypatch.setattr(DS.presence, "session_ended", lambda sid: False)
    monkeypatch.setattr(DS.presence, "composing", lambda sid: False)
    monkeypatch.setattr(DS.prefs, "notify_muted", lambda sid: False)
    monkeypatch.setattr(DS.config, "NOTIFY_TELEGRAM", True)
    monkeypatch.setattr(DS.config, "NOTIFY_DELAY_S", delay)
    audited = []
    monkeypatch.setattr(DS.A, "state_file", lambda *a, **k: audited.append(a))

    class FE:
        focused = False               # flip to simulate the kitty tab in front

        def get_text(self, w, extent="screen", ansi=False):
            return screen["txt"]

        def tab_focused(self, w, tree=None):
            return self.focused

    n = DS.Notifier()
    n.fe = FE()
    n.winmap = {win: {"sid": "sX"}}
    n._payload = lambda kind, state, row: {
        "kind": kind, "state": state, "sid": row["sid"]}
    sent = []
    n._telegram = lambda entry, *a: sent.append(entry)
    n._webpush = lambda entry: False   # no push subscribed → Telegram is the path
    n.push = lambda ev, pl: None
    return n, cur, done, sent, audited


_DONE_RULE = "\x1b[38:2:136:136:136m" + "─" * 100


def _done_screen(input_line):
    return (_DONE_RULE + "\n" + input_line + "\n" + _DONE_RULE + "\n"
            + "\x1b[m  status line\n")


def test_notify_suppressed_when_replying_at_terminal(monkeypatch):
    """A green 'done' tab whose `❯` input box gains REAL (non-faint) text drops
    the armed Telegram alert: you typing a reply at the keyboard moves neither
    the tab off green nor the transcript, so the input-box content is the only
    'I'm continuing the conversation in the kitty tab' signal."""
    screen = {"txt": _done_screen("\x1b[m❯\xa0\x1b[22;2mghost suggestion")}
    n, cur, done, sent, audited = _notifier_for_done(monkeypatch, screen)
    n.scan()                                 # baseline (prev is None)
    cur["states"] = {"9": done}
    n.scan()                                 # arm (box holds only a ghost)
    assert "9" in n.pending
    screen["txt"] = _done_screen("\x1b[m❯\xa0my typed reply")
    n.scan()                                 # real input → suppressed
    assert "9" not in n.pending and sent == []
    assert any(a[2] == "notify-suppress"
               and a[3].get("reason") == "terminal-input" for a in audited)
    monkeypatch.setattr(DS.config, "NOTIFY_DELAY_S", 0)
    n.scan()                                 # nothing left to fire
    assert sent == []


def test_notify_fires_when_input_box_ghost_only(monkeypatch):
    """The 'done' guard is precise: a box holding only a FAINT ghost suggestion
    (you never touched the keyboard) still fires after the grace window — the
    pre-filled suggestion must never look like the user replying."""
    screen = {"txt": _done_screen("\x1b[m❯\xa0\x1b[22;2mghost suggestion")}
    n, cur, done, sent, _ = _notifier_for_done(monkeypatch, screen, delay=0)
    n.scan()                                 # baseline
    cur["states"] = {"9": done}
    n.scan()                                 # arm + fire (delay 0, ghost ignored)
    assert sent and sent[0]["sid"] == "sX"


def test_notify_suppressed_when_kitty_tab_focused(monkeypatch):
    """At SEND time, if the session's kitty tab is FRONTMOST on your screen
    (`Frontend.tab_focused`), the off-device alert is dropped — you're already
    looking at it. A dashboard-spawned tab in a backgrounded kitty is is_active
    but NOT is_focused, so tab_focused (which keys on is_focused) never yields a
    false suppress there; this test drives the focused=True case directly."""
    screen = {"txt": _done_screen("\x1b[m❯\xa0")}   # empty box (no input suppress)
    n, cur, done, sent, audited = _notifier_for_done(monkeypatch, screen, delay=0)
    n.fe.focused = True                      # kitty tab is frontmost
    n.scan()                                 # baseline
    cur["states"] = {"9": done}
    n.scan()                                 # arm + fire-time focus check → suppress
    assert sent == []
    assert any(a[2] == "notify-suppress"
               and a[3].get("reason") == "tab-focused" for a in audited)


def test_notify_suppressed_when_web_viewing(monkeypatch):
    """At SEND time, if a browser is actively VIEWING the session (a fresh
    /api/session/<sid>/viewing heartbeat within CLAUDE_DASH_VIEW_TTL_S), the
    off-device alert is dropped — you're watching the dashboard."""
    screen = {"txt": _done_screen("\x1b[m❯\xa0")}
    n, cur, done, sent, audited = _notifier_for_done(monkeypatch, screen, delay=0)
    DS.presence._VIEWING.pop("sX", None)
    DS.mark_viewing("sX")                   # a fresh viewing beat
    try:
        n.scan()                             # baseline
        cur["states"] = {"9": done}
        n.scan()                             # arm + fire-time viewing check → suppress
        assert sent == []
        assert any(a[2] == "notify-suppress"
                   and a[3].get("reason") == "web-viewing" for a in audited)
    finally:
        DS.presence._VIEWING.pop("sX", None)


def test_notify_muted_drop_is_audited(monkeypatch):
    """A per-session MUTE drops the armed alert at send time — with a
    `notify-suppress` `reason='muted'` row. The `notify-arm` anchor promises
    every arm ends in exactly one of suppress / send / telegram, so this drop
    owed a row: unaudited (as it was), a muted session swallowing the
    off-device alert was indistinguishable from the ONE deliberate no-row case
    (you reacted and the tab moved off green — `_drop(win)` with no reason)."""
    screen = {"txt": _done_screen("\x1b[m❯\xa0")}   # empty box, tab not focused
    n, cur, done, sent, audited = _notifier_for_done(monkeypatch, screen, delay=0)
    monkeypatch.setattr(DS.prefs, "notify_muted", lambda sid: True)
    n.scan()                                  # baseline
    cur["states"] = {"9": done}
    n.scan()                                  # arm + fire-time mute check → drop
    assert "9" not in n.pending and sent == []
    assert any(a[2] == "notify-suppress" and a[3].get("reason") == "muted"
               and a[3].get("sid") == "sX" for a in audited)


def test_notify_reaction_drop_leaves_no_row(monkeypatch):
    """The counterpart: when you REACT (the tab leaves the armed state), the arm
    disappears with NO suppress row — deliberately, because the paired
    `tab_transitions` row already explains it. That silence is what makes the
    audited drops above readable, so it is pinned here too."""
    screen = {"txt": _done_screen("\x1b[m❯\xa0")}
    n, cur, done, sent, audited = _notifier_for_done(monkeypatch, screen, delay=999)
    n.scan()                                  # baseline
    cur["states"] = {"9": done}
    n.scan()                                  # arm
    assert "9" in n.pending
    cur["states"] = {"9": "working"}          # you answered — the tab moved on
    n.scan()
    assert "9" not in n.pending and sent == []
    assert not [a for a in audited if a[2] == "notify-suppress"]


def test_web_viewing_presence_expires(monkeypatch):
    """The viewing presence is TTL'd: a beat marks the sid fresh, and once the
    deadline passes (`web_viewing` GC's it) presence is gone — so the alert
    reverts to firing when you stop watching."""
    monkeypatch.setattr(DS.presence, "VIEW_TTL_S", 20)
    clock = [1000.0]
    monkeypatch.setattr(DS.time, "monotonic", lambda: clock[0])
    DS.presence._VIEWING.pop("sZ", None)
    assert DS.web_viewing("sZ") is False
    DS.mark_viewing("sZ")
    assert DS.web_viewing("sZ") is True
    clock[0] += 21                            # past the TTL
    assert DS.web_viewing("sZ") is False
    assert "sZ" not in DS.presence._VIEWING            # GC'd on the miss


def test_presence_maps_stay_bounded(monkeypatch):
    """Both in-memory presence maps are bounded in a days-long singleton — the
    key-set leak `read/cache.py` bounds its memos against, one dict per session /
    per browser ever seen.

    _VIEWING is swept EXACTLY: `web_viewing` only drops the one key it is asked
    about (and the notifier only asks about ARMED sessions), so the beat itself
    reaps the expired ones — nothing live is ever dropped, and only sessions
    actually being watched remain. _DEVICE_SEEN has no expiry by design (the MRU
    pick wants the LAST device you used, however long ago), so it is CAPPED
    instead; eviction drops the least-recently-BEATEN device, which cannot be
    the MRU target."""
    monkeypatch.setattr(DS.presence, "VIEW_TTL_S", 20)
    clock = [5000.0]
    monkeypatch.setattr(DS.time, "monotonic", lambda: clock[0])
    DS.presence._VIEWING.clear()
    for i in range(50):                       # 50 sessions viewed, none re-asked
        DS.mark_viewing("old%d" % i)
    assert len(DS.presence._VIEWING) == 50
    clock[0] += 21                            # every one of them lapsed
    DS.mark_viewing("live1")                 # …the next beat reaps them
    assert set(DS.presence._VIEWING) == {"live1"}
    DS.mark_viewing("live2")                 # a still-fresh entry SURVIVES
    assert set(DS.presence._VIEWING) == {"live1", "live2"}
    assert DS.web_viewing("live1") is True

    DS.presence._DEVICE_SEEN.clear()
    cap = DS.presence.DEVICE_SEEN_CAP
    for i in range(cap + 10):
        clock[0] += 1
        DS.mark_device("dev%d" % i)
    assert len(DS.presence._DEVICE_SEEN) == cap
    assert "dev0" not in DS.presence._DEVICE_SEEN                  # oldest beat evicted
    assert DS.device_seen("dev0") == float("-inf")       # …reads as never seen
    newest = "dev%d" % (cap + 9)
    assert max(DS.presence._DEVICE_SEEN, key=DS.device_seen) == newest   # MRU intact


def test_badge_counts_are_a_table_with_one_scope_owner(monkeypatch, tmp_path):
    """The session tab badges are a TABLE (`read/session.BADGES`) — four cheap
    counts with one shape — not four hand-written stanzas, and not two
    enumerations either.

    Each row carries BOTH names the fact travels under: the payload field
    (`error_count`, what session_payload sets and the page reads off `meta`) and
    the SSE event (`errors`, what the stream pushes). Those genuinely differ on
    the wire, and while the table lived in http/sse.py the two sides were
    enumerated separately — the events here, the `*_count` keys in
    session_payload — so a new badge meant two edits in two vocabularies.

    Its `memory` row is the interesting one: that badge is project-SCOPED, and
    the gate has ONE owner (`read/session.memory_count`) shared with the
    overview payload. The two readings had drifted apart — the payload reported 0
    off-scope while the stream kept pushing the real count, for a tab the page
    never builds there."""
    from dashboard.read import session as rsession
    proj = tmp_path / "code" / "01" / "aggregator-adapters"
    proj.mkdir(parents=True)
    # realpath: canon_cwd resolves symlinks (/var → /private/var on macOS) while
    # memory.project() only abspaths, so the seam has to be given the real path
    monkeypatch.setenv("BAQYLAU_MEMORY_PROJECT", os.path.realpath(str(proj)))
    monkeypatch.setattr(rsession.API, "memory_count", lambda sid: 5)
    off = str(tmp_path / "elsewhere")
    assert rsession.memory_count("s1", str(proj)) == 5
    assert rsession.memory_count("s1", off) == 0
    assert rsession.memory_scope(str(proj)) is True and rsession.memory_scope(off) is False
    table = {b.event: b for b in rsession.BADGES}
    # the (SSE event, payload field) pairs, pinned — the two spellings the same
    # fact travels under, declared once here and derived by both sides
    assert [(b.event, b.field) for b in rsession.BADGES] == [
        ("errors", "error_count"), ("monitors", "monitor_count"),
        ("jobs", "job_count"), ("memory", "memory_count")]
    # the stream's row IS the scope owner — not its own API call beside it
    assert table["memory"].count("s1", str(proj)) == 5
    assert table["memory"].count("s1", off) == 0
    # every row resolves its count at CALL time, so a patched sessionapi moves
    # the pushed number (a class-body-bound callable would have frozen it)
    monkeypatch.setattr(rsession.API, "error_count", lambda sid: 3)
    assert table["errors"].count("s1", off) == 3


def test_session_stream_channels_are_a_derived_table():
    """Everything the per-session stream pushes on change is a CHANNEL row
    (`_SLOW_CHANS` / `_FAST_CHANS`), and the per-connection last-sent map is
    DERIVED from those tables plus the inline keys — not a hand-written literal
    beside them.

    The literal had already drifted: `view_mode` was pushed every slow tick but
    never listed, so its slot only existed because `_push_changed` reads through
    `prev.get`. A derived map cannot drift, and the table is what let the loop
    stop carrying twenty locals in one flat scope (see the live-ops test below
    for what that scope cost)."""
    chans = DS.sse._SLOW_CHANS + DS.sse._FAST_CHANS
    keys = [c.key for c in chans]
    assert len(keys) == len(set(keys)), keys          # one slot per channel
    m = DS.sse._prev_map()
    assert set(m) == set(keys) | set(DS.sse._INLINE_KEYS)
    assert all(v is None for v in m.values())
    assert "view_mode" in m                           # the one the literal lost
    # the four badges are rows of this table, derived from the read model's
    # BADGES (one owner) — and their `prev` slot is the PAYLOAD field, so a
    # connection's last-sent map speaks the same vocabulary the initial payload
    # used rather than a second one
    from dashboard.read import session as rsession
    fields = [b.field for b in rsession.BADGES]
    assert [c.key for c in chans if c.key in fields] == fields
    assert [c.event for c in chans if c.key in fields] \
        == [b.event for b in rsession.BADGES]
    # a wrapped channel names ONE field; a verbatim one names none
    for c in chans:
        assert c.wrap is None or (isinstance(c.wrap, str) and c.wrap)


def test_live_ops_carry_the_session_key_not_a_badge_name(dash):
    """Every op the stream appends LIVE is stamped with the session's own
    mirror-log key — the key its ⧉ copy / click-to-view links resolve against
    (`data-cc="<key>/<g>/<what>"`).

    Regression (shipped 2026-07-25, bcc00a3): the tab-badge stanza inside the
    tick loop was `for key, count in the badge table's items()`, which rebound the
    loop's own `key` local — the session's mirror-log key, resolved once before
    the loop. From the SECOND tick on every live-streamed block was stamped
    `memory` (the badge table's last row), so its copy link resolved to a
    session that does not exist: an empty copy, a 404 view, and a `dashboard
    copy (state DB gone)` errors row lighting the ⚠ chip — while a reload read
    fine, because the backlog path passes the key before the loop. The same
    stanza rebound the tick counter to the memory-note count, making the SLOW
    cadence a function of the data (4 notes ⇒ every 'slow' probe, including the
    ghost-suggestion screen scrape, ran every tick). Both names are gone: the
    badges are table rows now, and no loop in the tick body binds either."""
    sid = "cckey"
    A.session_start({"session_id": sid, "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log(sid)
    seen = []
    r = _req(dash + "/events/session/%s?after=0&mpos=0" % sid, timeout=20)
    try:
        pending = None
        O.emit(log, O.label("▶ one", (1, 2, 3), g="g1"))
        for raw in r:
            line = raw.decode("utf-8", "replace").rstrip("\n")
            if line.startswith("event: "):
                pending = line[len("event: "):]
            elif line.startswith("data: ") and pending == "ops":
                items = json.loads(line[len("data: "):])["items"]
                html = "".join(it["html"] for it in items)
                if "data-cc" not in html:
                    continue
                seen.append(html)
                if len(seen) == 1:
                    # a SECOND op, necessarily delivered on a LATER tick — the
                    # tick from which the clobbered key used to show up
                    O.emit(log, O.label("▶ two", (1, 2, 3), g="g2"))
                else:
                    break
    finally:
        r.close()
    assert len(seen) == 2, seen
    for html in seen:
        assert 'data-cc="%s/' % sid in html, html
        assert "memory/" not in html


def test_notify_done_suppressed_when_seen_earlier_then_left(monkeypatch):
    """The user's rule: 'if I've SEEN the final message on the dashboard, no
    notification.' A done arm is checked EVERY scan while armed (not only at
    send time), so a single glance during the grace cancels it even after you
    navigate away — you don't need to be pinged about a result you already read."""
    screen = {"txt": _done_screen("\x1b[m❯\xa0")}   # empty box, not focused
    n, cur, done, sent, audited = _notifier_for_done(monkeypatch, screen, delay=999)
    n.scan()                                  # baseline
    cur["states"] = {"9": done}
    n.scan()                                  # arm — not watching yet
    assert "9" in n.pending
    DS.presence._VIEWING.pop("sX", None)
    DS.mark_viewing("sX")                    # you GLANCE at the final message
    n.scan()                                  # per-scan 'seen it' → dropped
    DS.presence._VIEWING.pop("sX", None)               # the glance is over; you moved on
    assert "9" not in n.pending
    assert any(a[2] == "notify-suppress"
               and a[3].get("reason") == "web-viewing" for a in audited)
    monkeypatch.setattr(DS.config, "NOTIFY_DELAY_S", 0)
    n.scan()                                  # grace passes — still nothing fires
    assert sent == []


def test_notify_asking_still_fires_after_earlier_glance(monkeypatch):
    """Deliberate asymmetry vs `done`: for an ASKING arm a mere earlier glance
    does NOT suppress — seeing the question isn't answering it, so if you looked
    then walked away without answering, the reminder must still fire. (Only
    looking RIGHT NOW at send time, or answering at the terminal, suppresses an
    ask.)"""
    screen = {"txt": "☒ Q\n❯ 1. Yes\n  2. No\nEnter to select"}
    n, cur, asking, sent, _ = _notifier_for_asking(monkeypatch, screen, delay=999)
    n.scan()                                  # baseline
    cur["states"] = {"9": asking}
    n.scan()                                  # arm
    assert "9" in n.pending
    DS.presence._VIEWING.pop("sX", None)
    DS.mark_viewing("sX")                    # you GLANCE at the ask on the dashboard
    n.scan()                                  # NOT cancelled — asking ignores a glance
    DS.presence._VIEWING.pop("sX", None)               # ...and you leave without answering
    assert "9" in n.pending
    monkeypatch.setattr(DS.config, "NOTIFY_DELAY_S", 0)
    n.scan()                                  # send time, not looking now → fires
    assert sent and sent[0]["sid"] == "sX"


def _pump_global(r, got, events=("sessions", "sessions-delta")):
    """Collect (event, data) frames from a global-SSE response into `got`."""
    def pump():
        pending = None
        try:
            for raw in r:
                line = raw.decode("utf-8", "replace").rstrip("\n")
                if line.startswith("event: "):
                    pending = line[len("event: "):]
                elif line.startswith("data: ") and pending in events:
                    got.append((pending, line[len("data: "):]))
        except Exception:
            pass                               # stream torn down by r.close()
    threading.Thread(target=pump, daemon=True).start()


def test_global_sse_diff_is_paused_blind(dash, monkeypatch):
    """The global stream's per-row change detection (row_key) ignores
    stats['paused'] — the scorebar's ~1/s awaiting-pause accumulator made the
    snapshot differ on EVERY tick, forcing a full resend + client list
    re-render per second on an idle dashboard. A paused-only bump must push
    NOTHING (no snapshot, no delta); a real change must — and its row still
    carries the exact paused value (only the DIFF is paused-blind)."""
    import time
    monkeypatch.setattr(DS.config, "GLOBAL_TICK_S", 0.05)
    A.session_start({"session_id": "dashg", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("dashg")
    S.incr(log, commands=1)
    got = []
    r = _req(dash + "/events")
    _pump_global(r, got)
    try:
        wait_until(lambda: len(got) == 1, desc="initial sessions snapshot")
        assert got[0][0] == "sessions"
        S.incr(log, paused=1.25)               # the scorebar's awaiting bump
        time.sleep(0.5)                        # many ticks — must stay silent
        assert len(got) == 1
        S.incr(log, commands=1)                # a real change still pushes
        wait_until(lambda: len(got) >= 2, desc="push after a real change")
        ev, data = got[-1]
        rows = json.loads(data)
        rows = rows["rows"] if ev == "sessions-delta" else rows
        row = next(x for x in rows if x["sid"] == "dashg")
        assert row["stats"].get("commands") == 2
        assert row["stats"].get("paused") == 1.25
    finally:
        r.close()


def test_global_sse_delta_and_resync(dash, monkeypatch):
    """The wire protocol (docs/dashboard.md, *The list renders once, then
    patches*): a content-only change rides a `sessions-delta` carrying ONLY
    the changed rows; a membership change (new session) forces a full
    `sessions` resync — a delta can't express insertion. Wire rows are
    stripped of the server-side paths (`transcript_path`, `log`) on both the
    SSE and /api/sessions."""
    monkeypatch.setattr(DS.config, "GLOBAL_TICK_S", 0.05)
    A.session_start({"session_id": "dashd1", "cwd": "/w",
                     "transcript_path": "/w/t1.jsonl"})
    A.session_start({"session_id": "dashd2", "cwd": "/w",
                     "transcript_path": "/w/t2.jsonl"})
    for row in _get_json(dash + "/api/sessions"):
        assert "transcript_path" not in row and "log" not in row
    got = []
    r = _req(dash + "/events")
    _pump_global(r, got)
    try:
        wait_until(lambda: len(got) == 1, desc="initial snapshot")
        S.incr(P.mirror_log("dashd1"), commands=1)     # content-only change
        # the DB-file creation and the counter commit can land on different
        # ticks (two deltas) — wait for the delta that carries the value
        def delta_rows():
            for ev, data in got[1:]:
                if ev == "sessions-delta":
                    rows = json.loads(data)["rows"]
                    row = next((x for x in rows if x["sid"] == "dashd1"), None)
                    if row and row["stats"].get("commands") == 1:
                        return rows
            return None
        wait_until(lambda: delta_rows() is not None,
                   desc="delta carrying the row change")
        rows = delta_rows()
        assert [x["sid"] for x in rows] == ["dashd1"]  # ONLY the changed row
        assert "transcript_path" not in rows[0] and "log" not in rows[0]
        assert all(ev == "sessions-delta" for ev, _ in got[1:])  # no resyncs
        n = len(got)
        A.session_start({"session_id": "dashd3", "cwd": "/w",
                         "transcript_path": ""})       # membership change
        wait_until(lambda: len(got) > n, desc="resync after a new session")
        ev, data = got[-1]
        assert ev == "sessions"                        # full snapshot, not delta
        assert any(x["sid"] == "dashd3" for x in json.loads(data))
    finally:
        r.close()


def test_accounts_strip_sse_push_is_score_blind(dash, monkeypatch):
    """The accounts strip rides the global stream: an `accounts` event carrying
    the full /api/accounts payload whenever it CHANGES. The diff is
    sched_score-blind (lists.accounts_key) — the score is remaining%/hours-to-
    reset and moves with the clock on every tick, so a full-payload diff would
    push perpetually on an idle dashboard. Connect pushes nothing (the page
    boot-fetches /api/accounts); a real usage change pushes a payload that
    still carries the exact score."""
    from core import sessionapi as API_MOD
    monkeypatch.setattr(DS.config, "GLOBAL_TICK_S", 0.05)
    now = time.time()
    usage = {"ts": now, "five_hour": 12, "five_hour_reset": now + 3600,
             "seven_day": 40, "seven_day_reset": now + 86400}
    monkeypatch.setattr(plugins, "accounts",
                        lambda: [{"slug": "c1", "label": "acct", "alias": "c1"}])
    monkeypatch.setattr(API_MOD, "account_usage", lambda limit=50, cache=None: {
        "c1": {"usage": dict(usage), "limit_hit": None, "logged_out": None}})
    got = []
    r = _req(dash + "/events")
    _pump_global(r, got, events=("sessions", "accounts"))
    try:
        wait_until(lambda: len(got) == 1, desc="initial sessions snapshot")
        assert got[0][0] == "sessions"
        time.sleep(0.5)                        # many ticks — sched_score moved
        assert len(got) == 1                   # …but the strip stayed silent
        usage["five_hour"] = 55                # a real snapshot change
        wait_until(lambda: any(ev == "accounts" for ev, _ in got),
                   desc="accounts push after a usage change")
        ev, data = got[-1]
        assert ev == "accounts"
        row = next(a for a in json.loads(data) if a["slug"] == "c1")
        assert row["usage"]["five_hour"] == 55
        assert isinstance(row["sched_score"], (int, float))  # payload keeps it
    finally:
        r.close()


def test_ops_endpoint_is_main_agent_only(dash, monkeypatch):
    """core.ops.emit stamps the producer source (ambient set_src/$CLAUDE_OPS_SRC
    or the explicit src= kwarg) and the dashboard's ops payload drops stamped
    ops — while the raw stream (what the terminal mirror paints) keeps them."""
    # Isolate the module-level ambient stamp; the env var itself is only
    # touched via monkeypatch so nothing leaks into later subprocess spawns.
    monkeypatch.setattr(O, "_SRC", None)
    monkeypatch.setattr(O, "_SRC_INIT", True)
    A.session_start({"session_id": "dashsrc", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("dashsrc")
    O.emit(log, O.label("lead header", (1, 2, 3)))            # main: unstamped
    O.emit(log, O.line("agent monitor line"), src="sub:a1")   # explicit kwarg
    monkeypatch.setattr(O, "_SRC", "team:t1")                 # ambient (set_src)
    O.emit(log, O.line("teammate line"))
    monkeypatch.setattr(O, "_SRC", None)
    # the lazy $CLAUDE_OPS_SRC read (what a spawned tailer relies on)
    monkeypatch.setenv("CLAUDE_OPS_SRC", "codex:review")
    monkeypatch.setattr(O, "_SRC_INIT", False)
    O.emit(log, O.line("codex line"))
    monkeypatch.setattr(O, "_SRC", None)
    monkeypatch.setattr(O, "_SRC_INIT", True)

    _last, raw = S.ops_after(log, 0)
    assert [op.get("src") for op in raw] == \
        [None, "sub:a1", "team:t1", "codex:review"]
    d = _get_json(dash + "/api/session/dashsrc/ops?after=0")
    assert d["last"] == 4, "stamped ops still advance the cursor"
    assert len(d["items"]) == 1 and "lead header" in d["items"][0]["html"], \
        "only the main session's op survives to the web stream"


def _sse_event(url, want, timeout=10):
    """Read a per-session SSE stream until an `event: <want>` frame arrives and
    return its data payload (raw JSON string); '' on timeout/EOF."""
    r = _req(url, timeout=timeout)
    try:
        pending = None
        for raw in r:
            line = raw.decode("utf-8", "replace").rstrip("\n")
            if line.startswith("event: "):
                pending = line[len("event: "):]
            elif line.startswith("data: ") and pending == want:
                return line[len("data: "):]
        return ""
    finally:
        r.close()


def test_running_ribbon_payload_and_sse(dash):
    """session_payload carries the live-slot ribbon (sessionapi.running()), and
    the per-session SSE announces it as a `running` event."""
    from core import slots
    A.session_start({"session_id": "run1", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("run1")
    slots.claim("monitor", log)                    # owned by THIS process -> alive
    slots.pid_set(log, "agentR", os.getpid())
    run = _get_json(dash + "/api/session/run1")["running"]
    assert "monitor" in run and run["monitor"][0]["alive"] is True
    assert "sub.pid" in run and run["sub.pid"][0]["key"] == "agentR"
    data = _sse_event(dash + "/events/session/run1?after=0&mpos=0", "running")
    assert data and "monitor" in json.loads(data)


def test_fg_elapsed_payload_and_sse(dash):
    """The live command-elapsed feed (docs/dashboard.md, *Live command
    elapsed*): session_payload carries the in-flight foreground command as
    `fg_running` {g, start_ts} — g being the mirror block's copy-group id — and
    the per-session SSE announces it as a fast-cadence `fgrun` event, so a page
    opened mid-command starts ticking from the real start."""
    A.session_start({"session_id": "fgr1", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("fgr1")
    S.hand_put(log, "fg-live", {"src": log + ".out", "own": True,
                                "pid": os.getpid(), "done": log + ".done",
                                "tid": "toolu_live", "ts": 1700.0})
    fg = _get_json(dash + "/api/session/fgr1")["fg_running"]
    assert fg == {"g": "toolu_live", "start_ts": 1700.0}
    data = _sse_event(dash + "/events/session/fgr1?after=0&mpos=0", "fgrun")
    assert data and json.loads(data)["fg"] == fg
    # nothing in flight -> null (the client retires its ticker)
    S.hand_del(log, "fg-live")
    assert _get_json(dash + "/api/session/fgr1")["fg_running"] is None


def test_error_badge_payload_and_sse(dash):
    """session_payload carries the live ⚠ error count (error_count, chain-aware
    COUNT — not len(errors())), and the per-session SSE announces it as an
    `errors` {count} event on the slow cadence."""
    A.session_start({"session_id": "errS", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("errS")
    A.error(log, "boom", {"n": 1})
    A.error(log, "bang", {"n": 2})
    ov = _get_json(dash + "/api/session/errS")
    assert ov["error_count"] == 2
    data = _sse_event(dash + "/events/session/errS?after=0&mpos=0", "errors")
    assert data and json.loads(data)["count"] == 2


def test_tasks_card_payload_and_sse(dash):
    """session_payload carries the pinned tasks card's list (the `tasks` kv
    task_fmt.py snapshots from the on-disk task dir — docs/dashboard.md, *Web
    tasks*; NOT live-gated, a parked session keeps its final list), and the
    per-session SSE announces it as a `tasks` event on the slow cadence."""
    A.session_start({"session_id": "tsk1", "cwd": "/w", "transcript_path": ""})
    tasks = [{"id": "1", "subject": "Ship it", "status": "completed",
              "blocks": [], "blockedBy": []},
             {"id": "2", "subject": "Test it", "status": "in_progress",
              "activeForm": "Testing it", "blocks": [], "blockedBy": ["1"]}]
    S.kv_set(P.mirror_log("tsk1"), "tasks", {"tasks": tasks})
    ov = _get_json(dash + "/api/session/tsk1")
    assert [t["id"] for t in ov["tasks"]] == ["1", "2"]
    assert ov["tasks"][0]["status"] == "completed"
    data = _sse_event(dash + "/events/session/tsk1?after=0&mpos=0", "tasks")
    assert data and json.loads(data)["tasks"][1]["activeForm"] == "Testing it"
    # an emptied list reads as None — the card hides
    S.kv_set(P.mirror_log("tsk1"), "tasks", {"tasks": []})
    assert _get_json(dash + "/api/session/tsk1")["tasks"] is None


def test_ask_draft_persist_payload_and_sse(dash, monkeypatch):
    """The web ask card's UNSUBMITTED selections survive a device switch (docs/
    dashboard.md, *Web ask*): POST /ask-draft writes the `ask-draft` kv (a pure
    state write — no terminal keys), the session snapshot carries `ask_draft`,
    and the per-session SSE re-broadcasts it as an `ask-draft` event so a peer
    card tracks the edits. A stale tool_use_id is refused."""
    monkeypatch.setenv("KITTY_WINDOW_ID", "55")
    A.session_start({"session_id": "adr1", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("adr1")
    qs = [{"question": "Which fruit?", "header": "Fruit",
           "options": [{"label": "Apple"}, {"label": "Banana"}],
           "multiSelect": False}]
    S.kv_set(log, "ask-pending", {"tool_use_id": "tu1", "questions": qs})
    ov = _get_json(dash + "/api/session/adr1")
    assert ov["ask"]["tool_use_id"] == "tu1" and ov["ask_draft"] is None
    # persist a selection — no terminal write, so no frontend needed
    body = {"tool_use_id": "tu1", "origin": "devA",
            "answers": [{"selected": ["Banana"], "other": ""}]}
    code, resp = _post(dash + "/api/session/adr1/ask-draft", body)
    assert code == 200 and json.loads(resp)["ok"]
    draft = S.kv_get(log, "ask-draft")
    assert draft["answers"][0]["selected"] == ["Banana"] and draft["origin"] == "devA"
    assert _get_json(dash + "/api/session/adr1")["ask_draft"]["answers"][0][
        "selected"] == ["Banana"]
    data = _sse_event(dash + "/events/session/adr1?after=0&mpos=0", "ask-draft")
    assert data and json.loads(data)["draft"]["origin"] == "devA"
    # a draft for a REPLACED/gone question is refused (409), draft untouched
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/adr1/ask-draft",
              {"tool_use_id": "OLD", "origin": "devA",
               "answers": [{"selected": [], "other": ""}]})
    assert e.value.code == 409
    assert S.kv_get(log, "ask-draft")["answers"][0]["selected"] == ["Banana"]


def test_ask_draft_tolerates_non_dict_answers(dash, monkeypatch):
    # answers is only LENGTH-validated; a non-dict element (malformed body) must
    # not reach `.get()` and raise AttributeError -> 500. It normalizes to an
    # empty selection (the old inline isinstance guard on `selected` was inert).
    A.session_start({"session_id": "adr3", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("adr3")
    qs = [{"question": "Which fruit?", "options": [{"label": "Apple"}],
           "multiSelect": False}]
    S.kv_set(log, "ask-pending", {"tool_use_id": "tu1", "questions": qs})
    code, resp = _post(dash + "/api/session/adr3/ask-draft",
                       {"tool_use_id": "tu1", "origin": "d", "answers": ["oops"]})
    assert code == 200 and json.loads(resp)["ok"]
    assert S.kv_get(log, "ask-draft")["answers"] == [{"selected": [], "other": ""}]


def test_ask_payload_carries_preamble_html(dash, monkeypatch, tmp_path):
    """The ask card shows Claude's prose LEAD-IN to the question (docs/
    dashboard.md, *Web ask*): the ask payload gains `preamble_html` — the
    preceding assistant message, rendered with the msg-bubble md_html (bold
    survives, html-escaped). Empty when the ask has no framing text."""
    monkeypatch.setenv("KITTY_WINDOW_ID", "77")
    tr = tmp_path / "pre.jsonl"
    tr.write_text("".join(json.dumps(o) + "\n" for o in [
        {"type": "assistant", "message": {"id": "m1", "content": [
            {"type": "text", "text": "Two **separate** problems here."}]}},
        {"type": "assistant", "message": {"id": "m2", "content": [
            {"type": "tool_use", "id": "tuX", "name": "AskUserQuestion",
             "input": {"questions": [
                 {"question": "Which?", "options": [{"label": "A"}]}]}}]}},
    ]), encoding="utf-8")
    A.session_start({"session_id": "pre1", "cwd": "/w",
                     "transcript_path": str(tr)})
    log = P.mirror_log("pre1")
    qs = [{"question": "Which?", "options": [{"label": "A"}]}]
    S.kv_set(log, "ask-pending", {"tool_use_id": "tuX", "questions": qs})
    ask = _get_json(dash + "/api/session/pre1")["ask"]
    assert ask["tool_use_id"] == "tuX"
    assert "<strong>separate</strong>" in ask["preamble_html"]
    assert "Two" in ask["preamble_html"]
    # a question whose tool_use_id has no framing text -> empty, never absent
    S.kv_set(log, "ask-pending", {"tool_use_id": "gone", "questions": qs})
    ask2 = _get_json(dash + "/api/session/pre1")["ask"]
    assert ask2["preamble_html"] == ""


def test_composer_draft_persist_payload_and_sse(dash, monkeypatch):
    """The web composer's UNSENT message survives a device switch / reopen /
    return-to-session (docs/dashboard.md, *Web composer draft*): POST
    /composer-draft writes the `composer-draft` kv (a pure state write — no
    terminal keys), the session snapshot carries `composer_draft`, and the
    per-session SSE re-broadcasts it as a `composer-draft` event so a peer box
    tracks the edits. An emptied box deletes the stash (the card clears)."""
    monkeypatch.setenv("KITTY_WINDOW_ID", "56")
    A.session_start({"session_id": "cdr1", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("cdr1")
    O.emit(log, O.label("hi", (1, 2, 3)))    # a live session has a state DB
    assert _get_json(dash + "/api/session/cdr1")["composer_draft"] is None
    # persist a half-typed message — no terminal write, so no frontend needed
    code, resp = _post(dash + "/api/session/cdr1/composer-draft",
                       {"text": "half a thought", "origin": "devA"})
    assert code == 200 and json.loads(resp)["ok"]
    draft = S.kv_get(log, "composer-draft")
    assert draft["text"] == "half a thought" and draft["origin"] == "devA"
    assert _get_json(dash + "/api/session/cdr1")["composer_draft"]["text"] \
        == "half a thought"
    data = _sse_event(dash + "/events/session/cdr1?after=0&mpos=0",
                      "composer-draft")
    assert data and json.loads(data)["draft"]["origin"] == "devA"
    # an emptied / whitespace-only box clears the stash → payload None (the
    # kv keeps an empty-text TOMBSTONE so a later stale seq can be rejected —
    # composer_draft reads a tombstone as None either way)
    code, _ = _post(dash + "/api/session/cdr1/composer-draft",
                    {"text": "   ", "origin": "devA"})
    assert code == 200
    assert ((S.kv_get(log, "composer-draft") or {}).get("text") or "") == ""
    assert _get_json(dash + "/api/session/cdr1")["composer_draft"] is None


def test_composer_draft_stale_seq_ignored(dash, monkeypatch):
    """The clear-on-send must win over a debounced save that races it over a
    slow link (docs/dashboard.md, *Web composer draft*; the "draft didn't clear
    after send" report, 2026-07-19). Each write carries a wall-clock `seq`; a
    write OLDER than what's stored is dropped, and the clear keeps a seq'd
    tombstone so a late straggler can't resurrect the just-sent draft."""
    monkeypatch.setenv("KITTY_WINDOW_ID", "57")
    A.session_start({"session_id": "cds1", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("cds1")
    O.emit(log, O.label("hi", (1, 2, 3)))
    # the clear (seq 100) lands first, then a stale save (seq 90) arrives late
    _post(dash + "/api/session/cds1/composer-draft",
          {"text": "", "origin": "d", "seq": 100})
    code, resp = _post(dash + "/api/session/cds1/composer-draft",
                       {"text": "resurrected!", "origin": "d", "seq": 90})
    assert code == 200 and json.loads(resp).get("stale") is True
    assert _get_json(dash + "/api/session/cds1")["composer_draft"] is None
    # a genuinely newer save (seq 110) is honored
    code, _ = _post(dash + "/api/session/cds1/composer-draft",
                    {"text": "typed again", "origin": "d", "seq": 110})
    assert _get_json(dash + "/api/session/cds1")["composer_draft"]["text"] \
        == "typed again"


def test_composer_draft_stale_seq_atomic_under_concurrency(dash, monkeypatch):
    """The seq guard must hold when the racing writes land in two CONCURRENT
    server threads, not just in order (the ThreadingHTTPServer TOCTOU: a queued
    send's clear lost to its own in-flight debounced save because the guard's
    read and its write straddled the peer thread's write, 2026-07-22). The
    higher-seq CLEAR must always win regardless of which thread commits last —
    the compare-and-set is one BEGIN IMMEDIATE, so the lower-seq save can never
    resurrect the just-sent draft."""
    monkeypatch.setenv("KITTY_WINDOW_ID", "59")
    A.session_start({"session_id": "cdc1", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("cdc1")
    O.emit(log, O.label("hi", (1, 2, 3)))
    url = dash + "/api/session/cdc1/composer-draft"
    # Fire the two racing writes together many times: a lower-seq SAVE (the
    # debounced draft) and a higher-seq CLEAR (the send). The clear must win
    # every round. Under the old read-then-write guard the save would sometimes
    # commit last and stick; the atomic CAS makes the invariant deterministic.
    for i in range(40):
        base = 1000 + i * 10
        # prime a stored draft older than both so neither is rejected on read
        _post(url, {"text": "old", "origin": "d", "seq": base})
        def fire(seq, text):
            _post(url, {"text": text, "origin": "d", "seq": seq})
        save = threading.Thread(target=fire, args=(base + 1, "resurrect"))
        clear = threading.Thread(target=fire, args=(base + 2, ""))
        save.start(); clear.start()
        save.join(); clear.join()
        assert _get_json(dash + "/api/session/cdc1")["composer_draft"] is None, \
            "round %d: the lower-seq save resurrected a cleared draft" % i


def test_composer_queue_persist_payload_and_sse(dash, monkeypatch):
    """The pending ⧗ queued-message chips survive a reload (docs/dashboard.md,
    *Web composer queue*; the "gone even from the queue after refresh" report):
    POST /composer-queue writes the `composer-queue` kv (a pure state write —
    no terminal keys), the snapshot carries `composer_queue`, and the SSE
    re-broadcasts it. An empty list deletes the stash."""
    monkeypatch.setenv("KITTY_WINDOW_ID", "58")
    A.session_start({"session_id": "cq1", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("cq1")
    O.emit(log, O.label("hi", (1, 2, 3)))
    assert _get_json(dash + "/api/session/cq1")["composer_queue"] is None
    code, resp = _post(dash + "/api/session/cq1/composer-queue",
                       {"items": [{"text": "do X"}, {"text": "then Y"}],
                        "origin": "devA"})
    assert code == 200 and json.loads(resp)["ok"]
    q = _get_json(dash + "/api/session/cq1")["composer_queue"]
    assert [it["text"] for it in q["items"]] == ["do X", "then Y"]
    data = _sse_event(dash + "/events/session/cq1?after=0&mpos=0",
                      "composer-queue")
    assert data and json.loads(data)["queue"]["origin"] == "devA"
    # an empty list (all delivered / hidden) deletes the stash → payload None
    code, _ = _post(dash + "/api/session/cq1/composer-queue",
                    {"items": [], "origin": "devA"})
    assert code == 200
    assert _get_json(dash + "/api/session/cq1")["composer_queue"] is None


def test_composer_queue_tolerates_non_string_text(dash, monkeypatch):
    # a non-string `text` (malformed body) must not raise AttributeError on
    # .strip() -> 500; both the filter and the value str() it. A number stays a
    # chip (its str), a falsy 0 drops out.
    A.session_start({"session_id": "cq2", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("cq2")
    O.emit(log, O.label("hi", (1, 2, 3)))     # materialize the state DB
    code, resp = _post(dash + "/api/session/cq2/composer-queue",
                       {"items": [{"text": 5}, {"text": "real"}, {"text": 0},
                                  "notadict"], "origin": "d"})
    assert code == 200 and json.loads(resp)["ok"]
    q = S.kv_get(log, "composer-queue")
    assert [it["text"] for it in q["items"]] == ["5", "real"]


def test_composer_queue_reconciles_delivered_chips(dash, monkeypatch, tmp_path):
    """A ⧗ chip whose message has ALREADY been delivered is reconciled out of
    the snapshot server-side (docs/dashboard.md, *Web composer queue*; the "still
    shows as queued after it was delivered" report). The client-side drain only
    matches NEW stream items, so a chip persisted by a client that then closed /
    reloaded before delivery re-seeded from the kv forever — buildQueueBar found
    the delivered prompt already in the backlog with no fresh item to drain it.
    `composer_queue` now drops any chip whose prompt appears among the
    transcript's delivered prompts (exact, or the tolerant attachment-prefix
    match `@path\\n<text>`), while a still-pending chip survives."""
    tr = tmp_path / "cq.jsonl"
    tr.write_text("".join(json.dumps(o) + "\n" for o in [
        # two DELIVERED queued messages (the TUI's queued_command attachment,
        # surfaced as prompts) — one plain, one with a leading @path mention.
        {"type": "attachment", "attachment": {
            "type": "queued_command", "commandMode": "prompt",
            "prompt": "deliver me"}},
        {"type": "attachment", "attachment": {
            "type": "queued_command", "commandMode": "prompt",
            "prompt": "@img.png\nwith attach"}},
    ]), encoding="utf-8")
    A.session_start({"session_id": "cq3", "cwd": "/w",
                     "transcript_path": str(tr)})
    log = P.mirror_log("cq3")
    O.emit(log, O.label("hi", (1, 2, 3)))     # materialize the state DB
    S.kv_set(log, "composer-queue", {"items": [
        {"text": "deliver me"},        # exact match -> reconciled out
        {"text": "with attach"},       # @path-prefix match -> reconciled out
        {"text": "still pending"},     # not delivered -> survives
    ], "origin": "devA"})
    q = _get_json(dash + "/api/session/cq3")["composer_queue"]
    assert [it["text"] for it in q["items"]] == ["still pending"]
    assert q["origin"] == "devA"         # unrelated fields preserved
    # and when EVERY chip has been delivered, the payload collapses to None
    S.kv_set(log, "composer-queue", {"items": [{"text": "deliver me"}],
                                     "origin": "devA"})
    assert _get_json(dash + "/api/session/cq3")["composer_queue"] is None


def test_ns_prefs_roundtrip(dash):
    """The new-session form's last-used {cwd, model, effort} live on the backend
    now (docs/dashboard.md, *New-session prefs*) so a launch on one device
    pre-selects on the next: GET /api/ns-prefs is {} until a POST remembers a
    launch, then reads it back. model/effort are validated against the launch
    allowlists — a bad value is dropped, never stored."""
    assert _get_json(dash + "/api/ns-prefs") == {}
    code, resp = _post(dash + "/api/ns-prefs",
                       {"cwd": "/proj", "model": "opus", "effort": "high"})
    assert code == 200 and json.loads(resp)["ok"]
    assert _get_json(dash + "/api/ns-prefs") == {
        "cwd": "/proj", "model": "opus", "effort": "high"}
    # a bad effort is dropped, the good fields still persist
    _post(dash + "/api/ns-prefs",
          {"cwd": "/proj2", "model": "sonnet", "effort": "bogus"})
    assert _get_json(dash + "/api/ns-prefs") == {"cwd": "/proj2",
                                                 "model": "sonnet"}


def test_ns_draft_roundtrip_stale_guard_and_audit(dash):
    """The new-session form's UNSENT first prompts (docs/dashboard.md,
    *New-session draft*): GET /api/ns-draft is the whole {cwd: {text, seq}} map
    — a draft PER DIRECTORY, so two projects never share one — empty until a
    POST saves one; a blank text is a CLEAR (an empty-text tombstone, not a
    delete, so its seq survives); a write with an older seq is DROPPED (the
    stale-write guard that stops a debounced save from resurrecting a launched
    prompt) and the guard is per directory; and every write leaves a global
    `ns-draft` state_files row carrying the directory + the LENGTH, never the
    text."""
    assert _get_json(dash + "/api/ns-draft") == {}
    code, resp = _post(dash + "/api/ns-draft",
                       {"cwd": "/projA", "text": "half typed", "seq": 10})
    assert code == 200 and json.loads(resp)["ok"]
    assert _get_json(dash + "/api/ns-draft") == {
        "/projA": {"text": "half typed", "seq": 10}}
    # a second directory keeps its OWN draft — neither touches the other
    _post(dash + "/api/ns-draft", {"cwd": "/projB", "text": "other idea", "seq": 11})
    assert _get_json(dash + "/api/ns-draft") == {
        "/projA": {"text": "half typed", "seq": 10},
        "/projB": {"text": "other idea", "seq": 11}}
    # a STALE write (older seq than THAT directory's) is dropped
    code, resp = _post(dash + "/api/ns-draft",
                       {"cwd": "/projA", "text": "older text", "seq": 5})
    assert code == 200 and json.loads(resp)["stale"]
    assert _get_json(dash + "/api/ns-draft")["/projA"]["text"] == "half typed"
    # a blank box clears that directory — but keeps its seq, so a straggler
    # can't resurrect it (and the other directory is untouched)
    _post(dash + "/api/ns-draft", {"cwd": "/projA", "text": "   ", "seq": 20})
    assert _get_json(dash + "/api/ns-draft")["/projA"] == {"text": "", "seq": 20}
    _post(dash + "/api/ns-draft", {"cwd": "/projA", "text": "half typed", "seq": 15})
    drafts = _get_json(dash + "/api/ns-draft")
    assert drafts["/projA"]["text"] == "" and drafts["/projB"]["text"] == "other idea"
    # a missing cwd is the ""-directory bucket (the form with nothing typed yet)
    _post(dash + "/api/ns-draft", {"text": "no dir yet", "seq": 21})
    assert _get_json(dash + "/api/ns-draft")[""]["text"] == "no dir yet"
    # a non-string text/cwd is an input reject, not a stored draft
    for bad in ({"cwd": "/projA", "text": 7, "seq": 30},
                {"cwd": 7, "text": "x", "seq": 30}):
        with pytest.raises(urllib.error.HTTPError) as e:
            _post(dash + "/api/ns-draft", bad)
        assert e.value.code == 400
    assert _get_json(dash + "/api/ns-draft")["/projA"]["seq"] == 20
    rows = _state_rows("ns-draft")
    assert {"action": "write", "cwd": "/projA", "chars": len("half typed"),
            "seq": 10} in rows
    assert {"action": "stale", "cwd": "/projA", "seq": 5} in rows
    assert {"action": "clear", "cwd": "/projA", "chars": 0, "seq": 20} in rows
    assert not any("text" in r for r in rows)     # the prose never lands in the audit


def test_ns_draft_map_is_pruned(dash):
    """The per-directory draft map is bounded: writing more than NS_DRAFT_MAX
    directories keeps the most RECENT ones (by seq — tombstones included, since
    recency is what decides) so a row per directory ever typed into can't
    accumulate forever."""
    n = prefs.NS_DRAFT_MAX + 5
    for i in range(n):
        _post(dash + "/api/ns-draft",
              {"cwd": "/p%02d" % i, "text": "draft %d" % i, "seq": 100 + i})
    drafts = _get_json(dash + "/api/ns-draft")
    assert len(drafts) == prefs.NS_DRAFT_MAX
    assert "/p%02d" % (n - 1) in drafts          # the newest survived
    assert "/p00" not in drafts                  # the oldest was pruned


def test_prefs_store_failures_are_audited(monkeypatch, tmp_path):
    """Every swallow site in the durable global prefs store leaves an audit
    `errors` row — the audit-before-swallow invariant, which this module used to
    break at all five of them (a locked/corrupt/unwritable prefs DB lost the
    toggle, the draft or the rename with NO trace, and mutate_map's callers still
    answer ok:True, so 'it didn't stick' was undebuggable from the DB).

    Real sqlite failures, no mocking: a BEFORE INSERT trigger that RAISEs makes
    every WRITE fail while reads keep working, and a non-JSON stored value makes
    the READ fail. Reads are additionally flood-guarded — they run on nearly
    every request and SSE tick, and a session_id='' errors row lights errwatch's
    `⚠ global:` chip in EVERY session's scorebar — so a broken read is audited
    at most ONCE per (operation, key) per process (errwatch's own guard, same
    reasoning) — by the PAIR, so a swallowed read can't then mask a connect
    failure against the same key."""
    import sqlite3
    monkeypatch.setattr(P, "DASH_PREFS_DB", str(tmp_path / "prefs.db"))
    monkeypatch.setattr(prefs, "_READ_FAILED", set())
    monkeypatch.setattr(A, "_CONN", None)
    monkeypatch.setattr(A, "_FAILED", False)

    def errs():
        A._CONN = None
        A._FAILED = False
        A._connect()
        conn = sqlite3.connect(A.db_path())
        try:
            return [f for (f,) in conn.execute("SELECT func FROM errors")]
        finally:
            conn.close()

    assert prefs.set("k", {"a": 1}) is True          # a healthy store, no rows
    assert prefs.get("k") == {"a": 1}
    assert errs() == []
    # a stored value that isn't JSON: the read swallow, and only ONE row for it
    conn = prefs._connect()
    conn.execute("UPDATE kv SET val='{not json' WHERE key=?", ("k",))
    conn.execute("CREATE TRIGGER block BEFORE INSERT ON kv "
                 "BEGIN SELECT RAISE(ABORT, 'read-only'); END")
    conn.commit()
    conn.close()
    assert prefs.get("k", "fallback") == "fallback"
    assert prefs.get("k", "fallback") == "fallback"
    assert errs() == ["dashboard prefs get"], "one row per (op, key) per process"
    # the two write swallows: set() reports False, mutate_map lies (it returns
    # the intended map) — so its row is the only evidence the write was lost
    assert prefs.set("k2", {"b": 2}) is False
    assert prefs.mutate_map("k3", lambda d: d.__setitem__("c", 3)) == {"c": 3}
    assert errs() == ["dashboard prefs get", "dashboard prefs set",
                      "dashboard prefs mutate"]
    # and connect(): a prefs path whose parent is a FILE can't be opened at all
    monkeypatch.setattr(P, "DASH_PREFS_DB", str(tmp_path / "prefs.db" / "x.db"))
    assert prefs.get("k", None) is None
    assert prefs.set("k", 1) is False
    # 2 rows: the read's guarded one plus set()'s, which isn't guarded at all
    assert errs().count("dashboard prefs connect") == 2


def test_hide_dir_prefs_roundtrip_and_validation(dash):
    """Hiding a directory from the list page (docs/dashboard.md *Hidden
    directories*): POST /api/dirs/hide stamps time.time() into the durable global
    prefs store (dashboard/prefs.py — not a session or terminal write) keyed by
    the list's group key, and returns the full {group_key: hidden_at} map; GET
    /api/dirs/hidden reads it back (durable across requests). The re-appear rule
    (a session started after hidden_at un-hides the group) is client-side over
    the wire rows' started_at, so the server contract is just: stamp stored,
    served, and a non-string key refused. The EMPTY string is a VALID key — the
    'no project' aggregate group — not a bad request."""
    assert _get_json(dash + "/api/dirs/hidden") == {}
    t0 = time.time()
    code, body = _post(dash + "/api/dirs/hide", {"cwd": "/w/proj"})
    d = json.loads(body)
    assert code == 200 and d["ok"] is True
    assert d["hidden"]["/w/proj"] >= t0
    # served back over GET, durable through the store
    assert _get_json(dash + "/api/dirs/hidden")["/w/proj"] == d["hidden"]["/w/proj"]
    # a re-hide (a re-appeared group hidden again) overwrites with a NEWER stamp
    time.sleep(0.01)
    code2, body2 = _post(dash + "/api/dirs/hide", {"cwd": "/w/proj"})
    assert code2 == 200
    assert json.loads(body2)["hidden"]["/w/proj"] > d["hidden"]["/w/proj"]
    # the "" key (the projectless aggregate group) is ACCEPTED and stored
    code3, body3 = _post(dash + "/api/dirs/hide", {"cwd": ""})
    assert code3 == 200 and "" in json.loads(body3)["hidden"]
    assert "" in _get_json(dash + "/api/dirs/hidden")
    # a non-string / missing cwd IS refused (400), the store untouched
    for bad in (5, None):
        with pytest.raises(urllib.error.HTTPError) as e:
            _post(dash + "/api/dirs/hide", {"cwd": bad} if bad is not None else {})
        assert e.value.code == 400
    assert set(_get_json(dash + "/api/dirs/hidden")) == {"/w/proj", ""}


def test_global_notify_toggle_roundtrip_and_validation(dash):
    """The GLOBAL alerts master switch (docs/dashboard.md *Global alerts toggle*):
    GET /api/notify-config defaults to {enabled: true} (an absent pref reads ON),
    POST /api/notify {enabled: bool} persists to the durable prefs store and is
    read back, and a non-bool `enabled` is refused (400) with the store untouched.
    A FIXED route, distinct from the per-session /api/session/<sid>/notify."""
    assert _get_json(dash + "/api/notify-config") == {"enabled": True}
    code, body = _post(dash + "/api/notify", {"enabled": False})
    assert code == 200 and json.loads(body) == {"ok": True, "enabled": False}
    assert _get_json(dash + "/api/notify-config") == {"enabled": False}
    # flips back on, durable through the store
    code, body = _post(dash + "/api/notify", {"enabled": True})
    assert code == 200 and json.loads(body)["enabled"] is True
    assert _get_json(dash + "/api/notify-config") == {"enabled": True}
    # a non-bool enabled is refused; the last good value (True) stands
    for bad in ("yes", 1, None):
        with pytest.raises(urllib.error.HTTPError) as e:
            _post(dash + "/api/notify",
                  {"enabled": bad} if bad is not None else {})
        assert e.value.code == 400
    assert _get_json(dash + "/api/notify-config") == {"enabled": True}


def test_limits_endpoint_serves_its_owners_numbers(dash, monkeypatch):
    """GET /api/limits (docs/dashboard.md *Served limits*) is the ONE channel for
    the server-side numbers the PAGE acts on: the upload/rename caps it enforces
    client-side and the presence TTL its heartbeat cadence is derived from. Each
    value comes from its owning module and is read PER REQUEST — patching the
    owner moves the served number, which is what a `mirrors the server's X`
    literal in the JS could never do (and `view_ttl_s` needs no commit to drift:
    it is CLAUDE_DASH_VIEW_TTL_S)."""
    got = _get_json(dash + "/api/limits")
    assert got == {"upload_max": DS.config.UPLOAD_MAX,
                   "rename_max": DS.config.RENAME_MAX,
                   "view_ttl_s": DS.presence.VIEW_TTL_S}
    monkeypatch.setattr(DS.presence, "VIEW_TTL_S", 4.0)
    monkeypatch.setattr(DS.config, "UPLOAD_MAX", 999)
    assert _get_json(dash + "/api/limits")["view_ttl_s"] == 4.0
    assert _get_json(dash + "/api/limits")["upload_max"] == 999
    # and the served cap IS the enforced one — post_upload reads the same
    # module-qualified knob, so the two can't disagree (a by-value import copy
    # would have frozen one of them at import time)
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/upload",
              {"name": "big.png", "mime": "image/png", "data": "A" * 2000})
    assert e.value.code == 413


def test_page_reads_the_served_limits_not_its_own_copies(dash):
    """The page must CONSUME /api/limits, not re-encode the caps: one `LIMITS`
    declaration (app.00-core.js, the pre-fetch fallback + the fetch target), and
    the consumers read `LIMITS.<k>`. A static check over the SERVED parts, so a
    future site that hardcodes the cap again is caught here — the drift this
    endpoint exists to end (the JS literals carried `mirrors the server's X`
    comments, and CLAUDE_DASH_VIEW_TTL_S broke the presence beat with no code
    change on either side)."""
    parts = ("00-core", "08-composer", "10-control", "13-init")
    body = {}
    for p in parts:
        code, body[p] = _get(dash + "/static/app.%s.js" % p)
        assert code == 200
    assert sum(b.count("const LIMITS") for b in body.values()) == 1
    assert "const LIMITS" in body["00-core"]
    assert "LIMITS.upload_max" in body["08-composer"]
    assert "LIMITS.rename_max" in body["10-control"]
    # the heartbeat is DERIVED from the served TTL, never a matching literal
    assert "LIMITS.view_ttl_s" in body["13-init"]
    assert "/api/limits" in body["13-init"]


def test_prefs_mutate_map_accumulates_atomically(monkeypatch, tmp_path):
    # mutate_map is a single-transaction read-modify-write: successive mutations
    # ACCUMULATE (no lost update), and it degrades to the intended map even when
    # the store can't open. Both hide_dir and set_notify_muted ride it.
    from dashboard import prefs
    monkeypatch.setattr(P, "DASH_PREFS_DB", str(tmp_path / "prefs.db"))
    assert prefs.hide_dir("/a", 1.0) == {"/a": 1.0}
    assert prefs.hide_dir("/b", 2.0) == {"/a": 1.0, "/b": 2.0}   # /a not lost
    assert prefs.hidden_dirs() == {"/a": 1.0, "/b": 2.0}
    assert prefs.set_notify_muted("s1", True) == {"s1": True}
    assert prefs.set_notify_muted("s2", True) == {"s1": True, "s2": True}
    assert prefs.set_notify_muted("s1", False) == {"s2": True}   # un-mute deletes
    assert prefs.notify_muted("s2") is True and prefs.notify_muted("s1") is False
    # the web-rename override rides mutate_map too — sticky, per-sid, no delete
    assert prefs.set_renamed_title("sidA", "picked") == {"sidA": "picked"}
    assert prefs.set_renamed_title("sidB", "other") == {"sidA": "picked",
                                                         "sidB": "other"}
    assert prefs.renamed_title("sidA") == "picked"
    assert prefs.renamed_title("nope") == ""       # never renamed
    # degraded (unopenable store — dirname is a FILE): still returns the
    # intended map, never raises
    afile = tmp_path / "afile"
    afile.write_text("x")
    monkeypatch.setattr(P, "DASH_PREFS_DB", str(afile / "no.db"))
    assert prefs.mutate_map("k", lambda d: d.__setitem__("x", 1)) == {"x": 1}


def test_hide_dir_behind_post_guard(dash, monkeypatch):
    """The hide POST is a control-plane write like every other — a missing
    X-Claude-Dash header is rejected (403) and READONLY disables it (403)."""
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/dirs/hide", {"cwd": "/w/proj"}, header=None)
    assert e.value.code == 403
    monkeypatch.setattr(DS.config, "READONLY", True)
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/dirs/hide", {"cwd": "/w/proj"})
    assert e.value.code == 403


def _reject_rows():
    """The `web-reject` state_files rows (_post_guard rejections). Same
    spool-drain dance as _hint_rows."""
    import sqlite3
    A._CONN = None
    A._FAILED = False
    A._connect()
    con = sqlite3.connect(A.db_path())
    try:
        return [(p, json.loads(c)) for (p, c) in con.execute(
            "SELECT path, content FROM state_files WHERE action='web-reject' "
            "ORDER BY ts")]
    finally:
        con.close()


def test_guard_rejection_is_audited(dash, monkeypatch):
    # THE close-blind-spot fix: a control POST that fails _post_guard (a missing
    # X-Claude-Dash header) previously vanished — no audit row at all, so a
    # browser /stop that never passed the guard was invisible server-side. Now
    # every guard reject writes a `web-reject` row naming the path + reason.
    monkeypatch.setenv("KITTY_WINDOW_ID", "77")
    A.session_start({"session_id": "rej1", "cwd": "/w", "transcript_path": ""})
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/rej1/stop", {}, header=None)
    assert e.value.code == 403
    rows = _reject_rows()
    hit = [r for r in rows if r[0].endswith("/session/rej1/stop")]
    assert hit and hit[-1][1]["code"] == 403 and "header" in hit[-1][1]["why"]


def test_hide_dir_refused_when_directory_has_a_live_session(dash):
    """A directory with at least one ACTIVE (live) session can't be hidden — the
    server 409s on the SAME grouping the list uses (dir_live_sessions over
    sessions_payload), the authoritative guard behind the disabled ✕
    (docs/dashboard.md *Hidden directories*). A group with only parked / no
    sessions still hides, and the same directory becomes hideable once its
    session parks."""
    # a LIVE session in /w — its state DB exists (any writer creates it), so
    # sessions_payload reports live=True (the fixture's live_windows→None keeps
    # the state-DB liveness signal, no window demotion)
    A.session_start({"session_id": "hidelive", "cwd": "/w", "transcript_path": ""})
    S.kv_set(P.mirror_log("hidelive"), "seed", 1)      # create the state DB → live
    # hiding /w is refused (409) and the store is left untouched
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/dirs/hide", {"cwd": "/w"})
    assert e.value.code == 409
    assert "/w" not in _get_json(dash + "/api/dirs/hidden")
    # the guard is TARGETED — a different directory with no live session still hides
    code, _ = _post(dash + "/api/dirs/hide", {"cwd": "/other"})
    assert code == 200 and "/other" in _get_json(dash + "/api/dirs/hidden")
    # park the session (its live state DB gone) → /w becomes hideable
    os.remove(P.state_db(P.mirror_log("hidelive")))
    code, body = _post(dash + "/api/dirs/hide", {"cwd": "/w"})
    assert code == 200 and "/w" in json.loads(body)["hidden"]


def test_sse_tab_re_resolves_window_after_resume(dash, monkeypatch):
    """A resume moves the session to a NEW kitty window (the SessionStart
    upsert refreshes the sessions row) — a session SSE stream opened BEFORE
    the move must re-resolve the window on the slow cadence instead of polling
    the dead window's lingering tab state forever (shipped: the page showed
    the dead window's green while kitty was magenta)."""
    monkeypatch.setenv("KITTY_WINDOW_ID", "71")
    A.session_start({"session_id": "resse", "cwd": "/w", "transcript_path": ""})
    monkeypatch.setattr(DS.API, "tab_states",
                        lambda: {"71": "awaiting-response", "72": "thinking"})
    seen = []
    r = _req(dash + "/events/session/resse?after=0&mpos=0", timeout=15)
    try:
        pending = None
        for raw in r:
            line = raw.decode("utf-8", "replace").rstrip("\n")
            if line.startswith("event: "):
                pending = line[len("event: "):]
            elif line.startswith("data: ") and pending == "tab":
                seen.append(json.loads(line[len("data: "):])["tab"])
                if seen[-1] == "thinking":
                    break
                # first tab arrived on the OLD window — now "resume": the
                # upsert moves the sessions row to window 72
                monkeypatch.setenv("KITTY_WINDOW_ID", "72")
                A.session_start({"session_id": "resse", "cwd": "/w",
                                 "transcript_path": ""})
    finally:
        r.close()
    assert seen[0] == "awaiting-response" and seen[-1] == "thinking", seen


def test_http_copy_and_view(dash):
    A.session_start({"session_id": "dash2", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("dash2")
    O.emit(log, O.label("hdr", (1, 2, 3), g="cg"), O.code("echo copyme", g="cg"),
           O.gut("outline", (1, 2, 3), g="cg"))
    S.kv_set(log, "view:vg", [{"t": "gut", "s": "stash body", "c": [1, 2, 3]}])
    code, text = _get(dash + "/api/session/dash2/copy/cg/cmd")
    assert code == 200 and "echo copyme" in text
    code, text = _get(dash + "/api/session/dash2/copy/cg/out")
    assert code == 200 and text.strip() == "outline"
    code, html = _get(dash + "/api/session/dash2/view/vg")
    assert code == 200 and "view-block" in html and "stash body" in html
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(dash + "/api/session/dash2/view/missing")
    assert e.value.code == 404
    # The web copy/view flows call collect()/view_payload DIRECTLY, bypassing
    # the terminal claude-copy.py entry's audit rows — so each must leave its own
    # trace (docs/dashboard.md, the web-copy/web-view schema rows).
    copies = _state_rows("web-copy")
    assert {"gid": "cg", "what": "cmd", "chars": len("echo copyme")} in copies
    assert any(c["what"] == "out" and c["chars"] == len("outline")
               for c in copies)
    views = _state_rows("web-view")
    assert {"gid": "vg", "ok": True} in views
    assert {"gid": "missing", "ok": False} in views


def test_input_validation_rejects_are_audited(dash):
    """Every control-plane INPUT reject (a bad/empty body field) leaves an
    `ok:False` state_files row under the handler's OWN action, FILED UNDER THE
    SESSION — closing the silent-4xx class (`_reject_input`'s reason for being,
    now reached from the session-scoped handlers too, not just the session-less
    ones). One representative bad body per handler."""
    A.session_start({"session_id": "rj9", "cwd": "/w", "transcript_path": ""})

    def bad(path, body):
        with pytest.raises(urllib.error.HTTPError) as e:
            _post(dash + path, body)
        assert 400 <= e.value.code < 500

    base = "/api/session/rj9/"
    bad(base + "message", {"text": "   "})                  # whitespace only
    bad(base + "rename", {"name": "   "})                   # empty after strip
    bad(base + "rewind-to", {"text": "x", "mode": "nope"})  # bad mode
    bad(base + "composer-draft", {"text": 5})               # not a string
    bad(base + "composer-queue", {"items": "x"})            # not a list
    bad(base + "hint-audit", {"phase": "bogus"})            # bad phase
    bad("/api/upload", {"sid": "rj9"})                      # missing name/data
    # ask-draft's answer-count check needs a pending stash to reach it
    S.kv_set(P.mirror_log("rj9"), "ask-pending",
             {"tool_use_id": "tuZ", "questions": [{"question": "q"}]})
    bad(base + "ask-draft", {"tool_use_id": "tuZ", "answers": []})  # wrong count
    checks = {"web-send": "empty text", "web-rename": "empty name",
              "web-rewind-to": "bad mode", "composer-draft": "bad text",
              "composer-queue": "bad items", "web-hint": "bad phase",
              "web-upload": "bad fields", "ask-draft": "answer count"}
    for action, why in checks.items():
        hit = [(s, c) for (s, c) in _sf_rows_full(action)
               if c.get("ok") is False and c.get("why") == why]
        assert hit, "no audited reject for %s (%s)" % (action, why)
        assert hit[-1][0] == "rj9", \
            "%s reject not filed under sid: %r" % (action, hit[-1][0])


def test_http_monitors_endpoint(dash, tmp_path):
    """The monitors tab's data path: plugins.monitors merges the MAIN transcript
    (Monitor tool_use + its 'Monitor started (task X)' result + queue-operation
    events) with the audit streams lifecycle state (kind='monitor'). The endpoint
    returns one monitor per task with command/description/events/state; the
    session overview carries the cheap monitor_count for the tab badge."""
    tp = tmp_path / "mon-sess.jsonl"
    tp.write_text(
        json.dumps({"type": "assistant", "message": {"id": "m1", "content": [
            {"type": "tool_use", "id": "t1", "name": "Monitor",
             "input": {"command": "tail -f build.log", "description": "watch build",
                       "persistent": True}}]}}) + "\n" +
        json.dumps({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1",
             "content": "Monitor started (task mtask1, persistent — runs until…)"}]}}) + "\n" +
        json.dumps({"type": "queue-operation", "content":
                    "<task-notification>\n<task-id>mtask1</task-id>\n"
                    "<summary>Monitor event</summary>\n<event>build ok</event>\n"
                    "</task-notification>"}) + "\n" +
        json.dumps({"type": "queue-operation", "content":
                    "<task-notification>\n<task-id>mtask1</task-id>\n"
                    "<status>completed</status>\n"
                    "<summary>Monitor \"watch build\" stream ended</summary>\n"
                    "</task-notification>"}) + "\n")
    log = P.mirror_log("mons1")
    A.session_start({"session_id": "mons1", "cwd": "/w", "transcript_path": str(tp)})
    rid = A.stream_start(log, "monitor", task_id="mtask1")
    A.stream_end(rid, "monitor-process-exited", lines_emitted=2)
    d = _get_json(dash + "/api/session/mons1/monitors")
    mons = d["monitors"]
    assert len(mons) == 1
    m = mons[0]
    assert m["task"] == "mtask1"
    assert m["command"] == "tail -f build.log"
    assert m["description"] == "watch build"
    assert m["persistent"] is True
    assert m["live"] is False and m["end_reason"] == "monitor-process-exited"
    assert m["event_count"] == 1            # the `event`, not the stream-ended status
    assert m["started_at"] and m["ended_at"]
    kinds = [("status" if "status" in e else "event") for e in m["events"]]
    assert kinds == ["event", "status"]
    # the session overview carries the cheap badge count (streams, no parse)
    assert _get_json(dash + "/api/session/mons1")["monitor_count"] == 1


def test_http_jobs_endpoint(dash):
    """The jobs tab's data path: sessionapi.jobs merges the audit streams state
    (kind='bg') with the command from the mirror ops copy-group, and the output
    is read from those same ops via /copy/<task>/out (a bg job's output is in the
    ops, not the transcript). The overview carries the cheap job_count badge."""
    A.session_start({"session_id": "jobs1", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("jobs1")
    # the mirror block a bg launch paints: header + command + output, group=taskId
    O.emit(log, O.label("▷ background", (211, 204, 173), g="bgt1"),
           O.code("sleep 30; echo done", g="bgt1"),
           O.gut("line one\nline two", (211, 204, 173), g="bgt1"))
    rid = A.stream_start(log, "bg", task_id="bgt1")
    A.stream_end(rid, "writer-gone", lines_emitted=2)
    d = _get_json(dash + "/api/session/jobs1/jobs")
    jobs = d["jobs"]
    assert len(jobs) == 1
    j = jobs[0]
    assert j["task"] == "bgt1"
    # the command is the ops `code` op text (bash pretty-printed — `;` → newlines)
    assert "sleep 30" in j["command"] and "echo done" in j["command"]
    assert j["live"] is False and j["end_reason"] == "writer-gone"
    assert j["started_at"] and j["ended_at"]
    # the overview carries the cheap badge count
    assert _get_json(dash + "/api/session/jobs1")["job_count"] == 1
    # the drill-down reads the job's OUTPUT from the same ops via /copy/<task>/out
    code, out = _get(dash + "/api/session/jobs1/copy/bgt1/out")
    assert code == 200 and "line one" in out and "line two" in out


def test_agent_scope_payload_and_removed_routes(dash, tmp_path):
    """Agent scope serves the agent's token rollup on the SESSION payload
    (`?agent=`), and the drill-down endpoints it replaced are gone."""
    tp = tmp_path / "agent-ag2.jsonl"
    tp.write_text(
        json.dumps({"type": "assistant", "message": {
            "id": "m1", "model": "claude-opus-4-8",
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "content": [{"type": "text", "text": "hi there"}]}}) + "\n")
    log = P.mirror_log("dash3")
    A.session_start({"session_id": "dash3", "cwd": "/w", "transcript_path": ""})
    rid = A.stream_start(log, "subagent", agent_id="ag2", src_path=str(tp))
    A.stream_end(rid, "stop-sentinel", lines_emitted=2)
    # unscoped: no agent_usage at all (it costs a transcript fold — only the
    # scoped request pays it)
    plain = _get_json(dash + "/api/session/dash3")
    assert "agent_usage" not in plain
    # agents list carries the streams keystone fields the cards render
    assert plain["agents"] and plain["agents"][0]["end_reason"] == "stop-sentinel"
    d = _get_json(dash + "/api/session/dash3?agent=ag2")["agent_usage"]
    assert d["model"] == "claude-opus-4-8"
    assert d["usage"]["in"] == 10 and d["usage"]["out"] == 5
    assert d["cost"] > 0                       # priced by the shared accountant
    # the drill-down endpoints agent scope replaced
    for path in ("/api/session/dash3/agent/ag2", "/api/session/dash3/activity",
                 "/events/agent/dash3/ag2"):
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(dash + path, timeout=5)
        assert e.value.code == 404


def _agent_transcript(tmp_path, sid, aid):
    """Seed an agent transcript + its audit streams row (the keystone
    sessionapi.agent_transcript resolves), returning its path."""
    tp = tmp_path / ("agent-%s.jsonl" % aid)
    tp.write_text(
        json.dumps({"type": "assistant", "message": {
            "id": "m1", "content": [
                {"type": "text", "text": "starting"},
                {"type": "tool_use", "id": "t1", "name": "Bash",
                 "input": {"command": "ls"}}]}}) + "\n" +
        json.dumps({"type": "user", "message": {
            "content": [{"type": "tool_result", "tool_use_id": "t1",
                         "content": "listing"}]}}) + "\n")
    A.session_start({"session_id": sid, "cwd": "/w", "transcript_path": ""})
    A.stream_start(P.mirror_log(sid), "subagent", agent_id=aid, src_path=str(tp))
    return tp


def test_context_saturation_payloads_and_sse(dash, tmp_path):
    """The ctx-saturation chips' one data path (plugins.context over transcript
    tails, (path, size)-cached): sessions rows and the session overview carry
    the MAIN transcript's {used, window, pct, model} — sidechain records
    skipped — agent rows carry their OWN transcript's, and the per-session SSE
    announces the main figure as a `ctx` event."""
    tp = tmp_path / "ctx-main.jsonl"
    tp.write_text(
        json.dumps({"type": "assistant", "message": {
            "id": "m1", "model": "claude-haiku-4-5",
            "usage": {"input_tokens": 1000, "cache_read_input_tokens": 99000,
                      "output_tokens": 5}}}) + "\n" +
        json.dumps({"type": "assistant", "isSidechain": True, "message": {
            "id": "m2", "model": "claude-haiku-4-5",
            "usage": {"input_tokens": 7, "output_tokens": 1}}}) + "\n")
    A.session_start({"session_id": "ctxS", "cwd": "/w",
                     "transcript_path": str(tp)})
    atp = tmp_path / "agent-agC.jsonl"
    atp.write_text(json.dumps({"type": "assistant", "isSidechain": True,
                               "message": {"id": "a1", "model": "claude-haiku-4-5",
                                           "usage": {"input_tokens": 60000,
                                                     "output_tokens": 9}}}) + "\n")
    A.stream_start(P.mirror_log("ctxS"), "subagent", agent_id="agC",
                   src_path=str(atp))
    row = next(r for r in _get_json(dash + "/api/sessions") if r["sid"] == "ctxS")
    assert row["ctx"] == {"used": 100000, "window": 200000, "pct": 50,
                          "model": "claude-haiku-4-5"}
    ov = _get_json(dash + "/api/session/ctxS")
    assert ov["ctx"]["pct"] == 50                   # the sidechain row didn't win
    ag = next(a for a in ov["agents"] if a["agent_id"] == "agC")
    assert ag["ctx"]["used"] == 60000 and ag["ctx"]["pct"] == 30
    # the model·effort card chip rides free off the ctx probe's model id;
    # haiku has no adaptive-reasoning default and no session effort here, so
    # the card shows model-only
    assert ag["model"] == "haiku-4.5" and "effort" not in ag
    data = _sse_event(dash + "/events/session/ctxS?after=0&mpos=0", "ctx")
    assert data and json.loads(data)["ctx"]["pct"] == 50


def test_agent_card_model_effort(dash, tmp_path):
    """An agent card carries its running model (shortened) + effort — the web
    echo of the terminal mirror's op tag. An adaptive-reasoning model with no
    session effort set falls to its own default (opus-4.8 -> high); the chip
    also rides the live `agents` SSE event."""
    tp = tmp_path / "meff-main.jsonl"
    tp.write_text(json.dumps({"type": "assistant", "message": {
        "id": "m1", "model": "claude-opus-4-8",
        "usage": {"input_tokens": 1000, "output_tokens": 5}}}) + "\n")
    A.session_start({"session_id": "meffS", "cwd": "/w",
                     "transcript_path": str(tp)})
    atp = tmp_path / "agent-agM.jsonl"
    atp.write_text(json.dumps({"type": "assistant", "isSidechain": True,
                               "message": {"id": "a1", "model": "claude-opus-4-8",
                                           "usage": {"input_tokens": 40000,
                                                     "output_tokens": 9}}}) + "\n")
    A.stream_start(P.mirror_log("meffS"), "subagent", agent_id="agM",
                   src_path=str(atp))
    ov = _get_json(dash + "/api/session/meffS")
    ag = next(a for a in ov["agents"] if a["agent_id"] == "agM")
    assert ag["model"] == "opus-4.8" and ag["effort"] == "high"
    sse = _sse_event(dash + "/events/session/meffS?after=0&mpos=0", "agents")
    assert sse
    agS = next(a for a in json.loads(sse) if a["agent_id"] == "agM")
    assert agS["model"] == "opus-4.8" and agS["effort"] == "high"


def test_git_chip_payloads(dash, tmp_path):
    """sessions rows and the overview carry the cwd's checkout state {branch,
    worktree, root, dirty}, branch/worktree read from the .git files directly
    (never a git subprocess): a main checkout resolves HEAD's ref short name,
    a linked worktree (a .git FILE pointing into .../worktrees/<name>) carries
    the worktree name PLUS root — the owning main checkout, the list page's
    grouping key (root||cwd), so worktree sessions file under their project —
    and a detached HEAD shows a 7-char sha, and a non-checkout cwd carries
    None. These synthetic .git dirs aren't real checkouts, so the dirty probe
    (`git status`, the branch chip's `*`) resolves to None = unknown — the
    degraded shape is itself the contract."""
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".git" / "HEAD").write_text("ref: refs/heads/feat/x\n")
    wtgd = repo / ".git" / "worktrees" / "wt1"
    wtgd.mkdir(parents=True)
    (wtgd / "HEAD").write_text("abcdef0123456789abcdef0123456789abcdef01\n")
    wt = tmp_path / "wt1"
    wt.mkdir()
    (wt / ".git").write_text("gitdir: %s\n" % wtgd)
    A.session_start({"session_id": "gitA", "cwd": str(repo), "transcript_path": ""})
    A.session_start({"session_id": "gitB", "cwd": str(wt), "transcript_path": ""})
    A.session_start({"session_id": "gitC", "cwd": str(tmp_path / "nowhere"),
                     "transcript_path": ""})
    rows = {r["sid"]: r for r in _get_json(dash + "/api/sessions")}
    assert rows["gitA"]["git"] == {"branch": "feat/x", "worktree": None,
                                   "root": None, "dirty": None}
    assert rows["gitB"]["git"] == {"branch": "abcdef0", "worktree": "wt1",
                                   "root": str(repo), "dirty": None}
    assert rows["gitC"]["git"] is None
    ov = _get_json(dash + "/api/session/gitB")
    assert ov["git"] == {"branch": "abcdef0", "worktree": "wt1",
                         "root": str(repo), "dirty": None}
    # group_dir is the list's grouping key: the frozen start_cwd resolved to its
    # linked-worktree OWNER. A main checkout / non-checkout groups under itself;
    # a worktree groups under its owning checkout (== git.root here). start_cwd
    # itself is server-internal (it only feeds group_dir) — never on the wire.
    assert rows["gitA"]["group_dir"] == rows["gitA"]["cwd"]
    assert rows["gitB"]["group_dir"] == rows["gitB"]["git"]["root"] == str(repo)
    assert rows["gitC"]["group_dir"] == rows["gitC"]["cwd"]
    assert "start_cwd" not in rows["gitA"]
    # HEAD is re-read each call: a branch switch shows without cache eviction
    (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    rows = {r["sid"]: r for r in _get_json(dash + "/api/sessions")}
    assert rows["gitA"]["git"]["branch"] == "main"


def test_group_dir_pins_to_start_cwd(dash, tmp_path):
    """The list groups on group_dir = the session's FROZEN original cwd
    (start_cwd), NOT the live cwd — so an agent's mid-session `cd` (which
    session_paths folds into the live cwd) can't move a card between groups.
    Regression for the reported 'cd changes the main-page aggregation' bug."""
    start = tmp_path / "proj"
    start.mkdir()
    moved = tmp_path / "elsewhere"
    moved.mkdir()
    A.session_start({"session_id": "pin1", "cwd": str(start),
                     "transcript_path": ""})
    # the agent cd's: session_paths re-stamps the LIVE cwd on the next event
    A.session_paths({"session_id": "pin1", "cwd": str(moved),
                     "transcript_path": ""})
    row = {r["sid"]: r for r in _get_json(dash + "/api/sessions")}["pin1"]
    assert os.path.basename(row["cwd"]) == "elsewhere"      # live cwd followed the cd
    assert os.path.basename(row["group_dir"]) == "proj"     # group pinned to start
    assert row["cwd"] != row["group_dir"]


def test_git_dirty_marker(dash, tmp_path):
    """the git payload's dirty flag over a REAL checkout: clean -> False,
    an untracked file -> True (any `git status --porcelain` output counts,
    the status-line `*` convention). Two separate cwds because the probe is
    TTL-cached per cwd (DIRTY_TTL_S) — a same-cwd flip inside the test would
    need a TTL wait."""
    if not shutil.which("git"):
        pytest.skip("no git binary")
    env = {"HOME": str(tmp_path), "PATH": os.environ.get("PATH", "")}
    for name, mess in (("clean", False), ("dirty", True)):
        repo = tmp_path / name
        repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)],
                       check=True, env=env)
        subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t",
                        "-c", "user.name=t", "commit", "-q", "--allow-empty",
                        "-m", "seed"], check=True, env=env)
        if mess:
            (repo / "untracked.txt").write_text("x\n")
        A.session_start({"session_id": "gd-" + name, "cwd": str(repo),
                         "transcript_path": ""})
    rows = {r["sid"]: r for r in _get_json(dash + "/api/sessions")}
    assert rows["gd-clean"]["git"] == {"branch": "main", "worktree": None,
                                       "root": None, "dirty": False}
    assert rows["gd-dirty"]["git"] == {"branch": "main", "worktree": None,
                                       "root": None, "dirty": True}


def test_agent_scope_filters_the_mirror_to_one_agent(dash, tmp_path):
    """The scoped mirror keeps only that agent's `src`-stamped ops — the lead's
    own and a DIFFERENT agent's both drop out (docs/dashboard.md *Agent
    scope*)."""
    A.session_start({"session_id": "scope1", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("scope1")
    O.emit(log, O.label("lead ran something", O.SLATE, g="glead"))
    O.emit(log, O.label("sub A worked", O.SLATE, g="ga"), src="sub:agA")
    O.emit(log, O.label("teammate B worked", O.SLATE, g="gb"), src="team:agB")
    # unscoped: the lead's own op only
    items = _get_json(dash + "/api/session/scope1/backlog")["items"]
    html = " ".join(i["html"] for i in items)
    assert "lead ran something" in html
    assert "sub A worked" not in html and "teammate B worked" not in html
    # scoped to agA: only its op
    items = _get_json(dash + "/api/session/scope1/backlog?agent=agA")["items"]
    html = " ".join(i["html"] for i in items)
    assert "sub A worked" in html
    assert "lead ran something" not in html and "teammate B worked" not in html
    # a teammate is named by the same agent_id space, and does not leak into a
    # sibling's scope
    items = _get_json(dash + "/api/session/scope1/backlog?agent=agB")["items"]
    assert "teammate B worked" in " ".join(i["html"] for i in items)


def test_jobs_and_monitors_are_lead_only_until_scoped(dash, tmp_path):
    """A session's Jobs/Monitors tabs show the LEAD's own work; an agent's is
    behind that agent's scope, with the command the audit recovered."""
    A.session_start({"session_id": "scope2", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("scope2")
    # the lead's own bg job: its command op shares the taskId group
    O.emit(log, O.code("echo lead", g="btask-lead"))
    A.stream_end(A.stream_start(log, "bg", task_id="btask-lead"), "writer-gone")
    # an AGENT's bg job — the stream carries its owner (CLAUDE_STREAM_AGENT)
    A.stream_end(A.stream_start(log, "bg", agent_id="agJ", task_id="btask-sub"),
                 "writer-gone")
    # …and the hook that launched it carries the command the ops group misses
    A.hook_event({"session_id": "scope2", "hook_event_name": "PostToolUse",
                  "tool_name": "Bash", "agent_id": "agJ",
                  "tool_use_id": "toolu_9", "tool_input": {"command": "echo sub"},
                  "tool_response": {"backgroundTaskId": "btask-sub"}},
                 handler="claude-cmd-fmt.py")
    lead = _get_json(dash + "/api/session/scope2/jobs")["jobs"]
    assert [j["task"] for j in lead] == ["btask-lead"]
    assert lead[0]["command"] == "echo lead"
    sub = _get_json(dash + "/api/session/scope2/jobs?agent=agJ")["jobs"]
    assert [j["task"] for j in sub] == ["btask-sub"]
    # the command came from the launch hook, and `group` points at the ops group
    # the substream actually painted under (the tool_use_id, not the taskId)
    assert sub[0]["command"] == "echo sub" and sub[0]["group"] == "toolu_9"
    # the badge count follows the same lead-only rule
    assert _get_json(dash + "/api/session/scope2")["job_count"] == 1


def test_hidden_agent_husk_rows_are_filtered(dash):
    # A SubagentStop with no SubagentStart (hidden auxiliary agent) leaves an
    # agents-table row with every field empty — the finaliser's 'never
    # started (hidden agent)' path. The dashboard must not show it; a row
    # with any real signal (desc, kind, transcript, slot, start) stays.
    A.session_start({"session_id": "dash7", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("dash7")
    S.agent_set(log, "husk1", done=0)                  # the hidden-agent shape
    S.agent_set(log, "real1", desc="do a thing")
    ags = _get_json(dash + "/api/session/dash7")["agents"]
    assert [a["agent_id"] for a in ags] == ["real1"]


def _req(url, headers=None, timeout=10):
    return urllib.request.urlopen(
        urllib.request.Request(url, headers=headers or {}), timeout=timeout)


def test_gzip_large_response_round_trips(dash):
    # A response at/above GZIP_MIN compresses when the client offers gzip, and
    # the compressed body decompresses to the byte-identical plain response.
    A.session_start({"session_id": "gz1", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("gz1")
    for i in range(60):
        O.emit(log, O.label("block %d" % i, (170, 185, 210), g="g%d" % i),
               O.gut("output line for block %d " % i * 3, (170, 185, 210),
                     g="g%d" % i))
    url = dash + "/api/session/gz1/ops?after=0"

    plain = _req(url)                                  # no Accept-Encoding
    assert plain.headers.get("Content-Encoding") is None
    assert plain.headers.get("Vary") == "Accept-Encoding"
    ref = plain.read()
    assert len(ref) >= DS.config.GZIP_MIN

    gz = _req(url, {"Accept-Encoding": "gzip, deflate"})
    assert gz.headers.get("Content-Encoding") == "gzip"
    raw = gz.read()                                    # urllib does not auto-inflate
    assert int(gz.headers.get("Content-Length")) == len(raw)
    assert len(raw) < len(ref)                         # smaller on the wire
    assert gzip.decompress(raw) == ref


def test_gzip_small_response_stays_plain(dash):
    # Below the threshold, gzip is skipped even when offered (framing overhead
    # would outweigh the win); an empty ops tail is well under GZIP_MIN.
    A.session_start({"session_id": "gz2", "cwd": "/w", "transcript_path": ""})
    r = _req(dash + "/api/session/gz2/ops?after=999999",
             {"Accept-Encoding": "gzip"})
    body = r.read()
    assert len(body) < DS.config.GZIP_MIN
    assert r.headers.get("Content-Encoding") is None
    assert json.loads(body)["items"] == []


def test_sse_global_says_hello_with_boot_id(dash):
    # the first /events frame is the server's boot id — the stale-open-page
    # detector: a reconnecting EventSource that sees a different boot knows
    # the server (and likely the JS it would serve) changed underneath it
    data = json.loads(_sse_event(dash + "/events", "hello"))
    assert data.get("boot") == DS.config.BOOT_ID


def test_sse_is_never_gzipped(dash):
    # SSE holds the response open and writes incremental frames; buffering it
    # through gzip would break the stream, so it must stay identity-encoded
    # even when the client offers gzip.
    r = _req(dash + "/events", {"Accept-Encoding": "gzip"})
    try:
        assert r.headers.get("Content-Type", "").startswith("text/event-stream")
        assert r.headers.get("Content-Encoding") is None
    finally:
        r.close()


def test_http_rejects_bad_sids(dash):
    for bad in ("a%2Fb", "a%20b"):
        with pytest.raises(urllib.error.HTTPError) as e:
            _get(dash + "/api/session/%s/ops" % bad)
        assert e.value.code == 404
