# L1f — the codex rollout parser (plugins/codex/rollout.py): the parse half
# of the codex stream's parse/paint split, and the timeline read model behind
# the codex plugins.activity() provider. Renderer equivalence is covered by
# the existing e2e codex suite (test_l6_codex.py) — these tests pin the
# parser's record contract, the timeline's shape/pairing, and the provider's
# audit-streams resolution directly. Event shapes match the real
# ~/.codex/sessions rollouts the e2e fixtures were verified against.
import json
import sys

from conftest import REPO

if REPO not in sys.path:
    sys.path.insert(0, REPO)

import core.audit as A
from core import paths as P
from core import sessionapi as API
from plugins.codex import rollout as RO


def _l(o):
    return json.dumps(o, ensure_ascii=False)


def _ev(typ, **kw):
    return {"type": "event_msg", "payload": {"type": typ, **kw}}


def _rsp(typ, **kw):
    return {"type": "response_item", "payload": {"type": typ, **kw}}


# ----------------------------------------------------------------- parse/parse_line

def test_bad_json_is_a_bad_record():
    rec = RO.parse_line("{nope")
    assert rec["kind"] == "bad" and rec["raw"] == "{nope"


def test_unknown_types_are_none():
    assert RO.parse({"type": "session_meta", "payload": {"cwd": "/w"}}) is None
    assert RO.parse(_ev("mystery_event")) is None
    assert RO.parse(_rsp("mystery_item")) is None


def test_turn_context_model_and_effort():
    rec = RO.parse({"type": "turn_context", "payload": {
        "model": " gpt-5-codex ",
        "collaboration_mode": {"settings": {"reasoning_effort": "medium"}}}})
    assert rec == {"kind": "turn_context", "model": "gpt-5-codex",
                   "effort": "medium"}
    # effortless context still yields a record (the renderer owns the guard)
    rec2 = RO.parse({"type": "turn_context", "payload": {}})
    assert rec2 == {"kind": "turn_context", "model": "", "effort": ""}


def test_token_count_needs_a_usage_dict():
    u = {"input_tokens": 1000, "cached_input_tokens": 600, "output_tokens": 50}
    rec = RO.parse(_ev("token_count", info={"total_token_usage": u}))
    assert rec == {"kind": "usage", "usage": u}
    # rate-limit-only events carry info=null — nothing renderable
    assert RO.parse(_ev("token_count", info=None)) is None


def test_patch_counts_add_delete_and_diff_lines():
    rec = RO.parse(_ev("patch_apply_end", success=True, changes={
        "/w/a.py": {"type": "update", "unified_diff": "@@\n-old\n+new\n+more\n"},
        "/w/b.sh": {"type": "add", "content": "#!/bin/sh\necho hi\n"},
        "/w/c.txt": {"type": "delete", "content": "one\ntwo\n"},
        "/w/junk": "not-a-dict",
    }))
    assert rec["kind"] == "patch" and rec["success"] is True
    assert rec["files"] == [
        {"path": "/w/a.py", "change": "update", "added": 2, "removed": 1},
        {"path": "/w/b.sh", "change": "add", "added": 2, "removed": 0},
        {"path": "/w/c.txt", "change": "delete", "added": 0, "removed": 2}]
    assert RO.parse(_ev("patch_apply_end", success=False, changes={}))["success"] is False


def test_messages_strip_and_empty_is_none():
    assert RO.parse(_ev("user_message", message=" fix it \n")) == \
        {"kind": "prompt", "text": "fix it"}
    assert RO.parse(_ev("user_message", message="  ")) is None
    assert RO.parse(_ev("agent_message", message="done")) == \
        {"kind": "message", "text": "done"}
    assert RO.parse(_ev("agent_reasoning", text="hmm")) == \
        {"kind": "reasoning", "text": "hmm"}
    assert RO.parse(_ev("agent_reasoning", text="")) is None


def test_lifecycle_and_compact_records():
    assert RO.parse(_ev("task_started", started_at=1.5)) == \
        {"kind": "task_started", "at": 1.5}
    assert RO.parse(_ev("task_complete", completed_at=9.0)) == \
        {"kind": "task_complete", "at": 9.0}
    assert RO.parse(_ev("turn_aborted")) == {"kind": "turn_aborted"}
    assert RO.parse(_ev("context_compacted")) == {"kind": "compact"}


def test_web_search_query():
    rec = RO.parse(_rsp("web_search_call",
                        action={"type": "search", "query": "kitty docs"}))
    assert rec == {"kind": "search", "query": "kitty docs"}
    assert RO.parse(_rsp("web_search_call", action={})) is None


def test_exec_command_args_decode_and_list_join():
    rec = RO.parse(_rsp("function_call", name="exec_command", call_id="c1",
                        arguments=json.dumps({"cmd": ["pytest", "-q"]})))
    assert rec == {"kind": "exec", "cmd": "pytest -q", "call_id": "c1"}
    # string form + the alternate "command" key
    rec2 = RO.parse(_rsp("function_call", name="exec_command",
                         arguments=json.dumps({"command": "ls"})))
    assert rec2 == {"kind": "exec", "cmd": "ls", "call_id": ""}
    # a non-exec function call and an empty cmd are not records
    assert RO.parse(_rsp("function_call", name="other_tool",
                         arguments="{}")) is None
    assert RO.parse(_rsp("function_call", name="exec_command",
                         arguments="{broken")) is None


def test_exec_output_exit_extraction_both_head_forms():
    rec = RO.parse(_rsp("function_call_output", call_id="c1",
                        output="Process exited with code 2\nOutput:\nboom"))
    assert rec == {"kind": "exec_result", "exit": "2",
                   "output": "Process exited with code 2\nOutput:\nboom",
                   "call_id": "c1"}
    assert RO.parse(_rsp("function_call_output",
                         output="Exit code: 0\nok"))["exit"] == "0"
    assert RO.parse(_rsp("function_call_output", output="plain"))["exit"] is None
    # the status line is only trusted in the head window
    far = "x" * (RO.EXIT_SCAN_B + 10) + "\nExit code: 3\n"
    assert RO.parse(_rsp("function_call_output", output=far))["exit"] is None


def test_usage_split_is_the_one_mapping():
    assert RO.usage_split({"input_tokens": 1000, "cached_input_tokens": 600,
                           "output_tokens": 50}) == (400, 50, 600, 1000)
    assert RO.usage_split({}) == (0, 0, 0, 0)
    # cached > input must never go negative
    assert RO.usage_split({"input_tokens": 5, "cached_input_tokens": 9})[0] == 0


# ---------------------------------------------------------------------- timeline

def _write(tmp_path, lines, name="rollout-2026-07-06T10-00-00-u1.jsonl"):
    p = tmp_path / name
    p.write_text("".join(_l(o) + "\n" for o in lines), encoding="utf-8")
    return str(p)


def test_timeline_shape_pairing_and_usage(tmp_path):
    path = _write(tmp_path, [
        {"type": "session_meta", "payload": {"cwd": "/w",
                                             "originator": "codex_exec"}},
        {"type": "turn_context", "payload": {
            "model": "gpt-5-codex",
            "collaboration_mode": {"settings": {"reasoning_effort": "medium"}}}},
        _ev("task_started"),
        _ev("user_message", message="fix the flaky test"),
        _ev("agent_reasoning", text="thinking hard"),   # not a timeline entry
        _rsp("function_call", name="exec_command", call_id="c1",
             arguments=json.dumps({"cmd": ["pytest", "-q"]})),
        _rsp("function_call_output", call_id="c1",
             output="Exit code: 1\nFAILED test_x"),
        _rsp("web_search_call", action={"query": "pytest flaky"}),
        _ev("patch_apply_end", success=True, changes={
            "/w/a.py": {"type": "update", "unified_diff": "@@\n-x\n+y\n+z\n"}}),
        _ev("context_compacted"),
        _ev("token_count", info={"total_token_usage": {
            "input_tokens": 1000, "cached_input_tokens": 600,
            "output_tokens": 50, "total_tokens": 1050}}),
        _ev("agent_message", message="all green now"),
        _ev("task_complete"),
    ])
    tl = RO.timeline(path)
    kinds = [e["t"] for e in tl["entries"]]
    assert kinds == ["prompt", "tool", "tool", "tool", "compact", "message"]
    ex = tl["entries"][1]
    assert ex["tool"] == "exec_command" and ex["input"] == {"cmd": "pytest -q"}
    assert ex["output"] == "Exit code: 1\nFAILED test_x" and ex["failed"] is True
    assert tl["entries"][2] == {"t": "tool", "tool": "web_search",
                                "input": {"query": "pytest flaky"}, "id": None}
    patch = tl["entries"][3]
    assert patch["tool"] == "apply_patch"
    assert patch["input"] == {"file_path": "/w/a.py", "change": "update",
                              "added": 2, "removed": 1}
    assert tl["entries"][-1]["final"] is True         # the returned result
    # same shape as the claude timeline: model / tools / usage / bad_lines
    assert tl["model"] == "gpt-5-codex" and tl["tools"] == 3
    assert tl["usage"] == {"in": 400, "out": 50, "cache": 600,
                           "create": 0, "create_1h": 0}
    assert tl["bad_lines"] == 0


def test_timeline_orphan_result_and_bad_lines(tmp_path):
    p = tmp_path / "r.jsonl"
    p.write_text(_l(_rsp("function_call_output", call_id="ghost",
                         output="Exit code: 7\nboom")) + "\n{oops\n",
                 encoding="utf-8")
    tl = RO.timeline(str(p))
    assert tl["entries"] == [{"t": "orphan-result",
                              "output": "Exit code: 7\nboom", "failed": True}]
    assert tl["bad_lines"] == 1


def test_timeline_failed_patch_is_one_failed_tool_entry(tmp_path):
    path = _write(tmp_path, [_ev("patch_apply_end", success=False, changes={
        "/w/a.py": {"type": "update", "unified_diff": "@@\n+x\n"}})])
    tl = RO.timeline(path)
    assert tl["entries"] == [{"t": "tool", "tool": "apply_patch",
                              "input": {}, "id": None, "failed": True}]


# ----------------------------------------------- the activity provider (end-to-end)

def _seed_run(sid, src, label="cli", end="task-complete"):
    rid = A.stream_start(P.mirror_log(sid), "codex", task_id=label, src_path=src)
    A.stream_end(rid, end, lines_emitted=3)


def test_activity_resolves_a_codex_run_from_the_streams_keystone(tmp_path):
    path = _write(tmp_path, [
        _ev("user_message", message="review the diff"),
        _ev("agent_message", message="looks fine")])
    _seed_run("cxa-sess", path)
    import plugins
    aid = API.codex_aid(path)
    assert aid == "rollout-2026-07-06T10-00-00-u1"
    tl = plugins.activity("cxa-sess", aid)
    assert tl and [e["t"] for e in tl["entries"]] == ["prompt", "message"]
    assert tl["entries"][0]["text"] == "review the diff"
    # an unknown agent id stays unclaimed by every provider
    assert plugins.activity("cxa-sess", "no-such-run") is None


def test_activity_main_thread_matches_a_standalone_rollout(tmp_path):
    # standalone codex: the rollout filename uuid IS the session id, so the
    # provider answers the MAIN-thread drill-down (agent_id=None) with it.
    sid = "11111111-2222-3333-4444-555555555555"
    path = _write(tmp_path, [_ev("user_message", message="standalone hello")],
                  name="rollout-2026-07-06T10-00-00-%s.jsonl" % sid)
    _seed_run(sid, path)
    import plugins
    tl = plugins.activity(sid)
    assert tl and tl["entries"] == [{"t": "prompt", "text": "standalone hello"}]


def test_activity_companion_log_runs_have_no_drilldown(tmp_path):
    # a companion job's .log is an activity log, not a rollout — listed by
    # agents()/codex_runs(), but the provider must decline to parse it.
    log = tmp_path / "job-ab12cd34.log"
    log.write_text("[2026-07-06T10:00:00.000Z] Running command: ls\n",
                   encoding="utf-8")
    _seed_run("cxc-sess", str(log), label="Review",
              end="sidecar-status: completed")
    import plugins
    assert plugins.activity("cxc-sess", "job-ab12cd34") is None
    runs = API.codex_runs("cxc-sess")
    assert len(runs) == 1 and runs[0]["agent_id"] == "job-ab12cd34"
    assert runs[0]["desc"] == "Review"


# ------------------------------------------------------------------ single owner

def test_renderer_consumes_the_parser():
    """The stream renderer must dispatch on rollout.py's records — a second
    rollout-grammar walk in stream.py is the drift the split removed."""
    import os
    src = open(os.path.join(REPO, "plugins", "codex", "stream.py"),
               encoding="utf-8").read()
    assert "from plugins.codex import rollout" in src
    for literal in ("Process exited with code", "web_search_call",
                    'get("arguments")', 'get("changes")',
                    'get("total_token_usage")'):
        assert literal not in src, "rollout grammar re-encoded in stream.py: " + literal
