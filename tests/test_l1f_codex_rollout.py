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
        {"path": "/w/a.py", "change": "update", "added": 2, "removed": 1,
         "diff": "@@\n-old\n+new\n+more\n"},
        {"path": "/w/b.sh", "change": "add", "added": 2, "removed": 0,
         "content": "#!/bin/sh\necho hi\n"},
        {"path": "/w/c.txt", "change": "delete", "added": 0, "removed": 2,
         "content": "one\ntwo\n"}]
    assert RO.parse(_ev("patch_apply_end", success=False, changes={}))["success"] is False


def test_codex_unified_diff_uses_shared_file_view_numbering():
    from core import streamfmt as SF
    assert SF.unified_diff_rows(
        "@@ -10,3 +20,4 @@ section\n ctx\n-old\n+new\n+more\n"
        "\\ No newline at end of file\n") == [
            (" ", 20, "ctx"), ("-", 11, "old"),
            ("+", 21, "new"), ("+", 22, "more")]


def test_messages_strip_and_empty_is_none():
    assert RO.parse(_ev("user_message", message=" fix it \n")) == \
        {"kind": "prompt", "text": "fix it"}
    assert RO.parse(_ev("user_message", message="  ")) is None
    assert RO.parse(_ev("agent_message", message="done")) == \
        {"kind": "message", "text": "done", "phase": "",
         "ts": None}
    assert RO.parse(_ev("agent_reasoning", text="hmm")) == \
        {"kind": "reasoning", "text": "hmm"}
    assert RO.parse(_ev("agent_reasoning", text="")) is None


def test_lifecycle_and_compact_records():
    assert RO.parse(_ev("task_started", started_at=1.5)) == \
        {"kind": "task_started", "at": 1.5, "ts": None, "turn": ""}
    assert RO.parse(_ev("task_complete", completed_at=9.0)) == \
        {"kind": "task_complete", "at": 9.0, "ts": None, "turn": "", "last": ""}
    assert RO.parse(_ev("turn_aborted")) == {"kind": "turn_aborted"}
    assert RO.parse(_ev("context_compacted")) == {"kind": "compact"}


def test_the_task_lifecycle_carries_the_turn_id_and_the_last_message():
    """The CHILD-TASK model's codex half (core/childtask.py). Measured payloads,
    session 019fb66b-12a0 (2026-07-31): `task_started`/`task_complete` name the
    TURN, and task_complete repeats what the turn answered.

    `turn` is what a child rollout's replayed prefix gives it — the parent turn it
    was spawned in — and `last` is the FALLBACK result text for a stream that
    never saw the message record itself. Both were dropped on the floor, which is
    why a child's completion could only ever be placed by its clock."""
    started = RO.parse(_ev("task_started", started_at=1785471906,
                           turn_id="019fb66b-325d", model_context_window=258400))
    assert started["turn"] == "019fb66b-325d" and started["at"] == 1785471906
    done = RO.parse(_ev("task_complete", turn_id="019fb66b-325d",
                        last_agent_message=" Denpasar: shower, 29C. ",
                        started_at=1785471906, completed_at=1785471929,
                        duration_ms=23023))
    assert done["turn"] == "019fb66b-325d"
    assert done["last"] == "Denpasar: shower, 29C."     # stripped, like every text
    # a rollout written before turn_id existed says so with "" — never a guess
    assert RO.parse(_ev("task_complete", completed_at=9.0))["turn"] == ""
    assert RO.parse(_ev("task_complete", completed_at=9.0))["last"] == ""


def test_an_agent_message_carries_its_phase_in_both_registers():
    """`phase: "final_answer"` is codex SAYING which message is the turn's answer
    — the fact that tells a child's result from its intermediate notes, instead of
    inferring it from whichever message happened to be pending at task_complete.

    Both registers carry it: codex writes one turn as an event_msg AND a
    response_item, and the web's conversation read takes whichever arrives first
    (plugins/codex/read.conversation), so a phase surviving only one spelling
    survives neither."""
    assert RO.parse(_ev("agent_message", message="working on it",
                        phase="commentary"))["phase"] == "commentary"
    final = RO.parse(_ev("agent_message", message="29C, shower",
                         phase=RO.PHASE_FINAL))
    assert final["phase"] == RO.PHASE_FINAL and final["text"] == "29C, shower"
    twin = RO.parse(_rsp("message", role="assistant", phase=RO.PHASE_FINAL,
                         content=[{"type": "output_text", "text": "29C, shower"}]))
    assert twin["kind"] == "chat" and twin["phase"] == RO.PHASE_FINAL
    # pre-phase rollouts: "" — the renderer's own fallback decides there
    assert RO.parse(_ev("agent_message", message="hi"))["phase"] == ""


def test_lifecycle_surfaces_the_envelope_timestamp():
    """Many codex versions omit started_at/completed_at entirely — the
    ENVELOPE's timestamp is then the only clock. It rides as its own `ts`,
    never folded into the numeric `at` the mirror subtracts."""
    o = _ev("task_complete")
    o["timestamp"] = "2026-07-29T10:00:00.000Z"
    assert RO.parse(o) == {"kind": "task_complete", "at": None, "turn": "",
                           "last": "", "ts": "2026-07-29T10:00:00.000Z"}
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
    # …and an assistant MESSAGE, whose clock a child's ⇠ result card is measured
    # to: a `final_answer` message ENDS the task ~100ms before task_complete, so
    # without this the card's duration would be measured to time.time() and a
    # replayed rollout would report the age of the file
    om = _ev("agent_message", message="hi")
    om["timestamp"] = "2026-07-29T09:59:04.000Z"
    assert RO.parse(om)["ts"] == "2026-07-29T09:59:04.000Z"
    # a record in neither family is not stamped
    assert "ts" not in RO.parse(_ev("agent_reasoning", text="hmm"))


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
                   "synthetic": False, "phase": ""}
    rec2 = RO.parse(_rsp("message", role="user",
                         content=[{"type": "input_text", "text": "fix it"}]))
    assert rec2 == {"kind": "chat", "role": "user", "text": "fix it",
                    "synthetic": False, "phase": ""}
    # multi-part content joins; empty content is not a record
    assert RO.parse(_rsp("message", role="user", content=[
        {"type": "input_text", "text": "a"},
        {"type": "input_text", "text": "b"}]))["text"] == "a\nb"
    assert RO.parse(_rsp("message", role="user", content=[])) is None
    assert RO.parse(_rsp("message", role="user", content=None)) is None


def test_synthetic_is_structural_not_an_allowlist():
    """codex machinery is told from a real turn STRUCTURALLY (docs/codex.md *Two
    registers*): the system channel is role developer/system; a role=user `<tag>`
    wrapper is a system injection UNLESS it is the INPUT wrapper `<task>`; a small
    non-tag supplement covers the rest. Robust to NEW system tags."""
    def synth(text, role):
        return RO.parse(_rsp("message", role=role, content=[
            {"type": "input_text", "text": text}]))["synthetic"]
    # 1. role developer/system -> always synthetic (no list needed)
    assert synth("<multi_agent_mode>\n…", "developer") is True
    assert synth("plain text with no tag", "developer") is True
    assert synth("anything", "system") is True
    # 2. role=user <tag> wrappers -> synthetic by default (incl. ones NEVER listed)
    for tag in ("<recommended_plugins>", "<environment_context>", "<turn_aborted>",
                "<permissions instructions>", "<brand_new_2027_tag>"):
        assert synth(tag + "\nbody", "user") is True, tag
    # ...leading whitespace can't smuggle one past
    assert synth("\n  <turn_aborted>x", "user") is True
    # 3. the non-tag supplement
    for mark in RO.SYNTHETIC_PREFIXES:
        assert synth(mark + "\nbody", "user") is True, mark
    # a REAL user prompt (free prose, even mentioning a tag) is NOT synthetic
    assert synth("Get the current weather in Bali", "user") is False
    assert synth("why does <turn_aborted> happen?", "user") is False
    # the INPUT wrapper <task> is a real turn, kept AND unwrapped to its inner text
    rec = RO.parse(_rsp("message", role="user", content=[
        {"type": "input_text", "text": "<task>\nReview the plan at /x/y.md\n</task>"}]))
    assert rec["synthetic"] is False
    assert rec["text"] == "Review the plan at /x/y.md"
    # strip_input_wrapper leaves non-wrapped text alone
    assert RO.strip_input_wrapper("just a prompt") == "just a prompt"
    assert RO.strip_input_wrapper("<recommended_plugins>x") == "<recommended_plugins>x"


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
    # the `cmd` key comes DOUBLE-QUOTED too (valid JSON), model/version dependent —
    # verified live from a gpt-5.6-terra `pwd` that showed NO block until handled
    jq = 'const r = await tools.exec_command({"cmd":"pwd","workdir":"/w"});'
    assert RO.parse(_rsp("custom_tool_call", name="exec", call_id="q1",
                         input=jq)) == {"kind": "exec", "cmd": "pwd",
                                        "call_id": "q1", "ts": None}
    # unparseable cmd -> no record (a broken block is worse than none)
    assert RO.parse(_rsp("custom_tool_call", name="exec", input="noop();")) is None


def test_a_non_shell_tools_fn_is_a_TOOL_record_not_a_command():
    """codex ≥ 0.146 runs MANY tools through the same `exec` custom tool — a
    web/MCP lookup is `tools.web__run({…})`, not `tools.exec_command({cmd:…})` —
    and the two are DIFFERENT KINDS of activity, so they get different records: a
    shell command keeps `exec`, everything else is a structured `tool` (name +
    arguments).

    It used to be laundered INTO the exec shape, which is how a codex subagent's
    entire real work came to render as `▶ cmd` blocks of raw JavaScript
    (measured on the real cli 0.146 child rollout 019fb363-4028…: five such calls,
    not one shell command among them)."""
    js = ('const r = await tools.web__run({weather:[{location:"Bali",duration:1}],'
          'response_length:"short"}); text(JSON.stringify(r));')
    assert RO.parse(_rsp("custom_tool_call", name="exec", call_id="w1",
                         input=js)) == {
        "kind": "tool", "name": "web__run", "call_id": "w1",
        "args": '{weather:[{location:"Bali",duration:1}],response_length:"short"}'}
    # a SHELL command still parses to exec, by its own cleaner {cmd:…} extraction
    assert RO.parse(_rsp("custom_tool_call", name="exec",
                         input='await tools.exec_command({cmd:"ls"});')) == {
        "kind": "exec", "cmd": "ls", "call_id": "", "ts": None}
    # neither shape -> no record at all (a broken block is worse than none)
    assert RO.parse(_rsp("custom_tool_call", name="exec", input="noop();")) is None


def test_tool_args_end_at_the_matching_paren_whatever_the_wrapper_tail():
    """The arguments are cut at the call's MATCHING close paren rather than at a
    known suffix, because codex's wrapper tail VARIES per call —
    `text(JSON.stringify(r))`, `text(r.content.map(x=>x.text||"").join("\\n"))`.
    The old fixed suffix list matched NONE of the five real calls in the measured
    rollout, so the whole `; text(…)` tail rode along as part of the command."""
    for tail in ('text(JSON.stringify(r));',
                 'text(r.content.map(x=>x.text||"").join("\\n"));',
                 'console.log(r)'):
        js = 'const r = await tools.web__run({q:"a (b) c"}); ' + tail
        assert RO.js_tool_call(js) == ("web__run", '{q:"a (b) c"}'), tail
    # a paren INSIDE a string never closes the call…
    assert RO.js_tool_call('tools.f({x:")"})') == ("f", '{x:")"}')
    # …and an unbalanced (truncated) input fails OPEN to the rest of the line
    assert RO.js_tool_call("tools.f({x:1}") == ("f", "{x:1}")
    assert RO.js_tool_call("no tools here") == ("", "")
    assert RO.js_tool_call("") == ("", "")


def test_web_search_end_is_a_search_only_when_it_names_a_query():
    """cli 0.146 writes NO `web_search_call` response_item at all: the measured
    child rollout carries five `web_search_end` EVENTS and zero of the other, so
    this event is the only place a codex search appears — without it a web search
    rendered nothing.

    Four of those five are the web tool's non-search actions (`action.type ==
    "other"` — opening a result it already found), which carry an empty query and
    must paint nothing; only the fifth names what was searched."""
    assert RO.parse(_ev("web_search_end", call_id="e1", query="",
                        action={"type": "other"}, results=[])) is None
    assert RO.parse(_ev("web_search_end", call_id="e2",
                        query="current weather Bali Indonesia",
                        action={"type": "search",
                                "query": "current weather Bali Indonesia"})) == {
        "kind": "search", "query": "current weather Bali Indonesia"}
    # the query may arrive only under `action`
    assert RO.parse(_ev("web_search_end",
                        action={"type": "search", "query": "kubectl logs"})) == {
        "kind": "search", "query": "kubectl logs"}


def test_the_two_agent_plumbing_events_stay_unparsed():
    """`sub_agent_activity` (a `{kind:"interacted"}` ping about a child thread,
    which has its own rollout and its own stream) and
    `inter_agent_communication_metadata` (`{trigger_turn:true}`) are deliberately
    NOT parsed — both measured in the real child rollout, both pure plumbing.
    Pinned so a later reader sees the decision rather than a gap."""
    assert RO.parse(_ev("sub_agent_activity", kind="interacted",
                        agent_path="/root")) is None
    assert RO.parse({"type": "inter_agent_communication_metadata",
                     "payload": {"trigger_turn": True}}) is None


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


# ------------------------------------------------- subagent rollout prefix boundary

def _write_subagent_rollout(tmp_path, fork_iso="2026-07-30T12:19:59.556Z",
                            fork_epoch=1785413999):
    """A minimal subagent rollout in the REAL shape (verified against the cli
    0.146 child rollout 019fb363-4028…): child session_meta
    (thread_source=subagent) + parent session_meta, the parent's REPLAYED turn —
    the developer/system injections, the `<environment_context>` role=user one,
    the parent's real human prompt, and the 2KB team-scaffolding developer
    message that follows it — then the child's OWN bootstrap task_started
    (started_at == the fork) and its work."""
    p = tmp_path / "rollout-2026-07-30T12-19-59-child.jsonl"

    def _msg(role, text):
        return {"type": "response_item", "timestamp": fork_iso,
                "payload": {"type": "message", "role": role,
                            "content": [{"type": "input_text", "text": text}]}}

    recs = [
        {"type": "session_meta", "timestamp": fork_iso,
         "payload": {"thread_source": "subagent", "timestamp": fork_iso,
                     "parent_thread_id": "PARENT", "agent_nickname": "Pauli",
                     "source": {"subagent": {"thread_spawn": {
                         "parent_thread_id": "PARENT"}}}}},
        {"type": "session_meta", "timestamp": fork_iso,
         "payload": {"thread_source": "user", "originator": "codex-tui"}},
        # --- parent's replayed turn (started BEFORE the fork) ---
        {"type": "event_msg", "timestamp": fork_iso,
         "payload": {"type": "task_started", "started_at": fork_epoch - 15}},
        _msg("developer", "<permissions instructions>\nFilesystem sandboxing…"),
        _msg("user", "<environment_context>\n  <cwd>/w</cwd>\n</environment_context>"),
        _msg("user", "run a subagent for weather"),
        {"type": "event_msg", "timestamp": fork_iso,
         "payload": {"type": "user_message", "message": "run a subagent for weather"}},
        {"type": "event_msg", "timestamp": fork_iso,
         "payload": {"type": "agent_message", "message": "I'll delegate that."}},
        # the team-scaffolding brief codex hands every agent — role=developer, and
        # carrying no task text at all (measured: 2.1KB of spawn_agent/
        # concurrency-slot instructions)
        _msg("developer", "You are an agent in a team of agents collaborating to "
                          "complete a task.\n\nYou can spawn sub-agents…"),
        # --- child's OWN bootstrap task_started (started_at == the fork) ---
        {"type": "event_msg", "timestamp": fork_iso,
         "payload": {"type": "task_started", "started_at": fork_epoch}},
        # --- the child's own work ---
        {"type": "event_msg", "timestamp": "2026-07-30T12:20:11.379Z",
         "payload": {"type": "agent_message", "message": "Checking Bali forecast."}},
    ]
    p.write_text("".join(json.dumps(r) + "\n" for r in recs), encoding="utf-8")
    return str(p)


def test_subagent_fork_epoch_only_for_a_subagent_rollout(tmp_path):
    sub = _write_subagent_rollout(tmp_path)
    assert RO.subagent_fork_epoch(sub) == 1785413999
    # a normal rollout (no thread_source==subagent) has no fork epoch
    normal = tmp_path / "rollout-normal.jsonl"
    normal.write_text(json.dumps({"type": "session_meta", "payload": {
        "cwd": "/w", "originator": "codex-tui"}}) + "\n", encoding="utf-8")
    assert RO.subagent_fork_epoch(str(normal)) is None


def test_subagent_body_offset_skips_the_replayed_parent_prefix(tmp_path):
    """The byte offset lands past the child's bootstrap task_started, so a reader
    starting there sees only the child's OWN turn — never the parent's replayed
    prompt/message."""
    sub = _write_subagent_rollout(tmp_path)
    off = RO.subagent_body_offset(sub)
    assert off > 0
    tail = open(sub, "rb").read()[off:].decode()
    assert "Checking Bali forecast." in tail            # child's own work kept
    assert "run a subagent for weather" not in tail      # parent prompt dropped
    assert "I'll delegate that." not in tail             # parent message dropped
    # a normal rollout is never trimmed (fail-open)
    normal = tmp_path / "rollout-normal.jsonl"
    normal.write_text(json.dumps({"type": "session_meta", "payload": {
        "cwd": "/w"}}) + "\n" + json.dumps(_ev("user_message", message="hi")) + "\n",
        encoding="utf-8")
    assert RO.subagent_body_offset(str(normal)) == 0


def test_subagent_brief_is_the_last_human_turn_before_the_bootstrap(tmp_path):
    """The brief behind a codex subagent's launch card.

    Measured on the real child rollout: the child's own NEW_TASK record carries
    the task as an `encrypted_content` part, so it CANNOT be read — the only
    plaintext statement of why the child exists is the last real human turn of
    the replayed-parent prefix. Everything else in that prefix is excluded
    STRUCTURALLY, needing no preamble heuristic: the `<environment_context>`
    injection by the `<tag>` rule, and the team-scaffolding brief ("You are an
    agent in a team of agents…") by its role=developer system channel."""
    sub = _write_subagent_rollout(tmp_path)
    assert RO.subagent_brief(sub) == "run a subagent for weather"
    # a NORMAL rollout is not a subagent and has no brief (never the lead's prose)
    normal = tmp_path / "rollout-normal.jsonl"
    normal.write_text(json.dumps({"type": "session_meta", "payload": {
        "cwd": "/w", "originator": "codex-tui"}}) + "\n", encoding="utf-8")
    assert RO.subagent_brief(str(normal)) == ""
    # …and so does a missing file (fail-open, never an exception into a tailer)
    assert RO.subagent_brief(str(tmp_path / "nope.jsonl")) == ""


def test_subagent_brief_unwraps_a_task_wrapper(tmp_path):
    """When codex delivers the task UNencrypted it is a `<task>…</task>` role=user
    turn — an INPUT wrapper, which is kept (not treated as machinery) and reduced
    to its inner text by the shared strip_input_wrapper, so the card opens on the
    task rather than on markup."""
    fork_iso, fork_epoch = "2026-07-30T12:19:59.556Z", 1785413999
    p = tmp_path / "rollout-2026-07-30T12-19-59-task.jsonl"
    recs = [
        {"type": "session_meta", "timestamp": fork_iso,
         "payload": {"thread_source": "subagent", "timestamp": fork_iso,
                     "source": {"subagent": {"thread_spawn": {}}}}},
        {"type": "response_item", "payload": {
            "type": "message", "role": "user", "content": [
                {"type": "input_text",
                 "text": "<task>\nGet the weather in Bali\n</task>"}]}},
        {"type": "event_msg", "payload": {"type": "task_started",
                                          "started_at": fork_epoch}},
        # the child's own turn — AFTER the bootstrap, so never the brief
        {"type": "response_item", "payload": {
            "type": "message", "role": "user",
            "content": [{"type": "input_text", "text": "a later child turn"}]}},
    ]
    p.write_text("".join(json.dumps(r) + "\n" for r in recs), encoding="utf-8")
    assert RO.subagent_brief(str(p)) == "Get the weather in Bali"


def test_is_child_bootstrap_needs_the_fork_epoch():
    parent = RO.parse(_ev("task_started", started_at=1785413984))
    child = RO.parse(_ev("task_started", started_at=1785413999))
    assert not RO.is_child_bootstrap(parent, 1785413999)
    assert RO.is_child_bootstrap(child, 1785413999)
    assert not RO.is_child_bootstrap(child, None)       # not a subagent rollout
    assert not RO.is_child_bootstrap(RO.parse(_ev("user_message", message="x")),
                                     1785413999)


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

def _renderer(tmp_path, name="dedup"):
    """A stream Renderer bound to a scratch mirror log — the same in-process paint
    path the tailer drives (`feed_rollout`), for the two RENDERER guards below.
    They live in this file, beside the drift contract, because each exists to
    protect a decision made in the PARSER: one kind answered from two registers,
    and one call_id shared by a request and its answer."""
    log = str(tmp_path / ("claude-mirror-%s.log" % name))
    ST._init(["claude-codex-stream.py", log, "1,2,3",
              str(tmp_path / "r.jsonl"), "-", "run"])
    return ST.Renderer(), log


def _labels(log):
    from core import render as R
    from core import state as S
    _last, ops = S.ops_after(log, 0)
    return [R.strip_ansi(o.get("s") or "") for o in ops if o.get("t") == "label"]


def test_the_renderer_collapses_a_search_that_arrives_from_both_registers(tmp_path):
    """`search` is the one kind BOTH rollout registers can answer, so a codex
    build that wrote both would hand the renderer the same search twice. An
    immediately-repeated query paints once; a genuine repeat LATER (anything else
    painted in between) still gets its own block — which is why the guard is
    adjacency and not a seen-set."""
    rd, log = _renderer(tmp_path)
    # (the stream's name rides as the op's own `who` field, so the chip TEXT is
    # just the marker — core/ops.py)
    rd.feed_rollout({"kind": "search", "query": "weather Bali"})
    rd.feed_rollout({"kind": "search", "query": "weather Bali"})   # the twin
    assert [s for s in _labels(log) if "search" in s] == ["⌕ search"]
    rd.feed_rollout({"kind": "task_started", "at": 1, "ts": None})
    rd.feed_rollout({"kind": "search", "query": "weather Bali"})
    assert [s for s in _labels(log) if "search" in s] == ["⌕ search", "⌕ search"]


def test_a_tool_calls_answer_lands_behind_its_own_request(tmp_path):
    """A `tool` record opens a `· <name>` block and its output closes THAT block —
    paired by call_id, because codex returns every custom-tool output through one
    output record that carries no tool name. Without the pairing the answer landed
    as a loose row (or, for an exec-shaped close, under the wrong header)."""
    rd, log = _renderer(tmp_path, "tool")
    rd.feed_rollout({"kind": "tool", "name": "web__run",
                     "args": '{q:"bali"}', "call_id": "c1"})
    rd.feed_rollout({"kind": "exec_result", "exit": None, "call_id": "c1",
                     "output": "27°C, scattered clouds", "ts": None})
    from core import render as R
    from core import state as S
    _last, ops = S.ops_after(log, 0)
    body = [o for o in ops if o.get("t") == "gut"]
    assert _labels(log) == ["· web__run"]        # `who` is a field, not text
    assert [o.get("g") for o in body] == [ops[0].get("g")] * 2, \
        "the request and its answer must share the block's copy group"
    assert '{q:"bali"}' in R.strip_ansi(body[0]["s"])
    assert "27°C" in R.strip_ansi(body[1]["s"])


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


def test_codex_prices_the_menu_models_and_refuses_to_guess():
    """CODEX_PRICES vs the published rates, and — the load-bearing half — the
    models it must still REFUSE to price.

    Every model the codex menu offers is now priced, from
    developers.openai.com/api/docs/pricing (retrieved 2026-07-31, each rate
    cross-checked on that model's own docs page). One row's arithmetic is
    spelled out end to end so a table edit that transposes a column fails here
    rather than in a scoreboard nobody reconciles.

    The refusals are the point of the version-exact prefix rule. There is no
    family-level `gpt-5.6` rate — sol, terra and luna are three separately
    priced models (terra and luna are the two that every third-party aggregator
    gets wrong, luna by 5x) — so an UNKNOWN `gpt-5.6-*` variant must fall through
    to "no cost shown", not inherit a sibling's rate. A bare `gpt-5.6` row would
    break exactly that, which is why there isn't one."""
    # gpt-5.6-terra: $2.00 in / $12.00 out per MTok, cached at 0.1x input.
    # 1M fresh + 1M cached + 1M out = 2.00 + 0.20 + 12.00
    assert ST.codex_cost_usd("gpt-5.6-terra", 1_000_000, 1_000_000,
                             1_000_000) == 14.20
    # and it scales linearly off that one verified row
    assert ST.codex_cost_usd("gpt-5.6-terra", 500_000, 0, 0) == 1.00

    # the whole menu is priced (docs/codex.md — the model picker's options)
    menu = ("gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna",
            "gpt-5.5", "gpt-5.4", "gpt-5.4-mini")
    for m in menu:
        assert ST.codex_cost_usd(m, 1_000_000, 0, 0) is not None, m
    # each at its OWN rate — no two of the gpt-5.6 trio share one, and
    # `gpt-5.4-mini` is not billed as `gpt-5.4` (the row-order hazard)
    rate = {m: ST.codex_cost_usd(m, 1_000_000, 0, 0) for m in menu}
    assert rate["gpt-5.6-sol"] == 5.00
    assert rate["gpt-5.6-terra"] == 2.00
    assert rate["gpt-5.6-luna"] == 0.20
    assert rate["gpt-5.4-mini"] == 0.75 and rate["gpt-5.4"] == 2.50

    # an UNVERIFIED variant stays unpriced rather than borrowing a sibling's
    assert ST.codex_cost_usd("gpt-5.6-nova", 1_000_000, 0, 0) is None
    assert ST.codex_cost_usd("gpt-5.6", 1_000_000, 0, 0) is None
    assert ST.codex_cost_usd("", 1, 0, 0) is None
