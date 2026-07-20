# L1e — the transcript parser (plugins/claude_code/transcript.py): the parse
# half of the substream's parse/paint split, and the timeline read model behind
# plugins.activity(). Renderer equivalence is covered by the existing substream
# suites (l1c dispatch + the e2e flows) — these tests pin the parser's record
# contract and the timeline's pairing/dedup semantics directly.
import json
import os
import sys

from conftest import REPO

if REPO not in sys.path:
    sys.path.insert(0, REPO)

from plugins.claude_code import transcript as TR


def _l(o):
    return json.dumps(o, ensure_ascii=False)


# ------------------------------------------------------------------ parse_line

def test_bad_json_is_a_bad_record():
    rec = TR.parse_line("{nope")
    assert rec["kind"] == "bad" and rec["raw"] == "{nope"


def test_compact_boundary():
    rec = TR.parse_line(_l({"type": "system", "subtype": "compact_boundary",
                            "compactMetadata": {"preTokens": 9}}))
    assert rec == {"kind": "compact", "meta": {"preTokens": 9}}


def test_blank_user_content_is_none():
    assert TR.parse_line(_l({"type": "user", "message": {"content": "  \n"}})) is None


def test_prompt_keeps_unstripped_text():
    # The renderer strips at paint (cap(text.strip())) — the parser must not
    # pre-strip, or the pre-split byte-identical contract breaks.
    rec = TR.parse_line(_l({"type": "user", "message": {"content": "  hi\n"}}))
    assert rec == {"kind": "prompt", "text": "  hi\n"}


def test_teammate_message_unwraps_sender_and_body():
    body = '<teammate-message teammate_id="lead" color="red">do the thing</teammate-message>'
    rec = TR.parse_line(_l({"type": "user", "message": {"content": body}}))
    assert rec == {"kind": "teammsg", "sender": "lead", "body": "do the thing"}


def test_results_collects_blocks_in_order_plus_texts():
    rec = TR.parse_line(_l({
        "type": "user", "toolUseResult": {"file": {"numLines": 3}},
        "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "one"},
            {"type": "text", "text": "a parent-transcript user turn"},
            {"type": "tool_result", "tool_use_id": "t2", "content": "two",
             "is_error": True},
        ]}}))
    assert rec["kind"] == "results"
    assert [b["tool_use_id"] for b in rec["blocks"]] == ["t1", "t2"]
    assert rec["tur"] == {"file": {"numLines": 3}}
    assert rec["texts"] == ["a parent-transcript user turn"]


def test_user_list_without_results_or_texts_is_none():
    rec = TR.parse_line(_l({"type": "user", "message": {"content": [
        {"type": "text", "text": "   "}, "loose string"]}}))
    assert rec is None


def test_assistant_blocks_preserve_order_and_skip_thinking():
    rec = TR.parse_line(_l({"type": "assistant", "message": {
        "id": "m1", "model": "claude-opus-4-8",
        "usage": {"input_tokens": 5, "output_tokens": 2},
        "content": [{"type": "thinking", "thinking": "…"},
                    {"type": "text", "text": "hi"},
                    {"type": "tool_use", "id": "t1", "name": "Bash",
                     "input": {"command": "ls"}}]}}))
    assert rec["kind"] == "assistant" and rec["id"] == "m1"
    assert rec["model"] == "claude-opus-4-8"
    assert rec["blocks"][0] == ("text", "hi")
    assert rec["blocks"][1][0] == "tool" and rec["blocks"][1][1]["name"] == "Bash"


def test_assistant_without_content_list_still_yields_record():
    # Usage/turn tracking must run even for a blocks-less assistant line.
    rec = TR.parse_line(_l({"type": "assistant",
                            "message": {"usage": {"input_tokens": 1}}}))
    assert rec["kind"] == "assistant" and rec["blocks"] == []
    rec2 = TR.parse_line(_l({"type": "assistant", "message": {}}))
    assert rec2["kind"] == "assistant" and rec2["usage"] is None


def test_unknown_type_is_none():
    assert TR.parse_line(_l({"type": "summary", "summary": "x"})) is None


def test_queued_command_attachment_is_a_prompt():
    # A message queued mid-turn is delivered ONLY as this attachment (never a
    # plain user string) — surface it as a prompt so the dashboard mirror shows
    # it AND the composer's ⧗ chip drains (the "stuck queued message" report).
    rec = TR.parse_line(_l({"type": "attachment", "attachment": {
        "type": "queued_command", "commandMode": "prompt",
        "origin": {"kind": "human"}, "prompt": "ship it\nnow"}}))
    assert rec == {"kind": "prompt", "text": "ship it\nnow"}


def test_task_notification_queued_command_is_none():
    # The harness re-injects task notifications as queued_command too, but they
    # are commandMode=="task-notification" — not user turns, so kept out.
    rec = TR.parse_line(_l({"type": "attachment", "attachment": {
        "type": "queued_command", "commandMode": "task-notification",
        "prompt": "<task-notification>\n<task-id>x</task-id>"}}))
    assert rec is None


def test_non_queued_attachment_is_none():
    assert TR.parse_line(_l({"type": "attachment", "attachment": {
        "type": "skill_listing", "content": "..."}})) is None


def _mon_note(*, task="b6c8b6c9r", summary="Monitor event: \"watch\"",
              event=None, status=None):
    parts = ["<task-notification>", "<task-id>%s</task-id>" % task,
             "<summary>%s</summary>" % summary]
    if event is not None:
        parts.append("<event>%s</event>" % event)
    if status is not None:
        parts.append("<status>%s</status>" % status)
    parts.append("</task-notification>")
    return "\n".join(parts)


def test_monitor_event_queue_operation_is_a_monitor_event():
    # A Monitor tool EVENT is delivered mid-turn as a queue-operation record
    # whose content is a <task-notification> block — surfaced for the drill-down.
    rec = TR.parse_line(_l({"type": "queue-operation", "operation": "enqueue",
                            "content": _mon_note(event="event 1: something")}))
    assert rec == {"kind": "monitor_event", "task": "b6c8b6c9r",
                   "summary": 'Monitor event: "watch"',
                   "event": "event 1: something", "status": None}


def test_monitor_stream_ended_notification_carries_status_not_event():
    rec = TR.parse_line(_l({"type": "queue-operation",
                            "content": _mon_note(summary="stream ended",
                                                 status="completed")}))
    assert rec["kind"] == "monitor_event"
    assert rec["status"] == "completed"
    assert rec["event"] is None


def test_non_task_notification_queue_operation_is_none():
    # queue-operation carries other harness traffic too — only task-notifications
    # are monitor events.
    assert TR.parse_line(_l({"type": "queue-operation",
                             "content": "some other queue payload"})) is None


def test_conversation_surfaces_delivered_queued_message(tmp_path):
    # End-to-end at the conversation() layer (the dashboard's provider): the
    # typed prompt AND the mid-turn queued one both land as prompt records; the
    # task-notification re-injection does not.
    p = tmp_path / "c.jsonl"
    p.write_text("".join(_l(o) + "\n" for o in [
        {"type": "user", "message": {"content": "first prompt"},
         "timestamp": "2026-07-20T00:46:56.000Z"},
        {"type": "attachment", "attachment": {
            "type": "queued_command", "commandMode": "prompt",
            "origin": {"kind": "human"}, "prompt": "queued while busy"},
         "timestamp": "2026-07-20T00:47:41.000Z"},
        {"type": "attachment", "attachment": {
            "type": "queued_command", "commandMode": "task-notification",
            "prompt": "<task-notification>\n<task-id>x</task-id>"},
         "timestamp": "2026-07-20T00:47:42.000Z"},
    ]), encoding="utf-8")
    recs, _ = TR.conversation(str(p), 0)
    prompts = [r["text"] for r in recs if r["kind"] == "prompt"]
    assert prompts == ["first prompt", "queued while busy"]


# ------------------------------------------------------------------ agent_paths

def test_agent_paths_layout():
    j, m = TR.agent_paths("/x/session-abc.jsonl", "ag1")
    assert j == "/x/session-abc/subagents/agent-ag1.jsonl"
    assert m == "/x/session-abc/subagents/agent-ag1.meta.json"
    # a non-.jsonl base is used verbatim
    j2, _ = TR.agent_paths("/x/session-abc", "ag1")
    assert j2 == "/x/session-abc/subagents/agent-ag1.jsonl"


# ------------------------------------------------------------------ timeline

def _write(tmp_path, lines):
    p = tmp_path / "t.jsonl"
    p.write_text("".join(_l(o) + "\n" for o in lines), encoding="utf-8")
    return str(p)


def test_timeline_pairs_results_and_dedups_usage(tmp_path):
    path = _write(tmp_path, [
        {"type": "user", "message": {"content": "do the thing"}},
        {"type": "assistant", "message": {
            "id": "m1", "model": "claude-opus-4-8",
            "usage": {"input_tokens": 10, "output_tokens": 3},
            "content": [{"type": "tool_use", "id": "t1", "name": "Bash",
                         "input": {"command": "ls"}}]}},
        # second JSONL line of the SAME message (per-content-block write):
        # usage must fold as a delta, not double-count (the 2.2× bug class).
        {"type": "assistant", "message": {
            "id": "m1", "model": "claude-opus-4-8",
            "usage": {"input_tokens": 10, "output_tokens": 7},
            "content": [{"type": "text", "text": "listing done"}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1",
             "content": "a.txt\nb.txt", "is_error": False}]}},
        {"type": "assistant", "message": {
            "id": "m2", "usage": {"input_tokens": 4, "output_tokens": 2},
            "content": [{"type": "text", "text": "all good"}]}},
    ])
    tl = TR.timeline(path)
    kinds = [e["t"] for e in tl["entries"]]
    assert kinds == ["prompt", "tool", "message", "message"]
    tool = tl["entries"][1]
    assert tool["tool"] == "Bash" and tool["input"] == {"command": "ls"}
    assert tool["output"] == "a.txt\nb.txt" and tool["failed"] is False
    assert tl["entries"][-1]["final"] is True          # the returned result
    assert "final" not in tl["entries"][2]
    assert tl["usage"] == {"in": 14, "out": 9, "cache": 0,
                           "create": 0, "create_1h": 0}
    assert tl["tools"] == 1 and tl["model"] == "claude-opus-4-8"
    assert tl["bad_lines"] == 0


def test_timeline_orphan_result_and_failed_flag(tmp_path):
    path = _write(tmp_path, [
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "never-seen",
             "content": "boom", "is_error": True}]}},
    ])
    tl = TR.timeline(path)
    assert tl["entries"] == [{"t": "orphan-result", "output": "boom",
                              "failed": True}]


def test_timeline_renders_parent_style_user_text_blocks(tmp_path):
    # A PARENT transcript's user turns arrive as text blocks inside list
    # content — invisible to the mirror renderer (deliberately), but the
    # timeline is the full-fidelity view and must surface them.
    path = _write(tmp_path, [
        {"type": "user", "message": {"content": [
            {"type": "text", "text": "please fix the bug"}]}},
    ])
    tl = TR.timeline(path)
    assert tl["entries"] == [{"t": "prompt", "text": "please fix the bug"}]


def test_timeline_counts_bad_lines(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text('{"type": "user", "message": {"content": "hi"}}\n{oops\n',
                 encoding="utf-8")
    tl = TR.timeline(str(p))
    assert tl["bad_lines"] == 1 and tl["entries"][0]["t"] == "prompt"


def test_timeline_surfaces_monitor_launch_and_events(tmp_path):
    # The Monitor launch is a `tool` entry (name "Monitor"); its EVENTS follow as
    # `monitor` entries — the full drill-down story of a monitor. The mirror shows
    # events via ops, so they must NOT also appear in conversation() (dupe).
    path = _write(tmp_path, [
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "Monitor",
             "input": {"command": "tail -f log", "description": "watch"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1",
             "content": "Monitor started"}]}},
        {"type": "queue-operation", "content": _mon_note(event="line A")},
        {"type": "queue-operation", "content": _mon_note(event="line B")},
        {"type": "queue-operation", "content": _mon_note(summary="ended",
                                                         status="completed")},
    ])
    tl = TR.timeline(path)
    kinds = [e["t"] for e in tl["entries"]]
    assert kinds == ["tool", "monitor", "monitor", "monitor"]
    assert tl["entries"][0]["tool"] == "Monitor"
    assert [e.get("event") for e in tl["entries"][1:]] == ["line A", "line B", None]
    assert tl["entries"][3]["status"] == "completed"
    # conversation() (the dashboard mirror provider) must drop monitor events —
    # they already ride the ops stream, so surfacing here would double them.
    recs, _ = TR.conversation(path, 0)
    assert not any(r["kind"] == "monitor" for r in recs)


# ------------------------------------------------------------ timeline_since

def _append(path, lines):
    with open(path, "a", encoding="utf-8") as fh:
        for o in lines:
            fh.write(_l(o) + "\n")


def test_timeline_since_pairs_within_and_resolves_across_increments(tmp_path):
    path = _write(tmp_path, [
        {"type": "user", "message": {"content": "go"}},
        {"type": "assistant", "message": {
            "id": "m1", "content": [
                {"type": "tool_use", "id": "t1", "name": "Bash",
                 "input": {"command": "ls"}},
                {"type": "tool_use", "id": "t2", "name": "Read",
                 "input": {"file_path": "/x"}}]}},
        # t2 resolves in the SAME window -> paired in place, no resolution
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t2", "content": "file body"}]}},
    ])
    ents, res, pos1 = TR.timeline_since(path, 0)
    assert [e["t"] for e in ents] == ["prompt", "tool", "tool"]
    assert ents[1]["id"] == "t1" and "output" not in ents[1]   # unresolved
    assert ents[2]["id"] == "t2" and ents[2]["output"] == "file body"
    assert res == [] and pos1 > 0

    # second increment: t1's result (its tool_use was in the PRIOR window ->
    # a cross-increment resolution, not an in-place patch) + a final message.
    _append(path, [
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1",
             "content": "a\nb", "is_error": False}]}},
        {"type": "assistant", "message": {
            "id": "m2", "content": [{"type": "text", "text": "done"}]}},
    ])
    ents2, res2, pos2 = TR.timeline_since(path, pos1)
    assert [e["t"] for e in ents2] == ["message"]      # no orphan-result entry
    assert res2 == [("t1", "a\nb", False)] and pos2 > pos1
    assert "final" not in ents2[-1]                    # increments never mark final


def test_timeline_since_reads_no_torn_record(tmp_path):
    # a trailing partial line (a mid-write tail) is not consumed; the cursor
    # stops before it, and completing the line makes it readable next call.
    p = tmp_path / "t.jsonl"
    p.write_text(_l({"type": "user", "message": {"content": "hi"}}) + "\n"
                 + '{"type": "assist', encoding="utf-8")
    ents, res, pos = TR.timeline_since(str(p), 0)
    assert [e["t"] for e in ents] == ["prompt"] and res == []
    with open(p, "a", encoding="utf-8") as fh:
        fh.write('ant", "message": {"content": '
                 '[{"type": "text", "text": "ok"}]}}\n')
    ents2, _res, _pos = TR.timeline_since(str(p), pos)
    assert [e["t"] for e in ents2] == ["message"]


def test_timeline_since_concatenation_matches_whole_file(tmp_path):
    # entries across increments + applied resolutions reproduce what a
    # whole-file timeline() reports for the finished transcript.
    lines = [
        {"type": "user", "message": {"content": "go"}},
        {"type": "assistant", "message": {
            "id": "m1", "content": [
                {"type": "tool_use", "id": "t1", "name": "Bash",
                 "input": {"command": "ls"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "out"}]}},
        {"type": "assistant", "message": {
            "id": "m2", "content": [{"type": "text", "text": "done"}]}},
    ]
    path = _write(tmp_path, lines[:2])
    ents, res, pos = TR.timeline_since(path, 0)
    _append(path, lines[2:])
    ents2, res2, _pos = TR.timeline_since(path, pos)
    merged = ents + ents2
    for tid, out, failed in res + res2:                # the consumer's fill-in
        for e in merged:
            if e.get("id") == tid:
                e["output"], e["failed"] = out, failed
    whole = TR.timeline(path)["entries"]
    whole[-1].pop("final", None)                        # increments don't mark it
    assert merged == whole


# ------------------------------------------------------------- context_probe

def _wt(tmp_path, name, *objs):
    p = tmp_path / name
    p.write_text("".join(json.dumps(o) + "\n" for o in objs))
    return str(p)


def test_context_probe_last_assistant_usage_wins(tmp_path):
    # The LAST assistant record's usage IS the occupied window (fresh +
    # cache-write + cache-read; output excluded), its model id sizes it.
    p = _wt(tmp_path, "ctx1.jsonl",
            {"type": "user", "message": {"content": "hi"}},
            {"type": "assistant", "message": {"id": "m1", "model": "claude-haiku-4-5",
             "usage": {"input_tokens": 10, "cache_creation_input_tokens": 5,
                       "cache_read_input_tokens": 100, "output_tokens": 999}}},
            {"type": "assistant", "message": {"id": "m2", "model": "claude-haiku-4-5",
             "usage": {"input_tokens": 20, "cache_creation_input_tokens": 0,
                       "cache_read_input_tokens": 79980, "output_tokens": 1}}})
    assert TR.context_probe(p) == {"used": 80000, "window": 200000, "pct": 40,
                                   "model": "claude-haiku-4-5"}


def test_context_probe_main_skips_sidechain(tmp_path):
    # main=True: an inline sidechain turn belongs to its agent — its smaller
    # usage must not paint a phantom shrink over the main thread's fill. An
    # agent's OWN transcript is its sidechain turns, so the default keeps them.
    p = _wt(tmp_path, "ctx2.jsonl",
            {"type": "assistant", "message": {"model": "claude-haiku-4-5",
             "usage": {"input_tokens": 100000, "output_tokens": 2}}},
            {"type": "assistant", "isSidechain": True,
             "message": {"model": "claude-haiku-4-5",
                         "usage": {"input_tokens": 50, "output_tokens": 1}}})
    assert TR.context_probe(p, main=True)["used"] == 100000
    assert TR.context_probe(p)["used"] == 50


def test_context_probe_none_without_usage(tmp_path):
    p = _wt(tmp_path, "ctx3.jsonl",
            {"type": "user", "message": {"content": "hi"}},
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": "no usage yet"}]}})
    assert TR.context_probe(p) is None
    assert TR.context_probe(str(tmp_path / "absent.jsonl")) is None


def test_context_probe_bounded_tail(tmp_path):
    # The no-full-read rule: a usage record buried deeper than CTX_TAIL_B is
    # deliberately out of reach; one within the window is found past torn-line
    # trimming even when the file itself is larger than the window.
    filler = [{"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "t", "content": "x" * 4000}]}}
        for _ in range(80)]
    usage_row = {"type": "assistant", "message": {"model": "claude-haiku-4-5",
                 "usage": {"input_tokens": 77, "output_tokens": 1}}}
    deep = _wt(tmp_path, "ctx4.jsonl", usage_row, *filler)
    assert os.path.getsize(deep) > TR.CTX_TAIL_B
    assert TR.context_probe(deep) is None
    near = _wt(tmp_path, "ctx5.jsonl", *filler, usage_row)
    assert os.path.getsize(near) > TR.CTX_TAIL_B
    assert TR.context_probe(near)["used"] == 77


# ---------------------------------------------------------------- single owner

def test_set_session_title_writer(tmp_path):
    """The write half of the naming channel: appends exactly one agent-name
    line (sessionId from the filename stem) that round-trips through
    session_title; refuses non-projects layouts and never creates a file."""
    d = tmp_path / "projects" / "-w-proj"
    d.mkdir(parents=True)
    p = d / "sid-1.jsonl"
    p.write_text(_l({"type": "ai-title", "aiTitle": "auto"}) + "\n")
    assert TR.set_session_title(str(p), "hand picked") is True
    lines = p.read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[-1]) == {"type": "agent-name",
                                     "agentName": "hand picked",
                                     "sessionId": "sid-1"}
    assert TR.session_title(str(p)) == "hand picked"
    # non-projects layout (a codex rollout): refused, untouched
    r = tmp_path / "rollouts" / "r1.jsonl"
    r.parent.mkdir()
    r.write_text("{}\n")
    assert TR.set_session_title(str(r), "x") is None
    assert r.read_text() == "{}\n"
    # a missing file is never created just to name it
    gone = d / "absent.jsonl"
    assert TR.set_session_title(str(gone), "x") is None
    assert not gone.exists()
    # not a .jsonl at all
    assert TR.set_session_title(str(d / "notes.txt"), "x") is None


def test_agent_name_record_has_one_owner():
    """The `agent-name` naming-record shape is transcript.py's (styleguide
    single-owner table) — reader AND writer; a second encoding anywhere in
    product code is drift. The tell is the `agentName` FIELD literal (prose
    mentions of "agent-name" in docstrings are fine and don't count)."""
    hits = []
    for root in ("core", "plugins", "frontends", "bin", "dashboard"):
        for dirpath, _dirs, files in os.walk(os.path.join(REPO, root)):
            for f in files:
                if not f.endswith(".py"):
                    continue
                p = os.path.join(dirpath, f)
                with open(p, encoding="utf-8", errors="replace") as fh:
                    if "agentName" in fh.read():
                        hits.append(os.path.relpath(p, REPO))
    assert hits == ["plugins/claude_code/transcript.py"], hits


def test_teammsg_regex_has_one_owner():
    """The teammate-message wire shape is transcript.py's (styleguide
    single-owner table) — a second copy anywhere in product code is drift."""
    hits = []
    for root in ("core", "plugins", "frontends", "bin"):
        for dirpath, _dirs, files in os.walk(os.path.join(REPO, root)):
            for f in files:
                if not f.endswith(".py"):
                    continue
                p = os.path.join(dirpath, f)
                with open(p, encoding="utf-8", errors="replace") as fh:
                    if "<teammate-message" in fh.read():
                        hits.append(os.path.relpath(p, REPO))
    assert hits == ["plugins/claude_code/transcript.py"], hits


def test_renderer_aliases_are_the_parser_functions():
    from plugins.claude_code import substream_render as SR
    assert SR.result_text is TR.result_text
    assert SR.input_summary is TR.input_summary
