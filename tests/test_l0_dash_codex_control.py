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


class _CxModelFE(_FakeFE):
    """A reactive codex /model picker: pasting `/model` opens step 1
    (Select Model), and ENTER advances through the 3 steps
    (Select Model → Select Model and Effort → Select Reasoning Level →
    closed). DOWN/UP walk the `›` cursor within a step."""

    _STEPS = ("Select Model", "Select Model and Effort",
              "Select Reasoning Level for gpt-5.6-terra")
    _ROWS = (("codex-auto-review", "All models (current)"),
             ("gpt-5.6-sol (current)", "gpt-5.6-terra", "gpt-5.6-luna"),
             ("Low", "Medium (default)", "High", "Extra high", "Max", "Ultra"))

    def __init__(self):
        super().__init__()
        self.step, self.cursor, self.picks = -1, 1, []

    def _default_cursor(self, step):
        """The row the picker pre-selects on entering `step` — its `(current)` /
        `(default)` row (1-based), else row 1 — so a bare Enter accepts it."""
        if 0 <= step < len(self._ROWS):
            for i, label in enumerate(self._ROWS[step], 1):
                if "(current)" in label or "(default)" in label:
                    return i
        return 1

    def paste_text(self, win, text):
        self.pasted.append((win, text))
        if text == "/model":
            self.step, self.cursor = 0, self._default_cursor(0)
        return True

    def get_text(self, win, extent="screen", ansi=False):
        if ansi or self.step < 0 or self.step >= len(self._STEPS):
            return "codex\n❯ \n[gpt-5.6-terra] │ ready\n"
        lines = ["  " + self._STEPS[self.step]]
        for i, label in enumerate(self._ROWS[self.step], 1):
            mark = "› " if i == self.cursor else "  "
            lines.append("%s%d. %s   desc" % (mark, i, label))
        lines.append("  Press enter to confirm or esc to go back")
        return "\n".join(lines)

    def send_key(self, win, *keys):
        self.keyed.append((win, keys))
        k = keys[0] if keys else ""
        if k == "down":
            self.cursor = min(self.cursor + 1, len(self._ROWS[self.step]))
        elif k == "up":
            self.cursor = max(self.cursor - 1, 1)
        elif k == "enter":
            self.picks.append((self.step, self._ROWS[self.step][self.cursor - 1]))
            self.step += 1                              # advance / close
            self.cursor = self._default_cursor(self.step)
        return True


def test_codex_model_preserves_current_effort(dash, tmp_path, monkeypatch):
    """A model switch PRESERVES the current reasoning level (the ✦/✧ axes are
    independent) — the gesture reads the current effort (here `low`) from the
    rollout and re-selects it at step 3, instead of accepting the new model's
    default. The reported bug: low → switch model → medium."""
    monkeypatch.setenv("KITTY_WINDOW_ID", "71")
    _seed(tmp_path, "cx2", [
        {"type": "session_meta", "payload": {"cwd": str(tmp_path)}},
        {"type": "turn_context",
         "payload": {"model": "gpt-5.6-sol", "effort": "low"}}])
    fe = _CxModelFE()
    _inject_fe(monkeypatch, fe)
    code, body = _post(dash + "/api/session/cx2/command",
                       {"cmd": "model", "arg": "gpt-5.6-terra"})
    assert code == 200 and json.loads(body)["ok"]
    # step1 → 'All models', step2 → the chosen model, step3 → the CURRENT level
    assert fe.pasted == [("71", "/model")]
    assert [p[1] for p in fe.picks] == ["All models (current)", "gpt-5.6-terra",
                                        "Low"]
    row = _last_state_file("cx2", "web-command")
    assert row["host"] == "codex" and row["cmd"] == "model" and row["ok"]


def test_codex_model_accepts_default_when_effort_unknown(dash, tmp_path,
                                                         monkeypatch):
    """No readable current effort (a fresh rollout) → the model switch accepts
    the new model's default level (the safe fallback, never a wrong one)."""
    monkeypatch.setenv("KITTY_WINDOW_ID", "73")
    _seed(tmp_path, "cx2c")           # session_meta only: no turn_context effort
    fe = _CxModelFE()
    _inject_fe(monkeypatch, fe)
    code, body = _post(dash + "/api/session/cx2c/command",
                       {"cmd": "model", "arg": "gpt-5.6-terra"})
    assert code == 200 and json.loads(body)["ok"]
    assert [p[1] for p in fe.picks] == ["All models (current)", "gpt-5.6-terra",
                                        "Medium (default)"]


def test_codex_effort_keeps_current_model(dash, tmp_path, monkeypatch):
    monkeypatch.setenv("KITTY_WINDOW_ID", "72")
    _seed(tmp_path, "cx2b")
    fe = _CxModelFE()
    _inject_fe(monkeypatch, fe)
    code, body = _post(dash + "/api/session/cx2b/command",
                       {"cmd": "effort", "arg": "xhigh"})
    assert code == 200 and json.loads(body)["ok"]
    # effort keeps the CURRENT model (the (current) row) and picks 'Extra high'
    assert [p[1] for p in fe.picks] == ["All models (current)",
                                        "gpt-5.6-sol (current)", "Extra high"]
    row = _last_state_file("cx2b", "web-command")
    assert row["host"] == "codex" and row["cmd"] == "effort" and row["ok"]


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


# ---------------------------------------------------------------- plan mode

def _plan_rollout_records(pid="p-77"):
    """A rollout whose last turn produced a Plan item and nothing after — the
    read-side 'plan pending' shape (plugins/codex/read.pending_dialog)."""
    return [{"type": "session_meta", "payload": {"cwd": "/tmp"}},
            {"type": "event_msg", "payload": {"type": "task_started",
                                              "collaboration_mode_kind": "plan"}},
            {"type": "event_msg", "payload": {
                "type": "item_completed",
                "item": {"type": "Plan", "id": pid,
                         "text": "# Plan\n1. do the thing\n"}}},
            {"type": "event_msg", "payload": {"type": "task_complete"}}]


class _CxPlanFE(_FakeFE):
    """A reactive codex plan-DECISION picker: a `›` cursor over three rows below
    an `Implement this plan?` header (with a numbered plan BODY above it, which
    the driver's region-scoping must ignore); DOWN/UP move, ENTER closes."""

    _ROWS = ("Yes, implement this plan", "Yes, clear context and implement",
             "No, stay in Plan mode")

    def __init__(self):
        super().__init__()
        self.cursor, self.closed, self.chosen = 1, False, None

    def get_text(self, win, extent="screen", ansi=False):
        if ansi:
            return ""
        if self.closed:
            return "codex\n❯ \n[gpt-5.6-sol] │ ready\n"
        lines = ["• Proposed Plan", "  1. do the thing", "",
                 "  Implement this plan?"]
        for i, label in enumerate(self._ROWS, 1):
            mark = "› " if i == self.cursor else "  "
            lines.append("%s%d. %s   a description" % (mark, i, label))
        lines.append("  Press enter to confirm or esc to go back")
        return "\n".join(lines)

    def send_key(self, win, *keys):
        self.keyed.append((win, keys))
        k = keys[0] if keys else ""
        if k == "down":
            self.cursor = min(self.cursor + 1, 3)
        elif k == "up":
            self.cursor = max(self.cursor - 1, 1)
        elif k == "enter":
            self.chosen, self.closed = self.cursor, True
        return True


def test_codex_plan_options_are_the_static_approve_rows(dash, tmp_path, monkeypatch):
    monkeypatch.setenv("KITTY_WINDOW_ID", "75")
    _seed(tmp_path, "cxp1", _plan_rollout_records())
    _inject_fe(monkeypatch, _CxPlanFE())
    code, body = _post(dash + "/api/session/cxp1/plan-options",
                       {"plan_id": "p-77"})
    assert code == 200
    labels = [o["label"] for o in json.loads(body)["options"]]
    # the two APPROVE rows, never the keep-planning row (the card's own button)
    assert labels == ["Yes, implement this plan",
                      "Yes, clear context and implement"]


def test_codex_plan_decision_routes_through_the_gesture(dash, tmp_path, monkeypatch):
    monkeypatch.setenv("KITTY_WINDOW_ID", "76")
    _seed(tmp_path, "cxp2", _plan_rollout_records())
    fe = _CxPlanFE()
    _inject_fe(monkeypatch, fe)
    code, body = _post(dash + "/api/session/cxp2/plan-decision",
                       {"plan_id": "p-77", "digit": "1",
                        "label": "Yes, implement this plan"})
    assert code == 200 and json.loads(body) == {"ok": True, "kind": "decide"}
    assert fe.chosen == 1 and fe.closed        # cursored onto row 1, Enter
    row = _last_state_file("cxp2", "web-plan")
    assert row["host"] == "codex" and row["ok"] and row["plan_id"] == "p-77"


def test_codex_plan_dismiss_picks_keep_planning(dash, tmp_path, monkeypatch):
    monkeypatch.setenv("KITTY_WINDOW_ID", "77")
    _seed(tmp_path, "cxp3", _plan_rollout_records())
    fe = _CxPlanFE()
    _inject_fe(monkeypatch, fe)
    code, body = _post(dash + "/api/session/cxp3/plan-decision",
                       {"plan_id": "p-77", "dismiss": True})
    assert code == 200 and json.loads(body) == {"ok": True, "kind": "dismiss"}
    assert fe.chosen == 3 and fe.closed        # 'No, stay in Plan mode'
    row = _last_state_file("cxp3", "web-plan")
    assert row["host"] == "codex" and row["ok"] and row["kind"] == "dismiss"


def test_codex_plan_decision_stale_plan_id_409s(dash, tmp_path, monkeypatch):
    monkeypatch.setenv("KITTY_WINDOW_ID", "78")
    _seed(tmp_path, "cxp4", _plan_rollout_records())
    fe = _CxPlanFE()
    _inject_fe(monkeypatch, fe)
    try:
        _post(dash + "/api/session/cxp4/plan-decision",
              {"plan_id": "stale", "dismiss": True})
        raise AssertionError("expected 409")
    except urllib.error.HTTPError as e:
        assert e.code == 409
    assert fe.keyed == []      # nothing driven for an expired plan
