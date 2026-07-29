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
    while the gestures codex cannot drive — rewind/plan/migrate/model/effort — stay
    inert and read False (greyed). `send` is not a gesture (never caps-gated). The
    caps are DERIVED from which methods CodexHost overrides, not an authored dict
    (plugins.host), so this pins the derivation end-to-end."""
    import plugins
    h = plugins.host_named("codex")
    assert h is not None and h.name == "codex" and h.launchable is True
    assert h.label == "Codex"
    assert h.caps() == {"interrupt": True, "send": False, "rename": True,
                        "rewind": False, "migrate": False, "compact": True,
                        "model": False, "effort": False, "ask": True,
                        "plan": False}
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
                   "pct": 13600 * 100 // 272000, "model": "gpt-5.1-codex"}
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
    # The STANDALONE branch resolves the session's own rollout — tested on the
    # codex read module DIRECTLY, because through plugins.conversation() the
    # fan-out asks claude_code FIRST and its rollout parse returns an empty [] (a
    # non-None answer) that SHADOWS this branch on purpose: a standalone codex run
    # already paints its prose into its (unstamped) ops, so bubbles here would
    # DOUBLE it (docs/codex.md).
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
