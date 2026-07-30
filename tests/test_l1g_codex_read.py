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
    drive — rewind/migrate — stay inert and read False (greyed). `send` reads True
    too, and is the one cap nothing GATES: the composer is always reachable, but
    P2 made the delivery itself host-routed (a plain bracketed paste here, with no
    clipboard-image wipe and no line-kill), so the host has a real body and the
    derivation reports it. The caps are DERIVED from which methods CodexHost
    overrides, not an authored dict (plugins.host), so this pins the derivation
    end-to-end."""
    import plugins
    h = plugins.host_named("codex")
    assert h is not None and h.name == "codex" and h.launchable is True
    # `HostControl.label` names the TOOL in the new-session picker and stays
    # "Codex" — deliberately NOT the usage strip's lowercase "codex · plus" row
    # name, which names an ACCOUNT-shaped reading beside Claude's slug rows.
    assert h.label == "Codex"
    assert h.caps() == {"interrupt": True, "send": True, "rename": True,
                        "rewind": False, "migrate": False, "compact": True,
                        "model": True, "effort": True, "ask": True,
                        "plan": True}
    assert list(h.model_choices())[0] == "gpt-5.6-sol"
    assert "xhigh" in h.effort_choices()
    # …and the resume argv is composed by launch_words, the ONE seam for it
    assert h.launch_words({"resume": "sid7"}) == ["resume", "sid7"]
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
    # OPS are dropped from the session view (op_items host_lead) and RE-BUBBLED
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


def test_codex_conversation_drops_system_scaffolding_keeps_input_output(tmp_path, monkeypatch):
    # A subagent's conversation must read like Claude's: only the real input +
    # assistant output, never codex's system scaffolding (docs/codex.md *Two
    # registers*). The role=developer block and the role=user `<recommended_plugins>`
    # wrapper are dropped structurally; a `<task>` INPUT wrapper is kept, unwrapped.
    from core import sessionapi as API
    from plugins.codex import read
    p = _rollout(tmp_path, [
        _resp("message", role="developer",
              content=[{"type": "input_text", "text": "<multi_agent_mode>\nscaffold"}]),
        _resp("message", role="user",
              content=[{"type": "input_text", "text": "<recommended_plugins>\nlist…"}]),
        _resp("message", role="user",
              content=[{"type": "input_text", "text": "<task>\nGet the weather in Bali\n</task>"}]),
        _resp("message", role="assistant",
              content=[{"type": "output_text", "text": "Bali is 27°C."}]),
    ])
    monkeypatch.setattr(API, "session_row", lambda sid: {"transcript_path": p})
    recs, _pos = read.conversation("sid1", 0, "")
    assert [(r["kind"], r["text"]) for r in recs] == [
        ("prompt", "Get the weather in Bali"),   # <task> kept + unwrapped
        ("message", "Bali is 27°C.")]            # assistant output
    # no scaffolding text anywhere in the bubbles
    blob = " ".join(r["text"] for r in recs)
    assert "recommended_plugins" not in blob and "multi_agent_mode" not in blob


def test_codex_conversation_sidecar_by_agent_id(tmp_path, monkeypatch):
    import plugins
    from plugins.codex import nested as CR
    p = _full_rollout(tmp_path)
    aid = "rollout-2026-07-29T10-00-00-%s" % _UUID
    monkeypatch.setattr(CR, "session_runs",
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


def test_a_codex_rename_moves_the_title_memo_key(tmp_path, monkeypatch):
    """THE codex-rename staleness bug: the name lives in codex's state index,
    so a rename leaves the ROLLOUT byte-identical — and the read model's title
    memo is keyed on (path, SIZE). The list page served the pre-rename title
    forever (measured: threads.title said `test`, the page did not).

    The fix is that the memo's freshness key comes from the OWNING HOST. This
    is the fixture the bug needs: an index that CHANGES while the transcript
    does not."""
    import plugins
    from dashboard.read import meta
    from plugins.codex import title
    p = _full_rollout(tmp_path)
    d = _fake_state_index(tmp_path, _UUID, "before")
    monkeypatch.setattr(title, "_CODEX_DIR", d)
    title._STATE_DB.clear()             # the resolved-path memo is per-dir
    size = os.path.getsize(p)
    assert meta.session_title(p) == "before"

    # rename THROUGH the index, exactly as the parked write does
    sig = title.state_sig()
    assert plugins.set_session_title(p, "after") is True
    assert os.path.getsize(p) == size   # the rollout never moved — the bug
    assert title.state_sig() != sig     # …but the host's stamp did
    assert meta.session_title(p) == "after"


def test_the_title_memo_is_unchanged_for_a_host_with_no_stamp(tmp_path):
    """…and the base "" stamp keeps the (path, size) memo exactly as it was:
    one compute per size, no re-read per call. Claude Code answers "" because
    for it the transcript really is the whole story."""
    from dashboard.read import cache
    from core import sessionapi as API
    memo = API.BoundedLRU(8)
    f = tmp_path / "t.jsonl"
    f.write_text("a\n")
    n = {"calls": 0}

    def compute():
        n["calls"] += 1
        return "T%d" % n["calls"]

    assert cache.size_cached(memo, str(f), compute, empty="") == "T1"
    assert cache.size_cached(memo, str(f), compute, empty="") == "T1"
    assert n["calls"] == 1                       # memoised on size alone
    assert cache.size_cached(memo, str(f), compute, empty="", sig="s1") == "T2"
    assert cache.size_cached(memo, str(f), compute, empty="", sig="s1") == "T2"
    assert cache.size_cached(memo, str(f), compute, empty="", sig="s2") == "T3"
    assert n["calls"] == 3                       # a moved stamp re-computes


def test_the_state_index_path_memo_never_caches_a_negative(tmp_path,
                                                            monkeypatch):
    """The resolved index PATH is TTL-memoised (only a codex upgrade changes
    which numbered file is newest), but "there is no index yet" must NOT be —
    a first codex run, or any fixture that writes the file after the first
    read, would otherwise be invisible for a whole TTL."""
    from plugins.codex import title
    d = tmp_path / ".codex"
    d.mkdir()
    monkeypatch.setattr(title, "_CODEX_DIR", str(d))
    title._STATE_DB.clear()
    assert title.state_sig() == ""               # nothing there yet
    _fake_state_index(tmp_path, _UUID, "x")      # …and now there is
    assert title.state_sig() != ""


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


class _LevelPickerFE:
    """A reactive /model picker whose Step 3 COLLAPSES Max/Ultra behind a
    `More reasoning…` row (gpt-5.6-terra's shape), opening an `Advanced Reasoning`
    sub-step — the geometry the reported effort→max failure hit. Starts already
    on Step 3 (the test drives only _pick_level). `chosen` records the final pick."""

    def __init__(self):
        self.view, self.cursor, self.chosen = "level", 1, None

    _LEVEL = ("Low", "Medium (default)", "High", "Extra high", "More reasoning…")
    _ADV = ("Max", "Ultra")

    def _rows(self):
        return self._LEVEL if self.view == "level" else self._ADV

    def get_text(self, win, extent="screen", ansi=False):
        if ansi or self.view == "done":
            return "codex\n❯ \n[gpt-5.6-terra] │ ready\n"
        head = "Select Reasoning Level for gpt-5.6-terra" \
            if self.view == "level" else "Advanced Reasoning"
        lines = ["  " + head]
        for i, label in enumerate(self._rows(), 1):
            lines.append(("› " if i == self.cursor else "  ")
                         + "%d. %s   desc" % (i, label))
        lines.append("  Press enter to confirm or esc to go back")
        return "\n".join(lines)

    def send_key(self, win, *keys):
        k = keys[0] if keys else ""
        if k == "down":
            self.cursor = min(self.cursor + 1, len(self._rows()))
        elif k == "up":
            self.cursor = max(self.cursor - 1, 1)
        elif k == "enter":
            label = self._rows()[self.cursor - 1]
            if self.view == "level" and label.startswith("More reasoning"):
                self.view, self.cursor = "advanced", 1     # open the sub-step
            else:
                self.chosen, self.view = label, "done"
        return True


class _ShortLevelFE:
    """A /model picker Step 3 for a model with only Low/Medium/High and NO
    `More reasoning…` row (gpt-5.4's shape — it has no Ultra). `entered` records
    each Enter's cursor row so a test can see whether a level or the default was
    taken."""

    _LEVEL = ("Low", "Medium (default)", "High")

    def __init__(self):
        self.cursor, self.entered, self.done = 2, [], False   # default row = Medium

    def get_text(self, win, extent="screen", ansi=False):
        if ansi or self.done:
            return "codex\n❯ \n[gpt-5.4] │ ready\n"
        lines = ["  Select Reasoning Level for gpt-5.4"]
        for i, label in enumerate(self._LEVEL, 1):
            lines.append(("› " if i == self.cursor else "  ")
                         + "%d. %s   desc" % (i, label))
        lines.append("  Press enter to confirm or esc to go back")
        return "\n".join(lines)

    def send_key(self, win, *keys):
        k = keys[0] if keys else ""
        if k == "down":
            self.cursor = min(self.cursor + 1, len(self._LEVEL))
        elif k == "up":
            self.cursor = max(self.cursor - 1, 1)
        elif k == "enter":
            self.entered.append(self._LEVEL[self.cursor - 1])
            self.done = True
        return True


def test_modeldialog_preserve_falls_back_to_default_on_unsupported_level():
    """PRESERVING a level the target model lacks (Ultra → gpt-5.4, which has no
    Ultra and no More-reasoning row) must NOT fail the model switch — strict=False
    accepts the model's DEFAULT. The reported break: model→gpt-5.4 while the
    session was on ultra."""
    import pytest
    from plugins.codex import modeldialog as MD
    fe = _ShortLevelFE()
    MD._pick_level(fe, "9", "Ultra", sleep=lambda s: None, strict=False)
    assert fe.entered == ["Medium (default)"]      # accepted the default, no raise
    # an EXPLICIT ✧ effort for a level the model lacks is strict → raises
    fe2 = _ShortLevelFE()
    with pytest.raises(MD.CodexModelError):
        MD._pick_level(fe2, "9", "Ultra", sleep=lambda s: None, strict=True)
    assert fe2.entered == []                        # nothing selected


def test_modeldialog_reaches_max_behind_more_reasoning():
    """effort→Max/Ultra on a model that collapses them behind `More reasoning…`
    opens the Advanced Reasoning sub-step and picks there — the reported
    'no Max under Select Reasoning Level' failure. A directly-listed level is
    unaffected."""
    from plugins.codex import modeldialog as MD
    fe = _LevelPickerFE()
    MD._pick_level(fe, "9", "Max", sleep=lambda s: None)
    assert fe.chosen == "Max"
    # a directly-listed level needs no sub-step
    fe2 = _LevelPickerFE()
    MD._pick_level(fe2, "9", "High", sleep=lambda s: None)
    assert fe2.chosen == "High"


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


def test_codex_usage_windows_keeps_one_window_when_secondary_is_null():
    """A NULL `secondary` yields ONE window, and it is whatever `primary` holds.

    This is the current plus-plan shape, captured verbatim from a live
    `account/rateLimits/read`: `secondary` is literal JSON null and `primary` is
    the WEEKLY window. So the strip's single codex bar is correct — the reading
    that looked like "the 5h window went missing" (docs/codex.md *One window, not
    two*). The trap this pins is the NAME: `primary`/`secondary` are slots, not
    durations, and codex really did ship a plus period (2026-06-26 → 07-07) where
    primary was the 5h one — which the case below still covers, unchanged.

    Skipping a null slot is right (a null is not a window); DROPPING a window
    that is merely at 0%, or unreadable, would not be — so those are pinned here
    too, since a falsy-check regression is exactly what this bug report would
    have looked like if it were real."""
    from plugins.codex import usage
    rl = {"planType": "plus", "limitId": "codex", "individualLimit": None,
          "primary": {"usedPercent": 4, "windowDurationMins": 10080,
                      "resetsAt": 1785944457},
          "secondary": None}
    got = usage._normalize({"rateLimits": rl})
    assert got == {"planType": "plus", "windows": [
        {"used_pct": 4, "window_mins": 10080, "resets_at": 1785944457}]}
    assert usage.window_rows(got["windows"])[0]["label"] == "7d"

    # 0% is a READING, not an absence — it must survive as 0, never be dropped
    zero = dict(rl, primary=dict(rl["primary"], usedPercent=0))
    assert usage._normalize({"rateLimits": zero})["windows"][0]["used_pct"] == 0
    # an unreadable percentage / duration is kept too — the painter ghosts it
    blank = dict(rl, primary={"usedPercent": None, "windowDurationMins": None,
                              "resetsAt": None})
    rows = usage.window_rows(usage._normalize({"rateLimits": blank})["windows"])
    assert rows[0]["used_pct"] is None and rows[0]["label"] == "primary"
    # only BOTH slots being non-dicts is "nothing to say" (a real historical
    # shape: the June limit_id="premium" readings carried two nulls)
    assert usage._normalize({"rateLimits": {"planType": "", "primary": None,
                                            "secondary": None}}) is None


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


# --------------------------------------------------- per-session rate limits (P3)

def _token_count(rate_limits, total=1000, window=272000):
    """A real-shaped `token_count` event. `info` is null on a RATE-LIMIT-ONLY
    event (codex really emits those), which is why the limits are read on their
    own rather than off the `usage` record — that record needs info."""
    p = {"rate_limits": rate_limits}
    if total is not None:
        p["info"] = {"total_token_usage": {"total_tokens": total},
                     "last_token_usage": {"total_tokens": total},
                     "model_context_window": window}
    else:
        p["info"] = None
    return _ev("token_count", **p)


# The rate_limits block exactly as measured in rollout 019fb363 (2026-07-30):
# snake_case, a WEEKLY primary, a null secondary, an epoch resets_at.
_RL = {"limit_id": "codex", "limit_name": None,
       "primary": {"used_percent": 4.0, "window_minutes": 10080,
                   "resets_at": 1785944457},
       "secondary": None, "credits": {"has_credits": False},
       "plan_type": "plus", "rate_limit_reached_type": None}


def test_codex_usage_probe_takes_the_last_non_null_rate_limits(tmp_path):
    """read.usage scans the tail for the last token_count whose `rate_limits` is
    non-null — NOT simply the last token_count.

    The field is nullable and codex emits usage events without it, so "the
    newest event" and "the newest event that says anything about limits" are
    different records. Reading the newest event would report None for a session
    that has perfectly good limits a few records back."""
    from plugins.codex import read
    path = _rollout(tmp_path, [
        _token_count(dict(_RL, primary=dict(_RL["primary"], used_percent=1.0))),
        _token_count(_RL),                       # the newest reading …
        _token_count(None),                      # … then two that carry none
        _token_count(None, total=None),          # (incl. the info-null shape)
    ])
    got = read.usage(path)
    assert got["planType"] == "plus"
    assert [w["window_mins"] for w in got["windows"]] == [10080]
    assert got["windows"][0]["used_pct"] == 4.0
    assert got["windows"][0]["resets_at"] == 1785944457
    # a rollout with no rate_limits anywhere says so, rather than inventing zeros
    assert read.usage(_rollout(tmp_path, [_token_count(None)])) is None
    assert read.usage("/nope/rollout.jsonl") is None


def test_codex_usage_probe_reads_both_windows(tmp_path):
    """A plan with two windows yields both, primary first — the order codex
    reports them in and the order the strip lays them out."""
    from plugins.codex import read
    rl = dict(_RL, primary={"used_percent": 42.4, "window_minutes": 300,
                            "resets_at": 111},
              secondary={"used_percent": 7.0, "window_minutes": 10080,
                         "resets_at": 222})
    got = read.usage(_rollout(tmp_path, [_token_count(rl)]))
    assert [w["window_mins"] for w in got["windows"]] == [300, 10080]


def test_codex_window_rows_speak_the_shared_vocabulary():
    """codex's windows map into the SAME row shape Claude's do — the point of the
    one vocabulary — and now with the same LABELS: it names a window by its
    DURATION (there is no key like `five_hour` in its payload), and the shared
    table (`plugins.window_label`) spells that duration for every host, so the
    10080-minute window reads "7d" here exactly as it does on a Claude row. It
    used to read "1w"; the strip's columns are keyed by duration, so those two
    bars are one column and it renamed itself halfway down the stack.

    codex's OWN ladder survives only where the shared table has nothing to say
    (1440, 90) — that is the whole remaining per-host part of the label. Every
    codex window is account-wide, so each keeps its reset column; percentages
    are rounded server-side, since only one host reported floats and the painter
    should not have to know which."""
    from plugins.codex import usage
    rows = usage.window_rows([
        {"used_pct": 42.4, "window_mins": 300, "resets_at": 111},
        {"used_pct": 7.0, "window_mins": 10080, "resets_at": 222},
        {"used_pct": None, "window_mins": None, "resets_at": None},
    ])
    assert [r["label"] for r in rows] == ["5h", "7d", "secondary"]
    assert [r["key"] for r in rows] == ["w300", "w10080", "secondary"]
    assert [r["used_pct"] for r in rows] == [42, 7, None]
    assert {r["scope"] for r in rows} == {"account"}
    # the fallback ladder, reachable only for a duration the table does not name
    assert usage.window_label(1440) == "1d" and usage.window_label(90) == "90m"
    assert usage.window_label(20160) == "2w"


def test_codex_strip_row_is_one_host_wide_reading():
    """codex contributes ONE row, not one per account: it has no subscription
    switcher, so `switchable` is False (which is what keeps it out of the
    new-session account picker) and there is no slug. The account-switcher
    fields are served as the honest empty so one painter reads every row the
    same way."""
    from plugins.codex import usage
    row = usage.strip_row({"planType": "plus", "windows": [
        {"used_pct": 4.0, "window_mins": 10080, "resets_at": 1785944457}]})
    assert row["host"] == "codex" and row["switchable"] is False
    assert row["slug"] == "" and row["label"] == "codex · plus"
    assert row["plan"] == "plus"
    assert row["usage"] is None and row["limit_hit"] is None
    assert row["logged_out"] is False
    assert row["windows"][0]["label"] == "7d"   # the SHARED duration word
    # no plan word → just the host name; no windows at all → no row (the strip
    # shows nothing rather than an empty pill)
    assert usage.strip_row({"windows": [{"used_pct": 1, "window_mins": 300,
                                         "resets_at": 0}]})["label"] == "codex"
    assert usage.strip_row({"planType": "plus", "windows": []}) is None
    assert usage.strip_row(None) is None


def test_session_facets_route_to_the_OWNING_host(tmp_path, monkeypatch):
    """plugins.session_usage / session_account / session_costs are sid-keyed and
    routed by OWNERSHIP (_first_owner), so a codex session gets codex's answers.

    This is the whole point of the routing: these providers PARSE, and the
    Claude ones answer confidently about a session they have never seen — its
    `usage` kv is absent (None, fine) but its OTEL sum is a truthful-looking
    ZERO for a run that really did cost something. First-plugin-wins would have
    served that zero."""
    import plugins
    from core import sessionapi as API
    from plugins.codex import usage as CXU
    p = _rollout(tmp_path, [_token_count(_RL)])
    monkeypatch.setattr(API, "session_row", lambda sid: {"transcript_path": p})
    # usage: the rollout's own last reading, in the shared vocabulary — where a
    # PARKED session's limits stood, which the app server can no longer say
    u = plugins.session_usage("cx1")
    assert u["plan"] == "plus"
    assert [w["label"] for w in u["windows"]] == ["7d"]
    assert u["windows"][0]["used_pct"] == 4
    # account: the minimal honest shape for a host with no switcher — no slug
    # (nothing to switch to), just the plan, so the header chip still reads
    assert plugins.session_account("cx1") == {"slug": "", "label": "codex · plus"}
    # costs: codex's OWN priced scoreboard (CODEX_PRICES folded it as the stream
    # read each turn), reported in the same envelope the OTEL side returns —
    # under one query_source named for the host, since codex has no
    # main/subagent/auxiliary split to report
    from core import ops as O
    from core import paths as P
    monkeypatch.setattr(P, "PREFIX", str(tmp_path) + "/claude-mirror-")
    monkeypatch.setattr(P, "HISTORY_DIR", str(tmp_path / "park"))
    log = P.mirror_log("cx1")
    O.bump(log, cost=0.42, tokens=900, tk_in=500, tk_out=400)
    costs = plugins.session_costs("cx1")
    assert costs["total_usd"] == 0.42
    assert costs["cost"] == {"codex": 0.42}
    assert costs["tokens"]["codex"] == {"tk_in": 500, "tk_out": 400}
    assert CXU.HOST == "codex"


def test_codex_session_facets_are_silent_without_a_rollout(tmp_path, monkeypatch):
    """A rollout that reports no limits yields None / {} — not a zeroed reading.
    "We do not know" and "0% used" are different claims, and only one of them is
    true here."""
    import plugins
    from core import sessionapi as API
    p = _rollout(tmp_path, [_token_count(None)])
    monkeypatch.setattr(API, "session_row", lambda sid: {"transcript_path": p})
    assert plugins.session_usage("cx2") is None
    assert plugins.session_account("cx2") == {}


def test_codex_usage_strip_provider_degrades_to_no_row(monkeypatch):
    """An unreachable app server contributes NO row (the strip simply has no
    codex entry) — it never raises into the read-side dashboard, and the degrade
    is audited once inside usage_windows, not here."""
    from plugins.codex import usage
    monkeypatch.setattr(usage, "usage_windows", lambda: None)
    assert usage.usage_strip() == []


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


def test_the_agent_class_is_decided_by_the_src_register_first():
    """WHICH of the three agent classes a block gets is the PRODUCER's `src`
    register, with the palette only as the fallback for ops that carry no stamp.

    That order is what makes one child-agent vocabulary cover two tools: a
    codex-NATIVE subagent stamps `sub:` and classifies as the AGENT it is, while
    `codex:` keeps meaning what it now says — a sidecar codex run inside a Claude
    host. Keyed off the palette instead, a codex child folded into 'ran 1 codex
    run' no matter what it did.

    All six cells pinned: three registers × stamped/unstamped."""
    from core import ops as O
    from core import slots as SL
    from dashboard.opshtml import actclass as AC
    codex_rgb, sub_rgb = SL.CODEX_PALETTE[0], SL.SUB_PALETTE[0]

    def act(rgb, src=None):
        op = O.label("⇠ result", rgb)
        if src:
            op["src"] = src
        return AC.classify(op)[0]

    # STAMPED — the register decides, whatever palette the op wears
    assert act(sub_rgb, "sub:a1") == AC.ACT_AGENT
    assert act(sub_rgb, "team:a1") == AC.ACT_TEAM
    assert act(codex_rgb, "codex:cli") == AC.ACT_CODEX
    # …and a codex-native subagent (SUB palette + `sub:`) is an AGENT, not a codex
    # run — the whole point of the register
    assert act(sub_rgb, "sub:rollout-2026-07-30T22-17-34-019fb363") == AC.ACT_AGENT
    # …while a `codex:`-stamped op stays a codex run even in another palette
    assert act(sub_rgb, "codex:cli") == AC.ACT_CODEX
    # UNSTAMPED (parked history, and a standalone host's own ops) — palette decides
    assert act(codex_rgb) == AC.ACT_CODEX
    assert act(sub_rgb) == AC.ACT_AGENT


def test_as_lead_recolours_a_child_block_by_register_or_palette():
    """…and the scope normalisation follows the same order: a command-family
    header of a STAMPED child is recoloured to the lead's SLATE (so cmd_note and
    classify, both colour-gated, recognise it), with every register's palette as
    the parked-history fallback.

    Both arms are host-BLIND. They were narrower — the two Claude palettes, and a
    `lead` field in the register table naming which registers may recolour — so
    the recolour was an enumeration of known hosts, and a register outside it
    kept the terminal's coloured pill in its own scope while its `gut` file ops
    never became `line` ops (invisible to every view-mode summary). A codex
    SIDECAR was the register left out, and it is included now: in ITS OWN scope
    its blocks read exactly as a Claude child's do."""
    from core import ops as O
    from core import slots as SL
    from dashboard.opshtml import actclass as AC
    stamped = O.label("▶ foreground", SL.CODEX_PALETTE[0], g="b1")
    stamped["src"] = "sub:a1"                       # a child, wrong palette
    assert tuple(AC.as_lead(stamped)["c"]) == tuple(O.SLATE)
    parked = O.label("▶ foreground", SL.SUB_PALETTE[0], g="b1")   # no stamp
    assert tuple(AC.as_lead(parked)["c"]) == tuple(O.SLATE)
    # a codex SIDECAR's command header, in its own scope, now too
    side = O.label("▶ cmd", SL.CODEX_PALETTE[0], g="b1")
    side["src"] = "codex:cli"
    assert tuple(AC.as_lead(side)["c"]) == tuple(O.SLATE)
    # …and so does a host with a register this build has never heard of, off the
    # stamp alone — the third-host arm, with no palette of ours at all
    gem = O.label("▶ foreground", (7, 7, 7), g="b1")
    gem["src"] = "gem:g1"
    assert tuple(AC.as_lead(gem)["c"]) == tuple(O.SLATE)
    # PROSE headers are still never recoloured — only the command family is
    prose = O.label("✎ message", SL.SUB_PALETTE[0], g="b1")
    prose["src"] = "sub:a1"
    assert tuple(AC.as_lead(prose)["c"]) == tuple(SL.SUB_PALETTE[0])


def test_codex_prose_drops_in_scope_via_the_bubbled_flag():
    """Sidecar parity, UNIFIED: a codex sidecar run's prose ops carry the
    producer-set `bubbled` flag (a ROLLOUT-backed run re-bubbles them via
    conversation()), so op_items drops them in agent scope — the SAME signal a
    Claude subagent uses, no per-tool `codexprose:` marker. A companion `.log`
    run's prose has NO bubbled (no rollout to re-bubble from) and stays; a command
    op (never bubbled) stays."""
    from core import ops as O
    from core import slots as SL
    from dashboard import opshtml
    rgb = SL.CODEX_PALETTE[0]
    # a rollout sidecar: prose carries bubbled=1 (stream._ro_message/_ro_reasoning)
    msg = O.label("✎ message", rgb, g="c1", bubbled=True); msg["src"] = "codex:cli"
    think = O.label("⋯ reasoning", rgb, g="c2", bubbled=True); think["src"] = "codex:cli"
    # a companion .log run: prose has NO bubbled; a command is never bubbled
    comp = O.label("✎ message", rgb, g="c4"); comp["src"] = "codex:cli"
    cmd = O.label("▶ cmd", rgb, g="c3"); cmd["src"] = "codex:cli"
    scope = "cli"                                    # agent_scope for aid 'cli'
    def kinds(items):
        return "".join(it.get("html", "") for it in items)
    assert "message" not in kinds(opshtml.op_items([msg], "k", scope=scope))
    assert "reasoning" not in kinds(opshtml.op_items([think], "k", scope=scope))
    assert "message" in kinds(opshtml.op_items([comp], "k", scope=scope))
    assert "cmd" in kinds(opshtml.op_items([cmd], "k", scope=scope))
    # the SESSION view (scope None) keeps a stamped op only when web/unstamped —
    # a bubbled codex sidecar op is stamped and not web, so it drops there too
    assert opshtml.op_items([msg], "k", scope=None) == []


def test_a_standalone_hosts_own_prose_and_chrome_drop_with_no_host_flag():
    """A STANDALONE codex session's SESSION view: op_items drops the PROSE ops
    (⇢/✎ header + body, re-bubbled via conversation) and the codex CHROME (the
    `codex ▶ <label>` banner, the `⚙ model` tag, the run footer) — so the view is
    bubbles + real activity, never "ran N codex runs". Command / file ops STAY.

    With NO per-host flag: this used to need `host_lead=True`, resolved from the
    owning host's `lead_prose` trait and threaded through seven call sites, and
    the whole point of the parameter was that a Claude session must not have
    these ops dropped. It doesn't — the sniffers are PALETTE-gated, so they match
    only codex's own unstamped ops, and a session that never hosted a codex run
    has none. Measured over the 187-session parked corpus: the session view is
    byte-identical for every one of them, the 25 standalone codex sessions
    included.

    The ops here are all PARKED-shaped (no `bubbled`/`chrome` flags) because that
    is the only era these sniffers exist for: a live standalone run paints no
    prose at all, and stamps `chrome` on its frame."""
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
    items = opshtml.op_items(ops, "k")
    txt = " ".join(re.sub("<[^>]+>", "", it.get("html", "") or "") for it in items)
    assert "prompt" not in txt and "message" not in txt          # prose headers gone
    assert "hi there" not in txt and "hello" not in txt          # prose bodies gone
    assert "codex ▶" not in txt and "⚙" not in txt               # chrome gone
    assert "ls -la" in txt                                        # command kept
    assert "ended" not in txt                                     # footer chrome gone too
    # …and a CLAUDE session's ops are untouched by either sniffer: same shapes,
    # an agent palette instead of codex's
    sub = SL.SUB_PALETTE[0]
    claude = [dict(O.label("⇢ prompt", sub, g="b1"), who="Explore"),
              O.gut("the brief", sub, g="b1")]
    assert len(opshtml.op_items(claude, "k")) == 2
