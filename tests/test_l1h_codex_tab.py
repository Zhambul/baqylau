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
    assert TS.resolve("PreToolUse", {"tool_name": "web_search"})[0] == WORKING
    assert TS.resolve("PostToolUse", {})[0] == WORKING
    assert TS.resolve("PostToolUseFailure", {})[0] == WORKING
    assert TS.resolve("PermissionRequest", {})[0] == AWAITING_COMMAND
    assert TS.resolve("PreCompact", {})[0] == WORKING
    assert TS.resolve("Stop", {})[0] == AWAITING_RESPONSE
    assert TS.resolve("SubagentStart", {})[0] == WORKING
    assert TS.resolve("SubagentStop", {})[0] == WORKING


def test_resolve_no_op_events():
    # PostCompact lets the next event repaint; an unknown event never paints.
    assert TS.resolve("PostCompact", {})[0] is None
    assert TS.resolve("SomethingBrandNew", {})[0] is None


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
    from dashboard.notify.notifier import Notifier
    n = Notifier()
    assert n._screen_scrapable("unseen") is True   # default allow (safe direction)
    n._claude_host = {"codexsid": False, "claudesid": True}
    assert n._screen_scrapable("codexsid") is False   # codex -> no scrape
    assert n._screen_scrapable("claudesid") is True


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
