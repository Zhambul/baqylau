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


def test_away_summary_is_a_recap_record():
    # Claude Code's recap: a `type=system` `subtype=away_summary` line whose
    # plain-string content is the summary. The trailing "(disable recaps in
    # /config)" hint (terminal-only menu) is stripped for the web bubble.
    rec = TR.parse_line(_l({
        "type": "system", "subtype": "away_summary",
        "content": "Fixed the bug; next is QA. (disable recaps in /config)"}))
    assert rec == {"kind": "recap", "text": "Fixed the bug; next is QA."}


def test_empty_away_summary_is_none():
    # A recap whose content is only the config hint (or blank) yields nothing —
    # an empty bubble is worse than no bubble.
    assert TR.parse_line(_l({"type": "system", "subtype": "away_summary",
                             "content": " (disable recaps in /config) "})) is None
    assert TR.parse_line(_l({"type": "system", "subtype": "away_summary"})) is None


def test_blank_user_content_is_none():
    assert TR.parse_line(_l({"type": "user", "message": {"content": "  \n"}})) is None


def test_prompt_keeps_unstripped_text():
    # The renderer strips at paint (cap(text.strip())) — the parser must not
    # pre-strip, or the pre-split byte-identical contract breaks.
    # `meta` rides along ALWAYS (False for a real prompt) rather than only when
    # set: a declared field that is sometimes absent is how a consumer ends up
    # reading a KeyError as "not injected".
    rec = TR.parse_line(_l({"type": "user", "message": {"content": "  hi\n"}}))
    assert rec == {"kind": "prompt", "text": "  hi\n", "meta": False}


def test_teammate_message_unwraps_sender_and_body():
    body = '<teammate-message teammate_id="lead" color="red">do the thing</teammate-message>'
    rec = TR.parse_line(_l({"type": "user", "message": {"content": body}}))
    assert rec == {"kind": "teammsg", "sender": "lead", "body": "do the thing"}


_ENVELOPE = (
    "Another Claude session sent a message:\n"
    '<teammate-message teammate_id="rev-observe" color="purple">\n'
    '{"type":"idle_notification","from":"rev-observe","idleReason":"available"}\n'
    "</teammate-message>\n\n"
    "This came from another Claude session — not typed by your user, but very "
    "likely working on their behalf.")


def test_teammate_mail_envelope_is_an_injected_prompt():
    # Claude Code delivers a peer session's message as a user turn of its OWN
    # making (framing sentence + the peer's block + its own instruction). It
    # carries NO structural flag — no isMeta, userType "external", exactly the
    # shape of a typed prompt (measured on the corpus) — so `meta` has to come
    # from the anchored text shape. Without it the dashboard rendered another
    # session's mail as a YOU bubble, in every view mode.
    rec = TR.parse_line(_l({"type": "user", "userType": "external",
                            "message": {"content": _ENVELOPE}}))
    assert rec["kind"] == "prompt"
    assert rec["meta"] is True


def test_a_message_that_only_QUOTES_an_envelope_stays_yours():
    # The false-positive that makes text-matching dangerous, and why the pattern
    # is anchored: asking "why is this in my transcript?" over a pasted envelope
    # is a real prompt, and must not be relabelled as system and hidden.
    rec = TR.parse_line(_l({"type": "user", "message": {
        "content": "why do I see this?\n\n" + _ENVELOPE}}))
    assert rec["kind"] == "prompt"
    assert rec["meta"] is False
    # …and the mark is the WHOLE shape: the framing sentence alone is not it
    plain = TR.parse_line(_l({"type": "user", "message": {
        "content": "Another Claude session sent a message: it went fine"}}))
    assert plain["meta"] is False


_STOP_FB = ("Stop hook feedback:\nWiki persistence check: if this turn "
            "uncovered any durable fact, persist it before stopping.")


def test_stop_hook_feedback_is_the_injection_that_RESUMED_an_ended_turn():
    # A blocking Stop hook fires BECAUSE the turn ended, so the reply in front of
    # its feedback is that turn's ANSWER — the fact the dashboard's focus mode
    # needs to keep it (docs/dashboard.md, *View modes*). Claude Code writes it
    # as a list-content user record (the shape measured on the corpus), so both
    # prompt-bearing kinds have to carry the flag.
    rec = TR.parse_line(_l({"type": "user", "isMeta": True, "message": {
        "content": [{"type": "text", "text": _STOP_FB}]}}))
    assert rec["kind"] == "results"
    assert rec["meta"] is True
    cx = TR._Conv()
    TR._conv_results(rec, cx)
    assert cx.out[0]["meta"] is True and cx.out[0]["resumed"] is True


def test_a_MID_turn_injection_does_not_claim_to_have_resumed_a_turn():
    # The other injections all arrive with the turn still running (a loaded
    # SKILL.md body, the post-/compact summary, teammate mail): the prose in
    # front of them is commentary, and marking them would manufacture an extra
    # "final" reply per injection — the behaviour focus mode already rejected.
    cx = TR._Conv()
    cx.add_prompt("Base directory for this skill: /x/.claude/skills/slack", True)
    # …and the anchor is what makes the mark safe to read out of text at all: a
    # turn that merely QUOTES the wording (this repo's own docs) is not one.
    cx.add_prompt("we hide the reply because of Stop hook feedback: the nudge",
                  True)
    assert [r.get("resumed") for r in cx.out] == [None, None]


def test_a_bare_teammate_block_is_still_teammate_mail_not_system():
    # The envelope mark must not swallow the UNWRAPPED form: that one already has
    # a sender to name, so it keeps its own ✉ record and bubble.
    rec = TR.parse_line(_l({"type": "user", "message": {
        "content": '<teammate-message teammate_id="lead">ping</teammate-message>'}}))
    assert rec == {"kind": "teammsg", "sender": "lead", "body": "ping"}


def test_a_slash_command_turn_is_unwrapped_into_the_prompt_the_user_typed():
    # A `/command` turn is recorded as a `<command-name>`/`<command-args>`
    # envelope, and the `<`-wrapper filter used to drop it with the teammate/
    # caveat plumbing — so a session opened by a slash command showed no first
    # message at all (for a skill, the first bubble was the loaded SKILL.md body
    # that follows it). The ARGS are the message, and they are regularly
    # MULTI-LINE — a sid on one line, the question on the next.
    cx = TR._Conv()
    cx.add_prompt("<command-message>audit-debug</command-message>\n"
                  "<command-name>/audit-debug</command-name>\n"
                  "<command-args>63209ded\nwhy is my first message missing"
                  "</command-args>", False)
    assert [r["text"] for r in cx.out] == [
        "/audit-debug 63209ded\nwhy is my first message missing"]
    # the argless form (and the tag order the CLI's own commands use) is the name
    cx = TR._Conv()
    cx.add_prompt("<command-name>/compact</command-name>\n"
                  "    <command-message>compact</command-message>\n"
                  "    <command-args></command-args>", False)
    assert [r["text"] for r in cx.out] == ["/compact"]


def test_the_other_bracket_wrappers_are_still_dropped():
    # The unwrap is gated on the `<command-name>` TAG, which is what separates a
    # typed command from the two other local-command records (measured: neither
    # carries one) — the caveat INJECTION and the command's own echoed OUTPUT.
    # Relaxing that gate to "starts with <command" would surface the stdout.
    cx = TR._Conv()
    cx.add_prompt("<local-command-caveat>Caveat: The messages below were "
                  "generated by the user while running local commands."
                  "</local-command-caveat>", True)
    cx.add_prompt("<local-command-stdout>Compacted (ctrl+o to see full "
                  "summary)</local-command-stdout>", False)
    assert cx.out == []


def test_a_slash_command_turn_is_prunable_like_any_prompt():
    # Display and the discard prune share _command_text, so a command turn
    # cannot be visible in the stream yet invisible to _dead_uuids (a slash
    # command taken back with Esc-Esc has to prune like a typed one).
    rec = TR.parse_line(_l({"type": "user", "message": {
        "content": "<command-name>/audit-debug</command-name>\n"
                   "<command-args>sid here</command-args>"}}))
    assert TR._prompt_bearing(rec) is True
    for wrapper in ("<local-command-stdout>out</local-command-stdout>",
                    '<teammate-message teammate_id="lead">ping</teammate-message>'):
        assert TR._prompt_bearing({"kind": "prompt", "text": wrapper}) is False


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
    # a monitor's stream-ended notification: no <event>, but a "Monitor …" summary
    rec = TR.parse_line(_l({"type": "queue-operation",
                            "content": _mon_note(summary='Monitor "x" stream ended',
                                                 status="completed")}))
    assert rec["kind"] == "monitor_event"
    assert rec["status"] == "completed"
    assert rec["event"] is None


def test_non_task_notification_queue_operation_is_none():
    # queue-operation carries other harness traffic too — only task-notifications
    # are monitor events.
    assert TR.parse_line(_l({"type": "queue-operation",
                             "content": "some other queue payload"})) is None


def test_background_completion_notification_is_not_a_monitor_event():
    # Background-job completions ride the SAME <task-notification> mechanism
    # (summary "Background command … completed") — they must NOT be parsed as
    # monitor events (they'd become phantom monitors + mislabel the timeline).
    rec = TR.parse_line(_l({"type": "queue-operation", "content":
        _mon_note(task="bgtask", status="completed",
                  summary='Background command "build" completed (exit code 0)')}))
    assert rec is None


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


def test_conversation_drops_a_discarded_prompt(tmp_path):
    # Esc-Esc right after a send DISCARDS the prompt (Claude Code hands it back
    # to the input) — but nothing is deleted from the transcript: the next
    # prompt just re-parents to the SAME parentUuid, orphaning the dead one.
    # conversation() must drop it, or the dashboard replays a message the
    # terminal already took back (the 2026-07-25 report).
    p = tmp_path / "d.jsonl"
    p.write_text("".join(_l(o) + "\n" for o in [
        {"type": "user", "uuid": "u1", "parentUuid": None,
         "message": {"content": "real one"},
         "timestamp": "2026-07-25T00:00:01.000Z"},
        {"type": "assistant", "uuid": "a1", "parentUuid": "u1",
         "message": {"content": [{"type": "text", "text": "on it"}]},
         "timestamp": "2026-07-25T00:00:02.000Z"},
        {"type": "user", "uuid": "u2", "parentUuid": "a1",
         "message": {"content": "testing"},
         "timestamp": "2026-07-25T00:00:03.000Z"},
        {"type": "user", "uuid": "u3", "parentUuid": "a1",
         "message": {"content": "what I meant"},
         "timestamp": "2026-07-25T00:00:04.000Z"},
    ]), encoding="utf-8")
    recs, _ = TR.conversation(str(p), 0)
    assert [r["text"] for r in recs if r["kind"] == "prompt"] \
        == ["real one", "what I meant"]
    # the survivor carries its tree position AND its own id — the page prunes
    # live off `par`, the take-back stash names a record by `uid`
    assert [r["par"] for r in recs if r["kind"] == "prompt"] == [None, "a1"]
    assert [r["uid"] for r in recs if r["kind"] == "prompt"] == ["u1", "u3"]


def test_conversation_drops_a_flagged_take_back_before_its_sibling(tmp_path):
    # A prompt Claude Code handed BACK to the input box is orphaned — but only
    # once the replacement arrives. Until then it has no sibling and looks
    # exactly like a live prompt, so the dashboard's observation (the uuid it
    # stashed when it found the message in the box) is what keeps the bubble
    # gone across a reload ("it reappeared in the transcript", 2026-07-25).
    p = tmp_path / "tb.jsonl"
    p.write_text("".join(_l(o) + "\n" for o in [
        {"type": "user", "uuid": "u1", "parentUuid": None,
         "message": {"content": "the one that ran"},
         "timestamp": "2026-07-25T00:00:01.000Z"},
        {"type": "assistant", "uuid": "a1", "parentUuid": "u1",
         "message": {"content": [{"type": "text", "text": "done"}]},
         "timestamp": "2026-07-25T00:00:02.000Z"},
        {"type": "user", "uuid": "u2", "parentUuid": "a1",
         "message": {"content": "taken back"},
         "timestamp": "2026-07-25T00:00:03.000Z"},
    ]), encoding="utf-8")
    assert [r["text"] for r in TR.conversation(str(p), 0)[0]
            if r["kind"] == "prompt"] == ["the one that ran", "taken back"]
    recs, _ = TR.conversation(str(p), 0, suspects=("u2",))
    assert [r["text"] for r in recs if r["kind"] == "prompt"] \
        == ["the one that ran"]


def test_a_flagged_prompt_with_children_is_kept(tmp_path):
    # The flag is ADVISORY and self-correcting: the observer reads a screen and
    # can be wrong (you might have retyped the same text into the box yourself),
    # but the transcript can't be — anything descending from that prompt proves
    # the turn really ran, so it stays.
    p = tmp_path / "tb2.jsonl"
    p.write_text("".join(_l(o) + "\n" for o in [
        {"type": "user", "uuid": "u1", "parentUuid": None,
         "message": {"content": "not really taken back"},
         "timestamp": "2026-07-25T00:00:01.000Z"},
        {"type": "assistant", "uuid": "a1", "parentUuid": "u1",
         "message": {"content": [{"type": "text", "text": "it ran"}]},
         "timestamp": "2026-07-25T00:00:02.000Z"},
    ]), encoding="utf-8")
    recs, _ = TR.conversation(str(p), 0, suspects=("u1",))
    assert [(r["kind"], r["text"]) for r in recs] \
        == [("prompt", "not really taken back"), ("message", "it ran")]


def test_a_flagged_prompt_whose_child_is_unparsable_is_kept(tmp_path):
    # …and "anything descending from it" means anything IN THE FILE, not just
    # what parses into a conversation record. Claude Code hangs an `attachment`
    # record off a prompt (a directory listing, a nested memory file); parse_line
    # yields nothing for one, and the rest of the turn parents to IT. Reading the
    # rescue off the parsed subset alone therefore called the prompt childless,
    # while the expansion walked the raw tree straight through that attachment
    # and pruned the whole session — a real session (2026-07-30) showed NO
    # messages at all in the dashboard, its own first prompt included.
    p = tmp_path / "tb3.jsonl"
    p.write_text("".join(_l(o) + "\n" for o in [
        {"type": "user", "uuid": "u1", "parentUuid": None,
         "message": {"content": "the flagged one"},
         "timestamp": "2026-07-30T00:00:01.000Z"},
        {"type": "attachment", "uuid": "x1", "parentUuid": "u1",
         "attachment": {"type": "nested_memory"},
         "timestamp": "2026-07-30T00:00:02.000Z"},
        {"type": "assistant", "uuid": "a1", "parentUuid": "x1",
         "message": {"content": [{"type": "text", "text": "it ran"}]},
         "timestamp": "2026-07-30T00:00:03.000Z"},
    ]), encoding="utf-8")
    recs, _ = TR.conversation(str(p), 0, suspects=("u1",))
    assert [(r["kind"], r["text"]) for r in recs] \
        == [("prompt", "the flagged one"), ("message", "it ran")]


def test_conversation_drops_a_rewound_away_turn(tmp_path):
    # A rewind supersedes the restored-to prompt the same way — but that one
    # HAS descendants (its whole turn ran), so the prune must walk the tree,
    # not just the one line.
    p = tmp_path / "rw.jsonl"
    p.write_text("".join(_l(o) + "\n" for o in [
        {"type": "user", "uuid": "u1", "parentUuid": None,
         "message": {"content": "keep me"},
         "timestamp": "2026-07-25T00:00:01.000Z"},
        {"type": "user", "uuid": "u2", "parentUuid": "u1",
         "message": {"content": "wrong turn"},
         "timestamp": "2026-07-25T00:00:02.000Z"},
        {"type": "assistant", "uuid": "a2", "parentUuid": "u2",
         "message": {"content": [{"type": "text", "text": "down the wrong path"}]},
         "timestamp": "2026-07-25T00:00:03.000Z"},
        {"type": "user", "uuid": "u3", "parentUuid": "u1",
         "message": {"content": "right turn"},
         "timestamp": "2026-07-25T00:00:04.000Z"},
    ]), encoding="utf-8")
    recs, _ = TR.conversation(str(p), 0)
    assert [(r["kind"], r["text"]) for r in recs] \
        == [("prompt", "keep me"), ("prompt", "right turn")]


def test_conversation_keeps_parallel_tool_branches(tmp_path):
    # The tree forks legitimately: PARALLEL tool calls each parent their
    # tool_result to the assistant message that issued them, and an attachment
    # hangs off the record it annotates. Only prompt-vs-prompt siblings are a
    # discard — a general "last sibling wins" rule would eat live content.
    p = tmp_path / "par.jsonl"
    p.write_text("".join(_l(o) + "\n" for o in [
        {"type": "user", "uuid": "u1", "parentUuid": None,
         "message": {"content": "run both"},
         "timestamp": "2026-07-25T00:00:01.000Z"},
        {"type": "assistant", "uuid": "a1", "parentUuid": "u1",
         "message": {"content": [
             {"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}]},
         "timestamp": "2026-07-25T00:00:02.000Z"},
        {"type": "assistant", "uuid": "a2", "parentUuid": "a1",
         "message": {"content": [
             {"type": "tool_use", "id": "t2", "name": "Bash", "input": {}}]},
         "timestamp": "2026-07-25T00:00:03.000Z"},
        {"type": "attachment", "uuid": "x1", "parentUuid": "a2",
         "attachment": {"type": "hook_success"},
         "timestamp": "2026-07-25T00:00:04.000Z"},
        {"type": "user", "uuid": "r1", "parentUuid": "a1",
         "message": {"content": [{"type": "tool_result", "tool_use_id": "t1",
                                  "content": "ok"}]},
         "timestamp": "2026-07-25T00:00:05.000Z"},
        {"type": "assistant", "uuid": "a3", "parentUuid": "r1",
         "message": {"content": [{"type": "text", "text": "both done"}]},
         "timestamp": "2026-07-25T00:00:06.000Z"},
    ]), encoding="utf-8")
    recs, _ = TR.conversation(str(p), 0)
    assert [(r["kind"], r["text"]) for r in recs] \
        == [("prompt", "run both"), ("message", "both done")]


def test_conversation_surfaces_recap(tmp_path):
    # A recap (away_summary) shows in the mirror conversation as a `recap`
    # bubble, its "(disable recaps…)" hint stripped.
    p = tmp_path / "r.jsonl"
    p.write_text("".join(_l(o) + "\n" for o in [
        {"type": "user", "message": {"content": "do the thing"},
         "timestamp": "2026-07-22T00:00:01.000Z"},
        {"type": "system", "subtype": "away_summary",
         "content": "Did the thing; nothing pending. (disable recaps in /config)",
         "timestamp": "2026-07-22T00:05:00.000Z"},
    ]), encoding="utf-8")
    recs, _ = TR.conversation(str(p), 0)
    assert [r["kind"] for r in recs] == ["prompt", "recap"]
    assert recs[1]["text"] == "Did the thing; nothing pending."


def test_format_questions_flattens_text_and_options():
    md = TR._format_questions({"questions": [
        {"header": "Drink", "question": "Coffee or tea?",
         "options": [{"label": "Coffee", "description": "hot"}, {"label": "Tea"}]},
        {"question": "Milk?",
         "options": [{"label": "Yes"}, {"label": "No"}], "multiSelect": True}]})
    assert "Coffee or tea?" in md and "- Coffee" in md and "- Tea" in md
    assert "Milk?" in md and "- Yes" in md and "- No" in md
    # a malformed shape renders nothing rather than raising a broken bubble
    assert TR._format_questions({}) == ""
    assert TR._format_questions({"questions": "nope"}) == ""
    assert TR._format_questions("garbage") == ""


def test_conversation_surfaces_ask_question_and_answer(tmp_path):
    # AskUserQuestion is recorded in the transcript as an assistant tool_use (the
    # question) then a user tool_result (the answer). conversation() — the
    # dashboard's mirror provider — surfaces BOTH: a `question` record carrying
    # the question text + offered options, and the `answer` recap. Every OTHER
    # tool_use (the Bash) stays the terminal mirror's job (an op), never a
    # conversation record — it only anchors the records that follow it.
    p = tmp_path / "ask.jsonl"
    p.write_text("".join(_l(o) + "\n" for o in [
        {"type": "assistant", "message": {"id": "m1", "content": [
            {"type": "text", "text": "let me check"},
            {"type": "tool_use", "id": "t1", "name": "Bash",
             "input": {"command": "ls"}},
            {"type": "tool_use", "id": "t2", "name": "AskUserQuestion",
             "input": {"questions": [
                 {"header": "Pets", "question": "Cats or dogs?",
                  "options": [{"label": "Cats"}, {"label": "Dogs"}]}]}}]},
         "timestamp": "2026-07-20T00:00:01.000Z"},
        {"type": "user", "toolUseResult": {"answers": [{"selected": ["Dogs"]}]},
         "message": {"content": [
             {"type": "tool_result", "tool_use_id": "t2",
              "content": "Your questions have been answered: Dogs"}]},
         "timestamp": "2026-07-20T00:00:09.000Z"},
    ]), encoding="utf-8")
    recs, _ = TR.conversation(str(p), 0)
    assert [r["kind"] for r in recs] == ["message", "question", "answer"]
    q = next(r for r in recs if r["kind"] == "question")
    assert "Cats or dogs?" in q["text"]
    assert "- Cats" in q["text"] and "- Dogs" in q["text"]
    assert q["anchor"] == "t1"        # anchors after the Bash op, before its own id
    ans = next(r for r in recs if r["kind"] == "answer")
    assert ans["text"].startswith("Your questions have been answered")


def _mail_tx(tmp_path):
    """A transcript whose one turn SENDS a piece of team mail (a SendMessage
    tool_use) — the shape both conversation() reads and the substream paints."""
    p = tmp_path / "mail.jsonl"
    p.write_text("".join(_l(o) + "\n" for o in [
        {"type": "assistant", "message": {"id": "m1", "content": [
            {"type": "text", "text": "delivering the report"},
            {"type": "tool_use", "id": "t1", "name": "SendMessage",
             "input": {"to": "main", "summary": "review",
                       "message": "line1\n" * 40 + "last"}}]},
         "timestamp": "2026-07-27T00:00:01.000Z"},
    ]), encoding="utf-8")
    return p


def test_conversation_surfaces_an_outgoing_message_only_when_asked(tmp_path):
    # An outgoing SendMessage is the OTHER half of a conversation whose incoming
    # half is already a `teammsg` record — but only an AGENT read asks for it
    # (conversation_for's `sends`), because the LEAD's sends are already mirror
    # rows that this transcript cannot match: mail_fmt.py writes one for every
    # teammate's send too, and the lead's transcript sees only its own.
    p = _mail_tx(tmp_path)
    lead, _ = TR.conversation(str(p), 0)
    assert [r["kind"] for r in lead] == ["message"]
    agent, _ = TR.conversation(str(p), 0, sends=True)
    assert [r["kind"] for r in agent] == ["message", "sendmsg"]
    sent = agent[-1]
    assert sent["to"] == "main"
    # …and it carries the WHOLE message. The op the substream paints for the same
    # send is capped to 12 lines for the terminal pane; the bubble is why the web
    # no longer ends at "… (29 more lines)".
    assert sent["text"].endswith("last") and sent["text"].count("\n") == 40
    assert sent["anchor"] is None          # no tool ran before it in this turn


def test_mail_send_reads_a_structured_message_body():
    # `message` may be a plain string OR content blocks — one owner reads that
    # shape (transcript.mail_send) for both the substream's chip and the web's
    # bubble, so a .strip() can never meet a dict (it crashed the streamer once).
    assert TR.mail_send({"to": "main", "message": "hi"}) == ("main", "hi")
    assert TR.mail_send({"recipient": "team-lead",
                         "content": [{"type": "text", "text": "hi"}]}) \
        == ("team-lead", "hi")
    assert TR.mail_send({}) == ("?", "")
    assert TR.mail_send(None) == ("?", "")


def test_conversation_answer_carries_qa_pairs(tmp_path):
    # Claude Code's REAL toolUseResult carries `answers` as a {question:
    # answer_string} map (+ `questions` for headers) — the answer record carries
    # the structured [{q, header, answer}] pairs the dashboard's answer card
    # highlights (multiSelect answers arrive ", "-joined).
    p = tmp_path / "qa.jsonl"
    # "Salt, pepper" is ONE option label containing ", " — the split must not
    # break it, and the trailing "extra" is a typed custom value
    qs = [{"header": "Pets", "question": "Cats or dogs?",
           "options": [{"label": "Cats"}, {"label": "Dogs"}]},
          {"header": "Sides", "question": "Which sides?", "multiSelect": True,
           "options": [{"label": "Fries"}, {"label": "Slaw"},
                       {"label": "Salt, pepper"}]}]
    p.write_text("".join(_l(o) + "\n" for o in [
        {"type": "assistant", "message": {"id": "m1", "content": [
            {"type": "tool_use", "id": "t1", "name": "AskUserQuestion",
             "input": {"questions": qs}}]},
         "timestamp": "2026-07-20T00:00:01.000Z"},
        {"type": "user", "toolUseResult": {
            "questions": qs,
            "answers": {"Cats or dogs?": "Dogs",
                        "Which sides?": "Fries, Salt, pepper, extra"}},
         "message": {"content": [
             {"type": "tool_result", "tool_use_id": "t1",
              "content": "Your questions have been answered: …"}]},
         "timestamp": "2026-07-20T00:00:09.000Z"},
    ]), encoding="utf-8")
    recs, _ = TR.conversation(str(p), 0)
    ans = next(r for r in recs if r["kind"] == "answer")
    assert ans["qa"] == [
        {"q": "Cats or dogs?", "header": "Pets", "values": ["Dogs"]},
        {"q": "Which sides?", "header": "Sides",
         "values": ["Fries", "Salt, pepper", "extra"]}]


def test_split_answer_label_aware():
    # a multiSelect answer = selected option labels (in option order) + the ONE
    # typed custom value, ", "-joined. peel KNOWN labels off the front; the
    # REMAINDER is a single custom value, even when it contains commas.
    labels = ["Coffee", "Dark chocolate", "Chips", "Fruit"]
    # the reported bug: a custom "test, test2 same line" is ONE value, not two
    assert TR._split_answer("Coffee, test, test2 same line", labels) == \
        ["Coffee", "test, test2 same line"]
    # a label that itself contains ", " stays whole, custom trailing kept whole
    assert TR._split_answer("Fries, Salt, pepper, x, y", ["Fries", "Salt, pepper"]) == \
        ["Fries", "Salt, pepper", "x, y"]
    # all-labels, no custom
    assert TR._split_answer("Coffee, Fruit", labels) == ["Coffee", "Fruit"]
    # pure custom (no option selected)
    assert TR._split_answer("just, my, words", labels) == ["just, my, words"]


def test_conversation_surfaces_declined_question(tmp_path):
    # A DECLINED question still records the question (the assistant tool_use is
    # written regardless), just with no `answer` recap — the transcript honestly
    # shows what was asked even when nothing was picked.
    p = tmp_path / "declined.jsonl"
    p.write_text("".join(_l(o) + "\n" for o in [
        {"type": "assistant", "message": {"id": "m1", "content": [
            {"type": "tool_use", "id": "t1", "name": "AskUserQuestion",
             "input": {"questions": [
                 {"question": "Proceed?", "options": [{"label": "Yes"}]}]}}]},
         "timestamp": "2026-07-20T00:00:01.000Z"},
    ]), encoding="utf-8")
    recs, _ = TR.conversation(str(p), 0)
    assert [r["kind"] for r in recs] == ["question"]
    assert "Proceed?" in recs[0]["text"]


def test_ask_preamble_from_separate_message(tmp_path):
    # The common shape: Claude writes the framing text in ONE assistant message,
    # then calls AskUserQuestion in the NEXT (a tool call and its lead-in are
    # usually separate messages). ask_preamble returns the preceding message's
    # text — the same `message` the stream shows before the `question`.
    p = tmp_path / "sep.jsonl"
    p.write_text("".join(_l(o) + "\n" for o in [
        {"type": "user", "message": {"content": "figure it out"}},
        {"type": "assistant", "message": {"id": "m1", "content": [
            {"type": "tool_use", "id": "t1", "name": "Bash",
             "input": {"command": "ls"}}]}},
        {"type": "assistant", "message": {"id": "m2", "content": [
            {"type": "text", "text": "Two problems, not one. **Red herring** ruled out."}]}},
        {"type": "assistant", "message": {"id": "m3", "content": [
            {"type": "tool_use", "id": "t2", "name": "AskUserQuestion",
             "input": {"questions": [
                 {"question": "Which fix?", "options": [{"label": "A"}]}]}}]},
        },
    ]), encoding="utf-8")
    assert TR.ask_preamble(str(p), "t2") == \
        "Two problems, not one. **Red herring** ruled out."


def test_ask_preamble_same_message_wins(tmp_path):
    # When the framing text and the ask share ONE assistant message, the
    # same-message text before the tool_use wins over an earlier message.
    p = tmp_path / "same.jsonl"
    p.write_text("".join(_l(o) + "\n" for o in [
        {"type": "assistant", "message": {"id": "m1", "content": [
            {"type": "text", "text": "stale earlier note"}]}},
        {"type": "assistant", "message": {"id": "m2", "content": [
            {"type": "text", "text": "here's the framing"},
            {"type": "tool_use", "id": "t9", "name": "AskUserQuestion",
             "input": {"questions": [
                 {"question": "Go?", "options": [{"label": "Yes"}]}]}}]}},
    ]), encoding="utf-8")
    assert TR.ask_preamble(str(p), "t9") == "here's the framing"


def test_ask_preamble_resets_on_new_prompt(tmp_path):
    # A real user prompt is a turn boundary: an assistant message from a PRIOR
    # turn is not the lead-in to this turn's ask. With no framing text in the
    # ask's own turn, the preamble is empty rather than a stale earlier message.
    p = tmp_path / "reset.jsonl"
    p.write_text("".join(_l(o) + "\n" for o in [
        {"type": "assistant", "message": {"id": "m1", "content": [
            {"type": "text", "text": "an answer from the last turn"}]}},
        {"type": "user", "message": {"content": "new question please"}},
        {"type": "assistant", "message": {"id": "m2", "content": [
            {"type": "tool_use", "id": "t1", "name": "AskUserQuestion",
             "input": {"questions": [
                 {"question": "Pick?", "options": [{"label": "A"}]}]}}]}},
    ]), encoding="utf-8")
    assert TR.ask_preamble(str(p), "t1") == ""


def test_ask_preamble_edge_cases(tmp_path):
    p = tmp_path / "edge.jsonl"
    p.write_text("".join(_l(o) + "\n" for o in [
        {"type": "assistant", "message": {"id": "m1", "content": [
            {"type": "text", "text": "lead"},
            {"type": "tool_use", "id": "t1", "name": "AskUserQuestion",
             "input": {"questions": [{"question": "?", "options": []}]}}]}},
    ]), encoding="utf-8")
    assert TR.ask_preamble(str(p), "t1") == "lead"
    assert TR.ask_preamble(str(p), "") == ""            # no id
    assert TR.ask_preamble(str(p), "nope") == ""        # id not found
    assert TR.ask_preamble(str(tmp_path / "gone.jsonl"), "t1") == ""  # unreadable


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


def test_monitors_correlates_launch_result_and_events(tmp_path):
    # monitors() ties a Monitor tool_use to its taskId (via the "Monitor started
    # (task X)" result) and gathers its events — the monitors-tab read model.
    path = _write(tmp_path, [
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "Monitor",
             "input": {"command": "tail -f log", "description": "watch",
                       "persistent": True}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1",
             "content": "Monitor started (task abc123, persistent — …)"}]}},
        {"type": "queue-operation", "content": _mon_note(task="abc123", event="A")},
        {"type": "queue-operation", "content": _mon_note(task="abc123", event="B")},
        {"type": "queue-operation", "content": _mon_note(task="abc123",
                                                         status="completed")},
    ])
    mons = TR.monitors(path)
    assert len(mons) == 1
    m = mons[0]
    assert m["task"] == "abc123"
    assert m["command"] == "tail -f log" and m["description"] == "watch"
    assert m["persistent"] is True and m["source"] == "command"
    assert [e.get("event") for e in m["events"]] == ["A", "B", None]
    assert m["events"][2]["status"] == "completed"


def test_monitors_ws_source_and_launchless_events(tmp_path):
    # a WebSocket monitor (ws.url, no command) records source "ws"; and an event
    # whose launch was never seen (truncated head) still surfaces the task.
    path = _write(tmp_path, [
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "Monitor",
             "input": {"ws": {"url": "wss://x/y"}, "description": "socket"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1",
             "content": "Monitor started (task wsock)"}]}},
        {"type": "queue-operation", "content": _mon_note(task="orphan", event="lone")},
    ])
    mons = {m["task"]: m for m in TR.monitors(path)}
    assert mons["wsock"]["source"] == "ws" and mons["wsock"]["command"] == "wss://x/y"
    assert mons["orphan"]["command"] == "" and mons["orphan"]["events"][0]["event"] == "lone"


# ------------------------------------------------------------ timeline_since

def _append(path, lines):
    with open(path, "a", encoding="utf-8") as fh:
        for o in lines:
            fh.write(_l(o) + "\n")


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


def _boundary(post=None, pre=523056):
    meta = {"trigger": "manual", "preTokens": pre, "durationMs": 127639}
    if post is not None:
        meta["postTokens"] = post
    return {"type": "system", "subtype": "compact_boundary",
            "content": "Conversation compacted", "compactMetadata": meta}


def test_context_probe_reads_post_compaction_occupancy(tmp_path):
    # A compaction writes NO assistant record, so between the boundary and the
    # next real turn the last assistant usage is the PRE-compaction figure —
    # 22 records' worth of lag in the sampled session. The boundary's postTokens
    # is what the context actually holds.
    row = {"type": "assistant", "message": {"model": "claude-haiku-4-5",
           "usage": {"input_tokens": 180000, "output_tokens": 9}}}
    before = _wt(tmp_path, "cb1.jsonl", row)
    assert TR.context_probe(before)["used"] == 180000
    after = _wt(tmp_path, "cb2.jsonl", row, _boundary(post=8969),
                {"type": "user", "isCompactSummary": True,
                 "message": {"content": "This session is being continued…"}})
    got = TR.context_probe(after)
    assert got["used"] == 8969
    # the model/window still come from the assistant record — a boundary names
    # no model, and compaction doesn't change the one in use
    assert (got["window"], got["model"]) == (200000, "claude-haiku-4-5")
    assert got["pct"] == 4


def test_context_probe_ignores_a_quoted_compact_boundary(tmp_path):
    # The marker must match as a RECORD, never as raw bytes: a transcript
    # quotes its own vocabulary constantly (a Read of the file documenting it,
    # a grep hit in a tool_result) and byte-matching one has already flipped a
    # tab green mid-turn once (CLAUDE.md, the interrupt-watch invariant).
    p = _wt(tmp_path, "cb3.jsonl",
            {"type": "assistant", "message": {"model": "claude-haiku-4-5",
             "usage": {"input_tokens": 180000, "output_tokens": 9}}},
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "t",
                 "content": 'grep hit: "subtype": "compact_boundary" '
                            'with "postTokens": 1'}]}})
    assert TR.context_probe(p)["used"] == 180000


def test_context_probe_only_the_newest_boundary_speaks(tmp_path):
    # An EARLIER boundary describes a context two compactions ago. The newest
    # one latches even when it carries no postTokens (older Claude Code builds
    # wrote none) — falling back to the assistant record, never to a stale
    # boundary's figure.
    p = _wt(tmp_path, "cb4.jsonl",
            _boundary(post=1000),
            {"type": "assistant", "message": {"model": "claude-haiku-4-5",
             "usage": {"input_tokens": 180000, "output_tokens": 9}}},
            _boundary(post=None))
    assert TR.context_probe(p)["used"] == 180000


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


def test_title_and_rename_reports_tail_rename(tmp_path):
    """title_and_rename returns (display_title, tail_rename): the second value is
    the `agent-name` still inside the 64KB title tail-window, so the dashboard can
    tell a CURRENT rename from one that scrolled out."""
    d = tmp_path / "projects" / "-w-proj"
    d.mkdir(parents=True)
    # rename present near EOF -> it wins AND is reported as the tail rename
    p = d / "sid-1.jsonl"
    p.write_text(_l({"type": "ai-title", "aiTitle": "auto"}) + "\n"
                 + _l({"type": "agent-name", "agentName": "picked"}) + "\n")
    assert TR.title_and_rename(str(p)) == ("picked", "picked")
    # only an ai-title -> title is the auto name, tail rename is '' (nothing to
    # reconcile means the dashboard override may stand in)
    q = d / "sid-2.jsonl"
    q.write_text(_l({"type": "ai-title", "aiTitle": "auto"}) + "\n")
    assert TR.title_and_rename(str(q)) == ("auto", "")


def test_title_and_rename_rename_scrolled_out_of_tail(tmp_path):
    """The rollback shape: a one-time `agent-name` written EARLY, then enough
    fresh `ai-title` re-emissions to push it past the 64KB tail-window. The
    ladder can no longer see the rename (tail_rename == '') and falls to the auto
    title — which is exactly why the dashboard keeps a durable override."""
    d = tmp_path / "projects" / "-w-proj"
    d.mkdir(parents=True)
    p = d / "sid-3.jsonl"
    lines = [_l({"type": "agent-name", "agentName": "picked"})]
    # pad past TITLE_TAIL_B with fresh ai-title rows (each near EOF, as Claude
    # Code re-emits them every few turns)
    filler = _l({"type": "ai-title", "aiTitle": "auto"})
    while sum(len(x) + 1 for x in lines) <= TR.TITLE_TAIL_B:
        lines.append(filler)
    p.write_text("\n".join(lines) + "\n")
    assert os.path.getsize(str(p)) > TR.TITLE_TAIL_B
    title, tail_named = TR.title_and_rename(str(p))
    assert title == "auto"        # the rename rolled back at the transcript layer
    assert tail_named == ""       # the durable override is what saves the display


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


# ------------------------------------------------------------------- goal_probe

def _goal_status(cond, met=False, sentinel=False):
    # The `/goal <cond>` attachment record Claude Code writes (measured 2.1.217).
    return {"type": "attachment",
            "attachment": {"type": "goal_status", "met": met,
                           "sentinel": sentinel, "condition": cond}}


def _goal_cmd(args):
    # The `/goal <args>` slash-command user record (args="" | "clear" | a cond).
    return {"type": "user", "message": {
        "content": ("<command-name>/goal</command-name>\n"
                    "            <command-message>goal</command-message>\n"
                    "            <command-args>%s</command-args>" % args)}}


def test_goal_probe_none_without_transcript(tmp_path):
    assert TR.goal_probe(str(tmp_path / "missing.jsonl")) is None


def test_goal_probe_no_goal_is_none(tmp_path):
    p = tmp_path / "g.jsonl"
    p.write_text(_l({"type": "user", "message": {"content": "hi"}}) + "\n",
                 encoding="utf-8")
    assert TR.goal_probe(str(p)) is None


def test_goal_probe_active_goal_from_status_attachment(tmp_path):
    p = tmp_path / "g.jsonl"
    p.write_text("".join(_l(o) + "\n" for o in [
        _goal_cmd("ship the feature"),
        _goal_status("ship the feature", met=False, sentinel=True),
    ]), encoding="utf-8")
    assert TR.goal_probe(str(p)) == {"condition": "ship the feature", "met": False}


def test_goal_probe_latest_status_wins_and_reports_met(tmp_path):
    # The checker re-stamps goal_status each turn; the last one is the state.
    p = tmp_path / "g.jsonl"
    p.write_text("".join(_l(o) + "\n" for o in [
        _goal_status("build it", met=False, sentinel=True),
        _goal_status("build it", met=False),
        _goal_status("build it", met=True),
    ]), encoding="utf-8")
    assert TR.goal_probe(str(p)) == {"condition": "build it", "met": True}


def test_goal_probe_cleared_status_is_none(tmp_path):
    # A cleared goal writes a goal_status with an empty condition.
    p = tmp_path / "g.jsonl"
    p.write_text("".join(_l(o) + "\n" for o in [
        _goal_status("old goal", met=False),
        _goal_status("", met=False),
    ]), encoding="utf-8")
    assert TR.goal_probe(str(p)) is None


def test_goal_probe_clear_command_after_status_ends_goal(tmp_path):
    # A bare `/goal clear` post-dating the last attachment ends the goal even if
    # the terminal wrote no cleared attachment.
    p = tmp_path / "g.jsonl"
    p.write_text("".join(_l(o) + "\n" for o in [
        _goal_status("old goal", met=False),
        _goal_cmd("clear"),
    ]), encoding="utf-8")
    assert TR.goal_probe(str(p)) is None


def test_goal_probe_status_query_command_is_skipped(tmp_path):
    # `/goal status` only queries — it must not mask the active goal beneath it.
    p = tmp_path / "g.jsonl"
    p.write_text("".join(_l(o) + "\n" for o in [
        _goal_status("keep going", met=False),
        _goal_cmd("status"),
    ]), encoding="utf-8")
    assert TR.goal_probe(str(p)) == {"condition": "keep going", "met": False}


def test_goal_probe_bare_set_command_without_attachment(tmp_path):
    # Defensive: a `/goal <cond>` command with no following attachment still
    # reads as active (condition from the command args).
    p = tmp_path / "g.jsonl"
    p.write_text(_l(_goal_cmd("do the thing")) + "\n", encoding="utf-8")
    assert TR.goal_probe(str(p)) == {"condition": "do the thing", "met": False}


def test_goal_probe_ignores_list_content_quoting_the_command(tmp_path):
    # A user record whose `content` is a LIST of blocks that merely QUOTES the
    # `<command-name>/goal</command-name>` literal (e.g. a transcript discussing
    # /goal, like this repo's own) must not crash or read as a command.
    p = tmp_path / "g.jsonl"
    p.write_text(_l({"type": "user", "message": {"content": [
        {"type": "text",
         "text": "see <command-name>/goal</command-name> <command-args>x"
                 "</command-args> in the output"}]}}) + "\n",
                 encoding="utf-8")
    assert TR.goal_probe(str(p)) is None


# ------------------------------------------------- prompt_count (the compact gate)

def test_prompt_count_none_without_transcript(tmp_path):
    # Fails OPEN: an unreadable file is "plenty" (the count only ever argues for
    # disabling the ⊜ compact button, so an unknown must never disable it).
    assert TR.prompt_count(str(tmp_path / "missing.jsonl")) == TR.PROMPT_CAP
    assert TR.prompt_count("") is None


def test_prompt_count_counts_only_what_you_typed(tmp_path):
    # Claude Code injects user-shaped records constantly — tool results, skill
    # loads, teammate mail — and none of them is a prompt YOU sent. Counting
    # bare type:"user" records would call a one-message session a long one.
    p = tmp_path / "t.jsonl"
    p.write_text("".join(_l(o) + "\n" for o in [
        {"type": "user", "message": {"content": "first"}},
        {"type": "assistant", "message": {"id": "m1", "content": [
            {"type": "text", "text": "hi"}]}},
        {"type": "user", "isMeta": True, "message": {"content": "injected"}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "content": "out"}]}},
    ]), encoding="utf-8")
    assert TR.prompt_count(str(p)) == 1


def test_prompt_count_zero_prompts_is_none(tmp_path):
    # "Nothing to conclude" — the same contract context_probe has for a file it
    # can't speak (a codex rollout reaches this path-keyed fan-out too).
    p = tmp_path / "t.jsonl"
    p.write_text(_l({"type": "assistant", "message": {"id": "m1", "content": [
        {"type": "text", "text": "hi"}]}}) + "\n", encoding="utf-8")
    assert TR.prompt_count(str(p)) is None


def test_prompt_count_caps_and_skips_a_big_transcript(tmp_path, monkeypatch):
    p = tmp_path / "t.jsonl"
    p.write_text("".join(_l({"type": "user", "message": {"content": "m%d" % i}})
                         + "\n" for i in range(30)), encoding="utf-8")
    assert TR.prompt_count(str(p), cap=3) == 3
    # past the scan budget the file is not read at all — a transcript that big
    # obviously has more conversation than the question is about
    monkeypatch.setattr(TR, "PROMPT_SCAN_B", 10)
    assert TR.prompt_count(str(p), cap=3) == 3


# ------------------------------------------------- the two presenters' registries

def test_both_transcript_presenters_cover_every_record_kind():
    """`parse_line`'s record vocabulary is DECLARED (`KINDS`), and the presenter
    that dispatches on it accounts for every kind — the conversation stream
    (`_CONV`) by handling or EXPLICITLY skipping.

    It was a 90-line elif ladder over that vocabulary, and for a while there
    were TWO of them (the drill-down timeline's `_FOLD` was the other, until
    agent scope replaced that whole read model with the scoped mirror). Adding a
    record kind meant editing the chain with nothing saying so, and a forgotten
    one is silent — the record simply never appears, which is indistinguishable
    from a transcript that didn't contain it. As a registry checked against one
    declared vocabulary, a new kind fails here until the table has said what it
    does with it, and a DROP has to be written down rather than implied by
    absence."""
    kinds = set(TR.KINDS)
    assert kinds, "the record vocabulary must not be empty"
    # the message stream handles or deliberately drops each, and never both
    assert set(TR._CONV) | TR._CONV_SKIP == kinds
    assert not (set(TR._CONV) & TR._CONV_SKIP)
    # …and neither table may invent a kind parse_line cannot produce
    assert set(TR._CONV) <= kinds and TR._CONV_SKIP <= kinds


def test_declared_kinds_are_the_kinds_parse_line_actually_emits():
    """KINDS is checked against parse_line itself, not just against the tables —
    otherwise the three could agree with each other and all be wrong. One line
    per declared kind, parsed for real."""
    samples = {
        "bad": "{nope",
        "compact": _l({"type": "system", "subtype": "compact_boundary",
                       "compactMetadata": {}}),
        "recap": _l({"type": "system", "subtype": "away_summary",
                     "content": "you were away"}),
        "prompt": _l({"type": "user", "message": {"content": "hello"}}),
        "teammsg": _l({"type": "user", "message": {"content":
                       '<teammate-message teammate_id="bob">hi</teammate-message>'}}),
        "results": _l({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "out"}]}}),
        "assistant": _l({"type": "assistant", "message": {"content": [], "id": "m1"}}),
        "monitor_event": _l({"type": "queue-operation", "content":
                             "<task-notification><task-id>t</task-id>"
                             "<event>tick</event></task-notification>"}),
    }
    assert set(samples) == set(TR.KINDS), "sample set drifted from KINDS"
    for kind, line in samples.items():
        rec = TR.parse_line(line)
        assert rec is not None and rec["kind"] == kind, (kind, rec)


# ------------------------------------------------------------- queue_drained

def test_queue_drained_reads_the_queue_records_not_the_prose(tmp_path):
    # The tell the dashboard's interrupt needs: Claude Code delivers a
    # mid-turn-queued message the instant the running turn ends, so a `dequeue`
    # (or the delivered `queued_command`) past the press-time offset means the
    # boundary happened — stop re-pressing Escape (docs/dashboard.md,
    # *Interrupt*). Only RECORDS count: a tool_result that merely quotes the
    # words is not a boundary (the is_interrupt_line lesson).
    p = tmp_path / "t.jsonl"
    p.write_text(_l({"type": "user", "message": {"content": "go"}}) + "\n")
    base = p.stat().st_size
    assert TR.queue_drained(str(p), base) == ""          # no growth yet
    with open(p, "a", encoding="utf-8") as f:
        f.write(_l({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1",
             "content": '{"type":"queue-operation","operation":"dequeue"}'}]}})
                + "\n")
    assert TR.queue_drained(str(p), base) == ""          # a QUOTE, not a drain
    with open(p, "a", encoding="utf-8") as f:
        f.write(_l({"type": "queue-operation", "operation": "enqueue",
                    "content": "later"}) + "\n")
    assert TR.queue_drained(str(p), base) == ""          # queued, not delivered
    with open(p, "a", encoding="utf-8") as f:
        f.write(_l({"type": "queue-operation", "operation": "dequeue"}) + "\n")
    assert TR.queue_drained(str(p), base) == "dequeue"
    # ...and the delivered message itself is the same conclusion
    p2 = tmp_path / "t2.jsonl"
    p2.write_text(_l({"type": "attachment", "attachment": {
        "type": "queued_command", "commandMode": "prompt", "prompt": "hi"}})
        + "\n")
    assert TR.queue_drained(str(p2), 0) == "queued_command"
    # a torn final line is undecidable (re-read whole next pass), and a missing
    # path / offset past EOF is just ""
    p3 = tmp_path / "t3.jsonl"
    p3.write_text(_l({"type": "queue-operation", "operation": "dequeue"}))
    assert TR.queue_drained(str(p3), 0) == ""
    assert TR.queue_drained("", 0) == ""
    assert TR.queue_drained(str(p2), 10_000) == ""


# ---------------------------------------------------------------- fallback_scan

def _fb_rec(frm="claude-fable-5", to="claude-opus-4-8", cat="cyber",
            content="Fable 5's safeguards flagged this message. "
                    "Switched to Opus 4.8."):
    # The `model_refusal_fallback` system record Claude Code writes when a
    # safeguard refusal reroutes the session (measured 2.1.220, 2026-07-29).
    return {"type": "system", "subtype": "model_refusal_fallback",
            "content": content, "level": "warning", "trigger": "refusal",
            "direction": "retry", "originalModel": frm, "fallbackModel": to,
            "apiRefusalCategory": cat,
            "timestamp": "2026-07-29T15:48:18.689Z"}


def test_fallback_scan_missing_file(tmp_path):
    assert TR.fallback_scan(str(tmp_path / "missing.jsonl")) == (None, 0)


def test_fallback_scan_finds_the_record(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text(_l({"type": "user", "message": {"content": "hi"}}) + "\n"
                 + _l(_fb_rec()) + "\n", encoding="utf-8")
    fb, pos = TR.fallback_scan(str(p))
    assert fb["from"] == "claude-fable-5"
    assert fb["to"] == "claude-opus-4-8"
    assert fb["category"] == "cyber"
    assert "safeguards" in fb["reason"]
    assert fb["ts"]
    assert pos == os.path.getsize(p)


def test_fallback_scan_quoting_the_marker_is_not_a_record(tmp_path):
    # a transcript QUOTES its own vocabulary constantly (a grep hit inside a
    # tool_result, a Read of the file documenting it) — the byte hit is only a
    # prefilter; the record match is type:"system" + subtype
    p = tmp_path / "t.jsonl"
    p.write_text(_l({"type": "user", "message": {
        "content": 'saw "model_refusal_fallback" in the transcript'}}) + "\n",
        encoding="utf-8")
    assert TR.fallback_scan(str(p))[0] is None


def test_fallback_scan_incremental_from_checkpoint(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text(_l(_fb_rec()) + "\n", encoding="utf-8")
    fb, pos = TR.fallback_scan(str(p))
    assert fb["to"] == "claude-opus-4-8"
    # nothing grew: no record after the checkpoint, the position holds
    assert TR.fallback_scan(str(p), pos) == (None, pos)
    # growth past the checkpoint: only the new bytes are read, last record wins
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(_l(_fb_rec(frm="claude-opus-4-8", to="claude-sonnet-5"))
                 + "\n")
    fb2, pos2 = TR.fallback_scan(str(p), pos)
    assert fb2["to"] == "claude-sonnet-5"
    assert pos2 > pos


def test_fallback_scan_torn_tail_line_not_consumed(tmp_path):
    # the read-exactly discipline: a record still being written (no newline)
    # is neither parsed nor consumed, so the next scan re-reads it whole
    p = tmp_path / "t.jsonl"
    p.write_text(_l(_fb_rec())[:40], encoding="utf-8")
    assert TR.fallback_scan(str(p)) == (None, 0)
