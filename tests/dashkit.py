# tests/dashkit.py — the shared rig for the L0 dashboard suite.
#
# The single L0 dashboard test module had grown to 8468 lines and 355 tests
# across eight unrelated subjects — the same monolith the dashboard PACKAGE
# was itself decomposed out of, and by then the largest file in the repo.
# It is one file
# per subject now (test_l0_dash_*.py); everything more than one of them needs
# lives here: the HTTP helpers, the audit-row readers, the fake frontend.
#
# NOT a conftest.py, deliberately: these are helpers a module IMPORTS BY NAME, so
# a reader can see where `_post` or `_FakeFE` came from. conftest.py stays for
# the hermetic-environment fixtures the whole suite gets implicitly — plus the
# one thing that cannot be imported, the `dash` server FIXTURE, which pytest
# resolves by NAME out of a test's signature.
import json
import sys
import urllib.error
import urllib.request

from conftest import REPO

if REPO not in sys.path:
    sys.path.insert(0, REPO)

import core.audit as A
from dashboard import server as DS


# ------------------------------------------------------------------ opshtml


def _get(url, timeout=10):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace")


def _get_json(url):
    code, body = _get(url)
    assert code == 200
    return json.loads(body)


def _state_rows(action):
    """state_files rows for an action, oldest-first. Same spool-drain dance as
    _reject_rows (the audit conn is per-process; a fresh connect flushes)."""
    import sqlite3
    A._CONN = None
    A._FAILED = False
    A._connect()
    con = sqlite3.connect(A.db_path())
    try:
        return [json.loads(c) for (c,) in con.execute(
            "SELECT content FROM state_files WHERE action=? ORDER BY ts",
            (action,))]
    finally:
        con.close()


def _sf_rows_full(action):
    """(session_id, content) for a state_files action, oldest-first — the
    session-filing check the plain _state_rows can't make."""
    import sqlite3
    A._CONN = None
    A._FAILED = False
    A._connect()
    con = sqlite3.connect(A.db_path())
    try:
        return [(s, json.loads(c)) for (s, c) in con.execute(
            "SELECT session_id, content FROM state_files WHERE action=? "
            "ORDER BY ts", (action,))]
    finally:
        con.close()


def _jl(*objs):
    return "".join(json.dumps(o) + "\n" for o in objs)


def _tw(tmp_path, name, *objs):
    p = tmp_path / name
    p.write_text(_jl(*objs))
    return str(p)


class _FakeFE:
    """A usable Frontend stub capturing control-plane writes (injected via
    monkeypatching frontends.get in the server module)."""

    def __init__(self, send_ok=True, launch_ok=True):
        self.sent = []
        self.pasted = []
        self.launched = []
        self.closed = []
        self.keyed = []
        self.titled = []
        self.send_ok = send_ok
        self.launch_ok = launch_ok
        self.wins = {}          # sid -> live window override (stale/missing tag)

    def usable(self):
        return True

    def window_for_session(self, sid, tree=None):
        # simulate the live claude_session=<sid> pane tag: by default the
        # recorded (healthy, non-stale) window id; a test sets self.wins[sid]
        # to model a stale/missing tag (None) that must be refused
        if sid in self.wins:
            return self.wins[sid]
        row = DS.API.session_row(sid) or {}
        return str(row.get("kitty_window_id") or "") or None

    def send_text(self, win, text):
        self.sent.append((win, text))
        return self.send_ok

    def paste_text(self, win, text):
        self.pasted.append((win, text))
        return self.send_ok

    def send_key(self, win, *keys):
        self.keyed.append((win, keys))
        return self.send_ok

    def get_text(self, win, extent="screen", ansi=False):
        # ansi=True is the INPUT-BOX read (post_interrupt's restore probe,
        # suggestion.typed) — its own fixture, so it can't disturb the
        # plain-text screens the interrupt's liveness delta pops through
        if ansi:
            return self.ansi_screen
        # screens pop in order; the last one sticks (a stable final state)
        if len(self.screens) > 1:
            return self.screens.pop(0)
        return self.screens[0] if self.screens else ""

    screens = ()
    ansi_screen = ""

    def export_env(self):
        pass

    def close_tab(self, win):
        self.closed.append(win)
        return True

    def set_tab_title(self, win, title):
        self.titled.append((win, title))
        return True

    def launch_tab(self, cwd, argv):
        self.launched.append((cwd, argv))
        return self.launch_ok

    def app_id(self):
        # "" = no OS-level app identity → the focus-bounce guard stays off;
        # the bounce tests override this with a real-looking bundle id
        return self.bundle_id

    bundle_id = ""


def _inject_fe(monkeypatch, fe):
    monkeypatch.setattr(DS.frontends, "get", lambda **kw: fe)


def _post(url, body=None, ctype="application/json", header="1", origin=None,
          raw=None):
    """A control-plane POST. Defaults pass the guard (JSON + X-Claude-Dash: 1,
    no Origin); pass ctype=None / header=None / origin=… to exercise a
    rejection."""
    data = raw if raw is not None else json.dumps(body or {}).encode()
    headers = {}
    if ctype is not None:
        headers["Content-Type"] = ctype
    if header is not None:
        headers["X-Claude-Dash"] = header
    if origin is not None:
        headers["Origin"] = origin
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, r.read().decode("utf-8", "replace")


def _last_state_file(sid, action):
    """The newest `action` state_files row for `sid`, decoded. Forces the audit
    drain first (a dashboard request thread SPOOLS its write — see
    _clientfail_rows)."""
    import sqlite3
    A._CONN = None
    A._FAILED = False
    A._connect()                     # drains spool.jsonl into the DB
    con = sqlite3.connect(A.db_path())
    try:
        rows = con.execute(
            "SELECT content FROM state_files WHERE session_id=? AND action=? "
            "ORDER BY ts DESC LIMIT 1", (sid, action)).fetchall()
    finally:
        con.close()
    return json.loads(rows[0][0]) if rows else None
