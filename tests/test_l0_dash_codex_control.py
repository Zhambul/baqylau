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

import pytest

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
    """A reactive codex request_user_input dialog, modelled on the LIVE geometry
    (re-measured 2026-07-31 against codex-cli 0.146.0 — docs/codex.md):

      · `Question N/M (K unanswered)`, DOWN/UP walking a `›` cursor and
        RIGHT/LEFT moving between questions WITHOUT answering them;
      · codex's OWN `None of the above` row appended after the model's options
        (invisible to the rollout, and the one way to give a free-text answer);
      · `tab` opening a notes field (footer `tab or esc to clear notes`);
      · THE CURSOR IS NOT THE SELECTION, but ENTER and TAB both TAKE it — the
        exact behaviour that made a typed answer submit option 1 with the user's
        real answer demoted to `user_note:`;
      · a submit that leaves any question unanswered raising `Submit with
        unanswered questions?` → `Proceed` / `Go back`.

    `submitted` is what codex would have recorded: {question index: [answers]},
    an unanswered question carrying []."""

    def __init__(self, questions=(("Apple", "Banana"),)):
        super().__init__()
        self.qopts = [list(o) for o in questions]
        self.m = len(self.qopts)
        self.q, self.cursor = 1, 1
        self.notes = None                    # None = closed, str = open
        self.submitted = {i: [] for i in range(self.m)}
        self.confirm, self.ccursor, self.closed = False, 1, False

    # --- what the screen shows ------------------------------------------------
    def _rows(self):
        return self.qopts[self.q - 1] + ["None of the above"]

    def get_text(self, win, extent="screen", ansi=False):
        if ansi:
            return ""
        if self.closed:
            return "codex done\n❯ \n[gpt-5.1-codex] │ ready\n"
        if self.confirm:
            miss = sum(1 for v in self.submitted.values() if not v)
            return "\n".join([
                "Submit with unanswered questions?",
                "%d unanswered question" % miss,
                "%s1. Proceed  Submit with %d unanswered question."
                % ("› " if self.ccursor == 1 else "  ", miss),
                "%s2. Go back  Return to the first unanswered question."
                % ("› " if self.ccursor == 2 else "  "),
                "Press enter to confirm or esc to go back"])
        miss = sum(1 for v in self.submitted.values() if not v)
        lines = ["Question %d/%d (%d unanswered)" % (self.q, self.m, miss)]
        for i, label in enumerate(self._rows(), 1):
            mark = "› " if i == self.cursor else "  "
            lines.append("%s%d. %s   a short description" % (mark, i, label))
        if self.notes is not None:
            lines.append("› " + (self.notes or "Add notes"))
            lines.append("tab or esc to clear notes | enter to submit answer")
        else:
            lines.append("tab to add notes | enter to submit %s | %s esc to interrupt"
                         % ("all" if self.q == self.m else "answer",
                            "←/→ to navigate questions |" if self.m > 1 else ""))
        return "\n".join(lines)

    # --- what the keys do -----------------------------------------------------
    def _submit_question(self):
        """ENTER (or the notes field's) — the cursor row becomes the answer."""
        ans = [self._rows()[self.cursor - 1]]
        if self.notes:
            ans.append("user_note: " + self.notes)
        self.submitted[self.q - 1] = ans
        self.notes = None
        if self.q < self.m:
            self.q, self.cursor = self.q + 1, 1
        elif any(not v for v in self.submitted.values()):
            self.confirm, self.ccursor = True, 1
        else:
            self.closed = True

    def send_key(self, win, *keys):
        self.keyed.append((win, keys))
        k = keys[0] if keys else ""
        if self.confirm:
            if k == "down":
                self.ccursor = min(self.ccursor + 1, 2)
            elif k == "up":
                self.ccursor = max(self.ccursor - 1, 1)
            elif k == "enter":
                if self.ccursor == 1:
                    self.closed, self.confirm = True, False
                else:
                    self.confirm = False
            return True
        if k == "down":
            self.cursor = min(self.cursor + 1, len(self._rows()))
        elif k == "up":
            self.cursor = max(self.cursor - 1, 1)
        elif k == "right":
            self.q, self.cursor, self.notes = min(self.q + 1, self.m), 1, None
        elif k == "left":
            self.q, self.cursor, self.notes = max(self.q - 1, 1), 1, None
        elif k == "tab":
            self.notes = "" if self.notes is None else None
        elif k == "enter":
            self._submit_question()
        return True

    def send_text(self, win, text):
        """kitten_send_text: the bytes, then a SEPARATE Enter."""
        if self.notes is None:            # would land in codex's composer
            return False
        self.notes += text
        self._submit_question()
        return True


def _ask_rollout_records(questions=(("q1", "pick", "which?",
                                     ("Apple", "Banana")),)):
    return [{"type": "response_item",
             "payload": {"type": "function_call", "name": "request_user_input",
                         "call_id": "call_9",
                         "arguments": json.dumps({"questions": [
                             {"id": qid, "header": hdr, "question": text,
                              "options": [{"label": o, "description": ""}
                                          for o in opts]}
                             for qid, hdr, text, opts in questions]})}}]


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
    assert fe.submitted == {0: ["Banana"]}
    row = _last_state_file("cx5", "web-answer")
    assert row["host"] == "codex" and row["ok"] and row["tool_use_id"] == "call_9"


def test_codex_free_text_answers_on_none_of_the_above(dash, tmp_path, monkeypatch):
    """A typed answer with NO option chosen must reach codex as the user's OWN
    words — not as a note stapled to option 1.

    codex's `tab` (add notes) TAKES the cursor as the selection, so the old
    driver's tab-and-type from the cursor's resting place recorded
    `["Apple", "user_note: <the real answer>"]` — measured in a live rollout
    (2026-07-31): the first option submitted as the answer, the user's own
    demoted to a footnote. The row codex appends for exactly this, `None of the
    above`, is invisible to the rollout, so the driver has to find it on SCREEN
    and cursor onto it BEFORE pressing tab."""
    monkeypatch.setenv("KITTY_WINDOW_ID", "75")
    _seed(tmp_path, "cx5b", _ask_rollout_records())
    fe = _CxAskFE()
    _inject_fe(monkeypatch, fe)
    code, body = _post(dash + "/api/session/cx5b/answer",
                       {"tool_use_id": "call_9",
                        "answers": [{"selected": [], "other": "neither — cherry"}]})
    assert code == 200 and json.loads(body) == {"ok": True, "chat": False}
    assert fe.closed and fe.cursor == 3            # the appended row, not Apple
    assert fe.submitted == {0: ["None of the above",
                                "user_note: neither — cherry"]}


def test_codex_an_option_plus_a_note_rides_the_same_tab(dash, tmp_path,
                                                        monkeypatch):
    """codex's dialog natively carries a note BESIDE a pick ("Optionally, add
    details in notes (tab)"), so a chosen option and typed text are not rivals —
    the driver cursors onto the option and types the text as its note."""
    monkeypatch.setenv("KITTY_WINDOW_ID", "76")
    _seed(tmp_path, "cx5c", _ask_rollout_records())
    fe = _CxAskFE()
    _inject_fe(monkeypatch, fe)
    code, _ = _post(dash + "/api/session/cx5c/answer",
                    {"tool_use_id": "call_9",
                     "answers": [{"selected": ["Apple"], "other": "the red one"}]})
    assert code == 200 and fe.closed
    assert fe.submitted == {0: ["Apple", "user_note: the red one"]}


def test_codex_chat_declines_by_submitting_unanswered(dash, tmp_path, monkeypatch):
    """"chat about this" on codex. It has no decline ROW (its Esc ABORTS the
    turn), so the word maps onto the next best thing codex does have: a submit
    that leaves questions UNANSWERED (`Submit with unanswered questions?` →
    `Proceed`, which sends them as `answers: []`).

    What codex does NOT have is a zero-answer submit — the submitting key takes
    the cursor — so the driver navigates to the LAST question (RIGHT never
    answers) and spends the one forced answer on `None of the above`. Everything
    before it goes through empty. The card used to show this button and get a
    409, since codex declared no decline at all."""
    monkeypatch.setenv("KITTY_WINDOW_ID", "77")
    _seed(tmp_path, "cx5d",
          _ask_rollout_records((("q1", "fruit", "which fruit?",
                                 ("Apple", "Banana")),
                                ("q2", "size", "which size?",
                                 ("Small", "Large")))))
    fe = _CxAskFE((("Apple", "Banana"), ("Small", "Large")))
    _inject_fe(monkeypatch, fe)
    code, body = _post(dash + "/api/session/cx5d/answer",
                       {"tool_use_id": "call_9", "chat": True})
    assert code == 200 and json.loads(body) == {"ok": True, "chat": True}
    assert fe.closed and not fe.confirm
    assert fe.submitted == {0: [], 1: ["None of the above"]}
    # …and it got there without ever answering question 1
    assert ("77", ("right",)) in fe.keyed
    row = _last_state_file("cx5d", "web-answer")
    assert row["host"] == "codex" and row["ok"] and row["chat"] is True


def test_codex_chat_carries_the_typed_message_as_the_note(dash, tmp_path,
                                                          monkeypatch):
    """A `chat` that carries typed text puts it in the decline row's NOTE, so the
    words ride the tool RESULT rather than racing the resumed turn as a pasted
    follow-up — and the host says so (`message_sent`) so the handler does not
    ALSO deliver them."""
    monkeypatch.setenv("KITTY_WINDOW_ID", "78")
    _seed(tmp_path, "cx5e", _ask_rollout_records())
    fe = _CxAskFE()
    _inject_fe(monkeypatch, fe)
    code, body = _post(dash + "/api/session/cx5e/answer",
                       {"tool_use_id": "call_9", "chat": True,
                        "message": "let's research this first"})
    assert code == 200
    assert json.loads(body) == {"ok": True, "chat": True, "message_sent": True}
    assert fe.submitted == {0: ["None of the above",
                                "user_note: let's research this first"]}
    assert fe.pasted == []            # NOT also delivered into the composer


def test_codex_free_text_fails_loudly_without_the_appended_row(dash, tmp_path,
                                                               monkeypatch):
    """The `None of the above` row is codex's, not the tool call's — so a codex
    that stops appending it must FAIL the step, never fall back to "cursor
    wherever it is and type". The whole bug was a silent wrong answer; a 409 the
    card can retry is the better failure, and the dialog is left OPEN."""
    monkeypatch.setenv("KITTY_WINDOW_ID", "79")
    _seed(tmp_path, "cx5f", _ask_rollout_records())
    fe = _CxAskFE()
    fe._rows = lambda: list(fe.qopts[fe.q - 1])      # no appended row
    _inject_fe(monkeypatch, fe)
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/cx5f/answer",
              {"tool_use_id": "call_9",
               "answers": [{"selected": [], "other": "something else"}]})
    assert e.value.code == 409
    assert json.loads(e.value.read())["step"] == "noneof"
    assert not fe.closed and fe.submitted == {0: []}   # nothing was answered


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
