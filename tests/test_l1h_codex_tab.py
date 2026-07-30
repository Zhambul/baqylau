# tests/test_l1h_codex_tab.py — the codex TAB PRODUCER (P4): the event→state
# decision, the record-matched interrupt/steer detection, the standalone-host
# nested-guard registry, and the Claude-screen-scraper host-gates. Pure/unit
# level (no kitten subprocess) — the codex hook lifecycle e2e lives in
# tests/test_l6_codex.py.
import json

from core.tabs import (AWAITING_COMMAND, AWAITING_RESPONSE, EXECUTING, THINKING,
                       WORKING)
from plugins.codex import tabstatus as TS


# --- the event -> (state, reason) decision -------------------------------------

def test_resolve_maps_every_codex_event():
    assert TS.resolve("UserPromptSubmit", {})[0] == THINKING
    assert TS.resolve("PreToolUse",
                      {"tool_name": "request_user_input"})[0] == AWAITING_COMMAND
    assert TS.resolve("PreToolUse", {"tool_name": "exec_command"})[0] == EXECUTING
    assert TS.resolve("PreToolUse", {"tool_name": "shell"})[0] == EXECUTING
    assert TS.resolve("PreToolUse", {"tool_name": "apply_patch"})[0] == EXECUTING
    # codex sends CLAUDE-COMPATIBLE tool names — a shell command arrives as `Bash`
    # (verified live), so it must go blue like claude's own Bash, not magenta
    assert TS.resolve("PreToolUse", {"tool_name": "Bash"})[0] == EXECUTING
    assert TS.resolve("PreToolUse", {"tool_name": "Task"})[0] == EXECUTING
    assert TS.resolve("PreToolUse",
                      {"tool_name": "AskUserQuestion"})[0] == AWAITING_COMMAND
    assert TS.resolve("PreToolUse", {"tool_name": "web_search"})[0] == WORKING
    assert TS.resolve("PostToolUse", {})[0] == WORKING
    assert TS.resolve("PostToolUseFailure", {})[0] == WORKING
    assert TS.resolve("PermissionRequest", {})[0] == AWAITING_COMMAND
    assert TS.resolve("PreCompact", {})[0] == WORKING
    assert TS.resolve("Stop", {})[0] == AWAITING_RESPONSE


def test_resolve_no_op_events():
    # PostCompact lets the next event repaint; an unknown event never paints.
    assert TS.resolve("PostCompact", {})[0] is None
    assert TS.resolve("SomethingBrandNew", {})[0] is None


def test_resolve_ignores_agent_id_inner_events():
    """MAIN SESSION ONLY (the shared tab doctrine): any codex event carrying an
    agent_id is a SUBAGENT's inner call and must NOT paint the lead's tab. This is
    the stuck-magenta fix — a codex SubagentStart/Stop always carries the child's
    agent_id, and a LATE SubagentStop (after the turn's real Stop) used to repaint
    WORKING over the resting green with nothing left to clear it."""
    for ev in ("SubagentStart", "SubagentStop", "PreToolUse", "PostToolUse", "Stop"):
        assert TS.resolve(ev, {"agent_id": "019fb2f7-9b51", "tool_name": "webrun"})[0] \
            is None, "%s with an agent_id must not paint the main tab" % ev
    # the SAME events with NO agent_id are the LEAD's and DO paint
    assert TS.resolve("Stop", {})[0] == AWAITING_RESPONSE
    assert TS.resolve("PostToolUse", {})[0] == WORKING


def test_bug_b_late_subagent_stop_leaves_the_tab_green():
    """Replay the 019fb2f7 sequence: the lead's Stop ends the turn (green), then a
    LATE SubagentStop (carrying the child's agent_id) arrives — it must be a NO-OP,
    so the tab stays green instead of stuck magenta."""
    assert TS.resolve("Stop", {})[0] == AWAITING_RESPONSE            # lead turn ends
    state, _ = TS.resolve("SubagentStop", {"agent_id": "019fb2f7-9b51"})
    assert state is None, "a late SubagentStop must not repaint the resting tab"


# --- interrupt recovery: record-matched abort + steer --------------------------

def _line(t, ptype, **extra):
    return json.dumps({"type": t, "payload": dict(extra, type=ptype)}).encode()


def test_is_abort_matches_record_not_bytes():
    assert TS._is_abort(_line("event_msg", "turn_aborted", reason="interrupted"))
    # A message that merely QUOTES the marker is NOT a cancel (the invariant that
    # a rollout echoing this very file must not flip the tab).
    quote = json.dumps({"type": "response_item", "payload": {
        "type": "message", "role": "assistant",
        "content": [{"type": "output_text", "text": "a turn_aborted record"}]}}).encode()
    assert not TS._is_abort(quote)
    assert not TS._is_abort(b"not json but has turn_aborted in it")


def test_new_turn_after_detects_steer():
    abort = _line("event_msg", "turn_aborted")
    task = _line("event_msg", "task_started")
    prompt = _line("event_msg", "user_message", message="the queued prompt")
    # queue+Esc STEER: task_started + user_message follow the abort -> a new turn.
    assert TS._new_turn_after(b"\n".join([abort, task, prompt]))
    assert TS._new_turn_after(b"\n".join([abort, prompt]))
    # a plain interrupt leaves the abort line last -> no new turn.
    assert not TS._new_turn_after(abort + b"\n")


def test_abort_mark_finds_offset():
    other = _line("event_msg", "agent_message", message="hello")
    abort = _line("event_msg", "turn_aborted")
    chunk = other + b"\n" + abort + b"\n"
    mark, nxt = TS._abort_mark(chunk, 0)
    assert mark == len(other) + 1               # start of the abort line
    assert nxt == mark
    # no abort -> -1, pos advanced over the complete lines only
    mark2, nxt2 = TS._abort_mark(other + b"\n", 0)
    assert mark2 == -1 and nxt2 == len(other) + 1


# --- the standalone-host nested-guard registry ---------------------------------

def test_codex_host_registry_roundtrip(tmp_path, monkeypatch):
    from core import tabs
    monkeypatch.setattr(tabs, "TABDB", str(tmp_path / "tab.db"))
    tabs._RO_CONNS.clear()                       # drop conns cached under the old path
    # unknown sid -> None == "not a standalone host" (the dispatcher then bails)
    assert tabs.codex_host_win("sid1") is None
    tabs.codex_host_mark("sid1", "42")
    assert tabs.codex_host_win("sid1") == "42"
    # a marked host with no window is "" (standalone, but paint impossible) —
    # distinct from None (nested/unknown).
    tabs.codex_host_mark("sid2", "")
    assert tabs.codex_host_win("sid2") == ""
    tabs.codex_host_clear("sid1")
    assert tabs.codex_host_win("sid1") is None
    tabs._RO_CONNS.clear()


# --- the Claude screen-scraper host-gates --------------------------------------

def test_notifier_screen_scrapable_gate():
    # The gate is no longer a per-sid BOOLEAN the notifier computes from a host
    # NAME: each probe is asked of the session's OWNING host, and a host with no
    # such screen geometry answers None by not implementing it (P2). What the
    # notifier keeps is the ROUTING — which host to ask, defaulting to the
    # default host for a sid it hasn't seen yet (the safe direction).
    import plugins
    from dashboard.notify.notifier import Notifier

    n = Notifier()
    claude = plugins.host_named(plugins.default_host())
    codex = plugins.host_named("codex")
    assert n._host_for("unseen") is claude          # default (safe direction)
    n._hosts = {"codexsid": codex, "claudesid": claude}
    assert n._host_for("codexsid") is codex
    assert n._host_for("claudesid") is claude
    # …and the two probes DECLINE for codex without touching the screen
    class _Boom:
        def get_text(self, *a, **k):
            raise AssertionError("a codex host must never be screen-scraped")
    n.fe = _Boom()
    assert n._dialog_region("codexsid", "7") is None
    assert n._input_typed("codexsid", "7") is None


def test_input_box_skips_a_codex_host(monkeypatch):
    from dashboard.read import session as S
    rollout = ("/home/u/.codex/sessions/2026/07/30/"
               "rollout-2026-07-30T10-00-00-11111111-2222-3333-4444-555555555555.jsonl")
    monkeypatch.setattr(S.API, "session_row", lambda sid: {"transcript_path": rollout})

    def boom(*a, **k):
        raise AssertionError("a codex host must never reach the ghost-suggestion probe")
    monkeypatch.setattr(S.launch, "frontend", boom)
    assert S.input_box("sid") == (None, None)     # gated off before the scrape


def test_input_box_allows_a_claude_host(monkeypatch):
    from dashboard.read import session as S
    # a bare <uuid>.jsonl is a Claude transcript (owns_by None) -> proceeds; and
    # an unprovable empty path stays the claude default too.
    monkeypatch.setattr(S.API, "session_row",
                        lambda sid: {"transcript_path": "/p/11111111-2222-3333-4444-555555555555.jsonl"})
    called = {}

    def marker():
        called["hit"] = True
    monkeypatch.setattr(S.launch, "frontend", marker)
    assert S.input_box("sid") == (None, None)
    assert called.get("hit")                      # the gate let it THROUGH to the frontend resolve
