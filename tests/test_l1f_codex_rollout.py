# L1f — the codex rollout parser (plugins/codex/rollout.py): the parse half
# of the codex stream's parse/paint split, and the ONE owner of the rollout
# record grammar. Renderer equivalence is covered by the existing e2e codex
# suite (test_l6_codex.py) — these tests pin the parser's record contract
# directly: every typed record's shape, the TWO registers (event_msg for the
# mirror, response_item for a conversation presenter — docs/codex.md *Two
# registers*), the synthetic-message suppression list, and the
# forward-compatible unknown-type degrade. Event shapes match the real
# ~/.codex/sessions rollouts the e2e fixtures were verified against.
import json
import sys

from conftest import REPO

if REPO not in sys.path:
    sys.path.insert(0, REPO)

from plugins.codex import rollout as RO
from plugins.codex import stream as ST


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
    # the older spelling: a bare TOP-LEVEL effort, no collaboration_mode
    rec3 = RO.parse({"type": "turn_context",
                     "payload": {"model": "gpt-5-codex", "effort": "high"}})
    assert rec3 == {"kind": "turn_context", "model": "gpt-5-codex",
                    "effort": "high"}


def test_token_count_needs_a_usage_dict():
    u = {"input_tokens": 1000, "cached_input_tokens": 600, "output_tokens": 50}
    rec = RO.parse(_ev("token_count", info={"total_token_usage": u}))
    assert rec == {"kind": "usage", "usage": u, "last": None, "window": None}
    # rate-limit-only events carry info=null — nothing renderable
    assert RO.parse(_ev("token_count", info=None)) is None


def test_token_count_keeps_last_usage_and_context_window():
    """The cumulative total never resets across a compaction, so a ctx-bar
    needs the LAST turn's usage over the model window — both retained."""
    u = {"input_tokens": 1000, "cached_input_tokens": 600, "output_tokens": 50}
    last = {"input_tokens": 120, "cached_input_tokens": 100, "output_tokens": 9,
            "total_tokens": 129}
    rec = RO.parse(_ev("token_count", info={
        "total_token_usage": u, "last_token_usage": last,
        "model_context_window": 272000}))
    assert rec == {"kind": "usage", "usage": u, "last": last,
                   "window": 272000}
    # a version without the extra fields still parses (both None)
    bare = RO.parse(_ev("token_count", info={"total_token_usage": u,
                                             "model_context_window": None}))
    assert bare["last"] is None and bare["window"] is None


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
        {"kind": "task_started", "at": 1.5, "ts": None}
    assert RO.parse(_ev("task_complete", completed_at=9.0)) == \
        {"kind": "task_complete", "at": 9.0, "ts": None}
    assert RO.parse(_ev("turn_aborted")) == {"kind": "turn_aborted"}
    assert RO.parse(_ev("context_compacted")) == {"kind": "compact"}


def test_lifecycle_surfaces_the_envelope_timestamp():
    """Many codex versions omit started_at/completed_at entirely — the
    ENVELOPE's timestamp is then the only clock. It rides as its own `ts`,
    never folded into the numeric `at` the mirror subtracts."""
    o = _ev("task_complete")
    o["timestamp"] = "2026-07-29T10:00:00.000Z"
    assert RO.parse(o) == {"kind": "task_complete", "at": None,
                           "ts": "2026-07-29T10:00:00.000Z"}
    o2 = _ev("task_started")
    o2["timestamp"] = "2026-07-29T09:59:00.000Z"
    assert RO.parse(o2)["ts"] == "2026-07-29T09:59:00.000Z"
    # the exec pair is stamped too (a codex exec carries no duration of its own —
    # the standalone command block times exec.ts -> exec_result.ts)
    oe = _rsp("function_call", name="exec_command",
              arguments=json.dumps({"cmd": ["true"]}))
    oe["timestamp"] = "2026-07-29T09:59:01.000Z"
    assert RO.parse(oe)["ts"] == "2026-07-29T09:59:01.000Z"
    orr = _rsp("function_call_output", output="ok")
    orr["timestamp"] = "2026-07-29T09:59:03.500Z"
    assert RO.parse(orr)["ts"] == "2026-07-29T09:59:03.500Z"
    # a non-lifecycle record is not stamped
    assert "ts" not in RO.parse(_ev("agent_message", message="hi"))


def test_web_search_query():
    rec = RO.parse(_rsp("web_search_call",
                        action={"type": "search", "query": "kitty docs"}))
    assert rec == {"kind": "search", "query": "kitty docs"}
    assert RO.parse(_rsp("web_search_call", action={})) is None


def test_exec_command_args_decode_and_list_join():
    rec = RO.parse(_rsp("function_call", name="exec_command", call_id="c1",
                        arguments=json.dumps({"cmd": ["pytest", "-q"]})))
    # the exec pair carries the envelope `ts` (None here — no timestamp set) so a
    # standalone command block can time itself; see the timestamp test below
    assert rec == {"kind": "exec", "cmd": "pytest -q", "call_id": "c1", "ts": None}
    # string form + the alternate "command" key
    rec2 = RO.parse(_rsp("function_call", name="exec_command",
                         arguments=json.dumps({"command": "ls"})))
    assert rec2 == {"kind": "exec", "cmd": "ls", "call_id": "", "ts": None}
    # a non-exec function call and an empty cmd are not records
    assert RO.parse(_rsp("function_call", name="other_tool",
                         arguments="{}")) is None
    assert RO.parse(_rsp("function_call", name="exec_command",
                         arguments="{broken")) is None


def test_exec_output_exit_extraction_both_head_forms():
    # exit is read from the FULL head (the preamble), then the body is stripped of
    # codex's `…Output:\n` status preamble so the block shows the real output
    rec = RO.parse(_rsp("function_call_output", call_id="c1",
                        output="Process exited with code 2\nOutput:\nboom"))
    assert rec == {"kind": "exec_result", "exit": "2", "output": "boom",
                   "call_id": "c1", "ts": None}
    assert RO.parse(_rsp("function_call_output",
                         output="Exit code: 0\nok"))["exit"] == "0"
    assert RO.parse(_rsp("function_call_output", output="plain"))["exit"] is None
    # the exact live 0.14x preamble (verified from a real `run ls`): exit 0, body
    # is the listing with the Chunk-ID/Wall-time/Process-exited noise stripped
    live = RO.parse(_rsp("function_call_output", output=(
        "Chunk ID: 83d778\nWall time: 0.0002 seconds\nProcess exited with code 0"
        "\nOriginal token count: 30\nOutput:\nbin\nCLAUDE.md\ncore")))
    assert live["exit"] == "0" and live["output"] == "bin\nCLAUDE.md\ncore"
    # the status line is only trusted in the head window
    far = "x" * (RO.EXIT_SCAN_B + 10) + "\nExit code: 3\n"
    assert RO.parse(_rsp("function_call_output", output=far))["exit"] is None


def test_usage_split_is_the_one_mapping():
    assert RO.usage_split({"input_tokens": 1000, "cached_input_tokens": 600,
                           "output_tokens": 50}) == (400, 50, 600, 1000)
    assert RO.usage_split({}) == (0, 0, 0, 0)
    # cached > input must never go negative
    assert RO.usage_split({"input_tokens": 5, "cached_input_tokens": 9})[0] == 0


# -------------------------------------------- the response_item (conversation) register

def test_response_item_message_is_its_own_chat_register():
    """response_item/message is the conversation as the model API records it —
    deliberately NOT kind "message"/"prompt" (the event_msg register the
    mirror paints), because one turn appears in BOTH and a shared kind would
    paint every message twice."""
    rec = RO.parse(_rsp("message", role="assistant",
                        content=[{"type": "output_text", "text": " hi there "}]))
    assert rec == {"kind": "chat", "role": "assistant", "text": "hi there",
                   "synthetic": False}
    rec2 = RO.parse(_rsp("message", role="user",
                         content=[{"type": "input_text", "text": "fix it"}]))
    assert rec2 == {"kind": "chat", "role": "user", "text": "fix it",
                    "synthetic": False}
    # multi-part content joins; empty content is not a record
    assert RO.parse(_rsp("message", role="user", content=[
        {"type": "input_text", "text": "a"},
        {"type": "input_text", "text": "b"}]))["text"] == "a\nb"
    assert RO.parse(_rsp("message", role="user", content=[])) is None
    assert RO.parse(_rsp("message", role="user", content=None)) is None


def test_every_synthetic_marker_is_flagged():
    """Codex re-injects its own context blocks as user/developer messages —
    each must come back flagged so a conversation presenter drops it."""
    for mark in RO.SYNTHETIC_PREFIXES:
        rec = RO.parse(_rsp("message", role="user", content=[
            {"type": "input_text", "text": mark + "\nbody\n"}]))
        assert rec["synthetic"] is True, mark
    # leading whitespace must not smuggle one past the check
    assert RO.parse(_rsp("message", role="user", content=[
        {"type": "input_text", "text": "\n  <turn_aborted>x"}]))["synthetic"]
    # ...and a real turn that merely mentions one is not synthetic
    assert RO.parse(_rsp("message", role="user", content=[
        {"type": "input_text", "text": "why <turn_aborted>?"}]))["synthetic"] is False


def test_response_item_reasoning_summary():
    rec = RO.parse(_rsp("reasoning", summary=[
        {"type": "summary_text", "text": "**Plan**\nread the file"}]))
    assert rec == {"kind": "think", "text": "**Plan**\nread the file"}
    # encrypted_content-only reasoning has an EMPTY summary — no record
    assert RO.parse(_rsp("reasoning", summary=[],
                         encrypted_content="gAAAA…")) is None
    assert RO.parse(_rsp("reasoning")) is None


# ------------------------------------------------------ apply_patch + the tool calls

def test_custom_tool_call_apply_patch_is_a_lightweight_marker():
    """The call carries repo-RELATIVE patch text only; patch_apply_end stays
    the authoritative file-op record, so this must not produce file rows."""
    body = "*** Begin Patch\n*** Update File: a.py\n-old\n+new\n*** End Patch"
    rec = RO.parse(_rsp("custom_tool_call", name="apply_patch", call_id="p1",
                        input=body))
    assert rec == {"kind": "patch_call", "patch": body, "call_id": "p1"}
    assert "files" not in rec
    # an unknown custom tool is forward-compatibly ignored
    assert RO.parse(_rsp("custom_tool_call", name="mystery", input="x")) is None


def test_custom_tool_call_exec_is_the_0_14x_command_channel():
    """0.14x+ runs a shell command through a `custom_tool_call` named "exec"
    whose input is a JS `tools.exec_command({cmd:…})` snippet — the channel a real
    `run ls` used (verified 0.144.1), and the reason a codex command showed no
    block before. The command is pulled out of the JS; a list joins on spaces."""
    js = ('const r = await tools.exec_command({cmd:"ls","workdir":"/w",'
          '"yield_time_ms":10000}); text(r.output);\n')
    assert RO.parse(_rsp("custom_tool_call", name="exec", call_id="c9",
                         input=js)) == {"kind": "exec", "cmd": "ls",
                                        "call_id": "c9", "ts": None}
    jl = 'await tools.exec_command({cmd:["bash","-lc","echo hi"]});'
    assert RO.parse(_rsp("custom_tool_call", name="exec", input=jl)) == {
        "kind": "exec", "cmd": "bash -lc echo hi", "call_id": "", "ts": None}
    # unparseable cmd -> no record (a broken block is worse than none)
    assert RO.parse(_rsp("custom_tool_call", name="exec", input="noop();")) is None


def test_custom_tool_call_output_is_an_exec_result():
    """A custom_tool_call_output carries no tool name, so it is the exec/patch
    OUTPUT for whatever opened its call_id — an exec_result either way (an
    apply_patch's is an orphan the renderer surfaces only on failure). codex's
    `…Output:\\n` status preamble is stripped so the body is the real output; the
    exit is still read from the whole head."""
    out = [{"type": "input_text", "text": "Script completed\nWall time 0.3 "
            "seconds\nOutput:\n"}, {"type": "input_text", "text": "01cloud\n1x2\n"}]
    rec = RO.parse(_rsp("custom_tool_call_output", call_id="c9", output=out))
    assert rec == {"kind": "exec_result", "exit": None, "output": "01cloud\n1x2",
                   "call_id": "c9", "ts": None}
    bad = RO.parse(_rsp("custom_tool_call_output",
                        output="Exit code: 1\nOutput:\npatch does not apply"))
    assert bad["exit"] == "1" and bad["output"] == "patch does not apply"
    # no preamble marker: the whole text is the body
    assert RO.parse(_rsp("custom_tool_call_output", output="hm"))["output"] == "hm"


def test_shell_and_write_stdin_function_calls():
    # `shell` is the pre-0.1x spelling of exec_command — same exec record
    rec = RO.parse(_rsp("function_call", name="shell", call_id="s1",
                        arguments=json.dumps({"command": ["ls", "-l"]})))
    assert rec == {"kind": "exec", "cmd": "ls -l", "call_id": "s1", "ts": None}
    # write_stdin is the backgrounded-exec poll: a light record so its
    # function_call_output is not orphaned (paired by call_id)
    rec2 = RO.parse(_rsp("function_call", name="write_stdin", call_id="s2",
                         arguments=json.dumps({"session_id": 3, "chars": "y\n"})))
    assert rec2 == {"kind": "stdin", "text": "y\n", "call_id": "s2"}
    assert RO.parse(_rsp("function_call", name="write_stdin", call_id="s2",
                         arguments="{}")) == {"kind": "stdin", "text": "",
                                              "call_id": "s2"}


def test_request_user_input_question_schema():
    args = {"questions": [{"header": "Scope", "id": "q1",
                           "question": "Which files?",
                           "options": [{"label": "all", "description": "the repo"},
                                       {"label": "one"}]}]}
    rec = RO.parse(_rsp("function_call", name="request_user_input", call_id="a1",
                        arguments=json.dumps(args)))
    assert rec == {"kind": "ask", "call_id": "a1", "questions": [
        {"id": "q1", "header": "Scope", "question": "Which files?",
         "options": [{"label": "all", "description": "the repo"},
                     {"label": "one", "description": ""}]}]}
    # no questions / broken arguments -> nothing
    assert RO.parse(_rsp("function_call", name="request_user_input",
                         arguments=json.dumps({"questions": []}))) is None
    assert RO.parse(_rsp("function_call", name="request_user_input",
                         arguments="{broken")) is None


# --------------------------------------------------------------- top-level records

def test_top_level_compacted_boundary():
    rec = RO.parse({"type": "compacted", "payload": {
        "message": "", "replacement_history": [{"a": 1}, {"b": 2}],
        "window_id": "w2", "previous_window_id": "w1"}})
    assert rec == {"kind": "compact_boundary", "message": "", "replaced": 2,
                   "window_id": "w2", "previous_window_id": "w1"}
    # the bare (un-enveloped) spelling carries the fields at the top level
    bare = RO.parse({"type": "compacted", "message": "summary",
                     "window_id": "w9"})
    assert bare["message"] == "summary" and bare["window_id"] == "w9"
    assert bare["replaced"] == 0 and bare["previous_window_id"] is None
    # ...and it is NOT the event_msg compact notice the mirror paints
    assert RO.parse(_ev("context_compacted")) == {"kind": "compact"}


def test_world_state_is_explicitly_ignored():
    assert RO.parse({"type": "world_state", "payload": {"files": ["a"] * 100}}) is None


def test_unknown_shapes_never_raise():
    """The grammar drifted across 0.95 → 0.144; an unknown type or a missing
    field must degrade to None, never to an exception."""
    for o in ({}, {"type": "brand_new"}, {"type": "event_msg"},
              {"type": "response_item", "payload": {}},
              {"type": "response_item", "payload": {"type": "message"}},
              {"type": "response_item", "payload": {"type": "custom_tool_call"}},
              {"type": "event_msg", "payload": {"type": "token_count"}},
              {"type": "turn_context"}):
        assert RO.parse(o) is None or isinstance(RO.parse(o), dict)


# ------------------------------------------------------------------ single owner

def test_renderer_consumes_the_parser():
    """The stream renderer must dispatch on rollout.py's records — a second
    rollout-grammar walk in stream.py is the drift the split removed.

    THE CONTRACT IS ONE GRAMMAR, NOT ONE CONSUMER: rollout.parse may have
    several sanctioned presenters (the mirror renderer here; the dashboard
    conversation provider next — docs/codex.md *Two registers*). What this
    pins is that no presenter re-encodes the raw record vocabulary; adding a
    consumer does not weaken the stream.py check below."""
    import os
    src = open(os.path.join(REPO, "plugins", "codex", "stream.py"),
               encoding="utf-8").read()
    assert "from plugins.codex import rollout" in src
    for literal in ("Process exited with code", "web_search_call",
                    'get("arguments")', 'get("changes")',
                    'get("total_token_usage")'):
        assert literal not in src, "rollout grammar re-encoded in stream.py: " + literal


# ---------------------------------------------------- the parser↔renderer drift contract
# rollout.KINDS is the ONE owner of the codex rollout kind vocabulary
# (docs/codex.md *Kind drift contract*). Every kind it lists must be DECIDED by
# the mirror renderer — either painted (stream.Renderer._RO) or explicitly
# ignored (stream.IGNORE_KINDS) — and nothing may claim a kind the parser never
# emits. This is the SAFETY NET the parser-deepening drift needed: a new/renamed
# parser kind, or a stale/typo'd handler, fails one of the three checks below.

def test_every_kind_is_decided_render_or_ignore():
    """No parser kind may sit undecided: each rollout.KINDS member is either a
    _RO handler key or an IGNORE_KINDS member (the drift the split reopened —
    the parser grew kinds the renderer silently dropped)."""
    decided = set(ST.Renderer._RO) | set(ST.IGNORE_KINDS)
    undecided = RO.KINDS - decided
    assert not undecided, "codex kinds neither rendered nor ignored: " + repr(sorted(undecided))


def test_no_handler_points_at_a_phantom_kind():
    """No _RO key or IGNORE_KINDS member may name a kind the parser never emits
    (a rename/typo that would leave a dead handler)."""
    decided = set(ST.Renderer._RO) | set(ST.IGNORE_KINDS)
    stale = decided - RO.KINDS
    assert not stale, "handlers point at kinds not in rollout.KINDS: " + repr(sorted(stale))


def test_render_and_ignore_are_disjoint():
    """A kind is painted OR ignored, never both."""
    both = set(ST.Renderer._RO) & set(ST.IGNORE_KINDS)
    assert not both, "codex kinds both rendered and ignored: " + repr(sorted(both))
