# tests/test_l0_dash_control.py — L0 dashboard: the control plane: send / close / rename / launch / uploads.
#
# One subject out of the former 8468-line L0 dashboard monolith; the
# shared HTTP/audit helpers live in tests/dashkit.py and the in-process
# server fixture (`dash`) in tests/conftest.py.
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

import pytest
from conftest import REPO, wait_until

if REPO not in sys.path:
    sys.path.insert(0, REPO)

import core.audit as A
from core import ops as O
from core import paths as P
from core import state as S
from dashboard import server as DS
from plugins.claude_code import askdialog as ASKD
from plugins.claude_code import confirmdialog as CFD
from plugins.claude_code import hostctl as CH
from plugins.claude_code import rewindmenu as RWM
from plugins.claude_code import tui as CTUI


# ------------------------------------------------------------------ opshtml
from dashkit import (_FakeFE, _get, _get_json, _inject_fe, _jl, _last_state_file, _post)


def _b64_png(b=b"\x89PNG\r\n\x1a\nfake"):
    import base64 as _b
    return _b.b64encode(b).decode()


def test_post_upload_writes_file_and_returns_path(dash, monkeypatch):
    """POST /api/upload stages the bytes under paths.UPLOADS_DIR/<sid>/ and
    hands back the absolute path (the composer's @-mention target) + an
    is_image flag for the thumbnail decision."""
    A.session_start({"session_id": "up1", "cwd": "/w", "transcript_path": ""})
    code, body = _post(dash + "/api/upload",
                       {"sid": "up1", "name": "shot.png", "mime": "image/png",
                        "data": _b64_png()})
    d = json.loads(body)
    assert code == 200 and d["ok"] and d["is_image"] is True
    assert d["path"].startswith(str(P.UPLOADS_DIR)) and os.path.isfile(d["path"])
    assert "up1" in d["path"] and d["path"].endswith("-shot.png")
    with open(d["path"], "rb") as f:
        assert f.read().startswith(b"\x89PNG")


def test_post_upload_sanitizes_traversal_name(dash):
    """A hostile filename can't escape the per-session dir — the basename is
    slugged, so `../../etc/x` lands as a plain file inside UPLOADS_DIR."""
    code, body = _post(dash + "/api/upload",
                       {"sid": "", "name": "../../etc/passwd", "mime": "text/plain",
                        "data": _b64_png(b"hi")})
    d = json.loads(body)
    assert code == 200
    assert os.path.realpath(d["path"]).startswith(os.path.realpath(str(P.UPLOADS_DIR)))
    assert "/etc/passwd" not in d["path"]


def test_post_upload_bad_base64_is_400(dash):
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/upload",
              {"name": "x.png", "mime": "image/png", "data": "not@@base64"})
    assert e.value.code == 400


def test_post_upload_over_cap_rejected(dash):
    # Content-Length past UPLOAD_MAX is rejected by the guard before any decode.
    # The guard closes the connection without draining the oversize body (the
    # _reject contract), so the client sees either a clean 413 or a reset —
    # both are "refused", which is the contract under test.
    raw = json.dumps({"name": "big", "mime": "image/png",
                      "data": "A" * (DS.config.UPLOAD_MAX + 10)}).encode()
    with pytest.raises((urllib.error.HTTPError, urllib.error.URLError)) as e:
        _post(dash + "/api/upload", raw=raw)
    if isinstance(e.value, urllib.error.HTTPError):
        assert e.value.code == 413


def test_post_upload_admits_body_over_post_max(dash):
    # the raised cap is the whole point: a payload well past the 64 KiB
    # control-plane POST_MAX still uploads (a real screenshot is ~MBs)
    big = _b64_png(b"\x89PNG\r\n\x1a\n" + b"x" * (DS.config.POST_MAX * 2))
    code, body = _post(dash + "/api/upload",
                       {"name": "big.png", "mime": "image/png", "data": big})
    assert code == 200 and os.path.isfile(json.loads(body)["path"])


def test_post_message_with_attachment_prepends_mention(dash, monkeypatch):
    """A message carrying vetted attachment paths delivers them as leading
    @-mentions ahead of the text — the TUI-native attach — over the same
    bracketed paste."""
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "51")
    A.session_start({"session_id": "att1", "cwd": "/w", "transcript_path": ""})
    _, body = _post(dash + "/api/upload",
                    {"sid": "att1", "name": "a.png", "mime": "image/png",
                     "data": _b64_png()})
    path = json.loads(body)["path"]
    code, _ = _post(dash + "/api/session/att1/message",
                    {"text": "look", "attachments": [path]})
    assert code == 200
    assert fe.pasted == [("51", "@%s\nlook" % path)]


def test_post_message_attachment_only_no_text(dash, monkeypatch):
    """A screenshot with no words is a valid message (the mention alone)."""
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "52")
    A.session_start({"session_id": "att2", "cwd": "/w", "transcript_path": ""})
    _, body = _post(dash + "/api/upload",
                    {"sid": "att2", "name": "a.png", "mime": "image/png",
                     "data": _b64_png()})
    path = json.loads(body)["path"]
    code, _ = _post(dash + "/api/session/att2/message",
                    {"text": "", "attachments": [path]})
    assert code == 200 and fe.pasted == [("52", "@" + path)]


def test_post_message_rejects_attachment_outside_uploads(dash, monkeypatch, tmp_path):
    """An @-path the server didn't stage (anywhere outside UPLOADS_DIR) is
    silently dropped — a page can't smuggle an arbitrary filesystem path into
    a mention. With no text left, that's an empty message → 400."""
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "53")
    A.session_start({"session_id": "att3", "cwd": "/w", "transcript_path": ""})
    evil = tmp_path / "secret.txt"
    evil.write_text("x")
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/att3/message",
              {"text": "", "attachments": [str(evil)]})
    assert e.value.code == 400
    assert fe.pasted == []


def test_post_new_session_prompt_carries_attachment(dash, monkeypatch):
    """The new-session launch prompt gets the @-mentions prepended too (covers
    the form AND the parked resume-&-send path)."""
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    _, body = _post(dash + "/api/upload",
                    {"name": "a.png", "mime": "image/png", "data": _b64_png()})
    path = json.loads(body)["path"]
    code, _ = _post(dash + "/api/sessions/new",
                    {"cwd": str(REPO), "prompt": "start", "attachments": [path]})
    assert code == 200
    (cwd, argv) = fe.launched[-1]
    assert ("@%s\nstart" % path) in " ".join(str(w) for w in argv)


def test_post_upload_control_plane_guarded(dash):
    """/api/upload is a control-plane write like every other — a missing
    X-Claude-Dash header is a 403."""
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/upload", {"name": "x", "mime": "image/png",
              "data": _b64_png()}, header=None)
    assert e.value.code == 403


def test_post_message_blocked_while_dialog_open(dash, monkeypatch):
    """A composer send while a modal dialog (AskUserQuestion/ExitPlanMode) is
    up would paste INTO the dialog and be lost (the "my queued message vanished
    mid ask" report, 2026-07-19) — it's refused with a 409 `modal` and NO
    paste, pointing the user at the card. Cleared once the dialog is gone."""
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "88")
    A.session_start({"session_id": "msgm", "cwd": "/w", "transcript_path": ""})
    S.kv_set(P.mirror_log("msgm"), "ask-pending",
             {"tool_use_id": "tu9", "questions": [{"question": "?",
              "options": [{"label": "A"}], "multiSelect": False}]})
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/msgm/message", {"text": "into the void"})
    assert e.value.code == 409
    assert json.loads(e.value.read())["modal"] is True
    assert fe.pasted == []                       # nothing typed into the dialog
    # dialog answered/gone → the send goes through
    S.kv_del(P.mirror_log("msgm"), "ask-pending")
    code, body = _post(dash + "/api/session/msgm/message", {"text": "now ok"})
    assert code == 200 and fe.pasted == [("88", "now ok")]


class _NoTermFE:
    """A frontend with no reachable control channel (dashboard started outside
    kitty) → frontend() returns None → a clean 503, never a 500."""

    def usable(self):
        return False


def test_post_message_no_terminal_is_503(dash, monkeypatch):
    _inject_fe(monkeypatch, _NoTermFE())
    monkeypatch.setenv("KITTY_WINDOW_ID", "5")
    A.session_start({"session_id": "msg4", "cwd": "/w", "transcript_path": ""})
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/msg4/message", {"text": "hi"})
    assert e.value.code == 503


def _clientfail_rows(sid):
    """Read the `web-clientfail` state_files rows written to the hermetic
    in-process audit DB. A dashboard REQUEST runs in its own thread, so its
    audit write SPOOLS (the cached _CONN is bound to another thread) rather than
    hitting the DB — the same degrade path production relies on, drained by the
    next process to open the DB. Force that drain here (fresh _connect ingests
    the spool) before reading."""
    import sqlite3
    A._CONN = None
    A._FAILED = False
    A._connect()                     # drains spool.jsonl into the DB
    con = sqlite3.connect(A.db_path())
    try:
        return [json.loads(c) for (c,) in con.execute(
            "SELECT content FROM state_files WHERE session_id=? "
            "AND action='web-clientfail' ORDER BY ts", (sid,))]
    finally:
        con.close()


def test_client_fail_beacon_records_transport_and_http(dash, monkeypatch):
    """A "send failed" toast is a CLIENT-side fetch rejection the server can't
    see (it audits `web-send` + returns 200 BEFORE the response travels back —
    a lost response toasts a failure over a send that SUCCEEDED). The page
    beacons what IT saw as a `web-clientfail` row: `kind:transport` (the fetch
    itself rejected — the audit-blind case) vs `kind:http` (a server error
    status; a paired failure row exists). Audit-only: 200, no terminal writes.
    docs/dashboard.md, *Client-observed send failures*."""
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "88")
    A.session_start({"session_id": "cf1", "cwd": "/w", "transcript_path": ""})
    O.emit(P.mirror_log("cf1"), O.label("hi", (1, 2, 3)))   # materialize state DB
    # transport failure (the lost-response class): no status, kind coerced
    code, body = _post(dash + "/api/session/cf1/client-fail",
                       {"gesture": "send", "kind": "transport",
                        "error": "Failed to fetch", "chars": 10})
    assert code == 200 and json.loads(body)["ok"] is True
    # a beacon never types into the terminal
    assert fe.pasted == [] and fe.sent == []
    # http failure carries the status through
    _post(dash + "/api/session/cf1/client-fail",
          {"gesture": "resume", "kind": "http", "error": "send failed",
           "status": 502})
    rows = _clientfail_rows("cf1")
    assert rows[0] == {"gesture": "send", "kind": "transport",
                       "error": "Failed to fetch", "chars": 10}
    assert rows[1]["gesture"] == "resume" and rows[1]["kind"] == "http"
    assert rows[1]["status"] == 502


def test_client_fail_beacon_defaults_bad_kind_and_guards(dash, monkeypatch):
    """An unknown/absent `kind` defaults to `transport` (the conservative
    audit-blind reading), and the beacon is behind the control-plane POST guard
    like every write — a missing X-Claude-Dash header is a 403."""
    A.session_start({"session_id": "cf2", "cwd": "/w", "transcript_path": ""})
    O.emit(P.mirror_log("cf2"), O.label("hi", (1, 2, 3)))
    code, _ = _post(dash + "/api/session/cf2/client-fail", {"gesture": "send"})
    assert code == 200
    assert _clientfail_rows("cf2")[0]["kind"] == "transport"
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/cf2/client-fail",
              {"gesture": "send"}, header=None)
    assert e.value.code == 403


def _hint_rows(sid):
    """The `web-hint` state_files rows (the optimistic-UI lifecycle beacons).
    Same spool-drain dance as _clientfail_rows — a request-thread audit write
    spools; force the drain before reading."""
    import sqlite3
    A._CONN = None
    A._FAILED = False
    A._connect()
    con = sqlite3.connect(A.db_path())
    try:
        return [json.loads(c) for (c,) in con.execute(
            "SELECT content FROM state_files WHERE session_id=? "
            "AND action='web-hint' ORDER BY ts", (sid,))]
    finally:
        con.close()


def test_hint_audit_records_op_lifecycle(dash, monkeypatch):
    """The optimistic-UI beacon (docs/dashboard.md, *Optimistic UI & the
    web-hint audit*): every op (composer | close | answer | plan) beacons its
    shown → reconciled/dropped/stale lifecycle as `web-hint` rows so a stuck
    greyed state is debuggable. Audit-only: 200, no terminal writes, and the op
    + phase (+ optional wait_ms/reason/chars) round-trip into content."""
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    A.session_start({"session_id": "wh1", "cwd": "/w", "transcript_path": ""})
    O.emit(P.mirror_log("wh1"), O.label("hi", (1, 2, 3)))   # materialize state DB
    # composer bubble (op omitted → defaults to composer), then its reconcile
    code, body = _post(dash + "/api/session/wh1/hint-audit",
                       {"phase": "shown", "chars": 12})
    assert code == 200 and json.loads(body)["ok"] is True
    _post(dash + "/api/session/wh1/hint-audit",
          {"op": "composer", "phase": "reconciled", "chars": 12, "wait_ms": 340})
    # a card op with a dropped reason, and a close op
    _post(dash + "/api/session/wh1/hint-audit",
          {"op": "answer", "phase": "dropped", "reason": "failed"})
    _post(dash + "/api/session/wh1/hint-audit", {"op": "close", "phase": "stale"})
    # a beacon never types into the terminal
    assert fe.pasted == [] and fe.sent == []
    rows = _hint_rows("wh1")
    assert rows[0] == {"op": "composer", "phase": "shown", "chars": 12}
    assert rows[1]["op"] == "composer" and rows[1]["phase"] == "reconciled"
    assert rows[1]["wait_ms"] == 340
    assert rows[2] == {"op": "answer", "phase": "dropped", "reason": "failed"}
    assert rows[3] == {"op": "close", "phase": "stale"}


def test_hint_audit_guards_bad_op_and_phase(dash, monkeypatch):
    """A bad phase or op is a 400 that now leaves an `ok:False` web-hint reject
    row (via `_reject_input`, filed under the session — no longer a silent 4xx);
    the beacon is behind the control-plane POST guard like every write (missing
    header → 403, a `web-reject` row, NOT a web-hint one)."""
    A.session_start({"session_id": "wh2", "cwd": "/w", "transcript_path": ""})
    O.emit(P.mirror_log("wh2"), O.label("hi", (1, 2, 3)))
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/wh2/hint-audit", {"phase": "bogus"})
    assert e.value.code == 400
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/wh2/hint-audit",
              {"op": "nonsense", "phase": "shown"})
    assert e.value.code == 400
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/wh2/hint-audit",
              {"phase": "shown"}, header=None)
    assert e.value.code == 403
    # the two bad-body 400s each left an audited reject; the guard 403 did NOT
    # write a web-hint row (it's a web-reject).
    rows = _hint_rows("wh2")
    assert rows == [{"ok": False, "why": "bad phase", "phase": "'bogus'"},
                    {"ok": False, "why": "bad op", "op": "'nonsense'"}]


def _client_rows(sid):
    """The `web-client` frontend-audit rows. Same spool-drain dance as
    _hint_rows — a request-thread audit write spools; force the drain."""
    import sqlite3
    A._CONN = None
    A._FAILED = False
    A._connect()
    con = sqlite3.connect(A.db_path())
    try:
        return [json.loads(c) for (c,) in con.execute(
            "SELECT content FROM state_files WHERE session_id=? "
            "AND action='web-client' ORDER BY ts", (sid,))]
    finally:
        con.close()


def test_client_log_records_frontend_audit_batch(dash, monkeypatch):
    """The frontend audit sink (docs/dashboard.md, *Frontend audit (clientlog)*):
    a BATCH of browser events lands as one `web-client` state_files row each,
    scoped to each event's OWN sid — the ground truth a control request the
    server never saw (a tunnel-dropped /stop) leaves ONLY on the client. Each row
    keeps the event name + its scalar fields + the shared connection snapshot; a
    session-less event (a boot record, a launch) lands under sid=''. Audit-only:
    200, no terminal writes."""
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    A.session_start({"session_id": "cl1", "cwd": "/w", "transcript_path": ""})
    O.emit(P.mirror_log("cl1"), O.label("hi", (1, 2, 3)))   # materialize state DB
    body = {
        "client": "abc123",
        "device": "dev-abc",
        "conn": {"online": True, "view": "session", "es": 2, "conn": 1},
        "events": [
            {"t": 1000, "sid": "cl1", "ev": "close.begin", "via": "header", "es": 2},
            {"t": 1100, "sid": "cl1", "ev": "close.fail",
             "kind": "transport", "aborted": True, "ms": 12000},
            {"t": 1200, "sid": "cl1", "ev": "sse.drop", "s": "session"},
            # the wider vocabulary the server records generically (any ev name +
            # its scalars): a numeric-field js.error, a session-view stuck, a
            # launch latency, and a session-less boot/stale build record
            {"t": 1150, "sid": "cl1", "ev": "js.error",
             "msg": "TypeError: x", "src": "static/app.js", "line": 878, "col": 28},
            {"t": 1250, "sid": "cl1", "ev": "meta.stuck", "tries": 12},
            {"t": 1350, "sid": "", "ev": "launch.hit", "ms": 2200, "quiet": False},
            {"t": 1300, "sid": "", "ev": "boot",
             "origin": "https://baqylau.zhambyl.top", "build": "b1"},
            {"t": 1400, "sid": "", "ev": "stale", "was": "b1", "now": "b2"},
        ],
    }
    code, resp = _post(dash + "/api/clientlog", body)
    assert code == 200 and json.loads(resp)["ok"] is True
    assert fe.pasted == [] and fe.sent == []       # telemetry never types
    rows = _client_rows("cl1")
    assert [r["ev"] for r in rows] == [
        "close.begin", "close.fail", "sse.drop", "js.error", "meta.stuck"]
    assert rows[0]["via"] == "header" and rows[0]["client"] == "abc123"
    # device attribution (the frontend side of notification device-routing)
    assert rows[0]["device"] == "dev-abc"
    assert rows[0]["t"] == 1000
    assert rows[0]["conn"] == {"online": True, "view": "session", "es": 2, "conn": 1}
    assert rows[1]["kind"] == "transport" and rows[1]["aborted"] is True
    js = next(r for r in rows if r["ev"] == "js.error")
    assert js["line"] == 878 and js["col"] == 28 and js["src"] == "static/app.js"
    assert next(r for r in rows if r["ev"] == "meta.stuck")["tries"] == 12
    # session-less rows (boot with its loaded build, stale, launch) land under ''
    less = {r["ev"]: r for r in _client_rows("")}
    assert less["boot"]["origin"] == "https://baqylau.zhambyl.top"
    assert less["boot"]["build"] == "b1"
    assert less["stale"] == {"ev": "stale", "client": "abc123", "device": "dev-abc",
                             "t": 1400, "was": "b1", "now": "b2",
                             "conn": {"online": True, "view": "session",
                                      "es": 2, "conn": 1}}
    assert less["launch.hit"]["ms"] == 2200 and less["launch.hit"]["quiet"] is False


def test_client_log_caps_guards_and_sanitizes(dash, monkeypatch):
    """The sink is bounded + guarded: a non-list `events` is 400; more than
    CLIENTLOG_MAX events are truncated; non-dict / blank-`ev` events are skipped;
    string fields are capped; and it sits behind the control-plane POST guard
    (missing X-Claude-Dash header → 403)."""
    A.session_start({"session_id": "cl2", "cwd": "/w", "transcript_path": ""})
    O.emit(P.mirror_log("cl2"), O.label("hi", (1, 2, 3)))
    # a non-list events payload is a 400
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/clientlog", {"events": "nope"})
    assert e.value.code == 400
    # an oversized batch is truncated to CLIENTLOG_MAX rows
    events = [{"sid": "cl2", "ev": "spam"} for _ in range(DS.config.CLIENTLOG_MAX + 20)]
    code, _ = _post(dash + "/api/clientlog", {"events": events})
    assert code == 200 and len(_client_rows("cl2")) == DS.config.CLIENTLOG_MAX
    # junk events skipped; a long string field capped
    A.session_start({"session_id": "cl3", "cwd": "/w", "transcript_path": ""})
    O.emit(P.mirror_log("cl3"), O.label("hi", (1, 2, 3)))
    _post(dash + "/api/clientlog", {"events": [
        "not-a-dict", {"sid": "cl3", "ev": ""},        # both skipped
        {"sid": "cl3", "ev": "boot", "big": "x" * 5000}]})
    rows = _client_rows("cl3")
    assert len(rows) == 1 and rows[0]["ev"] == "boot"
    assert len(rows[0]["big"]) == DS.config.CLIENTLOG_STR_MAX
    # behind the control-plane guard
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/clientlog", {"events": []}, header=None)
    assert e.value.code == 403


def test_post_command_sends_slash_text(dash, monkeypatch):
    # the quick-command row types the TUI's OWN slash commands — exact text,
    # bracketed paste like the composer (never send_text). Blank screens: no
    # switch-confirm menu opens, so model/effort reply confirm="none" with no
    # key pressed
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setattr(CFD, "OPEN_TIMEOUT_S", 0.05)
    monkeypatch.setenv("KITTY_WINDOW_ID", "61")
    A.session_start({"session_id": "qc1", "cwd": "/w", "transcript_path": ""})
    code, body = _post(dash + "/api/session/qc1/command", {"cmd": "compact"})
    assert code == 200 and json.loads(body) == {"ok": True, "queued": False,
                                                "tab": ""}
    code, body = _post(dash + "/api/session/qc1/command",
                       {"cmd": "model", "arg": "sonnet[1m]"})
    assert code == 200 and json.loads(body)["confirm"] == "none"
    code, body = _post(dash + "/api/session/qc1/command",
                       {"cmd": "effort", "arg": "low"})
    assert code == 200 and json.loads(body)["confirm"] == "none"
    # ✦ auto-rename: bare /rename — argless like compact, and no confirm
    # watch (Claude Code names the session, no switch-confirm menu opens)
    code, body = _post(dash + "/api/session/qc1/command", {"cmd": "rename"})
    assert code == 200 and json.loads(body) == {"ok": True, "queued": False,
                                                "tab": ""}
    assert fe.pasted == [("61", "/compact"), ("61", "/model sonnet[1m]"),
                         ("61", "/effort low"), ("61", "/rename")]
    assert fe.sent == [] and fe.keyed == []


# the switch-confirm menu as the TUI paints it (observed live 2026-07-18):
# indented ❯-cursored numbered options, Yes first — but the digit is resolved
# from the labels, never assumed. (_CONFIRM_SCREEN further down in this file
# is the rewind confirm pane — a different dialog.)
_SWITCH_CONFIRM_SCREEN = """\
 Change effort level?        Your next response will be slower

 This conversation is cached for the current effort level.

 ❯ 1. Yes, switch to low
     2. No, go back
"""


def test_post_command_answers_switch_confirm_menu(dash, monkeypatch):
    # /effort opened the TUI's are-you-sure menu (the prompt-cache warning) —
    # the server presses its own Yes digit and verifies the menu closed;
    # unanswered, the web click looked dead (reported live 2026-07-18)
    fe = _FakeFE()
    fe.screens = [_SWITCH_CONFIRM_SCREEN, ""]   # menu up → gone after Yes
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "65")
    A.session_start({"session_id": "qc5", "cwd": "/w", "transcript_path": ""})
    code, body = _post(dash + "/api/session/qc5/command",
                       {"cmd": "effort", "arg": "low"})
    assert code == 200 and json.loads(body)["confirm"] == "confirmed"
    assert fe.pasted == [("65", "/effort low")]
    assert fe.keyed == [("65", ("1",))]


def test_post_command_stuck_confirm_menu_reports_failed(dash, monkeypatch):
    # the menu never closes after Yes: still 200 (the command WAS typed) but
    # confirm="failed" so the page tells the user to answer in the terminal;
    # the menu is left open — never Escaped away
    fe = _FakeFE()
    fe.screens = [_SWITCH_CONFIRM_SCREEN]       # sticks forever
    _inject_fe(monkeypatch, fe)
    monkeypatch.setattr(CFD, "STEP_TIMEOUT_S", 0.05)
    monkeypatch.setenv("KITTY_WINDOW_ID", "66")
    A.session_start({"session_id": "qc6", "cwd": "/w", "transcript_path": ""})
    code, body = _post(dash + "/api/session/qc6/command",
                       {"cmd": "effort", "arg": "low"})
    assert code == 200 and json.loads(body)["confirm"] == "failed"
    assert fe.keyed == [("66", ("1",))]


def test_session_detail_effort_from_settings(dash, tmp_path):
    # the effort quick-button's label: the SAVED effortLevel (every applied
    # /effort writes itself through to settings — per-session effort is
    # readable from nowhere else), resolved for the session's cwd via the
    # plugins.effort_default fan-out; here the hermetic config dir's
    # settings.json is the only layer
    cfg = os.environ["CLAUDE_CONFIG_DIR"]
    with open(os.path.join(cfg, "settings.json"), "w", encoding="utf-8") as fh:
        json.dump({"effortLevel": "xhigh"}, fh)
    A.session_start({"session_id": "eff1", "cwd": str(tmp_path),
                     "transcript_path": ""})
    code, body = _get(dash + "/api/session/eff1")
    assert code == 200 and json.loads(body)["effort"] == "xhigh"


def test_session_detail_effort_per_account_config(dash, tmp_path, monkeypatch):
    # a session under a switcher account (statusline-stashed slug) resolves
    # THAT account's config dir (configs/<slug>/settings.json), not the
    # ambient one — each subscription account carries its own effortLevel
    from plugins.claude_code import account as ACC
    cfg = tmp_path / "configs" / "c9"
    cfg.mkdir(parents=True)
    (cfg / "settings.json").write_text(json.dumps({"effortLevel": "max"}))
    monkeypatch.setattr(ACC, "CONFIGS_DIR", str(tmp_path / "configs"))
    ambient = os.environ["CLAUDE_CONFIG_DIR"]
    with open(os.path.join(ambient, "settings.json"), "w", encoding="utf-8") as fh:
        json.dump({"effortLevel": "low"}, fh)
    A.session_start({"session_id": "eff2", "cwd": str(tmp_path),
                     "transcript_path": ""})
    S.kv_set(P.mirror_log("eff2"), "account", {"slug": "c9", "label": "c9"})
    code, body = _get(dash + "/api/session/eff2")
    assert code == 200 and json.loads(body)["effort"] == "max"


def test_confirm_find_menu_shape_not_prose():
    # detection is by SHAPE: a ❯-cursored numbered list with Yes+No labels.
    # The bare composer prompt and scrollback prose that happens to enumerate
    # Yes/No must NOT match (a false press would type a digit into the chat)
    cd = CFD
    assert cd.find_menu(_SWITCH_CONFIRM_SCREEN) == "1"
    assert cd.find_menu("") is None
    assert cd.find_menu("some output\n❯ \n") is None          # bare prompt
    assert cd.find_menu("1. Yes, option A\n2. No, option B\n") is None  # no ❯
    assert cd.find_menu(" ❯ 1. Restore code\n   2. No, go back\n") is None


def test_ask_current_question_longest_match():
    # only ONE question shows at a time, but if question i's stripped text is a
    # substring of question j's, a FIRST-match scan returns i while j is on
    # screen and drive()'s wait for j never resolves. The most specific
    # (longest) matching question is the one displayed.
    ad = ASKD
    qs = [{"question": "Pick a color"}, {"question": "Pick a color scheme"}]
    # ☐ anchors the region; "Enter to select" is the pane footer
    showing_j = "☐ chips\nPick a color scheme\n1. dark\nEnter to select"
    assert ad.current_question(showing_j, qs) == 1
    showing_i = "☐ chips\nPick a color\n1. red\nEnter to select"
    assert ad.current_question(showing_i, qs) == 0
    # the review pane repeats every question's text — still None
    assert ad.current_question("☐ x\nReview your answers\nPick a color", qs) is None


def test_ask_dialog_open_when_chip_bar_scrolled_off():
    # On a NARROW/SHORT window a tall dialog overflows the viewport and the
    # ☐/☒ chip bar scrolls off the top while the footer survives — get_text
    # returns only the visible screen. A chip-bar-only anchor returned "" and
    # false-bailed step:open on a genuinely-open dialog (session 819627e5).
    ad = ASKD
    # exactly what the errors-row `screen` capture showed: options + footer,
    # no ☐/☒ anywhere.
    off_screen = ("     approval.\n  3. Just diagnose\n  4. Type something.\n"
                  "──────────────────────────────\n  5. Chat about this\n\n"
                  "Enter to select · Tab/Arrow \nkeys to navigate · Esc to \ncancel\n")
    assert "☐" not in off_screen and "☒" not in off_screen
    assert ad.dialog_open(off_screen)                 # footer fallback anchors
    # the option/action rows are still parseable from the wider region
    labels = [r["label"] for r in ad.rows(off_screen)]
    assert "Type something." in labels and ad.CHAT_LABEL in labels
    # the chip-bar path stays primary (excludes transcript above the bar)
    with_bar = "prose above\n☐ Q1  ☒ Q2\n1. red\nEnter to select"
    assert ad.region(with_bar).startswith("☐ Q1")
    # a screen with neither chip bar nor footer is genuinely no-dialog
    assert not ad.dialog_open("just some transcript text\nno dialog here")
    assert ad.region("just some transcript text") == ""


def test_post_command_bad_vocabulary_is_400(dash, monkeypatch):
    # fixed vocabulary: unknown command, missing/dirty model arg (a shell
    # metacharacter must never reach the terminal), unknown effort level,
    # and compact/rename-with-arg all reject without a keystroke (this
    # endpoint's vocabulary is ARGLESS `/rename` — the ✦ auto button; a NAMED
    # rename is post_rename's, which types the same command with the name)
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "62")
    A.session_start({"session_id": "qc2", "cwd": "/w", "transcript_path": ""})
    for bad in ({"cmd": "clear"}, {"cmd": "model"},
                {"cmd": "model", "arg": "opus; rm -rf /"},
                {"cmd": "model", "arg": "opus[2m]"},
                {"cmd": "effort", "arg": "turbo"},
                {"cmd": "compact", "arg": "focus on the tests"},
                {"cmd": "rename", "arg": "my session"}):
        with pytest.raises(urllib.error.HTTPError) as e:
            _post(dash + "/api/session/qc2/command", bad)
        assert e.value.code == 400
    assert fe.pasted == []


def test_post_command_dialog_and_queue_tabs(dash, monkeypatch):
    # awaiting-command (red — a modal dialog is up) refuses: pasted text
    # would land IN the dialog, its digits deciding it. A busy tab queues
    # like any typed input and the reply says so.
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "63")
    A.session_start({"session_id": "qc3", "cwd": "/w", "transcript_path": ""})
    states = {"63": "awaiting-command"}
    monkeypatch.setattr(DS.API, "tab_states", lambda: dict(states))
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/qc3/command", {"cmd": "compact"})
    assert e.value.code == 409
    assert fe.pasted == []
    states["63"] = "working"
    code, body = _post(dash + "/api/session/qc3/command",
                       {"cmd": "effort", "arg": "high"})
    assert code == 200
    # queued: NO confirm watch (the command runs at the turn boundary — no
    # menu to wait for now), so no `confirm` field and no screen reads
    assert json.loads(body) == {"ok": True, "queued": True, "tab": "working"}
    assert fe.pasted == [("63", "/effort high")]
    assert fe.keyed == []


def test_post_command_no_window_is_409(dash, monkeypatch):
    _inject_fe(monkeypatch, _FakeFE())
    monkeypatch.delenv("KITTY_WINDOW_ID", raising=False)   # headless session
    A.session_start({"session_id": "qc4", "cwd": "/w", "transcript_path": ""})
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/qc4/command", {"cmd": "compact"})
    assert e.value.code == 409


def _rename_transcript(tmp_path, sid, *objs):
    # a transcript at the REAL layout (…/projects/<hash>/<sid>.jsonl) — the
    # set_session_title recognition gate refuses anything else
    d = tmp_path / "projects" / "-w-proj"
    d.mkdir(parents=True, exist_ok=True)
    p = d / (sid + ".jsonl")
    p.write_text(_jl(*objs))
    return str(p)


def test_post_rename_live_types_claude_codes_own_rename(dash, monkeypatch,
                                                        tmp_path):
    # THE CHANNEL FIX (2026-07-29): a LIVE rename is Claude Code's own
    # `/rename <name>`, pasted — NOT a record we append. Claude Code re-emits
    # its in-memory `agent-name` every turn, so a record it did not write is
    # clobbered within one turn; making it change its own mind is the only
    # thing every reader (picker, OSC tab title, hook payload, dashboard)
    # follows.
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "42")
    tp = _rename_transcript(tmp_path, "ren1",
                            {"type": "user", "message": {"content": "hi"}},
                            {"type": "ai-title", "aiTitle": "auto title"})
    A.session_start({"session_id": "ren1", "cwd": "/w", "transcript_path": tp,
                     "kitty_window_id": "42"})
    with open(tp, encoding="utf-8") as fh:
        before = fh.read()
    code, body = _post(dash + "/api/session/ren1/rename", {"name": "my session"})
    assert code == 200
    assert json.loads(body) == {"ok": True, "title": "my session",
                                "channel": "tui", "queued": False}
    # pasted (mode-proof), and the transcript is left ALONE — Claude Code
    # writes the record itself when it applies the command
    assert fe.pasted == [("42", "/rename my session")]
    with open(tp, encoding="utf-8") as fh:
        assert fh.read() == before
    # and the tab is NOT retitled: a sticky title would be a second writer of
    # the name, free to disagree with the session's own
    assert fe.titled == []


def test_post_rename_live_mid_turn_reports_queued(dash, monkeypatch, tmp_path):
    # mid-turn the paste lands in the TUI's message queue and applies at the
    # turn boundary — the page must not claim the name yet
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "43")
    tp = _rename_transcript(tmp_path, "ren1b",
                            {"type": "user", "message": {"content": "hi"}})
    A.session_start({"session_id": "ren1b", "cwd": "/w", "transcript_path": tp,
                     "kitty_window_id": "43"})
    monkeypatch.setattr(DS.API, "tab_states", lambda: {"43": "working"})
    code, body = _post(dash + "/api/session/ren1b/rename", {"name": "later"})
    assert code == 200 and json.loads(body)["queued"] is True
    assert fe.pasted == [("43", "/rename later")]


def test_post_rename_live_refuses_on_open_dialog(dash, monkeypatch, tmp_path):
    # a red awaiting-command tab means a modal dialog is up: pasted text would
    # land IN it (the same refusal post_command makes)
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "44")
    tp = _rename_transcript(tmp_path, "ren1c",
                            {"type": "user", "message": {"content": "hi"}})
    A.session_start({"session_id": "ren1c", "cwd": "/w", "transcript_path": tp,
                     "kitty_window_id": "44"})
    monkeypatch.setattr(DS.API, "tab_states",
                        lambda: {"44": "awaiting-command"})
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/ren1c/rename", {"name": "nope"})
    assert e.value.code == 409
    assert fe.pasted == []


def test_post_rename_parked_no_window_appends(dash, monkeypatch, tmp_path):
    # DELIBERATELY unlike post_message: no live window is NOT an error — it
    # selects the PARKED half, where the append is safe (nothing is running to
    # re-emit over it) and `claude --resume` reads the file fresh
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.delenv("KITTY_WINDOW_ID", raising=False)
    tp = _rename_transcript(tmp_path, "ren2",
                            {"type": "user", "message": {"content": "hi"}})
    A.session_start({"session_id": "ren2", "cwd": "/w", "transcript_path": tp})
    fe.wins["ren2"] = None                    # no live claude_session tag
    code, body = _post(dash + "/api/session/ren2/rename", {"name": "parked one"})
    assert code == 200
    d = json.loads(body)
    assert d["ok"] is True and d["channel"] == "transcript"
    with open(tp, encoding="utf-8") as fh:
        assert json.loads(fh.read().splitlines()[-1])["agentName"] == "parked one"
    assert fe.pasted == [] and fe.titled == []


def test_post_rename_no_terminal_still_appends(dash, monkeypatch, tmp_path):
    # ...and no terminal at all (dashboard outside kitty) is not an error
    # either — post_message's 503 deliberately does not apply here
    _inject_fe(monkeypatch, _NoTermFE())
    monkeypatch.setenv("KITTY_WINDOW_ID", "5")
    tp = _rename_transcript(tmp_path, "ren3",
                            {"type": "user", "message": {"content": "hi"}})
    A.session_start({"session_id": "ren3", "cwd": "/w", "transcript_path": tp})
    code, body = _post(dash + "/api/session/ren3/rename", {"name": "still works"})
    assert code == 200 and json.loads(body)["channel"] == "transcript"
    with open(tp, encoding="utf-8") as fh:
        assert json.loads(fh.read().splitlines()[-1])["agentName"] == "still works"


def test_post_rename_empty_name_is_400(dash, monkeypatch, tmp_path):
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "9")
    tp = _rename_transcript(tmp_path, "ren4",
                            {"type": "user", "message": {"content": "hi"}})
    A.session_start({"session_id": "ren4", "cwd": "/w", "transcript_path": tp})
    with open(tp, encoding="utf-8") as fh:
        before = fh.read()
    for bad in ({}, {"name": "   "}, {"name": "\x1b\x07\n \x00"}, {"name": 7}):
        with pytest.raises(urllib.error.HTTPError) as e:
            _post(dash + "/api/session/ren4/rename", bad)
        assert e.value.code == 400
    with open(tp, encoding="utf-8") as fh:
        assert fh.read() == before
    assert fe.pasted == []


def test_post_rename_no_transcript_is_409(dash, monkeypatch, tmp_path):
    _inject_fe(monkeypatch, _FakeFE())
    monkeypatch.setenv("KITTY_WINDOW_ID", "9")
    A.session_start({"session_id": "ren5", "cwd": "/w", "transcript_path": ""})
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/ren5/rename", {"name": "x"})
    assert e.value.code == 409
    # a recorded path that no longer exists: 409, and NEVER created just to
    # name it (the "a" open would)
    gone = str(tmp_path / "projects" / "-w-proj" / "ren5b.jsonl")
    A.session_start({"session_id": "ren5b", "cwd": "/w",
                     "transcript_path": gone})
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/ren5b/rename", {"name": "x"})
    assert e.value.code == 409
    assert not os.path.exists(gone)


def test_post_rename_unsupported_transcript_is_409(dash, monkeypatch,
                                                   tmp_path):
    # a transcript_path OUTSIDE the projects/ layout (a codex standalone
    # host's rollout) must receive NEITHER a Claude agent-name record NOR a
    # typed /rename — and its window carries the same claude_session tag, so
    # the plugins.renameable gate runs BEFORE the live/parked branch
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "9")
    d = tmp_path / "rollouts"
    d.mkdir()
    tp = str(d / "rollout-ren6.jsonl")
    with open(tp, "w", encoding="utf-8") as fh:
        fh.write(_jl({"type": "session_meta"}))
    A.session_start({"session_id": "ren6", "cwd": "/w", "transcript_path": tp,
                     "kitty_window_id": "9"})
    with open(tp, encoding="utf-8") as fh:
        before = fh.read()
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/ren6/rename", {"name": "x"})
    assert e.value.code == 409
    with open(tp, encoding="utf-8") as fh:
        assert fh.read() == before
    assert fe.pasted == []


def test_post_rename_strips_controls_and_caps(dash, monkeypatch, tmp_path):
    # control bytes (the OSC/CSI injection class) never enter the name — which
    # on the LIVE path is text PASTED into a terminal, so the strip is what
    # keeps an OSC out of the command line; over-long names cap at RENAME_MAX
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "11")
    tp = _rename_transcript(tmp_path, "ren7",
                            {"type": "user", "message": {"content": "hi"}})
    A.session_start({"session_id": "ren7", "cwd": "/w", "transcript_path": tp,
                     "kitty_window_id": "11"})
    code, body = _post(dash + "/api/session/ren7/rename",
                       {"name": "a\x1b]2;evil\x07b\nc"})
    stored = json.loads(body)["title"]
    assert stored == "a ]2;evil b c"
    assert fe.pasted[-1] == ("11", "/rename a ]2;evil b c")
    long = "x" * (DS.config.RENAME_MAX + 300)
    code, body = _post(dash + "/api/session/ren7/rename", {"name": long})
    assert json.loads(body)["title"] == "x" * DS.config.RENAME_MAX
    assert fe.pasted[-1] == ("11", "/rename " + "x" * DS.config.RENAME_MAX)


def test_post_rename_updates_session_payload_title(dash, monkeypatch,
                                                   tmp_path):
    # PARKED: the (path, size) title cache self-invalidates on the append — the
    # very next GET shows the new name (list + header payloads share
    # session_title)
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "12")
    tp = _rename_transcript(tmp_path, "ren8",
                            {"type": "ai-title", "aiTitle": "auto"})
    A.session_start({"session_id": "ren8", "cwd": "/w", "transcript_path": tp})
    fe.wins["ren8"] = None                    # parked: no live window
    assert _get_json(dash + "/api/session/ren8")["title"] == "auto"
    _post(dash + "/api/session/ren8/rename", {"name": "picked by hand"})
    assert _get_json(dash + "/api/session/ren8")["title"] == "picked by hand"


def test_post_rename_override_survives_tail_window_rollback(dash, monkeypatch,
                                                           tmp_path):
    # THE ROLLBACK FIX, on the PARKED path (the only one that writes the record
    # itself): the /rename `agent-name` scrolls out of session_title's 64KB
    # tail-window in a long session while fresh ai-title rows sit near EOF, so
    # the transcript ladder reverts to the auto title. The durable override
    # (prefs `renamed-title`) is what keeps the DASHBOARD title from rolling back.
    from plugins.claude_code import transcript as TR
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "77")
    tp = _rename_transcript(tmp_path, "ren9",
                            {"type": "ai-title", "aiTitle": "auto"})
    A.session_start({"session_id": "ren9", "cwd": "/w", "transcript_path": tp})
    fe.wins["ren9"] = None                    # parked: no live window
    code, _ = _post(dash + "/api/session/ren9/rename", {"name": "kept name"})
    assert code == 200
    # simulate time passing: append enough fresh ai-title rows to push the
    # appended agent-name past the tail-window (the real-world rollback trigger)
    with open(tp, "a", encoding="utf-8") as fh:
        filler = json.dumps({"type": "ai-title", "aiTitle": "auto"}) + "\n"
        while os.path.getsize(tp) <= TR.TITLE_TAIL_B:
            fh.write(filler)
    # the transcript layer has "rolled back" — the rename is out of the tail
    assert TR.title_and_rename(tp)[1] == ""
    # ...but the dashboard still shows the rename, sourced from the durable override
    assert _get_json(dash + "/api/session/ren9")["title"] == "kept name"
    # and a FRESH in-tail rename still supersedes the override (last rename wins)
    _post(dash + "/api/session/ren9/rename", {"name": "renamed again"})
    assert _get_json(dash + "/api/session/ren9")["title"] == "renamed again"


def test_post_guard_rejections(dash):
    url = dash + "/api/sessions/new"
    with pytest.raises(urllib.error.HTTPError) as e:      # missing custom header
        _post(url, {"cwd": "/w"}, header=None)
    assert e.value.code == 403
    with pytest.raises(urllib.error.HTTPError) as e:      # wrong origin
        _post(url, {"cwd": "/w"}, origin="https://evil.test")
    assert e.value.code == 403
    with pytest.raises(urllib.error.HTTPError) as e:      # not JSON content type
        _post(url, {"cwd": "/w"}, ctype="text/plain")
    assert e.value.code == 415
    with pytest.raises(urllib.error.HTTPError) as e:      # malformed JSON body
        _post(url, raw=b"{not json")
    assert e.value.code == 400


def test_post_guard_accepts_beacon_by_allowlisted_origin(dash, monkeypatch):
    # navigator.sendBeacon (the pagehide clientlog flush — flushClog) can't set
    # X-Claude-Dash, so a HEADERLESS POST is accepted when it carries a present,
    # allowlisted Origin — a cross-origin page can forge neither, so the Origin
    # allowlist is the CSRF gate (docs/dashboard.md *Frontend audit (clientlog)*).
    monkeypatch.setattr(DS.config, "ALLOWED_ORIGINS", DS.config.ALLOWED_ORIGINS | {dash})
    ep = dash + "/api/session/beacon1/hint-audit"
    body = {"op": "close", "phase": "shown"}
    code, _ = _post(ep, body, header=None, origin=dash)   # the sendBeacon shape
    assert code == 200
    with pytest.raises(urllib.error.HTTPError) as e:       # headerless + NO origin
        _post(ep, body, header=None)                       # still rejected (unchanged)
    assert e.value.code == 403
    with pytest.raises(urllib.error.HTTPError) as e:       # headerless + bad origin
        _post(ep, body, header=None, origin="https://evil.test")
    assert e.value.code == 403


def test_post_new_session_bad_cwd_is_400(dash, monkeypatch):
    _inject_fe(monkeypatch, _FakeFE())
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/sessions/new", {"cwd": "/no/such/dir/here"})
    assert e.value.code == 400


def test_post_new_session_launches(dash, monkeypatch, tmp_path):
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    code, body = _post(dash + "/api/sessions/new",
                       {"cwd": str(tmp_path), "prompt": "do the thing"})
    assert code == 200 and json.loads(body) == {"ok": True, "win": ""}
    # claude runs through the user's interactive login shell (kitty's own env
    # has no user PATH / aliases); the prompt is a POSITIONAL arg, never
    # interpolated into the fixed command string.
    cwd, argv = fe.launched[0]
    assert cwd == str(tmp_path)
    sh, flags, script, dollar0 = argv[:4]
    from plugins.claude_code import account as ACCT
    assert os.path.basename(sh) in ACCT.LAUNCH_SHELLS
    assert flags == "-lic" and script == 'claude "$@"' and dollar0 == "claude"
    assert argv[4:] == ["do the thing"]
    # no prompt → no positional args after the $0 placeholder
    _post(dash + "/api/sessions/new", {"cwd": str(tmp_path)})
    assert fe.launched[-1][1][4:] == []
    # a hostile prompt stays one argv word — nothing for the shell to parse
    evil = '"; rm -rf ~; echo "'
    _post(dash + "/api/sessions/new", {"cwd": str(tmp_path), "prompt": evil})
    assert fe.launched[-1][1][4:] == [evil]


def test_post_new_session_audits_step_timings(dash, monkeypatch, tmp_path):
    """The `web-launch` row carries the per-step latency breakdown (`ms`), so a
    slow launch is attributable to a step from the DB alone — the client's
    `new.ok` clientlog only bounds the whole round-trip."""
    _inject_fe(monkeypatch, _FakeFE())
    code, _ = _post(dash + "/api/sessions/new", {"cwd": str(tmp_path)})
    assert code == 200
    ms = (_last_state_file("", "web-launch") or {}).get("ms")
    # the steps every launch runs (a resume adds row/livewin) + the total
    assert set(ms) == {"fe", "front", "clip", "tab", "all"}
    assert all(isinstance(v, int) and v >= 0 for v in ms.values())
    assert ms["all"] >= ms["tab"]


class _WatchAudit:
    """Wraps the server's audit handle: records one action's state_file rows
    in-memory (a watch thread's audit write cross-thread would land in the
    spool, invisible to a same-process DB read) and delegates everything else
    to the real module."""

    def __init__(self, real, action="web-launch-steal-watch"):
        self.real, self.action, self.rows = real, action, []

    def __getattr__(self, name):
        return getattr(self.real, name)

    def state_file(self, log, path, action, content=""):
        if action == self.action:
            self.rows.append(content)
        return self.real.state_file(log, path, action, content)


@pytest.fixture(autouse=True)
def _fast_launch_wake(monkeypatch):
    """Every successful /api/sessions/new spawns a launch_wake poller thread;
    at the product's 15s budget one would outlive its test and keep polling the
    shared audit DB while later tests run. Clamp the budget module-wide; the
    wake tests below re-raise it themselves."""
    monkeypatch.setattr(DS.launch, "LAUNCHWAKE_MAX_S", 0.2)
    monkeypatch.setattr(DS.launch, "LAUNCHWAKE_POLL_S", 0.01)


def _watch_rig(monkeypatch, fronts, bundle="app.term"):
    """Wire the steal watch for a test: a _FakeFE with an OS app id, a
    scripted front_app sequence (call 1 = the pre-launch capture, the rest =
    the watch polls; the last value repeats), a fast poll cadence, a recorded
    audit. Returns (fe, rows) — rows collects the watch's audit content."""
    fe = _FakeFE()
    fe.bundle_id = bundle
    seq = list(fronts)
    monkeypatch.setattr(DS.launch, "front_app",
                        lambda: seq.pop(0) if len(seq) > 1 else seq[0])
    monkeypatch.setattr(DS.launch, "STEALWATCH_POLL_S", 0.005)
    aud = _WatchAudit(DS.launch.A)
    monkeypatch.setattr(DS.launch, "A", aud)
    return fe, aud.rows


def test_new_session_steal_watch_records_takeovers(dash, monkeypatch,
                                                   tmp_path):
    # the watch records each TRANSITION onto the terminal (steal → back to the
    # browser → steal again = 2 entries, not one per poll while stolen), and
    # NEVER intervenes — there is deliberately no focus-changing code left in
    # the dashboard (the 2026-07-18 bounce-back yanked users who genuinely
    # switched to the terminal; the fix lives in launch_pane's conditional
    # --keep-focus instead)
    fe, rows = _watch_rig(
        monkeypatch, ["com.browser", "app.term", "app.term", "com.browser",
                      "app.term"])
    _inject_fe(monkeypatch, fe)
    code, _ = _post(dash + "/api/sessions/new", {"cwd": str(tmp_path)})
    assert code == 200 and fe.launched
    wait_until(lambda: rows, desc="steal watch wrote its audit row")
    assert len(rows[0]["steals"]) == 2
    assert rows[0]["before"] == "com.browser"
    assert rows[0]["terminal"] == "app.term"


def test_new_session_steal_watch_clean_run(dash, monkeypatch, tmp_path):
    # frontmost never lands on the terminal (unchanged, or the user switching
    # to some OTHER app) → an empty steals list
    fe, rows = _watch_rig(monkeypatch, ["com.browser", "com.other"])
    _inject_fe(monkeypatch, fe)
    code, _ = _post(dash + "/api/sessions/new", {"cwd": str(tmp_path)})
    assert code == 200
    wait_until(lambda: rows, desc="steal watch wrote its audit row")
    assert rows[0]["steals"] == []


def test_new_session_watch_off_without_app_id(dash, monkeypatch, tmp_path):
    # a frontend with no OS-level app identity (the inert stub, a future
    # terminal that can't name itself) → the watch never probes the OS
    fe = _FakeFE()                                     # bundle_id stays ""
    _inject_fe(monkeypatch, fe)
    probed = []
    monkeypatch.setattr(DS.launch, "front_app", lambda: probed.append(1) or "x")
    code, _ = _post(dash + "/api/sessions/new", {"cwd": str(tmp_path)})
    assert code == 200 and probed == []


def test_launch_wake_pushes_and_audits(dash, monkeypatch, tmp_path):
    # the post-launch wake watch: the launched session's SessionStart appears
    # → ONE NOTIFIER `wake` naming the sid (the page's fast jump, matched by
    # the window id kitty printed at launch) + one web-launch-wake audit row
    # carrying the measured launch→appearance latency
    fe = _FakeFE()
    fe.launch_ok = "88"                     # kitty printed the new window's id
    _inject_fe(monkeypatch, fe)
    monkeypatch.setattr(DS.launch, "LAUNCHWAKE_MAX_S", 5.0)
    aud = _WatchAudit(DS.launch.A, "web-launch-wake")
    monkeypatch.setattr(DS.launch, "A", aud)
    # prime core.audit's process-wide sqlite conn from THIS thread: the first
    # audit write binds the conn to its creating thread, and if the POST
    # handler's thread claims it, the session_start below silently degrades
    # to the spool — a DB the watcher's read never sees (same per-process
    # caching story as conftest._fresh_audit_conn)
    monkeypatch.delenv("KITTY_WINDOW_ID", raising=False)
    A.session_start({"session_id": "prime0", "cwd": "/elsewhere",
                     "transcript_path": ""})
    q = DS.NOTIFIER.register()
    try:
        code, body = _post(dash + "/api/sessions/new", {"cwd": str(tmp_path)})
        assert code == 200 and json.loads(body) == {"ok": True, "win": "88"}
        # SessionStart lands while the watcher is polling
        monkeypatch.setenv("KITTY_WINDOW_ID", "88")
        A.session_start({"session_id": "wake1", "cwd": str(tmp_path),
                         "transcript_path": ""})
        ev, payload = q.get(timeout=5)
        assert ev == "wake"
        assert payload["sid"] == "wake1" and payload["win"] == "88"
        wait_until(lambda: aud.rows, desc="wake audit row")
        assert aud.rows[0]["ok"] is True and aud.rows[0]["sid"] == "wake1"
        assert aud.rows[0]["waited_s"] >= 0
    finally:
        DS.NOTIFIER.unregister(q)


def test_launch_wake_timeout_audits_without_push(dash, monkeypatch, tmp_path):
    # no session ever appears → the watcher gives up at its budget, audits the
    # timeout (sid empty, ok False) and pushes NOTHING — a wake with no sid
    # would have nothing for the page to jump to
    fe = _FakeFE()                          # launch_ok True → no window id
    _inject_fe(monkeypatch, fe)
    aud = _WatchAudit(DS.launch.A, "web-launch-wake")
    monkeypatch.setattr(DS.launch, "A", aud)
    q = DS.NOTIFIER.register()
    try:
        code, body = _post(dash + "/api/sessions/new", {"cwd": str(tmp_path)})
        assert code == 200 and json.loads(body) == {"ok": True, "win": ""}
        wait_until(lambda: aud.rows, desc="wake timeout audit row")
        assert aud.rows[0]["ok"] is False and aud.rows[0]["sid"] == ""
        assert q.empty()
    finally:
        DS.NOTIFIER.unregister(q)


def test_facade_re_exports_no_config_knob_flat():
    """dashboard/server.py is a FACADE, and a name belongs there only while
    something actually reaches it through `dashboard.server` — a third of the
    original list was reached by nobody, which reads as a supported API for
    internals that had merely moved.

    A CONFIG KNOB is the case worth PINNING, because its flat alias is worse than
    dead surface: it is a patch trap. Every reader of a live knob reads `config.X`
    module-qualified (the styleguide's rule, which is what lets a test move it),
    so `monkeypatch.setattr(DS, "NOTIFY_DELAY_S", 0)` would bind a name nobody
    consults — a green test that changed nothing. There is exactly one handle:
    `DS.config`."""
    cfg = open(os.path.join(REPO, "dashboard", "config.py"), encoding="utf-8").read()
    owned = set(re.findall(r"^(\w+)\s*=", cfg, re.M)) | set(re.findall(r"^def (\w+)", cfg, re.M))
    assert "NOTIFY_DELAY_S" in owned and "UPLOAD_MAX" in owned    # the scan works
    leaked = sorted(n for n in owned - {"config"} if hasattr(DS, n))
    assert not leaked, "config knobs re-exported flat (a patch trap): %s" % leaked


def test_extra_origins_parse():
    assert DS.config.extra_origins("https://dash.zhambyl.top, https://a.b ,,") == \
        {"https://dash.zhambyl.top", "https://a.b"}
    assert DS.config.extra_origins(None) == set()
    assert DS.config.extra_origins("") == set()


def test_proxied_origin_allowed(dash, monkeypatch, tmp_path):
    # a CLAUDE_DASH_ORIGINS origin passes the guard (proxied deployment —
    # docs/remote.md); anything else stays 403 (covered by the guard test)
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    ext = "https://dash.zhambyl.top"
    monkeypatch.setattr(DS.config, "ALLOWED_ORIGINS", DS.config.ALLOWED_ORIGINS | {ext})
    code, body = _post(dash + "/api/sessions/new",
                       {"cwd": str(tmp_path)}, origin=ext)
    assert code == 200 and json.loads(body) == {"ok": True, "win": ""}


def test_readonly_kills_control_plane(dash, monkeypatch, tmp_path):
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setattr(DS.config, "READONLY", True)
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/sessions/new", {"cwd": str(tmp_path)})
    assert e.value.code == 403
    assert fe.launched == []
    assert _get(dash + "/api/sessions")[0] == 200      # reads unaffected


def test_post_new_session_model_effort(dash, monkeypatch, tmp_path):
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    # flags ride as "$@" words AHEAD of the prompt
    _post(dash + "/api/sessions/new",
          {"cwd": str(tmp_path), "model": "opus", "effort": "high",
           "prompt": "go"})
    assert fe.launched[-1][1][4:] == ["--model", "opus",
                                     "--effort", "high", "go"]
    # either alone
    _post(dash + "/api/sessions/new", {"cwd": str(tmp_path), "effort": "low"})
    assert fe.launched[-1][1][4:] == ["--effort", "low"]
    _post(dash + "/api/sessions/new",
          {"cwd": str(tmp_path), "model": "claude-fable-5"})
    assert fe.launched[-1][1][4:] == ["--model", "claude-fable-5"]
    # invalid values are 400, never launched
    n = len(fe.launched)
    for bad in ({"effort": "turbo"}, {"model": "opus high"},
                {"model": "a b; c"}, {"model": 7}):
        with pytest.raises(urllib.error.HTTPError) as e:
            _post(dash + "/api/sessions/new", dict({"cwd": str(tmp_path)}, **bad))
        assert e.value.code == 400
    assert len(fe.launched) == n


def test_post_new_session_codex_launch(dash, monkeypatch, tmp_path):
    """A fresh codex launch routes through the CODEX host: the fixed command word
    is `codex` (not an account alias), the cwd rides as `-C`, the model as `-m`,
    and the effort as `-c model_reasoning_effort=` (codex has no --effort flag),
    with the prompt the trailing positional. The web-launch row names the tool."""
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    code, _ = _post(dash + "/api/sessions/new",
                    {"cwd": str(tmp_path), "tool": "codex",
                     "model": "gpt-5.1-codex", "effort": "high",
                     "prompt": "do it"})
    assert code == 200
    cwd, argv = fe.launched[-1]
    assert cwd == str(tmp_path)
    _sh, flags, script, dollar0 = argv[:4]
    assert flags == "-lic" and script == 'codex "$@"' and dollar0 == "codex"
    assert argv[4:] == ["-C", str(tmp_path), "-m", "gpt-5.1-codex",
                        "-c", "model_reasoning_effort=high", "do it"]
    assert (_last_state_file("", "web-launch") or {}).get("tool") == "codex"


def test_post_new_session_codex_resume_is_owner_routed(dash, monkeypatch, tmp_path):
    """A RESUME is routed by the OWNING host, not the request's tool: a parked
    codex session (plugins.owns_by → codex) comes back with `codex resume <sid>`
    even though the body's tool defaults to claude — and the mismatched claude
    model/effort are DROPPED (a codex resume must never get a claude --model)."""
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    # PARKED: with KITTY_WINDOW_ID in the env (this runs inside kitty),
    # A.session_start would stamp a live window id and the resume guard would
    # refuse it as "session already live" — a resume launches only against a
    # parked session.
    monkeypatch.delenv("KITTY_WINDOW_ID", raising=False)
    # a GENUINE codex rollout layout (rollout-*.jsonl under a `sessions/` tree),
    # so the server process's REAL plugins.owns_by resolves it to codex — a
    # test-process monkeypatch can't reach the separate dash server.
    d = tmp_path / "sessions" / "2026" / "07" / "29"
    d.mkdir(parents=True, exist_ok=True)
    tpath = d / "rollout-1-abc.jsonl"
    tpath.write_text("{}\n")                     # the missing-file 410 guard
    A.session_start({"session_id": "cdx-1", "cwd": str(tmp_path),
                     "transcript_path": str(tpath)})
    code, _ = _post(dash + "/api/sessions/new",
                    {"cwd": str(tmp_path), "resume": "cdx-1",
                     "model": "opus", "effort": "high", "prompt": "carry on"})
    assert code == 200
    _cwd, argv = fe.launched[-1]
    assert argv[1:4] == ["-lic", 'codex "$@"', "codex"]
    # resume subcommand + id FIRST; claude model/effort dropped (tool mismatch)
    assert argv[4:] == ["resume", "cdx-1", "-C", str(tmp_path), "carry on"]
    assert (_last_state_file("", "web-launch") or {}).get("tool") == "codex"


# --- P2: every gesture is the owning HOST's, and a host it can't drive 409s ----
#
# The sessions below are GENUINE codex rollouts (rollout-*.jsonl under a
# `sessions/` tree), so the dash server's OWN plugins.owns_by resolves them to
# codex — a test-process monkeypatch cannot reach it.

def _codex_session(tmp_path, sid, win=None):
    """A codex-OWNED session row (its transcript is a real rollout layout), so
    the server routes its gestures to CodexHost."""
    d = tmp_path / "sessions" / "2026" / "07" / "31"
    d.mkdir(parents=True, exist_ok=True)
    tpath = d / ("rollout-%s.jsonl" % sid)
    tpath.write_text("{}\n")
    A.session_start({"session_id": sid, "cwd": str(tmp_path),
                     "transcript_path": str(tpath)})
    return str(tpath)


def test_autoname_on_a_host_without_one_is_409_not_a_foreign_command(
        dash, monkeypatch, tmp_path):
    """`{"cmd": "rename"}` is the ✦ AUTO-rename — bare `/rename`, "generate the
    title yourself". codex has no such command (its `/rename` takes a name), and
    the endpoint used to have neither a cap row nor a host branch for it, so
    Claude Code's argless `/rename` was bracket-pasted into a codex composer (the
    P2 bug list, item 3).

    Now `rename` is in CAP_BY_CMD (codex CAN rename, so the cap passes) and the
    refusal comes one level down, from the `autoname` gesture codex declines —
    which is the point of a cap SHARER: "can rename" and "can invent a name" are
    different answers, and only the host knows the second one."""
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "77")
    _codex_session(tmp_path, "cdxauto")
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/cdxauto/command", {"cmd": "rename"})
    assert e.value.code == 409
    assert json.loads(e.value.read())["cap"] == "rename"
    assert fe.pasted == [] and fe.sent == []      # nothing typed at the terminal
    row = _last_state_file("cdxauto", "web-command")
    assert row["ok"] is False and row["cap"] == "rename"


def test_codex_send_pastes_without_the_claude_paste_machinery(
        dash, monkeypatch, tmp_path):
    """A codex composer send is the HOST's `send` gesture: a plain bracketed
    paste. No clipboard-image wipe (codex's TUI does not auto-attach the board —
    `paste_grabs_clipboard_image` is False, and the osascript round-trip ran on
    every message for nothing), and no Ctrl+U/Ctrl+K line kill even when the body
    asks for one (codex's composer is a different input model; `clear_input` is
    inert rather than guessed). The row records both as the honest zero."""
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "78")
    _codex_session(tmp_path, "cdxsend")
    wiped = []
    monkeypatch.setattr(CH.clipimg, "clear_image",
                        lambda: wiped.append(1) or True)
    code, body = _post(dash + "/api/session/cdxsend/message",
                       {"text": "hello codex", "clear_draft": True})
    assert code == 200 and json.loads(body)["ok"] is True
    assert fe.pasted == [("78", "hello codex")]
    assert wiped == []                            # NO clipboard round-trip
    assert fe.keyed == []                         # NO line-kill keystrokes
    row = _last_state_file("cdxsend", "web-send")
    assert row["host"] == "codex" and row["clip"] is False
    assert row["draft_lines"] == 0 and row["chars"] == len("hello codex")


def test_a_claude_send_still_wipes_the_clipboard_and_kills_the_draft(
        dash, monkeypatch):
    """The same endpoint against the DEFAULT host keeps every step of the Claude
    delivery — the guard, the per-line kill, the bracketed paste, the row's own
    fields. The gesture moved; the behaviour did not."""
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "79")
    A.session_start({"session_id": "clsend", "cwd": "/w", "transcript_path": ""})
    wiped = []
    monkeypatch.setattr(CH.clipimg, "clear_image",
                        lambda: wiped.append(1) or True)
    monkeypatch.setattr(CTUI, "CLEAR_GAP_S", 0)
    code, _ = _post(dash + "/api/session/clsend/message",
                    {"text": "hello claude", "clear_draft": True})
    assert code == 200
    assert wiped == [1]
    assert fe.keyed == [("79", ("ctrl+u",)), ("79", ("ctrl+k",))]
    assert fe.pasted == [("79", "hello claude")]
    row = _last_state_file("clsend", "web-send")
    assert row["clip"] is True and row["draft_lines"] == 1
    assert "host" not in row                     # the default host's own row


def test_an_attachment_rides_the_hosts_own_mention_grammar(tmp_path):
    """`@path` is Claude Code's TUI mention, not a universal one. The composer
    asks the OWNING host for it, and a host with no mention grammar gets the BARE
    PATH — a file the model can still open, where a foreign sigil would arrive as
    literal text (the P2 bug list, item 5)."""
    import plugins
    from dashboard.http.post.files import _FilesMixin

    claude = plugins.host_named(plugins.default_host())
    codex = plugins.host_named("codex")
    with_att = _FilesMixin._with_attachments
    assert with_att(None, "hi", ["/u/a.png"], claude) == "@/u/a.png\nhi"
    assert with_att(None, "hi", ["/u/a.png"], codex) == "/u/a.png\nhi"
    assert with_att(None, "", ["/u/a.png", "/u/b.png"], codex) \
        == "/u/a.png /u/b.png"
    assert with_att(None, "hi", [], codex) == "hi"


def test_a_plan_decision_names_the_hosts_own_vocabulary(dash, monkeypatch,
                                                        tmp_path):
    """The plan card's `feedback` box is Claude Code's "Tell Claude what to
    change" row. codex's picker has no such row, so the request is refused with a
    409 that NAMES what codex does accept — where the old codex branch answered a
    generic 400 "no action" and the typed feedback simply vanished."""
    import plugins
    codex = plugins.host_named("codex")
    claude = plugins.host_named(plugins.default_host())
    assert claude.plan_decisions() == ("decide", "feedback", "dismiss")
    assert codex.plan_decisions() == ("decide", "dismiss")
    # the message the handler builds from each vocabulary (its one owner)
    from dashboard.http.post.dialogs import _vocab, _vocab_help
    assert _vocab_help(claude.plan_decisions()) \
        == "digit+label, feedback, or dismiss"
    assert _vocab_help(codex.plan_decisions()) == "digit+label or dismiss"
    # …while `chat` IS in both ask vocabularies: Claude Code has a decline ROW,
    # codex spells the same word as a submit that leaves the questions
    # unanswered (plugins/codex/dialog.decline).
    assert _vocab(claude.ask_declines()) == "chat"
    assert _vocab(codex.ask_declines()) == "chat"


def test_post_new_session_bad_tool_is_400(dash, monkeypatch, tmp_path):
    """An unknown/non-launchable tool is a clean 400 (validated against the
    launchable hosts registry), never an argv composed for a host that isn't
    there."""
    _inject_fe(monkeypatch, _FakeFE())
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/sessions/new", {"cwd": str(tmp_path), "tool": "nope"})
    assert e.value.code == 400


def test_hosts_endpoint_lists_launchable_hosts(dash):
    """GET /api/hosts feeds the new-session tool picker — claude_code + codex,
    each with a launchable flag."""
    hosts = _get_json(dash + "/api/hosts")
    names = {h["name"] for h in hosts}
    assert {"claude_code", "codex"} <= names
    assert all(isinstance(h["launchable"], bool) for h in hosts)


def test_usage_strip_is_one_payload_over_every_host(dash, monkeypatch):
    """GET /api/accounts serves the WHOLE usage strip — every host's rows in one
    array, in the one usage-window vocabulary — so a single painter renders it.

    codex used to have an endpoint of its own (/api/codex-usage), which is
    deleted: a host-NAMED route over a first-plugin-wins fan-out could only ever
    describe one host, and the second one had to be a second everything (route,
    DOM node, poll, painter, CSS). Here it is one more row, told apart by
    `switchable` — which is also what keeps it OUT of the new-session account
    picker, since it is not an account you can launch under."""
    from plugins import codex as CX
    monkeypatch.setattr(CX, "usage_strip", lambda cache=None, limit=50: [
        {"host": "codex", "switchable": False, "slug": "", "plan": "pro",
         "label": "codex · pro", "ts": None,
         "usage": None, "limit_hit": None, "logged_out": False,
         "windows": [{"key": "w300", "label": "5h", "used_pct": 42,
                      "resets_at": 0, "window_mins": 300, "scope": "account"}]}])
    rows = _get_json(dash + "/api/accounts")
    by_host = {}
    for r in rows:
        by_host.setdefault(r["host"], []).append(r)
    cx = by_host["codex"]
    assert len(cx) == 1 and cx[0]["label"] == "codex · pro"
    assert cx[0]["windows"][0]["used_pct"] == 42
    assert cx[0]["switchable"] is False
    # …and the CLAUDE rows are still there, still switchable, still carrying the
    # picker's fields — the strip merge changed no account's wire shape
    for r in by_host.get("claude_code", []):
        assert r["switchable"] is True
        assert {"slug", "usage", "five_hour_eff", "sched_score", "sched_ok",
                "limit_hit", "logged_out", "windows"} <= set(r)
    # a host with nothing to say contributes nothing (no empty pill)
    monkeypatch.setattr(CX, "usage_strip", lambda cache=None, limit=50: [])
    assert all(r["host"] != "codex" for r in _get_json(dash + "/api/accounts"))


def _stop_rows(sid):
    """The `web-stop` state_files rows (the close attempt/done pair), ts-ordered.
    Same spool-drain dance as _hint_rows."""
    import sqlite3
    A._CONN = None
    A._FAILED = False
    A._connect()
    con = sqlite3.connect(A.db_path())
    try:
        return [json.loads(c) for (c,) in con.execute(
            "SELECT content FROM state_files WHERE session_id=? "
            "AND action='web-stop' ORDER BY ts", (sid,))]
    finally:
        con.close()


def test_post_stop_closes_tab(dash, monkeypatch):
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "55")
    A.session_start({"session_id": "stop1", "cwd": "/w", "transcript_path": ""})
    code, body = _post(dash + "/api/session/stop1/stop", {})
    assert code == 200 and json.loads(body) == {"ok": True}
    assert fe.closed == ["55"]
    # the close is audited as an attempt BEFORE close_tab then a done outcome —
    # so a close_tab that hangs leaves a lone `attempt` (the stuck-close signal)
    rows = _stop_rows("stop1")
    assert [r.get("phase") for r in rows] == ["attempt", "done"]
    assert rows[0]["win"] == "55" and rows[1] == {"win": "55", "phase": "done",
                                                  "ok": True}


def test_post_stop_failed_close_still_audits_attempt(dash, monkeypatch):
    # A close that FAILS (close_tab → False) must still leave the attempt row
    # plus a done(ok:false): the gap this closes is a close that vanishes from
    # the audit, not just one that succeeds.
    fe = _FakeFE()
    monkeypatch.setattr(fe, "close_tab", lambda win: False)
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "60")
    A.session_start({"session_id": "stopf", "cwd": "/w", "transcript_path": ""})
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/stopf/stop", {})
    assert e.value.code == 502
    rows = _stop_rows("stopf")
    assert [r.get("phase") for r in rows] == ["attempt", "done"]
    assert rows[1]["ok"] is False


def test_post_stop_refuses_stale_window(dash, monkeypatch):
    # the bug: a session's recorded window id goes stale (kitty reuses ids), so
    # the pane is no longer tagged with this sid. Stop must resolve the LIVE
    # tag (window_for_session), find none, and refuse — never close the tab
    # that inherited the stale id.
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "11")
    A.session_start({"session_id": "stale1", "cwd": "/w", "transcript_path": ""})
    fe.wins["stale1"] = None                  # the claude_session tag is gone
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/stale1/stop", {})
    assert e.value.code == 409
    assert fe.closed == []                     # nothing closed — the fix
    # message is refused the same way (typing into a reused id is just as bad)
    monkeypatch.setenv("KITTY_WINDOW_ID", "5")
    A.session_start({"session_id": "stale2", "cwd": "/w", "transcript_path": ""})
    fe.wins["stale2"] = None
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/stale2/message", {"text": "hi"})
    assert e.value.code == 409
    assert fe.sent == [] and fe.pasted == []


def test_closed_tab_not_marked_live(dash, monkeypatch):
    # a session whose state DB lingers but whose tab is gone must NOT show live
    monkeypatch.setenv("KITTY_WINDOW_ID", "11")
    A.session_start({"session_id": "ghost", "cwd": "/w", "transcript_path": ""})
    # backdate past the just-started grace (within_live_grace) so the missing-
    # window demotion applies — this test is the LEAKED/CRASHED lingering
    # session, not a brand-new launch (that case is covered separately below)
    A._connect().execute("UPDATE sessions SET started_at=? WHERE session_id=?",
                         (time.time() - 3600, "ghost"))
    A._connect().commit()
    log = P.mirror_log("ghost")
    O.emit(log, O.label("x", (1, 2, 3)))       # create the state DB (state-DB live)
    # window enumeration returns a map WITHOUT this sid → tab is closed
    monkeypatch.setattr(DS.launch, "live_windows", lambda: {"other": "99"})
    row = next(r for r in _get_json(dash + "/api/sessions") if r["sid"] == "ghost")
    assert row["live"] is False                # demoted — the requirement
    ov = _get_json(dash + "/api/session/ghost")
    assert ov["live"] is False and ov["kitty_window_id"] == ""
    # when the tab IS open (sid in the map) it stays live and controllable
    monkeypatch.setattr(DS.launch, "live_windows", lambda: {"ghost": "11"})
    row = next(r for r in _get_json(dash + "/api/sessions") if r["sid"] == "ghost")
    assert row["live"] is True
    # the LIST serves the SAME live-RESOLVED window id as /api/session (not the
    # raw audit id): the two endpoints must agree or the client's
    # updateHeadFromList thrashes the header (the action-row flicker fix)
    assert row["kitty_window_id"] == "11"
    ov = _get_json(dash + "/api/session/ghost")
    assert ov["live"] is True and ov["kitty_window_id"] == "11"
    assert row["kitty_window_id"] == ov["kitty_window_id"]


def test_fresh_session_within_grace_stays_live(dash, monkeypatch):
    # a JUST-started session whose pane isn't tagged claude_session=<sid> yet
    # (the startup tag-race: A.session_start writes the sessions row before
    # split.tag_window runs, and live_windows is memoized on top) must NOT be
    # demoted to not-live during _LIVE_GRACE_S — else the card flashes "parked"
    # and the detail header (meta fetched once) froze on it, uncloseable.
    monkeypatch.setenv("KITTY_WINDOW_ID", "12")
    A.session_start({"session_id": "fresh", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("fresh")
    O.emit(log, O.label("x", (1, 2, 3)))       # create the state DB (state-DB live)
    # window map WITHOUT this sid — the pane hasn't been tagged yet — but the
    # session started just now, so the grace keeps it live and controllable
    monkeypatch.setattr(DS.launch, "live_windows", lambda: {"other": "99"})
    row = next(r for r in _get_json(dash + "/api/sessions") if r["sid"] == "fresh")
    assert row["live"] is True                 # inside the grace — not demoted
    # mid-tag-race the list's window id is "" (live-resolved, pane not tagged yet)
    # — the SAME blank /api/session serves, NOT the raw start-time id from the env
    # (KITTY_WINDOW_ID=12). Serving the raw id here is what fought loadMeta and
    # flickered the action row every list tick (fixed 2026-07-24).
    assert row["kitty_window_id"] == ""
    ov = _get_json(dash + "/api/session/fresh")
    # live (no parked flash), but the control plane's window resolves from the
    # live tag map — still "" until the pane is actually tagged (kitty_window_id
    # fills in the moment tag_window lands; the client re-renders on that flip)
    assert ov["live"] is True and ov["kitty_window_id"] == ""
    # once the grace has elapsed, the same missing-window state DOES demote
    A._connect().execute("UPDATE sessions SET started_at=? WHERE session_id=?",
                         (time.time() - 3600, "fresh"))
    A._connect().commit()
    row = next(r for r in _get_json(dash + "/api/sessions") if r["sid"] == "fresh")
    assert row["live"] is False                # past the grace — demoted


def test_post_interrupt_sends_escape(dash, monkeypatch):
    # interrupt = an Escape key EVENT into the session's window (send_key,
    # never send_text bytes) — the turn stops, the session stays up
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "66")
    A.session_start({"session_id": "intr1", "cwd": "/w", "transcript_path": ""})
    code, body = _post(dash + "/api/session/intr1/interrupt", {})
    # `restored` is "" — nothing in this session's transcript to hand back
    assert code == 200 and json.loads(body) == {"ok": True, "tab": "",
                                                "queued": False, "restored": ""}
    assert fe.keyed == [("66", ("escape",))]
    assert fe.closed == []                    # never touches the tab


def test_post_interrupt_magenta_spawns_escape_recheck(dash, monkeypatch,
                                                      tmp_path):
    # an Esc into a THINKING tab may be the signal-less mid-thinking cancel —
    # the endpoint spawns the escape-recheck with the press-time transcript
    # size as the growth baseline; a non-busy tab spawns nothing
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    spawned = []
    monkeypatch.setattr(DS.SP, "spawn_detached",
                        lambda path, argv, log, env=None, purpose="", **kw:
                        spawned.append((path, argv, env, purpose)) or None)
    tp = tmp_path / "intr3.jsonl"
    tp.write_text('{"type":"user"}\n')
    monkeypatch.setenv("KITTY_WINDOW_ID", "77")
    A.session_start({"session_id": "intr3", "cwd": "/w",
                     "transcript_path": str(tp)})
    monkeypatch.setattr(DS.API, "tab_states", lambda: {"77": "thinking"})
    code, body = _post(dash + "/api/session/intr3/interrupt", {})
    assert code == 200 and json.loads(body) == {"ok": True, "tab": "thinking",
                                                "queued": False, "restored": ""}
    assert len(spawned) == 1
    path, argv, env, purpose = spawned[0]
    assert path.endswith("claude-tab-status.py")
    assert argv[:2] == ["escape-recheck", DS.P.mirror_log("intr3")]
    assert argv[2] == str(tp)
    assert argv[3] == str(tp.stat().st_size)      # press-time baseline
    assert env["KITTY_WINDOW_ID"] == "77"
    assert purpose == "watcher:escape-recheck"
    # green tab -> no recheck (nothing to recover)
    monkeypatch.setattr(DS.API, "tab_states",
                        lambda: {"77": "awaiting-response"})
    _post(dash + "/api/session/intr3/interrupt", {})
    assert len(spawned) == 1


def test_post_interrupt_verifies_and_re_presses(dash, monkeypatch):
    # a single Escape does not reliably stop a busy turn (vim editorMode's first
    # Esc only leaves INSERT mode; send-key is ~2/3 reliable), so a BUSY-tab
    # interrupt re-presses WHILE the turn is still LIVE — where "live" = the
    # screen is still CHANGING between two captures (robust across thinking
    # levels; no fragile marker string). Iter 1: two captures differ (still
    # animating -> the Esc missed) -> re-press; iter 2: two identical captures
    # (static -> dead) -> stopped, no further Escapes.
    fe = _FakeFE()
    #             pre        i1a          i1b(changed)   i2a       i2b(same)
    fe.screens = ["✻ Alpha…", "✻ Alpha…", "✻ Bravo…", "❯ idle", "❯ idle"]
    _inject_fe(monkeypatch, fe)
    monkeypatch.setattr(CH, "INTERRUPT_RETRY_S", 0)
    monkeypatch.setenv("KITTY_WINDOW_ID", "78")
    A.session_start({"session_id": "intrv", "cwd": "/w", "transcript_path": ""})
    monkeypatch.setattr(DS.API, "tab_states", lambda: {"78": "thinking"})
    code, body = _post(dash + "/api/session/intrv/interrupt", {})
    assert code == 200 and json.loads(body) == {"ok": True, "tab": "thinking",
                                                "queued": False, "restored": ""}
    assert fe.keyed == [("78", ("escape",)), ("78", ("escape",))]  # one re-press
    row = _last_state_file("intrv", "web-interrupt")
    assert row["attempts"] == 2 and row["stopped"] is True
    assert row["probes"][0]["at"] == "pre-esc"     # ground-truth capture present


def test_post_interrupt_stops_pressing_when_the_queue_is_delivered(dash,
                                                                   monkeypatch,
                                                                   tmp_path):
    # A message QUEUED mid-turn is delivered by Claude Code the instant the
    # Escape lands, so a NEW turn starts and the screen keeps animating —
    # screen-delta alone reads "still live" and re-presses, which interrupts the
    # very message it just delivered and hands it back to the TUI's input box
    # (measured 2026-07-27, session 3266f418: 4 Escapes, the queued prompt gone
    # from the web, re-sent by hand). The queue records outrank the screen: one
    # press, then the `queue-operation`/dequeue in the transcript says the turn
    # boundary happened — stop, and tell the page the turn goes ON (`queued`),
    # so it doesn't claim "your turn".
    tp = tmp_path / "intrq.jsonl"
    tp.write_text('{"type":"user"}\n')

    class _DeliverFE(_FakeFE):
        def send_key(self, win, *keys):
            # the Esc lands: Claude Code drains its queue into a new turn
            with open(tp, "a", encoding="utf-8") as f:
                f.write('{"type":"queue-operation","operation":"dequeue"}\n')
            return _FakeFE.send_key(self, win, *keys)

    fe = _DeliverFE()
    fe.screens = ["✻ Alpha…", "✻ Bravo…", "✻ Delta…", "✻ Echo…", "✻ Golf…"]
    _inject_fe(monkeypatch, fe)
    monkeypatch.setattr(CH, "INTERRUPT_RETRY_S", 0)
    spawned = []
    monkeypatch.setattr(DS.SP, "spawn_detached",
                        lambda path, argv, log, env=None, purpose="", **kw:
                        spawned.append(argv) or None)
    monkeypatch.setenv("KITTY_WINDOW_ID", "80")
    A.session_start({"session_id": "intrq", "cwd": "/w",
                     "transcript_path": str(tp)})
    monkeypatch.setattr(DS.API, "tab_states", lambda: {"80": "thinking"})
    code, body = _post(dash + "/api/session/intrq/interrupt", {})
    assert code == 200
    assert json.loads(body)["queued"] is True     # the page keeps queue mode
    assert fe.keyed == [("80", ("escape",))]      # ONE press — no hammering
    row = _last_state_file("intrq", "web-interrupt")
    assert row["attempts"] == 1 and row["stopped"] is True
    assert row["drained"] == "dequeue"            # WHY it stopped, in the audit
    # the delivered turn is mid-flight and still deserves cancel recovery —
    # the escape-recheck's own queued-prompt guard keeps watching it
    assert len(spawned) == 1 and spawned[0][0] == "escape-recheck"


def test_post_interrupt_not_confirmed_is_502_no_recheck(dash, monkeypatch):
    # the screen NEVER goes static (every capture differs = the turn keeps
    # animating) = the Esc never landed (the stuck-turn bug). Report a 502 and
    # spawn NO escape-recheck — flipping the tab green would MASK a live turn.
    fe = _FakeFE()
    fe.screens = ["scr%d" % i for i in range(9)]   # every capture distinct = live
    _inject_fe(monkeypatch, fe)
    monkeypatch.setattr(CH, "INTERRUPT_RETRY_S", 0)
    spawned = []
    monkeypatch.setattr(DS.SP, "spawn_detached",
                        lambda path, argv, log, env=None, purpose="", **kw:
                        spawned.append(argv) or None)
    monkeypatch.setenv("KITTY_WINDOW_ID", "79")
    A.session_start({"session_id": "intrn", "cwd": "/w", "transcript_path": ""})
    monkeypatch.setattr(DS.API, "tab_states", lambda: {"79": "thinking"})
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/intrn/interrupt", {})
    assert e.value.code == 502
    assert spawned == []                          # no masking escape-recheck
    row = _last_state_file("intrn", "web-interrupt")
    assert row["stopped"] is False and row["attempts"] == CH.INTERRUPT_TRIES + 1


def test_post_rewind_idle_types_the_command(dash, monkeypatch):
    # IDLE double-Esc = the rewind menu: TYPES /rewind (documented identical
    # to double-Esc, and deterministic where synthesized double-press key
    # events were ~2/3 flaky at any gap) — no Escape key events
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "88")
    A.session_start({"session_id": "rew1", "cwd": "/w", "transcript_path": ""})
    code, body = _post(dash + "/api/session/rew1/interrupt", {})
    assert json.loads(body) == {"ok": True, "tab": "", "queued": False,
                                "restored": ""}
    assert fe.keyed == [("88", ("escape",))]          # single press = interrupt
    code, body = _post(dash + "/api/session/rew1/rewind", {})
    assert code == 200
    assert json.loads(body) == {"ok": True, "tab": ""}
    assert fe.pasted == [("88", "/rewind")]     # BRACKETED paste, not keystrokes
    assert fe.sent == []                       # never raw text (vim-mode unsafe)
    assert fe.keyed == [("88", ("escape",))]          # no extra Escapes
    assert fe.closed == []
    # same live-tag discipline as interrupt/stop
    fe.wins["rew1"] = None
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/rew1/rewind", {})
    assert e.value.code == 409
    assert fe.pasted == [("88", "/rewind")]


def test_post_interrupt_reports_the_taken_back_message(dash, monkeypatch):
    # Interrupt a turn early enough and Claude Code DISCARDS the prompt and
    # hands it back to the input box (measured 2026-07-25 — one Escape does
    # this; it was never the ⊘ cancel button's second press). The endpoint
    # notices by READING the box, so the composer can mirror it: the screen
    # says WHETHER, the transcript says WHAT (exact text, newlines intact).
    fe = _FakeFE()
    fe.ansi_screen = ("\x1b[m\x1b[38:2:136:136:136m" + "\u2500" * 100 + "\n"
                      "\x1b[m\u276f\xa0testing the take-back\n"
                      "\x1b[m  with a second line\n"
                      "\x1b[m\x1b[38:2:136:136:136m" + "\u2500" * 100 + "\n")
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "91")
    A.session_start({"session_id": "tb1", "cwd": "/w", "transcript_path": ""})
    S.kv_set(P.mirror_log("tb1"), "seed", 1)   # the state DB must EXIST: the
    #   stash writes through kv_set_at, which never creates one
    monkeypatch.setattr(DS.session, "last_prompt_rec",
                        lambda sid: ("testing the take-back\nwith a second line",
                                     "u-taken"))
    code, body = _post(dash + "/api/session/tb1/interrupt", {})
    assert code == 200
    # the TRANSCRIPT's text, not the box's whitespace-flattened capture
    assert json.loads(body)["restored"] == \
        "testing the take-back\nwith a second line"
    row = _last_state_file("tb1", "web-interrupt")
    assert row["phase"] == "restore" and row["restored"] is True
    # the record is FLAGGED, so the bubble stays gone across a reload — it has
    # no sibling yet, so nothing on disk would say so
    assert row["uid"] == "u-taken" and row["flagged"] is True
    from plugins.claude_code import transcript as TR
    assert TR.taken_back("tb1") == ("u-taken",)


def test_menu_open_survives_the_chord_label_formats():
    """Claude Code composes the menu footer at runtime as `<chord> to
    <action>`, and the chord label has three formats (`Enter`/`enter`/`⏎`).
    Matching the whole phrase in one of them is what broke every web rewind on
    v2.1.220 with `step: "open"` while the menu was open on screen
    (2026-07-25) — only the action half is the product's own literal."""
    RW = RWM

    def screen(foot):
        return "\n".join(["", "  Rewind", "", "  ❯ a prompt", "", "  " + foot])

    for foot in ("Enter to continue · Esc to cancel",
                 "enter to continue · esc to cancel",
                 "\u23ce to continue · \u238b to cancel"):
        assert RW.menu_open(screen(foot)), foot
    # the confirm menu is NOT the first menu, whatever the footer says
    assert not RW.menu_open("\n  Rewind\n\n  Confirm you want to restore to"
                            " the point\n  enter to continue")


# ---------------------------------------------------------------- caps gating

def test_session_payload_serves_full_caps_for_a_claude_session(dash):
    """The multi-tool control gate (P1a): a Claude session (here an EMPTY
    transcript_path — a daemon scrubbed-env session that must NOT fail closed)
    is served host="claude_code" + every capability True, so the client greys
    nothing and the guard fires nowhere (byte-identical control plane)."""
    from plugins.host import GESTURES

    A.session_start({"session_id": "cap-c", "cwd": "/w", "transcript_path": ""})
    data = _get_json(dash + "/api/session/cap-c")
    assert data["host"] == "claude_code"
    assert data["caps"] == {g: True for g in GESTURES}


def test_caps_guard_409s_a_gesture_a_non_claude_host_cant_do(dash, monkeypatch):
    """A session owned by another tool whose host leaves a gesture inert is
    degraded cleanly: the payload advertises that tool's OWN NAME + all-False
    caps, and the gated POST 409s with the cap named plus an ok:False `web-*`
    audit row — the copilot/opencode path proven with a fake owner before either
    tool exists. (P1: `host` carries the real owner where it used to blank to
    "" — the JS reads `caps`, never `host`.)"""
    import plugins
    from plugins.host import GESTURES

    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "88")
    A.session_start({"session_id": "cap-x", "cwd": "/w",
                     "transcript_path": "/w/other.rollout",
                     "kitty_window_id": "88"})
    # resolve the transcript to a NON-claude host with every gesture inert
    monkeypatch.setattr(plugins, "owns_by",
                        lambda p: "faketool" if p else None)
    monkeypatch.setattr(plugins, "host_caps",
                        lambda name: {g: False for g in GESTURES})

    data = _get_json(dash + "/api/session/cap-x")
    assert data["host"] == "faketool"
    assert data["caps"] == {g: False for g in GESTURES}

    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/cap-x/interrupt", {})
    assert e.value.code == 409
    assert json.loads(e.value.read())["cap"] == "interrupt"
    row = _last_state_file("cap-x", "web-interrupt")
    assert row and row["ok"] is False and row["cap"] == "interrupt"
