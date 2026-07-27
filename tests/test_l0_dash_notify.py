# tests/test_l0_dash_notify.py — L0 dashboard: the notification watcher, presence and the deferred alerts.
#
# One subject out of the former 8468-line L0 dashboard monolith; the
# shared HTTP/audit helpers live in tests/dashkit.py and the in-process
# server fixture (`dash`) in tests/conftest.py.
import json
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from conftest import REPO, wait_until

if REPO not in sys.path:
    sys.path.insert(0, REPO)

import core.audit as A
from core import paths as P
from dashboard import opshtml
from dashboard import server as DS


# ------------------------------------------------------------------ opshtml
from dashkit import (_FakeFE, _get, _get_json, _inject_fe, _last_state_file, _post, _tw)


def test_notifier_transitions(monkeypatch):
    n = DS.Notifier()
    n.winmap = {"7": {"sid": "s7", "cwd": "/w/proj",
                      "transcript_path": "/w/t.jsonl"}}
    monkeypatch.setattr(DS.notifier, "session_title",
                        lambda p: "fix the flaky test" if p else "")
    q = n.register()
    seq = [{"7": "working"}, {"7": "working"}, {"7": "awaiting-command"},
           {"7": "awaiting-command"}, {"7": "awaiting-response"}]
    monkeypatch.setattr(DS.API, "tab_states", lambda: seq.pop(0))
    n.scan()                                  # baseline — never news
    n.scan()                                  # unchanged — nothing
    n.scan()                                  # -> asking
    n.scan()                                  # unchanged again — nothing
    n.scan()                                  # -> done
    got = []
    while not q.empty():
        got.append(q.get_nowait())
    assert [(ev, p["kind"]) for ev, p in got] == \
        [("notify", "asking"), ("notify", "done")]
    assert got[0][1]["sid"] == "s7" and got[0][1]["project"] == "proj"
    assert got[0][1]["title"] == "fix the flaky test"
    n.unregister(q)


def test_notifier_refires_after_empty_tab_table(monkeypatch):
    # When the tab table momentarily EMPTIES (all sessions closed), self.prev
    # becomes {}. Treating an empty prev as a fresh baseline (the old `not prev`)
    # swallowed the very next transition into red/green. Only the true first
    # scan (prev is None) is a baseline; an empty {} is a real state.
    n = DS.Notifier()
    n.winmap = {"7": {"sid": "s7", "cwd": "/w/proj",
                      "transcript_path": "/w/t.jsonl"}}
    monkeypatch.setattr(DS.notifier, "session_title", lambda p: "t" if p else "")
    q = n.register()
    seq = [{"7": "working"}, {}, {"7": "awaiting-command"}]
    monkeypatch.setattr(DS.API, "tab_states", lambda: seq.pop(0))
    n.scan()                                  # baseline (prev is None)
    n.scan()                                  # table empties -> prev == {}
    n.scan()                                  # -> asking: MUST still fire
    got = []
    while not q.empty():
        got.append(q.get_nowait())
    assert [(ev, p["kind"]) for ev, p in got] == [("notify", "asking")]
    n.unregister(q)


def test_notifier_telegram_deferred_arm_cancel_fire(monkeypatch, tmp_path):
    """The deferred off-device (Telegram) alert (docs/dashboard.md *Telegram
    alerts*): a red/green transition ARMS a pending entry; it only FIRES if the
    tab is still in that state past NOTIFY_DELAY_S (you didn't react), and it is
    CANCELLED the moment the tab leaves that state before then. Driven with a
    controllable monotonic clock so the timing is deterministic, not slept."""
    monkeypatch.setattr(P, "DASH_PREFS_DB", str(tmp_path / "prefs.db"))
    monkeypatch.setattr(DS.config, "NOTIFY_DELAY_S", 30.0)
    monkeypatch.setattr(DS.config, "NOTIFY_TELEGRAM", True)
    monkeypatch.setattr(DS.notifier, "session_title", lambda p: "t" if p else "")
    clock = [0.0]
    monkeypatch.setattr(DS.time, "monotonic", lambda: clock[0])
    sent = []
    n = DS.Notifier()
    monkeypatch.setattr(n, "_telegram", lambda entry, *a: sent.append(entry))
    n.winmap = {
        "7": {"sid": "s7", "cwd": "/w/proj", "transcript_path": "/w/t.jsonl"},
        "8": {"sid": "s8", "cwd": "/w/proj2", "transcript_path": "/w/t2.jsonl"}}
    states = {"7": "working", "8": "working"}
    monkeypatch.setattr(DS.API, "tab_states", lambda: dict(states))
    n.scan()                                   # baseline — never news
    states["7"], states["8"] = "awaiting-command", "awaiting-response"
    n.scan()                                   # both transition -> both armed
    assert sent == [] and set(n.pending) == {"7", "8"}
    clock[0] = 10.0                            # win8 reacts before the delay
    states["8"] = "working"
    n.scan()
    assert "8" not in n.pending and sent == []
    clock[0] = 40.0                            # win7 still red past the delay
    n.scan()
    assert [e["sid"] for e in sent] == ["s7"] and sent[0]["kind"] == "asking"
    assert "7" not in n.pending                # popped — fires exactly once
    n.scan()
    assert [e["sid"] for e in sent] == ["s7"]


def test_notifier_telegram_dropped_when_session_closed(monkeypatch, tmp_path):
    """Closing a session (you were satisfied and moved on) must cancel its
    pending alert even if the tab row lingers red/green: the audit `ended_at`
    is the signal, dropped in the cancel pass so nothing fires past the delay."""
    monkeypatch.setattr(P, "DASH_PREFS_DB", str(tmp_path / "prefs.db"))
    monkeypatch.setattr(DS.config, "NOTIFY_DELAY_S", 30.0)
    monkeypatch.setattr(DS.config, "NOTIFY_TELEGRAM", True)
    monkeypatch.setattr(DS.notifier, "session_title", lambda p: "t")
    clock = [0.0]
    monkeypatch.setattr(DS.time, "monotonic", lambda: clock[0])
    sent = []
    n = DS.Notifier()
    monkeypatch.setattr(n, "_telegram", lambda entry, *a: sent.append(entry))
    n.winmap = {"9": {"sid": "s9", "cwd": "/w/p", "transcript_path": "/w/t.jsonl"}}
    A.session_start({"session_id": "s9", "cwd": "/w/p", "transcript_path": ""})
    states = {"9": "working"}
    monkeypatch.setattr(DS.API, "tab_states", lambda: dict(states))
    n.scan()                                   # baseline
    states["9"] = "awaiting-response"
    n.scan()                                   # -> done, armed
    assert set(n.pending) == {"9"}
    # the user closes the session on the dashboard; the tab row lingers green
    A.session_end({"session_id": "s9"})
    clock[0] = 5.0
    n.scan()                                   # ended -> dropped before the delay
    assert "9" not in n.pending
    clock[0] = 40.0
    n.scan()
    assert sent == []                          # never fired — session was closed


def test_notifier_telegram_suppressed_while_composing(monkeypatch, tmp_path):
    """An unsent web composer draft = you're working on a reply, so the pending
    alert is cancelled (don't nag about a session you're already handling).
    Clearing the draft after that does NOT resurrect the popped alert."""
    monkeypatch.setattr(P, "DASH_PREFS_DB", str(tmp_path / "prefs.db"))
    monkeypatch.setattr(DS.config, "NOTIFY_DELAY_S", 30.0)
    monkeypatch.setattr(DS.config, "NOTIFY_TELEGRAM", True)
    monkeypatch.setattr(DS.notifier, "session_title", lambda p: "t")
    clock = [0.0]
    monkeypatch.setattr(DS.time, "monotonic", lambda: clock[0])
    draft = {"s7": {"text": "half-written reply"}}   # sid -> draft (or absent)
    monkeypatch.setattr(DS.presence, "composer_draft", lambda sid: draft.get(sid))
    sent = []
    n = DS.Notifier()
    monkeypatch.setattr(n, "_telegram", lambda entry, *a: sent.append(entry))
    n.winmap = {"7": {"sid": "s7", "cwd": "/w/p", "transcript_path": "/w/t.jsonl"}}
    states = {"7": "working"}
    monkeypatch.setattr(DS.API, "tab_states", lambda: dict(states))
    n.scan()                                   # baseline
    states["7"] = "awaiting-response"
    n.scan()                                   # -> done, armed
    clock[0] = 5.0
    n.scan()                                   # composing -> dropped
    assert "7" not in n.pending
    draft.clear()                              # cleared the draft (still didn't send)
    clock[0] = 40.0
    n.scan()
    assert sent == []                          # stays quiet — the entry was popped


def test_notifier_telegram_muted_and_disabled(monkeypatch, tmp_path):
    """A muted session (the ◉/○ opt-out) never fires even when it sits red past
    the delay — the mute is checked at SEND time. And CLAUDE_DASH_NOTIFY_TELEGRAM
    off (DS.config.NOTIFY_TELEGRAM False) arms nothing at all."""
    monkeypatch.setattr(P, "DASH_PREFS_DB", str(tmp_path / "prefs.db"))
    monkeypatch.setattr(DS.config, "NOTIFY_DELAY_S", 0.0)  # fire on the next scan
    monkeypatch.setattr(DS.notifier, "session_title", lambda p: "t")
    clock = [0.0]
    monkeypatch.setattr(DS.time, "monotonic", lambda: clock[0])
    sent = []
    n = DS.Notifier()
    monkeypatch.setattr(n, "_telegram", lambda entry, *a: sent.append(entry))
    n.winmap = {"7": {"sid": "s7", "cwd": "/w/p", "transcript_path": "/w/t.jsonl"}}
    states = {"7": "working"}
    monkeypatch.setattr(DS.API, "tab_states", lambda: dict(states))

    # muted -> armed but never sent
    monkeypatch.setattr(DS.config, "NOTIFY_TELEGRAM", True)
    DS.prefs.set_notify_muted("s7", True)
    n.scan()                                   # baseline
    states["7"] = "awaiting-command"
    n.scan()                                   # arm + immediately past delay
    assert sent == []                          # suppressed by the mute
    DS.prefs.set_notify_muted("s7", False)     # un-mute -> next fire lands

    # master switch off -> nothing even arms
    n2 = DS.Notifier()
    monkeypatch.setattr(n2, "_telegram", lambda entry, *a: sent.append(entry))
    n2.winmap = n.winmap
    monkeypatch.setattr(DS.config, "NOTIFY_TELEGRAM", False)
    states["7"] = "working"
    n2.scan()
    states["7"] = "awaiting-command"
    n2.scan()
    assert sent == [] and n2.pending == {}


def test_telegram_send_invokes_notify_cmd(monkeypatch, tmp_path):
    """_telegram Popens the reused notify script (CLAUDE_DASH_NOTIFY_CMD) with a
    single message argv carrying the project + deep link — the reuse of the
    global `notify` skill. A recorder script stands in for notify.py."""
    rec = tmp_path / "rec.txt"
    script = tmp_path / "recorder.py"
    script.write_text(
        "import sys, pathlib\n"
        "pathlib.Path(%r).write_text(sys.argv[1] if len(sys.argv) > 1 else '')\n"
        % str(rec))
    monkeypatch.setattr(DS.config, "NOTIFY_CMD", str(script))
    # the deep link points at the PUBLIC proxied origin, not the 127.0.0.1 bind
    monkeypatch.setattr(DS.config, "NOTIFY_URL_BASE", "https://dash.example")
    n = DS.Notifier()
    n._telegram({"kind": "done", "sid": "s9", "project": "proj", "title": "all green"})
    wait_until(rec.exists, desc="recorder ran")
    msg = rec.read_text()
    assert "proj is done" in msg and "all green" in msg
    # ?s=<sid> query param, NOT a #fragment (Telegram drops the fragment)
    assert "https://dash.example/?s=s9" in msg and "#" not in msg


def test_notify_mute_endpoint_roundtrip_and_validation(dash):
    """POST /api/session/<sid>/notify flips the per-session Telegram opt-out in
    the durable global prefs store and surfaces it in the session meta
    (`notify_muted`), live or parked; a non-bool `muted` is refused (400)."""
    A.session_start({"session_id": "nm1", "cwd": "/w", "transcript_path": ""})
    assert _get_json(dash + "/api/session/nm1")["notify_muted"] is False
    code, body = _post(dash + "/api/session/nm1/notify", {"muted": True})
    d = json.loads(body)
    assert code == 200 and d["ok"] is True and d["muted"] is True
    assert _get_json(dash + "/api/session/nm1")["notify_muted"] is True
    assert DS.prefs.notify_muted("nm1") is True
    code, body = _post(dash + "/api/session/nm1/notify", {"muted": False})
    assert json.loads(body)["muted"] is False
    assert _get_json(dash + "/api/session/nm1")["notify_muted"] is False
    for bad in (1, "yes", None):
        with pytest.raises(urllib.error.HTTPError) as e:
            _post(dash + "/api/session/nm1/notify",
                  {"muted": bad} if bad is not None else {})
        assert e.value.code == 400


def test_notify_mute_behind_post_guard(dash, monkeypatch):
    """The mute POST is a control-plane write — a missing X-Claude-Dash header
    is 403 and READONLY disables it."""
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/nm2/notify", {"muted": True}, header=None)
    assert e.value.code == 403
    monkeypatch.setattr(DS.config, "READONLY", True)
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/nm2/notify", {"muted": True})
    assert e.value.code == 403


def test_viewing_heartbeat_marks_presence(dash):
    """POST /api/session/<sid>/viewing (an empty body) marks the session as
    being watched — `web_viewing` flips true — so the deferred alert can
    suppress. Behind the control-plane guard: a missing header is 403."""
    DS.presence._VIEWING.pop("vh1", None)
    assert DS.web_viewing("vh1") is False
    code, body = _post(dash + "/api/session/vh1/viewing", {})
    assert code == 200 and json.loads(body)["ok"] is True
    assert DS.web_viewing("vh1") is True
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/vh1/viewing", {}, header=None)
    assert e.value.code == 403
    DS.presence._VIEWING.pop("vh1", None)


def test_presence_beat_marks_device_and_viewing(dash):
    """POST /api/presence marks BOTH device presence (for on-device push
    routing) and, when a sid rides along, session viewing (for suppression).
    Behind the control-plane guard: a missing header is 403."""
    DS.presence._DEVICE_SEEN.pop("devQ", None)
    DS.presence._VIEWING.pop("pv1", None)
    code, body = _post(dash + "/api/presence", {"device": "devQ", "sid": "pv1"})
    assert code == 200 and json.loads(body)["ok"] is True
    assert DS.device_seen("devQ") != float("-inf")   # device recorded
    assert DS.web_viewing("pv1") is True             # session viewing recorded
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/presence", {"device": "x"}, header=None)
    assert e.value.code == 403
    DS.presence._DEVICE_SEEN.pop("devQ", None)
    DS.presence._VIEWING.pop("pv1", None)


def test_add_push_subscription_stores_device_and_label(monkeypatch, tmp_path):
    """A subscription is stored WITH its device id + label so the notifier can
    route to the most-recently-used device (webpush.send ignores the extras)."""
    monkeypatch.setattr(P, "DASH_PREFS_DB", str(tmp_path / "prefs.db"))
    sub = {"endpoint": "https://push/dev1", "keys": {"p256dh": "k", "auth": "a"}}
    DS.prefs.add_push_subscription(sub, device="mac-1", label="macOS")
    stored = DS.prefs.push_subscriptions()
    assert len(stored) == 1
    assert stored[0]["device"] == "mac-1" and stored[0]["label"] == "macOS"
    assert stored[0]["endpoint"] == "https://push/dev1"   # wire fields intact


def test_post_message_success(dash, monkeypatch):
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "42")      # session_start reads the env
    A.session_start({"session_id": "msg1", "cwd": "/w", "transcript_path": ""})
    code, body = _post(dash + "/api/session/msg1/message",
                       {"text": "hello claude"})
    # no tab state recorded → not mid-turn → queued False
    assert code == 200 and json.loads(body) == {"ok": True, "queued": False,
                                                "tab": ""}
    # composer sends go through a bracketed paste (atomic — a raw send drops
    # bytes depending on TUI state), never send_text
    assert fe.pasted == [("42", "hello claude")]
    assert fe.sent == []


def test_clear_clipboard_image_only_when_image(monkeypatch):
    """The clipboard-image guard empties the macOS clipboard ONLY when it holds
    an image flavor (so Claude Code can't auto-attach it to a bracketed paste,
    docs/dashboard.md *Clipboard-image guard*) — a text-only clipboard is left
    untouched, and it never runs off macOS."""
    calls = []

    def fake_run(argv, **kw):
        calls.append(argv)
        r = type("R", (), {})()
        r.stdout = fake_run.info if argv[2:3] == ["clipboard info"] else ""
        return r
    monkeypatch.setattr(DS.launch.sys, "platform", "darwin")
    monkeypatch.setattr(DS.launch.subprocess, "run", fake_run)
    # an image on the clipboard → detected and cleared
    fake_run.info = "«class PNGf», 70, «class utf8», 3"
    calls.clear()
    assert DS.clear_clipboard_image() is True
    assert any('set the clipboard to ""' in " ".join(c) for c in calls)
    # a text-only clipboard → left alone (no set-clipboard command issued)
    fake_run.info = "«class utf8», 12"
    calls.clear()
    assert DS.clear_clipboard_image() is False
    assert not any("set the clipboard" in " ".join(c) for c in calls)
    # off macOS → never even probes
    monkeypatch.setattr(DS.launch.sys, "platform", "linux")
    calls.clear()
    assert DS.clear_clipboard_image() is False and calls == []


def test_post_message_runs_clipboard_guard(dash, monkeypatch):
    """A composer send empties an image clipboard BEFORE the bracketed paste (the
    fix for the spurious-screenshot bug) and still delivers the message."""
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "42")
    A.session_start({"session_id": "msgclip", "cwd": "/w", "transcript_path": ""})
    calls = []
    monkeypatch.setattr(DS.launch, "clear_clipboard_image",
                        lambda: calls.append(1) or True)
    code, _ = _post(dash + "/api/session/msgclip/message", {"text": "hi"})
    assert code == 200
    assert calls                                 # the clipboard-image guard ran
    assert fe.pasted == [("42", "hi")]           # …and the message still delivered


def test_post_message_reports_queued_mid_turn(dash, monkeypatch):
    # a send while the tab is busy lands in Claude Code's own message queue —
    # the response says so (`queued`), and the web-send audit row carries the
    # tab state at send time ("my message vanished" → "it queued mid-turn")
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "77")
    A.session_start({"session_id": "msgq", "cwd": "/w", "transcript_path": ""})
    states = {"77": "working"}
    monkeypatch.setattr(DS.API, "tab_states", lambda: dict(states))
    code, body = _post(dash + "/api/session/msgq/message", {"text": "later"})
    assert code == 200
    assert json.loads(body) == {"ok": True, "queued": True, "tab": "working"}
    states["77"] = "awaiting-response"               # turn over: immediate send
    code, body = _post(dash + "/api/session/msgq/message", {"text": "now"})
    assert json.loads(body) == {"ok": True, "queued": False,
                                "tab": "awaiting-response"}
    # awaiting-command (a dialog is up) must NEVER claim queued — typed text
    # goes to the dialog, not the queue
    states["77"] = "awaiting-command"
    code, body = _post(dash + "/api/session/msgq/message", {"text": "hm"})
    assert json.loads(body)["queued"] is False


def test_post_message_queued_is_verified_against_a_live_screen(dash, monkeypatch):
    """A QUEUE_TABS tab colour is NOT enough to promise `queued` — the promise is
    VERIFIED against a live screen (docs/dashboard.md, *Web composer queue*).

    Claude Code fires no hook on cancel, so a turn cancelled AT THE TERMINAL
    (Esc-Esc) leaves the tab frozen on magenta; the colour-only verdict then
    promised `queued` for a message the idle TUI submitted instantly, pinning a
    ⧗ chip with no delivery to wait for (session bdeca061, 2026-07-25 —
    UserPromptSubmit fired 0.1s after a `tab: thinking` send). So a busy colour
    with a STATIC screen reports queued False; a screen still animating reports
    True. Both land in the web-send audit row (`live`/`queued`)."""
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setattr(DS.post_interrupt, "QUEUE_VERIFY_GAP_S", 0.0)
    monkeypatch.setenv("KITTY_WINDOW_ID", "88")
    A.session_start({"session_id": "msgv", "cwd": "/w", "transcript_path": ""})
    monkeypatch.setattr(DS.API, "tab_states", lambda: {"88": "thinking"})
    # STALE magenta: the screen is static (the turn was cancelled at the
    # terminal) -> not queued, despite the busy colour
    fe.screens = ["idle box"]
    code, body = _post(dash + "/api/session/msgv/message", {"text": "go"})
    assert code == 200
    assert json.loads(body) == {"ok": True, "queued": False, "tab": "thinking"}
    row = _last_state_file("msgv", "web-send")
    assert row["live"] is False and row["queued"] is False
    assert row["tab"] == "thinking"           # the raw colour is still recorded
    # a REAL mid-turn send: the screen keeps changing -> queued
    fe.screens = ["spinner .", "spinner .."]
    code, body = _post(dash + "/api/session/msgv/message", {"text": "later"})
    assert json.loads(body) == {"ok": True, "queued": True, "tab": "thinking"}
    row = _last_state_file("msgv", "web-send")
    assert row["live"] is True and row["queued"] is True
    # an UNREADABLE screen keeps the colour's verdict (never lose a real queue)
    fe.screens = [""]
    code, body = _post(dash + "/api/session/msgv/message", {"text": "hm"})
    assert json.loads(body)["queued"] is True
    assert _last_state_file("msgv", "web-send")["live"] is None


def test_post_message_settled_tab_skips_the_screen_probe(dash, monkeypatch):
    """The queued-verify is paid ONLY on a QUEUE_TABS send (where the message is
    queueing anyway) — a settled tab is already a `queued: False` and must not
    spend a screen capture / the verify gap on it."""
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "89")
    A.session_start({"session_id": "msgv2", "cwd": "/w", "transcript_path": ""})
    monkeypatch.setattr(DS.API, "tab_states", lambda: {"89": "awaiting-response"})
    reads = []
    fe.get_text = lambda win, extent="screen": reads.append(win) or ""
    code, body = _post(dash + "/api/session/msgv2/message", {"text": "hi"})
    assert json.loads(body)["queued"] is False
    assert reads == []                        # no probe on a settled tab
    assert _last_state_file("msgv2", "web-send")["live"] is None


def test_chip_delivered_matches_a_glued_prefix():
    """The delivered-prompt match rule (`chip_delivered` — owner of the fact,
    twinned in app.js `promptMatches`) is a SUFFIX match, because what the
    composer sent can arrive with anything prepended:

    · attachments prepend `@path` mentions + a newline (server-side), and
    · text ALREADY IN THE TUI INPUT BOX is glued on with NO separator — a
      terminal-side Esc-Esc cancel-edit restores the previous message there and
      the page can't know, so the paste lands right after it. The old
      newline-ONLY tolerance missed that, and the chip pinned forever (session
      bdeca061, 2026-07-25: `testing` + the sent text arrived as one prompt).

    Empty chip text must never match (it would reconcile every chip away)."""
    D = DS.chip_delivered
    assert D("hello", ["hello"])                        # exact
    assert D("hello", ["@a.png\nhello"])                # attachment mentions
    assert D("hello", ["testinghello"])                 # restored draft, glued
    assert D("hello", ["nope", "x\nhello"])             # any delivered prompt
    assert not D("hello", ["hello there"])              # a PREFIX is not a match
    assert not D("hello", ["heLLo"])                    # not case-folded
    assert not D("hello", [])
    assert not D("", ["anything at all"])               # empty never matches
    assert not D("   ", ["anything at all"])


def test_app_js_drains_through_the_shared_prompt_match(dash):
    """Both client reconcilers — drainQueue (the ⧗ queued chips) and
    drainPending (the greyed optimistic bubbles) — must go through the ONE
    shared `promptMatches` rule, the deliberate twin of the server's
    `chip_delivered`. Three copies of this match drifted apart once (the
    newline-only tolerance that pinned a chip forever); a static check on the
    served bundles keeps them from re-splitting."""
    code, core = _get(dash + "/static/app.00-core.js")
    assert code == 200 and "function promptMatches(" in core
    for part in ("app.05-session.js", "app.08-composer.js"):
        code, body = _get(dash + "/static/" + part)
        assert code == 200
        assert "promptMatches(" in body, part
        # the old hand-rolled forms must be gone from both drains
        assert 'endsWith("\\n" + ' not in body, part


def test_app_js_tints_the_picked_slash_command(dash):
    """A picked command/skill reads TINTED inside BOTH message boxes. The tint
    must be built inside the ONE shared `slashMenu` helper — that is what makes
    the composer and the new-session prompt carry it alike — and the mirror div
    must COPY the textarea's metrics instead of re-declaring the font/padding in
    CSS (a second declaration is exactly how such a mirror drifts a glyph out of
    alignment). Static checks on the served bundles — no JS engine needed."""
    code, ses = _get(dash + "/static/app.05-session.js")
    assert code == 200 and "function cmdHighlight(" in ses
    inside = ses.split("function slashMenu(")[1]
    assert "cmdHighlight(ta, host" in inside, "both boxes get it via slashMenu"
    assert "getComputedStyle(ta)" in ses and "HL_METRICS" in ses
    code, css = _get(dash + "/static/style.css")
    assert code == 200
    # the hue has ONE owner (--cmdtint), shared with the transcript's .cmdtok
    assert "var(--cmdtint)" in css.split(".cmhlt {")[1].split("}")[0]
    assert "var(--exec)" in css.split("--cmdtint:")[1].split(";")[0]
    mirror = css.split(".cmhl {")[1].split("}")[0]
    assert "font" not in mirror and "padding" not in mirror   # copied, not declared
    # every PROGRAMMATIC value change re-places the mirror (no `input` fires)
    code, comp = _get(dash + "/static/app.08-composer.js")
    assert code == 200 and "ta.cmdPaint()" in comp


def test_app_js_slash_menu_matches_on_contains(dash):
    """The "/" menu matches a typed token ANYWHERE in a command name (`/commit`
    finds `gh:commit`), prefix hits first — the namespaced/plugin names are
    unfindable under a starts-with filter unless you already recall the
    namespace. Static check on the served bundle (no JS engine in this suite):
    the matcher is the one `cmdMatches` the menu calls, and the old prefix-only
    filter must be gone so it can't come back alongside it."""
    code, ses = _get(dash + "/static/app.05-session.js")
    assert code == 200
    assert "function cmdMatches(" in ses
    assert "indexOf(q)" in ses                       # contains, not startsWith
    assert "cmdMatches(cmds, tok)" in ses            # …and the menu goes through it
    assert "startsWith(q)" not in ses, "the prefix-only filter must be gone"


def test_conv_items_carry_kind_and_prompt_text():
    items = DS.conv_items([
        {"kind": "prompt", "text": "do the thing"},
        {"kind": "message", "text": "on it"},
        {"kind": "teammsg", "text": "hi", "sender": "reviewer"},
    ])
    assert [it["kind"] for it in items] == ["prompt", "message", "teammsg"]
    assert items[0]["text"] == "do the thing"        # the queue-chip match key
    assert "text" not in items[1] and "text" not in items[2]
    assert all(it["t"] == "msg" and it["g"] is None for it in items)


def test_prompt_bubbles_tint_a_real_slash_command(dash, tmp_path):
    """A prompt SENT as a slash command reads tinted in the transcript — the
    server-rendered twin of the composer's own `/command` tint. Real commands
    only: the leading token must name one the session's cwd actually has (a CLI
    built-in or a discovered .claude/commands entry), so a message that merely
    opens with a slash is left alone. End-to-end through the read model
    (cwd -> meta.cmd_names -> msg_html), which is where it could silently stop
    tinting without either half changing."""
    proj = tmp_path / "proj"
    (proj / ".claude" / "commands").mkdir(parents=True)
    (proj / ".claude" / "commands" / "deploy.md").write_text(
        "---\ndescription: ship it\n---\nbody\n")
    tp = _tw(tmp_path, "cmds.jsonl",
             {"type": "user", "message": {"content": "/deploy staging"}},
             {"type": "user", "message": {"content": "/compact"}},
             {"type": "user", "message": {"content": "/notacommand at all"}})
    A.session_start({"session_id": "dashct", "cwd": str(proj),
                     "transcript_path": tp})
    _last, _mpos, _oldest, items = DS.merged_backlog("dashct", "dashct")
    got = [it["html"] for it in items if it.get("kind") == "prompt"]
    assert len(got) == 3
    assert '<span class="cmdtok">/deploy</span> staging' in got[0]   # discovered
    assert '<span class="cmdtok">/compact</span>' in got[1]          # built-in
    assert "cmdtok" not in got[2]                       # not a command: no tint


def test_msg_html_tint_is_prompt_only_and_fail_safe():
    """The tint's guard rails: prompts only (Claude's own messages quote
    commands often enough to be noise), an unknown name is never tinted, and the
    wrap is STRUCTURAL — the token has to sit right after the body's first
    opening tag or the body is returned untouched, so an unexpected render can't
    be corrupted by a blind replace."""
    cmds = frozenset({"compact"})
    assert '<span class="cmdtok">/compact</span> now' in \
        opshtml.msg_html("prompt", "/compact now", cmds=cmds)
    assert "cmdtok" not in opshtml.msg_html("message", "/compact now", cmds=cmds)
    assert "cmdtok" not in opshtml.msg_html("prompt", "/other now", cmds=cmds)
    assert "cmdtok" not in opshtml.msg_html("prompt", "/compact now")  # no list
    assert "cmdtok" not in opshtml.msg_html("prompt", "hi /compact", cmds=cmds)
    # a body whose first block is not the bare token (a list item) is left alone
    assert "cmdtok" not in opshtml.msg_html("prompt", "- /compact", cmds=cmds)
    # escaping is unchanged around the tinted token
    h = opshtml.msg_html("prompt", "/compact <b>x</b> & co", cmds=cmds)
    assert "&lt;b&gt;x&lt;/b&gt; &amp; co" in h and "<b>" not in h


def test_app_js_tints_the_client_built_prompt_bubbles(dash):
    """The two prompt bubbles the page builds ITSELF (the optimistic stand-in
    and the ⧗ queued chip) never pass through msg_html, so they carry their own
    tint — through the ONE shared promptMd, off the SERVER's name list
    (meta.commands), so the two renderers can't disagree about what a real
    command is. Static check on the served bundle + the payload field."""
    code, body = _get(dash + "/static/app.08-composer.js")
    assert code == 200
    assert "function leadCmd(" in body and "leadCmd(text)" in body
    assert "meta.commands" in body, "the name list must come from the server"
    assert 'el("span", "cmdtok", tok)' in body
    A.session_start({"session_id": "dashcm", "cwd": "/w", "transcript_path": ""})
    ov = _get_json(dash + "/api/session/dashcm")
    assert isinstance(ov.get("commands"), list) and "compact" in ov["commands"]


def test_post_message_no_window_is_409(dash, monkeypatch):
    _inject_fe(monkeypatch, _FakeFE())
    monkeypatch.delenv("KITTY_WINDOW_ID", raising=False)   # headless session
    A.session_start({"session_id": "msg2", "cwd": "/w", "transcript_path": ""})
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/msg2/message", {"text": "hi"})
    assert e.value.code == 409


def test_post_message_empty_text_is_400(dash, monkeypatch):
    _inject_fe(monkeypatch, _FakeFE())
    monkeypatch.setenv("KITTY_WINDOW_ID", "9")
    A.session_start({"session_id": "msg3", "cwd": "/w", "transcript_path": ""})
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/msg3/message", {"text": "   "})
    assert e.value.code == 400


# ----------------------------------------------------- alert retraction
# docs/dashboard.md, *Alert retraction*. A DELIVERED alert is taken back once
# the session stops needing you: the Telegram message is deleted, and a resolve
# push closes the on-device banner.


def _armed_and_sent(monkeypatch, tmp_path, kind="asking", handle=None):
    """Drive one alert all the way to DELIVERED and hand back (notifier, states,
    clock). Telegram is the channel (NOTIFY_WEBPUSH off → the no-device
    immediate fallback), stubbed at channels so nothing touches a wire."""
    monkeypatch.setattr(P, "DASH_PREFS_DB", str(tmp_path / "prefs.db"))
    monkeypatch.setattr(DS.config, "NOTIFY_DELAY_S", 30.0)
    monkeypatch.setattr(DS.config, "NOTIFY_TELEGRAM", True)
    monkeypatch.setattr(DS.config, "NOTIFY_WEBPUSH", False)
    monkeypatch.setattr(DS.notifier, "session_title", lambda p: "t")
    clock = [0.0]
    monkeypatch.setattr(DS.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(DS.channels, "send_telegram",
                        lambda entry, reason=None: handle)
    n = DS.Notifier()
    n.winmap = {"7": {"sid": "s7", "cwd": "/w/p", "transcript_path": "/w/t.jsonl"}}
    states = {"7": "working"}
    monkeypatch.setattr(DS.API, "tab_states", lambda: dict(states))
    n.scan()                                   # baseline
    states["7"] = ("awaiting-command" if kind == "asking" else "awaiting-response")
    n.scan()                                   # -> armed
    clock[0] = 40.0
    n.scan()                                   # past the grace -> DELIVERED
    return n, states, clock


def test_retract_deletes_the_message_when_you_answer(monkeypatch, tmp_path):
    """The whole feature, end to end at the watcher: an alert that was SENT is
    retracted the moment the tab leaves the state it alerted about — with a
    `notify-retract` row naming the channel, the reason and the outcome."""
    h = {"ch": "telegram", "sid": "s7", "kind": "asking"}
    n, states, clock = _armed_and_sent(monkeypatch, tmp_path, handle=h)
    assert [r["handle"] for r in n.sent] == [h]     # delivered + tracked
    calls = []
    monkeypatch.setattr(DS.channels, "retract",
                        lambda handle, reason, badge=0:
                        (calls.append((handle, reason, badge)), DS.channels.OK)[1])
    audited = []
    monkeypatch.setattr(DS.A, "state_file", lambda *a, **k: audited.append(a))
    clock[0] = 50.0
    states["7"] = "working"                        # you answered
    n.scan()
    assert calls and calls[0][0] is h and calls[0][1] == "tab-moved"
    assert n.sent == []                            # retracted exactly once
    rows = [a[3] for a in audited if a[2] == "notify-retract"]
    assert len(rows) == 1
    assert rows[0]["channel"] == "telegram" and rows[0]["reason"] == "tab-moved"
    assert rows[0]["outcome"] == "ok" and rows[0]["ok"] is True
    n.scan()
    assert len(calls) == 1                         # and never twice


@pytest.mark.parametrize("reason,signal", [
    ("session-ended", ("session_ended", lambda sid: True)),
    ("composing", ("composer_draft", lambda sid: {"text": "half a reply"})),
])
def test_retract_on_session_end_and_on_composing(monkeypatch, tmp_path,
                                                 reason, signal):
    """The other two RETRACT_REASONS: you closed the session, or you're typing a
    reply in the web composer. Both mean the alert's premise is gone."""
    h = {"ch": "telegram", "sid": "s7", "kind": "done"}
    n, states, clock = _armed_and_sent(monkeypatch, tmp_path, kind="done",
                                       handle=h)
    got = []
    monkeypatch.setattr(DS.channels, "retract",
                        lambda handle, r, badge=0: (got.append(r), DS.channels.OK)[1])
    monkeypatch.setattr(DS.presence, *signal)
    clock[0] = 50.0
    n.scan()
    assert got == [reason] and n.sent == []


def test_a_glance_never_retracts_a_delivered_alert(monkeypatch, tmp_path):
    """THE design rule (notifier.RETRACT_REASONS). Looking at a session cancels
    an alert not yet sent — "you don't need to be told". It must NOT delete one
    already delivered: glance at a red tab, walk away, and the deletion would
    have destroyed your only reminder while the tab is still sitting there
    asking. So a watching signal leaves the delivered alert alone."""
    h = {"ch": "telegram", "sid": "s7", "kind": "done"}
    n, states, clock = _armed_and_sent(monkeypatch, tmp_path, kind="done",
                                       handle=h)
    assert len(n.sent) == 1
    calls = []
    monkeypatch.setattr(DS.channels, "retract",
                        lambda *a, **k: (calls.append(a), DS.channels.OK)[1])
    # you are looking right at it — both channels of _watching, plus the two
    # screen-scraped "I'm on it" signals, all say so
    monkeypatch.setattr(n, "_watching", lambda win, sid, tree=None: "tab-focused")
    monkeypatch.setattr(n, "_input_typed", lambda win: "a half-typed reply")
    clock[0] = 50.0
    n.scan()
    assert calls == [] and len(n.sent) == 1       # still out there, still tracked
    # ...and the moment the tab actually moves, it goes
    states["7"] = "working"
    n.scan()
    assert len(calls) == 1 and n.sent == []


def test_retract_retries_while_the_send_is_still_in_flight(monkeypatch, tmp_path):
    """The Telegram send runs on its own thread, so a retraction can genuinely
    beat the message id home. PENDING keeps the record for the next tick rather
    than dropping it — otherwise a fast answer would strand the message."""
    h = {"ch": "telegram", "sid": "s7", "kind": "asking"}
    n, states, clock = _armed_and_sent(monkeypatch, tmp_path, handle=h)
    outcomes = [DS.channels.PENDING, DS.channels.PENDING, DS.channels.OK]
    seen = []
    monkeypatch.setattr(DS.channels, "retract",
                        lambda *a, **k: (seen.append(1), outcomes.pop(0))[1])
    states["7"] = "working"
    for t in (50.0, 51.0):
        clock[0] = t
        n.scan()
        assert len(n.sent) == 1                   # still pending -> still tracked
    clock[0] = 52.0
    n.scan()
    assert len(seen) == 3 and n.sent == []


def test_untracked_delivery_when_nothing_retractable_came_back(monkeypatch, tmp_path):
    """A channel that returns no handle delivered nothing retractable — the
    legacy `notify` script send, whose message id goes to DEVNULL. Nothing is
    tracked, and the retraction pass has nothing to do."""
    n, states, clock = _armed_and_sent(monkeypatch, tmp_path, handle=None)
    assert n.sent == []
    calls = []
    monkeypatch.setattr(DS.channels, "retract", lambda *a, **k: calls.append(a))
    clock[0] = 50.0
    states["7"] = "working"
    n.scan()
    assert calls == []


def test_delivered_alert_expires_past_the_ttl(monkeypatch, tmp_path):
    """Past RETRACT_S the delivery stops being tracked — Telegram's own 48h
    delete window is the ceiling. It is audited as `expired`, because an alert
    left behind is exactly the thing worth being able to find in the DB."""
    h = {"ch": "telegram", "sid": "s7", "kind": "asking"}
    n, states, clock = _armed_and_sent(monkeypatch, tmp_path, handle=h)
    monkeypatch.setattr(DS.config, "RETRACT_S", 100.0)
    audited = []
    monkeypatch.setattr(DS.A, "state_file", lambda *a, **k: audited.append(a))
    clock[0] = 60.0
    n.scan()
    assert len(n.sent) == 1                       # inside the window
    clock[0] = 200.0
    n.scan()
    assert n.sent == []
    rows = [a[3] for a in audited if a[2] == "notify-retract"]
    assert len(rows) == 1
    assert rows[0]["outcome"] == "expired" and rows[0]["reason"] == "ttl"
    assert rows[0]["ok"] is False


def test_resolve_push_goes_to_the_alerted_subscriptions(monkeypatch):
    """The on-device retraction: a `type:"resolve"` payload, carrying the SAME
    tag sw.js showed the banner under, to the subscriptions the alert actually
    went to — not to whatever device is most-recently-used by now, which may not
    be the one holding the banner."""
    sent = []
    monkeypatch.setattr(DS.channels, "_webpush_fanout",
                        lambda subs, payload, action: sent.append((subs, payload, action)))
    subs = [{"endpoint": "https://p/ipad", "keys": {}, "device": "ipad"}]
    h = {"ch": "webpush", "sid": "s7", "kind": "asking", "subs": subs,
         "tag": DS.channels.push_tag("s7")}
    out = DS.channels.retract(h, "tab-moved", badge=2)
    assert out == DS.channels.OK
    assert len(sent) == 1
    got_subs, payload, action = sent[0]
    assert got_subs is subs and action == "resolve"
    assert payload["type"] == "resolve" and payload["tag"] == "claude-s7"
    assert payload["sid"] == "s7" and payload["badge"] == 2


def test_resolve_push_kill_switch(monkeypatch):
    """CLAUDE_DASH_RESOLVE_PUSH=0 — the off switch for the one push that
    deliberately raises no notification (iOS userVisibleOnly, see
    channels._retract_webpush). Nothing goes on the wire."""
    monkeypatch.setattr(DS.config, "RESOLVE_PUSH", False)
    sent = []
    monkeypatch.setattr(DS.channels, "_webpush_fanout",
                        lambda *a: sent.append(a))
    h = {"ch": "webpush", "sid": "s7", "kind": "asking",
         "subs": [{"endpoint": "https://p/x", "keys": {}}], "tag": "claude-s7"}
    assert DS.channels.retract(h, "tab-moved") == DS.channels.NOTHING
    assert sent == []


def test_push_tag_agrees_with_the_service_worker(dash):
    """The tag is a CONTRACT between channels.push_tag and sw.js: the resolve
    push closes by tag, so a drift here leaves the banner up forever. Checked
    against the served worker, the same way the other cross-file literals are."""
    code, body = _get(dash + "/sw.js")
    assert code == 200
    assert '"claude-" + (sid || "")' in body, "sw.js must build channels.push_tag"
    assert 'd.type === "resolve"' in body and "getNotifications" in body
    assert DS.channels.push_tag("abc") == "claude-abc"


def test_telegram_transport_sends_and_deletes(monkeypatch, tmp_path):
    """dashboard/telegram.py against a fake Bot API (CLAUDE_DASH_TELEGRAM_API —
    the env-knob convention dictate.py's grant URL set). The point of the module
    is that `send` KEEPS the message_id the fire-and-forget script threw away,
    and that `delete` can then reach the message with it."""
    seen = []

    class Bot(BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            args = urllib.parse.parse_qs(self.rfile.read(n).decode())
            seen.append((self.path, {k: v[0] for k, v in args.items()}))
            out = ({"ok": True, "result": {"message_id": 4242,
                                           "chat": {"id": 209}}}
                   if self.path.endswith("/sendMessage")
                   else {"ok": True, "result": True})
            body = json.dumps(out).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Bot)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        creds = tmp_path / "tg"
        creds.mkdir()
        (creds / "bot-token").write_text("tok-123\n")
        (creds / "chat-id").write_text("209\n")
        monkeypatch.setenv("CLAUDE_DASH_TELEGRAM_DIR", str(creds))
        monkeypatch.setenv("CLAUDE_DASH_TELEGRAM_API",
                           "http://127.0.0.1:%d" % srv.server_address[1])
        assert DS.telegram.enabled() is True

        res = DS.telegram.send("🔴 proj needs you")
        assert res.ok and res.message_id == 4242 and res.chat == 209
        path, args = seen[0]
        assert path == "/bottok-123/sendMessage"
        assert args["chat_id"] == "209" and "needs you" in args["text"]

        gone = DS.telegram.delete(res.chat, res.message_id)
        assert gone.ok
        path, args = seen[1]
        assert path == "/bottok-123/deleteMessage"
        assert args == {"chat_id": "209", "message_id": "4242"}
    finally:
        srv.shutdown()


def test_telegram_transport_off_without_credentials(monkeypatch, tmp_path):
    """Unconfigured = invisible, never broken: enabled() is False (the notifier
    then uses the legacy script, which is why an alert still arrives), and a
    stray call still returns a Result rather than raising into the watcher."""
    monkeypatch.setenv("CLAUDE_DASH_TELEGRAM_DIR", str(tmp_path / "nope"))
    assert DS.telegram.enabled() is False
    assert DS.telegram.send("hi").ok is False
    assert DS.telegram.delete(None, None).ok is False


def test_telegram_delete_of_a_vanished_message_is_not_a_failure(monkeypatch, tmp_path):
    """A 400 'message to delete not found' means someone cleared the chat first
    — the message is out, which is what we wanted. It must read as `gone`, not
    as a failed retraction (which would light up the audit for nothing)."""
    class Bot(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.dumps({"ok": False,
                               "description": "Bad Request: message to delete not found"}).encode()
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Bot)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("CLAUDE_DASH_TELEGRAM_TOKEN", "t")
        monkeypatch.setenv("CLAUDE_DASH_TELEGRAM_CHAT", "9")
        monkeypatch.setenv("CLAUDE_DASH_TELEGRAM_API",
                           "http://127.0.0.1:%d" % srv.server_address[1])
        res = DS.telegram.delete(9, 1)
        assert res.ok is False and res.gone is True
        # and the channel reports that as a settled retraction, not a failure.
        # The delete runs OFF the watcher thread (a 10s-timeout round-trip can't
        # sit in the 1s scan), so it settles over two ticks: PENDING, then the
        # outcome the thread left.
        h = {"ch": "telegram", "sid": "s7", "kind": "done",
             "chat": 9, "msg_id": 1, "done": True}
        assert DS.channels.retract(h, "tab-moved") == DS.channels.PENDING
        wait_until(lambda: DS.channels.retract(h, "tab-moved") == DS.channels.GONE,
                   desc="the delete thread settles the handle as gone")
    finally:
        srv.shutdown()


def test_telegram_handle_is_pending_until_the_send_lands(monkeypatch):
    """The handle's PENDING state: created synchronously by send_telegram, filled
    by the sender thread. Until `done` the retraction must say PENDING (retry),
    and a send that failed outright leaves NOTHING to retract."""
    h = {"ch": "telegram", "sid": "s7", "kind": "done",
         "chat": None, "msg_id": None, "done": False}
    assert DS.channels.retract(h, "tab-moved") == DS.channels.PENDING
    h["done"] = True                                # thread finished, send failed
    assert DS.channels.retract(h, "tab-moved") == DS.channels.NOTHING


def test_neither_channel_blocks_the_watcher_thread(monkeypatch, tmp_path):
    """The 1 s scan loop must never sit on a network round-trip — the rule the
    send already followed and the retraction had to be held to. Both wire calls
    are stubbed to hang; a scan that delivers AND a scan that retracts must each
    return promptly, with the retraction reporting PENDING rather than waiting."""
    import time as _real_time
    hanging = threading.Event()
    monkeypatch.setattr(DS.telegram, "enabled", lambda: True)
    # the stubs return a real Result once released, so the freed threads finish
    # cleanly rather than raising into pytest's unhandled-thread warning
    def stalled(*a):
        hanging.wait(30)
        return DS.telegram.Result(error="stub")
    monkeypatch.setattr(DS.telegram, "send", stalled)
    monkeypatch.setattr(DS.telegram, "delete", stalled)
    h = {"ch": "telegram", "sid": "s7", "kind": "done",
         "chat": 9, "msg_id": 1, "done": True}
    try:
        t0 = _real_time.monotonic()
        assert DS.channels.send_telegram({"sid": "s7", "kind": "done"}) is not None
        assert DS.channels.retract(h, "tab-moved") == DS.channels.PENDING
        assert DS.channels.retract(h, "tab-moved") == DS.channels.PENDING
        assert _real_time.monotonic() - t0 < 2.0, "a wire call reached the watcher"
    finally:
        hanging.set()


def test_a_wedged_retraction_still_ages_out(monkeypatch, tmp_path):
    """The TTL is a bound on the record's LIFETIME, not just on the un-resolved
    case: a channel stuck answering PENDING (a wedged sender thread) must still
    age out, or the bound would hold only while another thread behaves."""
    h = {"ch": "telegram", "sid": "s7", "kind": "asking"}
    n, states, clock = _armed_and_sent(monkeypatch, tmp_path, handle=h)
    monkeypatch.setattr(DS.config, "RETRACT_S", 100.0)
    monkeypatch.setattr(DS.channels, "retract",
                        lambda *a, **k: DS.channels.PENDING)   # never settles
    audited = []
    monkeypatch.setattr(DS.A, "state_file", lambda *a, **k: audited.append(a))
    states["7"] = "working"                    # resolved, but the wire is wedged
    clock[0] = 60.0
    n.scan()
    assert len(n.sent) == 1                    # still retrying inside the window
    clock[0] = 200.0
    n.scan()
    assert n.sent == []
    rows = [a[3] for a in audited if a[2] == "notify-retract"]
    assert rows and rows[-1]["outcome"] == "expired" and rows[-1]["reason"] == "ttl"


def test_badge_counts_only_live_sessions(monkeypatch):
    """The pushed app-icon badge must count LIVE sessions needing you, not raw
    tab rows. Red/green are RESTING states and the tab DB is keyed by kitty
    window, so a session whose terminal went away without a SessionEnd leaves its
    row sitting on one forever — measured 148 stale rows against 1 real ask, an
    icon stuck at three digits that no retraction could bring down. Same
    predicate as the browser's own needsYouCount (`r.live` + the state)."""
    n = DS.Notifier()
    n.winmap = {
        "1": {"sid": "live-asking", "live": True},
        "2": {"sid": "live-done", "live": True},
        "3": {"sid": "live-busy", "live": True},
        "4": {"sid": "dead-but-still-red", "live": False},
        "5": {"sid": "dead-but-still-green", "live": False},
    }
    monkeypatch.setattr(DS.API, "tab_states", lambda: {
        "1": "awaiting-command", "2": "awaiting-response", "3": "working",
        "4": "awaiting-command", "5": "awaiting-response",
        "99": "awaiting-command",          # a window no session maps to at all
    })
    assert n._needs_you_count() == 2
    monkeypatch.setattr(DS.API, "tab_states", lambda: {})
    assert n._needs_you_count() == 0


def test_badge_survives_an_unreadable_tab_table(monkeypatch):
    """It rides every push payload, so a tab-DB read miss must degrade to 0, not
    raise into the watcher."""
    n = DS.Notifier()
    n.winmap = {"1": {"sid": "s", "live": True}}

    def boom():
        raise OSError("tab db gone")
    monkeypatch.setattr(DS.API, "tab_states", boom)
    assert n._needs_you_count() == 0
