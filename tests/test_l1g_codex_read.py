# tests/test_l1g_codex_read.py — codex READ providers (P3).
#
# The codex ownership + read fan-out surface: owns/context/prompts/conversation
# over a real rollout fixture, title over a fake codex state index, effort over a
# fake config.toml, pending_dialog off the rollout tail, and usage_windows'
# degrade path. Plus the view-mode/sidecar-parity classifier bits that are pure
# Python (actclass codex act + codex prose-drop in scope). Rollout-record PARSING
# has its own suite (test_l1f_codex_rollout.py); this pins the READ MODELS built
# on it.
import json
import os
import sqlite3
import sys

from conftest import REPO

if REPO not in sys.path:
    sys.path.insert(0, REPO)

# A full-uuid rollout under a sessions/ tree — the shape codex actually writes
# (the L1-contracts fixture uses a truncated uuid on purpose, so codex declines
# it; a real rollout carries a full uuid and codex owns it).
_UUID = "0f0f0f0f-0000-4000-8000-000000000042"


def _rollout(tmp_path, records):
    d = tmp_path / ".codex" / "sessions" / "2026" / "07" / "29"
    d.mkdir(parents=True, exist_ok=True)
    p = d / ("rollout-2026-07-29T10-00-00-%s.jsonl" % _UUID)
    with open(p, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
    return str(p)


def _ev(ptype, **payload):
    return {"type": "event_msg", "timestamp": "2026-07-29T10:00:01Z",
            "payload": dict(payload, type=ptype)}


def _resp(ptype, **payload):
    return {"type": "response_item", "timestamp": "2026-07-29T10:00:02Z",
            "payload": dict(payload, type=ptype)}


def _full_rollout(tmp_path):
    return _rollout(tmp_path, [
        {"type": "session_meta", "payload": {"cwd": "/w", "originator": "codex-tui"}},
        {"type": "turn_context", "timestamp": "2026-07-29T10:00:00Z",
         "payload": {"model": "gpt-5.1-codex", "effort": "high"}},
        _resp("message", role="user",
              content=[{"type": "input_text", "text": "fix the parser"}]),
        _resp("reasoning", summary=[{"type": "summary_text", "text": "let me look"}]),
        _resp("message", role="assistant",
              content=[{"type": "output_text", "text": "done, fixed it"}]),
        # a re-injected context block as a user message — SYNTHETIC, dropped
        _resp("message", role="user",
              content=[{"type": "input_text", "text": "<environment_context>x"}]),
        _ev("token_count", info={"total_token_usage": {"input_tokens": 9000,
                                                       "cached_input_tokens": 1000,
                                                       "output_tokens": 500},
                                 "last_token_usage": {"total_tokens": 13600},
                                 "model_context_window": 272000}),
    ])


# ------------------------------------------------------------------ ownership

def test_codex_owns_real_rollouts_only(tmp_path):
    import plugins
    from plugins.codex import rollout as RO
    p = _full_rollout(tmp_path)
    assert RO.owns(p) is True
    assert plugins.owns_by(p) == "codex"
    # a Claude-shaped bare-uuid transcript is not ours; nor a non-rollout file
    # under sessions/, nor a rollout- file NOT under a sessions/ tree
    assert RO.owns(str(tmp_path / "abc.jsonl")) is False
    assert RO.owns(str(tmp_path / "sessions" / "notes.jsonl")) is False
    assert RO.owns(str(tmp_path / "rollout-x-stray.jsonl")) is False
    assert RO.owns("") is False


def test_codex_host_caps_reflect_wired_gestures(tmp_path):
    """codex is a launchable HOST that drives its SUPPORTED gestures (P5):
    interrupt/compact/rename/ask read True (the dashboard un-greys those buttons),
    plus `plan` (plan-mode DECISION picker) and `model`/`effort` (the interactive
    /model picker — plugins/codex/modeldialog.py), while the gestures codex cannot
    drive — rewind/migrate — stay inert and read False (greyed). `send` is not a
    gesture (never caps-gated). The caps are DERIVED from which methods CodexHost
    overrides, not an authored dict (plugins.host), so this pins the derivation
    end-to-end."""
    import plugins
    h = plugins.host_named("codex")
    assert h is not None and h.name == "codex" and h.launchable is True
    assert h.label == "Codex"
    assert h.caps() == {"interrupt": True, "send": False, "rename": True,
                        "rewind": False, "migrate": False, "compact": True,
                        "model": True, "effort": True, "ask": True,
                        "plan": True}
    assert list(h.model_choices())[0] == "gpt-5.6-sol"
    assert "xhigh" in h.effort_choices()
    assert h.resume_words("sid7") == ["resume", "sid7"]
    p = _full_rollout(tmp_path)
    assert plugins.host_of(p).name == "codex"
    assert "codex" in {x["name"] for x in plugins.hosts()}


# ------------------------------------------------------------------ ctx / prompts

def test_codex_context_reads_last_turn_over_window(tmp_path):
    import plugins
    p = _full_rollout(tmp_path)
    ctx = plugins.context(p)
    assert ctx == {"used": 13600, "window": 272000,
                   "pct": 13600 * 100 // 272000, "model": "gpt-5.1-codex",
                   "effort": "high"}    # effort rides the ctx (the ✧ button's source)
    # a rollout with no token_count yields nothing (fresh run)
    bare = _rollout(tmp_path, [{"type": "session_meta", "payload": {"cwd": "/w"}}])
    assert plugins.context(bare) is None


def test_codex_prompts_counts_non_synthetic_user_turns(tmp_path):
    import plugins
    p = _full_rollout(tmp_path)
    # one real user turn; the <environment_context> re-injection is synthetic
    assert plugins.prompts(p) == 1


# ------------------------------------------------------------------ conversation

def test_codex_conversation_standalone(tmp_path, monkeypatch):
    # A STANDALONE codex session's OWN conversation, from its rollout. Its prose
    # OPS are dropped from the session view (op_items codex_lead) and RE-BUBBLED
    # from here instead — so a codex session reads as ordinary conversation, not
    # "ran N codex runs" (docs/codex.md *Standalone mirror parity*).
    from core import sessionapi as API
    from plugins.codex import read
    p = _full_rollout(tmp_path)
    monkeypatch.setattr(API, "session_row", lambda sid: {"transcript_path": p})
    recs, _pos = read.conversation("sid1", 0, "")
    kinds = [(r["kind"], r["text"]) for r in recs]
    # user -> prompt, reasoning -> message, assistant -> message; synthetic dropped
    assert kinds == [("prompt", "fix the parser"),
                     ("message", "let me look"),
                     ("message", "done, fixed it")]
    # the assistant bubbles are authored "codex" (the reply must NOT read
    # "claude" — msg_html's default); the user prompt has no author override
    who = {(r["kind"], r["text"]): r.get("who") for r in recs}
    assert who[("message", "done, fixed it")] == "codex"
    assert who[("prompt", "fix the parser")] is None


def test_codex_conversation_reads_event_msg_register_deduped(tmp_path, monkeypatch):
    # Codex writes a turn in BOTH registers — event_msg (user_message /
    # agent_message) AND response_item (message). An interactive `codex` often
    # writes ONLY the event_msg one, so reading just response_item (the old code)
    # returned NOTHING and the web showed no messages. Read both, de-doubled by
    # text so a turn present in both bubbles ONCE.
    from core import sessionapi as API
    from plugins.codex import read
    p = _rollout(tmp_path, [
        _ev("user_message", message="hi there"),
        _resp("message", role="user",
              content=[{"type": "input_text", "text": "hi there"}]),   # same turn, other register
        _ev("agent_message", message="hello back"),
        _resp("message", role="assistant",
              content=[{"type": "output_text", "text": "hello back"}]),  # dup
    ])
    monkeypatch.setattr(API, "session_row", lambda sid: {"transcript_path": p})
    recs, _pos = read.conversation("sid1", 0, "")
    assert [(r["kind"], r["text"]) for r in recs] == [
        ("prompt", "hi there"), ("message", "hello back")]   # each once, not twice


def test_codex_conversation_sidecar_by_agent_id(tmp_path, monkeypatch):
    import plugins
    from core import sessionapi as API
    p = _full_rollout(tmp_path)
    aid = "rollout-2026-07-29T10-00-00-%s" % _UUID
    monkeypatch.setattr(API, "codex_runs",
                        lambda sid: [{"agent_id": aid, "transcript": p, "kind": "codex"}])
    # claude_code declines a codex agent_id (no such Claude transcript), so the
    # fan-out reaches codex — the SIDECAR → subagent-parity path
    got = plugins.conversation("sid1", 0, aid)
    assert got is not None and got[0], "sidecar codex conversation resolved by agent_id"
    assert got[0][0]["kind"] == "prompt"
    # an unknown agent id resolves to nothing
    assert plugins.conversation("sid1", 0, "nope") is None


# ------------------------------------------------------------------ pending ask

def test_codex_pending_dialog_reads_open_ask(tmp_path, monkeypatch):
    import plugins
    from core import sessionapi as API
    recs = [_resp("function_call", name="request_user_input", call_id="call_9",
                  arguments=json.dumps({"questions": [
                      {"id": "q1", "header": "pick", "question": "which?",
                       "options": [{"label": "a", "description": ""},
                                   {"label": "b", "description": ""}]}]}))]
    p = _rollout(tmp_path, recs)
    monkeypatch.setattr(API, "session_row", lambda sid: {"transcript_path": p})
    got = plugins.pending_dialog("sid1")
    assert got["kind"] == "ask" and got["tool_use_id"] == "call_9"
    assert got["questions"][0]["header"] == "pick"
    assert [o["label"] for o in got["questions"][0]["options"]] == ["a", "b"]
    # an ANSWERED ask (a function_call_output for that call id) is not pending
    recs2 = recs + [_resp("function_call_output", call_id="call_9", output="ok")]
    p2 = _rollout(tmp_path, recs2)
    monkeypatch.setattr(API, "session_row", lambda sid: {"transcript_path": p2})
    assert plugins.pending_dialog("sid1") is None


def test_codex_pending_dialog_reads_open_plan(tmp_path, monkeypatch):
    import plugins
    from core import sessionapi as API
    recs = [_ev("task_started", collaboration_mode_kind="plan"),
            _ev("item_completed",
                item={"type": "Plan", "id": "p-9", "text": "# Plan\n1. do X\n"}),
            _ev("task_complete")]
    p = _rollout(tmp_path, recs)
    monkeypatch.setattr(API, "session_row", lambda sid: {"transcript_path": p})
    got = plugins.pending_dialog("sid1")
    assert got["kind"] == "plan" and got["plan_id"] == "p-9"
    assert "do X" in got["plan"]
    # the static APPROVE options ride along (the card paints them without a
    # screen read), keep-planning excluded
    assert [o["label"] for o in got["options"]] == [
        "Yes, implement this plan", "Yes, clear context and implement"]
    # a DECIDED plan (a newer turn started after it) is no longer pending
    recs2 = recs + [_ev("task_started", collaboration_mode_kind="default")]
    p2 = _rollout(tmp_path, recs2)
    monkeypatch.setattr(API, "session_row", lambda sid: {"transcript_path": p2})
    assert plugins.pending_dialog("sid1") is None


# ------------------------------------------------------ P5 control GESTURES

def _ask_recs():
    return [_resp("function_call", name="request_user_input", call_id="call_9",
                  arguments=json.dumps({"questions": [
                      {"id": "q1", "header": "pick", "question": "which?",
                       "options": [{"label": "a", "description": ""},
                                   {"label": "b", "description": ""}]}]}))]


class _KeyFE:
    """A minimal Frontend for the codex gestures: records send_key/paste_text and,
    on an Escape, appends whatever rollout lines `on_esc` yields (simulating codex
    writing its `turn_aborted` record after the press)."""

    def __init__(self, rollout=None, on_esc=None):
        self.rollout, self.on_esc = rollout, on_esc
        self.keys, self.pastes = [], []

    def send_key(self, win, *keys):
        self.keys.append(keys)
        if keys and keys[0] == "escape" and self.rollout and self.on_esc:
            with open(self.rollout, "a", encoding="utf-8") as fh:
                for rec in self.on_esc():
                    fh.write(json.dumps(rec) + "\n")
        return True

    def paste_text(self, win, text):
        self.pastes.append(text)
        return True


def test_codex_interrupt_verifies_turn_aborted(tmp_path):
    from plugins.codex import hostctl
    p = _rollout(tmp_path, [{"type": "session_meta", "payload": {"cwd": "/w"}}])
    fe = _KeyFE(rollout=p, on_esc=lambda: [_ev("turn_aborted")])
    res = hostctl.CodexHost().interrupt(fe, "42", {"rollout": p})
    assert res["status"] == "acknowledged"
    assert res["ok"] and res["verified"] and not res["steered"]
    assert res["tries"] == 1 and ("escape",) in fe.keys


def test_codex_interrupt_reports_steer(tmp_path):
    """A queued message delivered right after the abort (task_started + prompt) is
    a STEER — reported so the ⧗ chip drains via conversation reconciliation."""
    from plugins.codex import hostctl
    p = _rollout(tmp_path, [{"type": "session_meta", "payload": {"cwd": "/w"}}])
    fe = _KeyFE(rollout=p, on_esc=lambda: [_ev("turn_aborted"),
                                           _ev("task_started"),
                                           _ev("user_message", message="go on")])
    res = hostctl.CodexHost().interrupt(fe, "42", {"rollout": p})
    assert res["status"] == "acknowledged" and res["verified"] and res["steered"]


def test_codex_interrupt_indeterminate_without_record(tmp_path, monkeypatch):
    """Esc landed but no turn_aborted appeared → INDETERMINATE (audited)."""
    from plugins.codex import hostctl
    monkeypatch.setattr(hostctl, "INTERRUPT_VERIFY_S", 0.05)
    monkeypatch.setattr(hostctl, "INTERRUPT_POLL_S", 0.01)
    monkeypatch.setattr(hostctl, "INTERRUPT_TRIES", 1)
    p = _rollout(tmp_path, [{"type": "session_meta", "payload": {"cwd": "/w"}}])
    fe = _KeyFE(rollout=p, on_esc=lambda: [])   # nothing written
    res = hostctl.CodexHost().interrupt(fe, "42", {"rollout": p})
    assert res["status"] == "indeterminate" and res["ok"] and not res["verified"]


class _MultiQFE:
    """A reactive codex request_user_input dialog with TWO questions, matching the
    live geometry: a `Question N/M` header, a `›` cursor DOWN/UP walks, and — on
    the LAST question — the footer switches from `enter to submit answer` to
    `enter to submit all` (the drift that made the driver bail early before FOOT
    matched the `to submit` stem). ENTER on Q1 advances; ENTER on Q2 closes."""

    def __init__(self):
        self.n, self.cursor, self.picked = 1, 1, {}
        self._closed = False

    def get_text(self, win, extent="screen", ansi=False):
        if ansi:
            return ""
        if self._closed:
            return "Questions 2/2 answered\n❯ \n[gpt-5.6-sol] │ ready\n"
        opts = ["a", "b"] if self.n == 1 else ["c", "d"]
        lines = ["Question %d/2 (%d unanswered)" % (self.n, 3 - self.n)]
        for i, label in enumerate(opts, 1):
            mark = "› " if i == self.cursor else "  "
            lines.append("%s%d. %s   desc" % (mark, i, label))
        foot = "submit answer" if self.n == 1 else "submit all"
        lines.append("tab to add notes | enter to %s | ←/→ to navigate | esc" % foot)
        return "\n".join(lines)

    def send_key(self, win, *keys):
        k = keys[0] if keys else ""
        if k == "down":
            self.cursor = min(self.cursor + 1, 2)
        elif k == "up":
            self.cursor = max(self.cursor - 1, 1)
        elif k == "enter":
            self.picked[self.n] = self.cursor
            if self.n == 1:
                self.n, self.cursor = 2, 1     # advance to Q2
            else:
                self._closed = True            # submit all
        return True

    def send_text(self, win, text):
        return True


def test_codex_ask_driver_covers_all_questions_incl_submit_all(monkeypatch):
    """A SINGLE drive() call answers BOTH questions of a multi-question codex
    dialog, following the footer's switch to `submit all` on the last one (the
    live bug the plan-with-questions experiment surfaced). Q1←'b', Q2←'c'."""
    from plugins.codex import dialog
    fe = _MultiQFE()
    questions = [
        {"id": "q1", "header": "h1", "question": "one?",
         "options": [{"label": "a"}, {"label": "b"}]},
        {"id": "q2", "header": "h2", "question": "two?",
         "options": [{"label": "c"}, {"label": "d"}]}]
    answers = [{"selected": ["b"], "other": ""},
               {"selected": ["c"], "other": ""}]
    assert dialog.drive(fe, "9", questions, answers, sleep=lambda s: None) \
        == {"submitted": True}
    assert fe.picked == {1: 2, 2: 1} and fe._closed   # b (row2), c (row1), closed


def test_codex_compact_and_rename_paste(tmp_path):
    from plugins.codex import hostctl
    h = hostctl.CodexHost()
    fe = _KeyFE()
    assert h.compact(fe, "42", {})["status"] == "acknowledged"
    assert fe.pastes == ["/compact"]
    r = h.rename("sid1", "new name", {"fe": fe, "win": "42"})
    assert r["status"] == "acknowledged" and r["ok"]
    assert fe.pastes[-1] == "/rename new name"
    # no window → REJECTED (the caller falls back to the parked rename path)
    assert h.rename("sid1", "x", {})["status"] == "rejected"


class _DialogFE:
    """A stateful fake codex question dialog: a `›` cursor over two options that
    DOWN/UP move and ENTER submits (closing the dialog)."""

    def __init__(self):
        self.cursor, self.closed, self.keys = 1, False, []

    def get_text(self, win, extent="screen", ansi=False):
        if self.closed:
            return "codex done\n❯ \n[gpt-5.1-codex] │ ready\n"
        lines = ["Question 1/1 (1 unanswered)"]
        for i, label in enumerate(("Apple", "Banana"), 1):
            mark = "› " if i == self.cursor else "  "
            lines.append("%s%d. %s   a short description" % (mark, i, label))
        lines.append("tab to add notes | enter to submit answer | esc to interrupt")
        return "\n".join(lines)

    def send_key(self, win, *keys):
        self.keys.append(keys)
        k = keys[0] if keys else ""
        if k == "down":
            self.cursor = min(self.cursor + 1, 2)
        elif k == "up":
            self.cursor = max(self.cursor - 1, 1)
        elif k == "enter":
            self.closed = True
        return True


def test_codex_dialog_driver_selects_and_submits():
    from plugins.codex import dialog
    fe = _DialogFE()
    qs = [{"id": "q1", "header": "pick", "question": "which?",
           "options": [{"label": "Apple"}, {"label": "Banana"}]}]
    res = dialog.drive(fe, "42", qs, [{"selected": ["Banana"], "other": ""}],
                       sleep=lambda s: None)
    assert res == {"submitted": True}
    assert fe.cursor == 2 and fe.closed   # cursored onto Banana, then Enter


def test_codex_dialog_driver_bails_open_with_no_dialog():
    from plugins.codex import dialog

    class Blank:
        def get_text(self, win, extent="screen", ansi=False):
            return "just a shell prompt\n❯ \n"
        def send_key(self, win, *keys):
            return True

    try:
        dialog.drive(Blank(), "42", [{"options": []}], [{"selected": []}],
                     sleep=lambda s: None)
        raise AssertionError("expected CodexAskError")
    except dialog.CodexAskError as e:
        assert e.step == "open"


def test_codex_host_ask_gesture_wraps_the_driver():
    from plugins.codex import hostctl
    fe = _DialogFE()
    qs = [{"id": "q1", "header": "pick",
           "options": [{"label": "Apple"}, {"label": "Banana"}]}]
    res = hostctl.CodexHost().ask(fe, "42", [{"selected": ["Apple"], "other": ""}],
                                  {"questions": qs})
    assert res["status"] == "acknowledged" and res["ok"]
    assert fe.closed


def test_ask_pending_surfaces_codex_dialog(tmp_path, monkeypatch):
    """The dashboard's ask source is host-aware: a codex session with an open
    request_user_input surfaces through plugins.pending_dialog into the SAME
    ask_pending the card + post_answer read (no claude ask-pending kv)."""
    import plugins
    from core import sessionapi as API
    from dashboard.read import session as rsession
    p = _rollout(tmp_path, _ask_recs())
    monkeypatch.setattr(API, "session_row", lambda sid: {"transcript_path": p})
    monkeypatch.setattr(rsession, "session_kv", lambda sid, key, sdb=None: None)
    got = rsession.ask_pending("sid1")
    assert got and got["kind"] == "ask" and got["tool_use_id"] == "call_9"
    # a CLAUDE session (empty/unknown path → DEFAULT_HOST) never reads the rollout
    monkeypatch.setattr(API, "session_row", lambda sid: {"transcript_path": ""})
    called = {"n": 0}
    monkeypatch.setattr(plugins, "pending_dialog",
                        lambda sid: called.__setitem__("n", called["n"] + 1))
    assert rsession.ask_pending("sid1") is None and called["n"] == 0


# ------------------------------------------------------------------ title

def _fake_state_index(tmp_path, uuid, title):
    d = tmp_path / ".codex"
    d.mkdir(parents=True, exist_ok=True)
    db = d / "state_5.sqlite"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE threads(id TEXT PRIMARY KEY, title TEXT)")
    conn.execute("INSERT INTO threads(id, title) VALUES(?,?)", (uuid, title))
    conn.commit()
    conn.close()
    return str(d)


def test_codex_title_from_state_index(tmp_path, monkeypatch):
    import plugins
    from plugins.codex import title
    p = _full_rollout(tmp_path)
    monkeypatch.setattr(title, "_CODEX_DIR",
                        _fake_state_index(tmp_path, _UUID, "My Codex Session"))
    assert plugins.session_title(p) == "My Codex Session"
    assert plugins.title_and_rename(p) == ("My Codex Session", "")
    assert plugins.renameable(p) is True


def test_codex_title_falls_back_to_first_prompt(tmp_path, monkeypatch):
    import plugins
    from plugins.codex import title
    p = _full_rollout(tmp_path)
    # a state index with no matching row -> first real user prompt
    monkeypatch.setattr(title, "_CODEX_DIR",
                        _fake_state_index(tmp_path, "other-uuid", "x"))
    assert plugins.session_title(p) == "fix the parser"


def test_codex_set_session_title_writes_the_index(tmp_path, monkeypatch):
    import plugins
    from plugins.codex import title
    p = _full_rollout(tmp_path)
    monkeypatch.setattr(title, "_CODEX_DIR",
                        _fake_state_index(tmp_path, _UUID, "old"))
    assert plugins.set_session_title(p, "renamed via web") is True
    monkeypatch.setattr(title, "_CODEX_DIR",
                        os.path.join(str(tmp_path), ".codex"))
    assert plugins.session_title(p) == "renamed via web"
    # a non-codex path is declined (no write)
    assert plugins.set_session_title(str(tmp_path / "x.jsonl"), "no") is None


# ------------------------------------------------------------------ effort

def test_codex_declines_the_cwd_keyed_effort_default(monkeypatch):
    """codex provides NO effort_default: that fan-out is cwd-keyed and takes the
    first TRUTHY answer, so a codex read of the global ~/.codex/config.toml would
    leak its effort into a CLAUDE session (a Claude opus agent card read 'low' off
    this machine's codex config). A codex session's effort is a per-turn rollout
    fact (context() from turn_context.effort), and the ✧ button is capability-gated
    off for codex — so codex must not answer this global fan-out at all."""
    import plugins
    from plugins import codex
    assert not hasattr(codex, "effort_default")
    # the fan-out still works for claude_code and never resolves to codex
    assert plugins.provider(codex, "effort_default") is None


# ------------------------------------------------------------------ slash commands

def test_codex_slash_commands_are_codex_vocabulary(monkeypatch, tmp_path):
    """A codex session's "/" menu is codex's OWN commands (/plan, /approvals, …),
    never Claude's (/goal, /rewind, /agents) — the reported '/plan not
    recognized' gap. Custom user prompts ($CODEX_HOME/prompts/*.md) layer in."""
    from plugins.codex import commands
    home = tmp_path / "codexhome"
    (home / "prompts").mkdir(parents=True)
    (home / "prompts" / "shipit.md").write_text(
        "---\ndescription: ship the release\n---\nbody\n")
    monkeypatch.setenv("CODEX_HOME", str(home))
    rows = {c["name"]: c for c in commands.slash_commands("")}
    assert rows["plan"]["src"] == "built-in"
    assert "approvals" in rows and "review" in rows and "usage" in rows
    assert "goal" not in rows and "rewind" not in rows   # never Claude's
    assert rows["shipit"] == {"name": "shipit", "desc": "ship the release",
                              "src": "user"}
    names = [c["name"] for c in commands.slash_commands("")]
    assert names == sorted(names)


def test_codex_effort_provider_reads_the_last_turn_context(tmp_path):
    """plugins.effort (path-keyed, ownership-gated) returns a codex rollout's
    last turn_context level even with NO usage record (unlike context()), so the
    ✧ button never falls back to Claude's cwd-keyed effort_default (the reported
    `high` on a `low` codex run). A Claude/unknown path → '' (no codex provider)."""
    import plugins
    p = _rollout(tmp_path, [
        {"type": "session_meta", "payload": {"cwd": "/w"}},
        {"type": "turn_context", "payload": {"model": "gpt-5.6-sol",
                                             "effort": "low"}}])
    assert plugins.effort(p) == "low"
    # context() alone is None here (no token_count), proving effort is a
    # separate, usage-independent read
    from plugins.codex import read as RD
    assert RD.context(p) is None and RD.codex_effort(p) == "low"
    # a non-codex path is not claimed → ''
    assert plugins.effort(str(tmp_path / "nope.jsonl")) == ""


def test_codex_effort_prefers_newest_picker_change_over_turn_context(tmp_path):
    """A /model change writes a `thread_settings_applied` but NO turn_context
    (that is per-turn) — so after a switch made without running a turn, the
    settings record is FRESHER. context()/codex_effort take the newest of the
    two, else the header lagged (the reported `terra high` picker state read as a
    stale `sol high` from the last turn_context)."""
    import plugins
    p = _rollout(tmp_path, [
        {"type": "session_meta", "payload": {"cwd": "/w"}},
        # the one turn that ran, at sol/high
        {"type": "turn_context", "payload": {"model": "gpt-5.6-sol",
                                             "effort": "high"}},
        {"type": "event_msg", "payload": {"type": "token_count",
            "info": {"total_token_usage": {"input_tokens": 1000},
                     "last_token_usage": {"total_tokens": 1000},
                     "model_context_window": 100000}}},
        # then the user switched model+level via the picker — settings only
        {"type": "event_msg", "payload": {
            "type": "thread_settings_applied",
            "thread_settings": {"model": "gpt-5.6-terra",
                                "reasoning_effort": "low"}}}])
    from plugins.codex import read as RD
    assert RD.codex_effort(p) == "low"                 # the picker's, not sol/high
    ctx = RD.context(p)
    assert ctx["model"] == "gpt-5.6-terra" and ctx["effort"] == "low"
    assert plugins.effort(p) == "low"


def test_slash_commands_fan_out_is_host_scoped():
    """plugins.slash_commands routes to exactly the OWNING host, not a concat: a
    codex session gets codex's list, a Claude one gets Claude's, an unknown host
    an empty menu (never another tool's), and host=None defaults to Claude (the
    new-session form's tool today)."""
    import plugins
    cx = {c["name"] for c in plugins.slash_commands("", "codex")}
    cc = {c["name"] for c in plugins.slash_commands("", "claude_code")}
    assert "plan" in cx and "goal" not in cx
    assert "goal" in cc and "plan" not in cc
    assert plugins.slash_commands("", "copilot") == []
    assert [c["name"] for c in plugins.slash_commands("")] == \
        [c["name"] for c in plugins.slash_commands("", "claude_code")]


# ------------------------------------------------------------------ usage windows

def test_codex_usage_windows_normalizes(monkeypatch):
    from plugins.codex import usage
    usage._CACHE = None
    monkeypatch.setattr(usage, "_rpc_read_ratelimits", lambda: {
        "rateLimits": {"planType": "pro",
                       "primary": {"usedPercent": 42.0, "windowDurationMins": 300,
                                   "resetsAt": "2026-07-29T15:00:00Z"},
                       "secondary": {"usedPercent": 7.0, "windowDurationMins": 10080,
                                     "resetsAt": None}}})
    out = usage.usage_windows()
    assert out["planType"] == "pro"
    assert [w["used_pct"] for w in out["windows"]] == [42.0, 7.0]
    assert out["windows"][0]["window_mins"] == 300


def test_codex_usage_windows_degrades_to_none(monkeypatch):
    from plugins.codex import usage
    usage._CACHE = None
    monkeypatch.setattr(usage, "_rpc_read_ratelimits", lambda: None)
    assert usage.usage_windows() is None       # degrades, never raises


def test_codex_spawn_env_prepends_node_bin_dirs(monkeypatch, tmp_path):
    # The launchd dashboard runs with a STRIPPED PATH (/usr/bin:/bin:…) that can
    # find neither `codex` nor the `node` it shebangs — the "codex missing from
    # the accounts list" bug. codex_spawn_env must PREPEND an existing install
    # dir so the app-server spawn resolves both.
    from plugins.codex import usage
    node_bin = tmp_path / "node-bin"
    node_bin.mkdir()
    monkeypatch.setattr(usage, "CODEX_BIN_DIRS", (str(node_bin), "/no/such/dir"))
    monkeypatch.delenv("CODEX_BIN_DIR", raising=False)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    env = usage.codex_spawn_env()
    parts = env["PATH"].split(os.pathsep)
    assert parts[0] == str(node_bin)            # existing dir prepended
    assert "/no/such/dir" not in parts          # a missing dir is skipped
    assert parts[-2:] == ["/usr/bin", "/bin"]   # the original PATH is preserved


def test_codex_spawn_env_override_wins(monkeypatch, tmp_path):
    from plugins.codex import usage
    over = tmp_path / "custom"
    over.mkdir()
    monkeypatch.setattr(usage, "CODEX_BIN_DIRS", ())
    monkeypatch.setenv("CODEX_BIN_DIR", str(over))
    monkeypatch.setenv("PATH", "/usr/bin")
    assert usage.codex_spawn_env()["PATH"].split(os.pathsep)[0] == str(over)


# ------------------------------------------------------------------ view modes / parity

def test_codex_blocks_classify_as_codex_act():
    """A codex chip (codex palette) classifies ACT_CODEX — its own act, so the
    default summary NAMES the codex run instead of folding it into 'ran N agents'
    (deliverable B)."""
    from core import ops as O
    from core import slots as SL
    from dashboard.opshtml import actclass as AC
    codex_rgb = SL.CODEX_PALETTE[0]
    assert AC.classify(O.label("▶ cmd", codex_rgb, g="c1")) == (AC.ACT_CODEX, False)
    assert AC.classify(O.label("✎ message", codex_rgb)) == (AC.ACT_CODEX, False)
    assert AC.ACT_CODEX in AC.ACTS


def test_codex_prose_drops_in_scope_only_for_rollout_backed_runs():
    """Sidecar parity: a rollout-backed codex run's prose ops drop in scope (its
    bubbles come from conversation()), a companion .log run's stay (no rollout to
    re-bubble from) — keyed on the `codexprose:<label>` scope marker
    (deliverable C)."""
    from core import ops as O
    from core import slots as SL
    from dashboard.opshtml import actclass as AC
    rgb = SL.CODEX_PALETTE[0]
    msg = O.label("✎ message", rgb, g="c1")
    msg["src"] = "codex:cli"
    think = O.label("⋯ reasoning", rgb, g="c2")
    think["src"] = "codex:cli"
    cmd = O.label("▶ cmd", rgb, g="c3")
    cmd["src"] = "codex:cli"
    rollout_scope = {"codex:cli", "codexprose:cli"}
    companion_scope = {"codex:cli"}                 # no prose marker
    # rollout-backed: prose drops, command stays
    assert AC.prose_block(msg, rollout_scope) is True
    assert AC.prose_block(think, rollout_scope) is True
    assert AC.prose_block(cmd, rollout_scope) is False
    # companion: nothing drops (no bubbles to replace it)
    assert AC.prose_block(msg, companion_scope) is False
    assert AC.prose_block(think, companion_scope) is False
    # no scope (session view) never drops a codex prose op
    assert AC.prose_block(msg, None) is False


def test_op_items_codex_lead_drops_prose_and_chrome_keeps_activity():
    """A STANDALONE codex session's SESSION view (codex_lead=True): op_items
    drops the PROSE ops (⇢/✎ header + body, re-bubbled via conversation) and the
    codex CHROME (the `codex ▶ <label>` banner + `⚙ model` tag) — so the view is
    bubbles + real activity + footer, never "ran N codex runs". Command / file /
    footer ops STAY. Without codex_lead nothing is dropped (a sidecar or
    non-codex host)."""
    import re
    from core import ops as O, render as R, slots as SL
    from dashboard import opshtml
    rgb = SL.CODEX_PALETTE[0]
    ops = [
        O.label("codex ▶ cli", rgb),                                   # chrome
        O.gut(R.fg(*O.SLATE) + "⚙ gpt-5-codex · low" + R.RST, rgb),    # chrome
        dict(O.label("⇢ prompt", rgb, g="b1"), who="codex"),           # prose hdr
        O.gut("hi there", rgb, g="b1"),                                # prose body
        dict(O.label("✎ message", rgb, g="b2"), who="codex"),          # prose hdr
        O.gut("hello", rgb, g="b2"),                                   # prose body
        dict(O.label("▶ cmd", rgb, g="b3"), who="codex"),              # command — KEPT
        O.code("ls -la", g="b3"),
        O.label("■ codex cli ended · 1.0s", rgb),                      # footer — DROPPED
    ]
    items = opshtml.op_items(ops, "k", codex_lead=True)
    txt = " ".join(re.sub("<[^>]+>", "", it.get("html", "") or "") for it in items)
    assert "prompt" not in txt and "message" not in txt          # prose headers gone
    assert "hi there" not in txt and "hello" not in txt          # prose bodies gone
    assert "codex ▶" not in txt and "⚙" not in txt               # chrome gone
    assert "ls -la" in txt                                        # command kept
    assert "ended" not in txt                                     # footer chrome gone too
    # a non-codex-host view (codex_lead False) keeps everything
    assert len(opshtml.op_items(ops, "k", codex_lead=False)) > len(items)
