# tests/test_l0_dash_codex_control.py — L0 dashboard: the CODEX control plane.
#
# P5 routes the dashboard's control handlers through a session's owning
# HostControl when it is NOT claude_code: a codex session (transcript_path is a
# rollout, so plugins.owns_by → "codex") drives interrupt/compact/rename/ask
# through plugins/codex/hostctl.CodexHost, while a claude_code session keeps its
# byte-identical inline path (proven by the rest of the L0 control/dialogs
# suites, unchanged). These tests pin the codex ROUTING end-to-end over the real
# in-process server: the gesture ran (fe.pasted/keyed), the reply + `web-*` audit
# row carry `host: codex`, and the caps guard 409s the gestures codex can't drive.
import json
import sys
import urllib.error

from conftest import REPO

if REPO not in sys.path:
    sys.path.insert(0, REPO)

import core.audit as A
from dashkit import _FakeFE, _inject_fe, _last_state_file, _post

_UUID = "0f0f0f0f-0000-4000-8000-000000000042"


def _rollout(tmp_path, records):
    """A real codex rollout under a sessions/ tree — plugins.owns_by(path) names
    `codex` for it, so the session is attributed to the codex host."""
    d = tmp_path / ".codex" / "sessions" / "2026" / "07" / "29"
    d.mkdir(parents=True, exist_ok=True)
    p = d / ("rollout-2026-07-29T10-00-00-%s.jsonl" % _UUID)
    with open(p, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
    return str(p)


def _seed(tmp_path, sid, records=None):
    ro = _rollout(tmp_path, records or [
        {"type": "session_meta", "payload": {"cwd": str(tmp_path)}}])
    A.session_start({"session_id": sid, "cwd": str(tmp_path),
                     "transcript_path": ro})
    return ro


def test_codex_compact_routes_through_the_gesture(dash, tmp_path, monkeypatch):
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "70")
    _seed(tmp_path, "cx1")
    code, body = _post(dash + "/api/session/cx1/command", {"cmd": "compact"})
    assert code == 200 and json.loads(body) == {"ok": True, "queued": False,
                                                "tab": ""}
    # codex pastes its OWN /compact — no confirm menu (that is Claude's
    # prompt-cache prompt), no keystrokes
    assert fe.pasted == [("70", "/compact")] and fe.keyed == []
    row = _last_state_file("cx1", "web-command")
    assert row["host"] == "codex" and row["cmd"] == "compact" and row["ok"]


def test_codex_model_and_effort_are_capped_off(dash, tmp_path, monkeypatch):
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "71")
    _seed(tmp_path, "cx2")
    for cmd, arg in (("model", "sonnet[1m]"), ("effort", "low")):
        try:
            _post(dash + "/api/session/cx2/command", {"cmd": cmd, "arg": arg})
            raise AssertionError("expected 409")
        except urllib.error.HTTPError as e:
            assert e.code == 409
            assert json.loads(e.read())["cap"] == cmd
    assert fe.pasted == []   # nothing typed for an unsupported gesture


def test_codex_rename_live_routes_through_the_gesture(dash, tmp_path, monkeypatch):
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "72")
    _seed(tmp_path, "cx3")
    code, body = _post(dash + "/api/session/cx3/rename", {"name": "my codex"})
    d = json.loads(body)
    assert code == 200 and d["ok"] and d["channel"] == "tui"
    assert fe.pasted == [("72", "/rename my codex")]
    row = _last_state_file("cx3", "web-rename")
    assert row["host"] == "codex" and row["channel"] == "tui"


class _CxIntFE(_FakeFE):
    """send_key appends codex's `turn_aborted` on the Escape — the verify signal
    (codex fires no Stop hook)."""

    def __init__(self, rollout):
        super().__init__()
        self.rollout = rollout

    def send_key(self, win, *keys):
        self.keyed.append((win, keys))
        if keys and keys[0] == "escape":
            with open(self.rollout, "a", encoding="utf-8") as f:
                f.write(json.dumps({"type": "event_msg",
                                    "payload": {"type": "turn_aborted"}}) + "\n")
        return True


def test_codex_interrupt_is_a_single_verified_esc(dash, tmp_path, monkeypatch):
    monkeypatch.setenv("KITTY_WINDOW_ID", "73")
    ro = _seed(tmp_path, "cx4")
    fe = _CxIntFE(ro)
    _inject_fe(monkeypatch, fe)
    code, body = _post(dash + "/api/session/cx4/interrupt", {})
    d = json.loads(body)
    assert code == 200 and d["ok"] and d["verified"] is True
    assert d["queued"] is False and d["restored"] == ""
    # a SINGLE Esc (codex is not modal) — no re-press loop, no escape-recheck
    assert fe.keyed == [("73", ("escape",))]
    row = _last_state_file("cx4", "web-interrupt")
    assert row["host"] == "codex" and row["verified"] is True


class _CxAskFE(_FakeFE):
    """A reactive codex request_user_input dialog: a `›` cursor over two options
    that DOWN/UP move and ENTER submits (closing the dialog)."""

    def __init__(self):
        super().__init__()
        self.cursor, self.closed = 1, False

    def get_text(self, win, extent="screen", ansi=False):
        if ansi:
            return ""
        if self.closed:
            return "codex done\n❯ \n[gpt-5.1-codex] │ ready\n"
        lines = ["Question 1/1 (1 unanswered)"]
        for i, label in enumerate(("Apple", "Banana"), 1):
            mark = "› " if i == self.cursor else "  "
            lines.append("%s%d. %s   a short description" % (mark, i, label))
        lines.append("tab to add notes | enter to submit answer | esc to interrupt")
        return "\n".join(lines)

    def send_key(self, win, *keys):
        self.keyed.append((win, keys))
        k = keys[0] if keys else ""
        if k == "down":
            self.cursor = min(self.cursor + 1, 2)
        elif k == "up":
            self.cursor = max(self.cursor - 1, 1)
        elif k == "enter":
            self.closed = True
        return True


def _ask_rollout_records():
    return [{"type": "response_item",
             "payload": {"type": "function_call", "name": "request_user_input",
                         "call_id": "call_9",
                         "arguments": json.dumps({"questions": [
                             {"id": "q1", "header": "pick", "question": "which?",
                              "options": [{"label": "Apple", "description": ""},
                                          {"label": "Banana", "description": ""}]}
                         ]})}}]


def test_codex_answer_routes_through_the_dialog_driver(dash, tmp_path, monkeypatch):
    monkeypatch.setenv("KITTY_WINDOW_ID", "74")
    _seed(tmp_path, "cx5", _ask_rollout_records())
    fe = _CxAskFE()
    _inject_fe(monkeypatch, fe)
    code, body = _post(dash + "/api/session/cx5/answer",
                       {"tool_use_id": "call_9",
                        "answers": [{"selected": ["Banana"], "other": ""}]})
    assert code == 200 and json.loads(body) == {"ok": True, "chat": False}
    assert fe.cursor == 2 and fe.closed   # cursored onto Banana, then Enter
    row = _last_state_file("cx5", "web-answer")
    assert row["host"] == "codex" and row["ok"] and row["tool_use_id"] == "call_9"
