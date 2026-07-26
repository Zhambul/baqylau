# tests/test_l0_dash_conversation.py — L0 dashboard: session titles + the merged ops/conversation stream.
#
# One subject out of the former 8468-line L0 dashboard monolith; the
# shared HTTP/audit helpers live in tests/dashkit.py and the in-process
# server fixture (`dash`) in tests/conftest.py.
import json
import os
import sys

from conftest import REPO

if REPO not in sys.path:
    sys.path.insert(0, REPO)

import plugins
import core.audit as A
from core import ops as O
from core import paths as P
from dashboard import server as DS


# ------------------------------------------------------------------ opshtml
from dashkit import (_get_json, _jl, _tw)


def test_session_title_prefers_summary_then_first_real_prompt(tmp_path):
    from plugins.claude_code import transcript as TR
    p = _tw(tmp_path, "t1.jsonl",
            {"type": "summary", "summary": "old summary"},
            {"type": "summary", "summary": "newest summary"},
            {"type": "user", "isMeta": True,
             "message": {"content": "<local-command-caveat>x</local-command-caveat>"}},
            {"type": "user", "message": {"content": "real question here\nmore"}})
    assert TR.session_title(p) == "newest summary"
    q = _tw(tmp_path, "t2.jsonl",
            {"type": "user", "message": {"content": "<command-name>/clear</command-name>"}},
            {"type": "user", "message": {"content": "fix the flaky test\nplease"}})
    assert TR.session_title(q) == "fix the flaky test"
    assert TR.session_title(str(tmp_path / "absent.jsonl")) == ""


def test_session_title_prefers_naming_records(tmp_path):
    # The naming records (docs/session-naming-findings.md) are what the kitty
    # tab shows — they beat summary/prompt, last-of-kind wins, and a custom
    # agent-name beats the auto ai-title regardless of order.
    from plugins.claude_code import transcript as TR
    p = _tw(tmp_path, "n1.jsonl",
            {"type": "summary", "summary": "a summary"},
            {"type": "user", "message": {"content": "first prompt"}},
            {"type": "ai-title", "aiTitle": "old auto title"},
            {"type": "ai-title", "aiTitle": "new auto title"})
    assert TR.session_title(p) == "new auto title"
    q = _tw(tmp_path, "n2.jsonl",
            {"type": "agent-name", "agentName": "my-renamed-session"},
            {"type": "ai-title", "aiTitle": "auto title after rename"})
    assert TR.session_title(q) == "my-renamed-session"


def test_session_title_finds_ai_title_past_head_window(tmp_path):
    # ai-title rows land near EOF — far beyond TITLE_SCAN in a long transcript.
    from plugins.claude_code import transcript as TR
    rows = [{"type": "user", "message": {"content": "the first prompt"}}]
    rows += [{"type": "assistant", "message": {"content": [{"type": "text", "text": "x" * 400}]}}
             for _ in range(TR.TITLE_SCAN + 20)]
    rows.append({"type": "ai-title", "aiTitle": "title near eof"})
    p = _tw(tmp_path, "n3.jsonl", *rows)
    assert os.path.getsize(p) > TR.TITLE_TAIL_B     # tail seek path, torn first line
    assert TR.session_title(p) == "title near eof"


def test_session_title_falls_back_to_slash_command(tmp_path):
    # A short slash-command session (first prompt is a <command-*> wrapper) with
    # no summary/ai-title/plain prompt gets the /command as its name instead of
    # a bare sid (docs/session-naming-findings.md, *Fallbacks*).
    from plugins.claude_code import transcript as TR
    p = _tw(tmp_path, "c1.jsonl",
            {"type": "user", "message": {"content":
             "<command-message>slack-monitor</command-message>\n"
             "<command-name>/slack-monitor</command-name>"}})
    assert TR.session_title(p) == "/slack-monitor"
    # command-args ride along when present
    q = _tw(tmp_path, "c2.jsonl",
            {"type": "user", "message": {"content":
             "<command-name>/task</command-name>\n"
             "<command-args>fix the flaky test</command-args>"}})
    assert TR.session_title(q) == "/task fix the flaky test"
    # a later plain prompt (or a summary) still WINS over the command fallback
    r = _tw(tmp_path, "c3.jsonl",
            {"type": "user", "message": {"content": "<command-name>/plugin</command-name>"}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "ok"}]}},
            {"type": "user", "message": {"content": "now do the real thing"}})
    assert TR.session_title(r) == "now do the real thing"


def test_conversation_anchors_and_cursor(tmp_path):
    from plugins.claude_code import transcript as TR
    p = _tw(tmp_path, "c1.jsonl",
            {"type": "user", "message": {"content": "do the thing"}},
            {"type": "assistant", "message": {"id": "m1", "content": [
                {"type": "text", "text": "starting"},
                {"type": "tool_use", "id": "t1", "name": "Bash",
                 "input": {"command": "ls"}}]}},
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}},
            {"type": "assistant", "message": {"id": "m2", "content": [
                {"type": "text", "text": "done"}]}})
    recs, pos = TR.conversation(p, 0)
    assert [(r["kind"], r["anchor"]) for r in recs] == \
        [("prompt", None), ("message", None), ("message", "t1")]
    assert pos > 0
    # incremental: nothing new -> empty, cursor stable
    assert TR.conversation(p, pos) == ([], pos)
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "user",
                             "message": {"content": "next ask"}}) + "\n")
    recs2, pos2 = TR.conversation(p, pos)
    assert [r["kind"] for r in recs2] == ["prompt"] and pos2 > pos


def test_conversation_surfaces_ask_answer(tmp_path):
    """An AskUserQuestion answer is a tool_result, not plain user text, so it
    landed in `blocks` and never showed in the dashboard mirror (the "my answer
    didn't appear in this session" report, 2026-07-19). It's surfaced as a
    distinct `answer` record — keyed off the toolUseResult sidecar's `answers`,
    so a Bash tool_result stays out (docs/dashboard.md, *Web ask*)."""
    from plugins.claude_code import transcript as TR
    p = _tw(tmp_path, "ans.jsonl",
            {"type": "assistant", "message": {"id": "m1", "content": [
                {"type": "tool_use", "id": "aq1", "name": "AskUserQuestion",
                 "input": {"questions": []}}]}},
            {"type": "user", "toolUseResult": {"answers": [{}], "questions": []},
             "message": {"content": [
                {"type": "tool_result", "tool_use_id": "aq1",
                 "content": 'Your questions have been answered: "Scope"='
                            '"Fix all four now".'}]}},
            # a plain Bash tool_result must NOT be surfaced (no `answers`)
            {"type": "assistant", "message": {"id": "m2", "content": [
                {"type": "tool_use", "id": "b1", "name": "Bash",
                 "input": {"command": "ls"}}]}},
            {"type": "user", "toolUseResult": {"stdout": "x"},
             "message": {"content": [
                {"type": "tool_result", "tool_use_id": "b1", "content": "x"}]}})
    recs, _ = TR.conversation(p, 0)
    kinds = [r["kind"] for r in recs]
    assert "answer" in kinds and kinds.count("answer") == 1
    ans = next(r for r in recs if r["kind"] == "answer")
    assert ans["text"].startswith("Your questions have been answered")


def test_http_sessions_carry_titles(dash, tmp_path):
    tp = _tw(tmp_path, "titled.jsonl",
             {"type": "user", "message": {"content": "build the dashboard"}})
    A.session_start({"session_id": "dash5", "cwd": "/w", "transcript_path": tp})
    rows = _get_json(dash + "/api/sessions")
    row = next(r for r in rows if r["sid"] == "dash5")
    assert row["title"] == "build the dashboard"


def test_merged_backlog_interleaves_by_anchor(dash, tmp_path):
    # ops for tool t1 + a conversation (prompt -> tool t1 -> message):
    # the message must land AFTER t1's last op, the prompt before everything.
    tp = _tw(tmp_path, "conv.jsonl",
             {"type": "user", "message": {"content": "run it"}},
             {"type": "assistant", "message": {"id": "m1", "content": [
                 {"type": "tool_use", "id": "t1", "name": "Bash",
                  "input": {"command": "echo hi"}}]}},
             {"type": "user", "message": {"content": [
                 {"type": "tool_result", "tool_use_id": "t1", "content": "hi"}]}},
             {"type": "assistant", "message": {"id": "m2", "content": [
                 {"type": "text", "text": "all done"}]}})
    A.session_start({"session_id": "dash6", "cwd": "/w", "transcript_path": tp})
    log = P.mirror_log("dash6")
    O.emit(log, O.label("▶ foreground", (170, 185, 210), g="t1"),
           O.gut("hi", (170, 185, 210), g="t1"))
    last, mpos, oldest, items = DS.merged_backlog("dash6", "dash6")
    kinds = ["prompt" if "msg prompt" in it["html"] else
             "message" if "msg message" in it["html"] else "op"
             for it in items]
    assert kinds == ["prompt", "op", "op", "message"]
    assert last >= 2 and mpos > 0
    assert oldest == 0            # whole history fits under the default tail
    assert "run it" in items[0]["html"] and "all done" in items[-1]["html"]


def test_merged_backlog_interleaves_by_timestamp(dash, tmp_path):
    # Timestamps are PRIMARY over anchors: the "between" message is anchored to
    # x2 (by anchor it would follow op-two) but its transcript timestamp falls
    # BETWEEN the two ops' emit stamps, so it must land between them.
    import time
    from datetime import datetime, timezone
    tp = str(tmp_path / "ts.jsonl")
    A.session_start({"session_id": "dash7", "cwd": "/w", "transcript_path": tp})
    log = P.mirror_log("dash7")
    O.emit(log, O.label("op-one", (1, 2, 3), g="x1"))
    time.sleep(0.02)
    O.emit(log, O.label("op-two", (1, 2, 3), g="x2"))
    sdb = DS.API.state_db_for("dash7")
    _, ops = DS.API.ops_at(sdb, 0)
    t1, t2 = ops[0]["_ts"], ops[1]["_ts"]
    assert t1 and t2 and t1 < t2

    def iso(e):
        return datetime.fromtimestamp(e, tz=timezone.utc).isoformat()

    with open(tp, "w", encoding="utf-8") as fh:
        fh.write(_jl(
            {"type": "user", "timestamp": iso(t1 - 1),
             "message": {"content": "first ask"}},
            {"type": "assistant", "timestamp": iso(t1),
             "message": {"id": "m1", "content": [
                 {"type": "tool_use", "id": "x2", "name": "Bash",
                  "input": {"command": "echo hi"}}]}},
            {"type": "assistant", "timestamp": iso((t1 + t2) / 2),
             "message": {"id": "m2", "content": [
                 {"type": "text", "text": "between msg"}]}},
            {"type": "assistant", "timestamp": iso(t2 + 1),
             "message": {"id": "m3", "content": [
                 {"type": "text", "text": "final msg"}]}}))
    last, mpos, oldest, items = DS.merged_backlog("dash7", "dash7")
    kinds = ["prompt" if "msg prompt" in it["html"] else
             "message" if "msg message" in it["html"] else "op"
             for it in items]
    assert kinds == ["prompt", "op", "message", "op", "message"]
    # "between msg" precedes op-two -> the timestamp beat the x2 anchor
    between = next(i for i, it in enumerate(items) if "between msg" in it["html"])
    optwo = next(i for i, it in enumerate(items) if "op-two" in it["html"])
    assert between < optwo
    assert "first ask" in items[0]["html"] and "final msg" in items[-1]["html"]
    assert last >= 2 and mpos > 0


def test_merge_live_interleaves_delta_by_timestamp(dash, tmp_path):
    # The LIVE SSE delta merge (merge_live) is the increment-side twin of the
    # backlog merge. Regression guard for "messages come after commands": a
    # turn's TEXT (emitted just before its command's PreToolUse) must sort
    # BEFORE the command op even though the two land in one SSE tick — the loop
    # used to emit ops then a separate msgs event, prepending the text ABOVE the
    # command in the newest-top feed. A later comment (post-result) sorts AFTER.
    from datetime import datetime, timezone
    tp = str(tmp_path / "live.jsonl")
    A.session_start({"session_id": "live1", "cwd": "/w", "transcript_path": tp})
    log = P.mirror_log("live1")
    O.emit(log, O.label("cmd A", (1, 2, 3), g="a1"))
    sdb = DS.API.state_db_for("live1")
    _, ops = DS.API.ops_at(sdb, 0)
    tcmd = ops[0]["_ts"]
    assert tcmd

    def iso(e):
        return datetime.fromtimestamp(e, tz=timezone.utc).isoformat()

    # "before cmd" precedes the command in time; "after cmd" follows it.
    with open(tp, "w", encoding="utf-8") as fh:
        fh.write(_jl(
            {"type": "assistant", "timestamp": iso(tcmd - 1),
             "message": {"id": "m1", "content": [
                 {"type": "text", "text": "before cmd"}]}},
            {"type": "assistant", "timestamp": iso(tcmd + 1),
             "message": {"id": "m2", "content": [
                 {"type": "text", "text": "after cmd"}]}}))
    recs, _pos = plugins.conversation("live1", 0)
    items = DS.merge_live(ops, recs, "live1")
    kinds = ["message" if it.get("t") == "msg" else "op" for it in items]
    assert kinds == ["message", "op", "message"]
    assert "before cmd" in items[0]["html"]
    assert "cmd A" in items[1]["html"]
    assert "after cmd" in items[2]["html"]


def _blocks(sid, n):
    """Seed a session with `n` standalone label-op blocks (distinct group), the
    simplest thing that counts as one stream block each. Returns the op ids in
    emit order."""
    A.session_start({"session_id": sid, "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log(sid)
    for i in range(n):
        O.emit(log, O.label("block %d" % i, (170, 185, 210), g="b%d" % i))
    _, ops = DS.API.ops_at(DS.API.state_db_for(sid), 0)
    return [op["_id"] for op in ops]


def test_merged_backlog_tail_limit_and_oldest(dash):
    ids = _blocks("lz1", 6)
    # the whole history fits under a generous limit -> no lazy-load cursor
    _, _, oldest_all, items_all = DS.merged_backlog("lz1", "lz1", blocks=100)
    assert oldest_all == 0 and len(items_all) == 6
    # a tail of 2 blocks paints only the newest two, and reports the smallest
    # painted op id as the `oldest` cursor (block 4's op id, 0-indexed).
    _, _, oldest, items = DS.merged_backlog("lz1", "lz1", blocks=2)
    texts = [it["html"] for it in items]
    assert len(items) == 2
    assert "block 4" in texts[0] and "block 5" in texts[1]
    assert oldest == ids[4]                        # smallest painted op id


def test_history_chains_to_exhaustion_no_gap_no_overlap(dash):
    _blocks("lz2", 7)
    full = DS.merged_backlog("lz2", "lz2", blocks=1000)[3]     # the unlimited merge
    last, mpos, oldest, items = DS.merged_backlog("lz2", "lz2", blocks=3)
    assert len(items) == 3 and oldest > 0
    acc = list(items)
    guard = 0
    while oldest > 0:
        guard += 1
        assert guard < 50                          # must terminate
        oldest, page = DS.history("lz2", "lz2", oldest, 3)
        acc = page + acc                            # pages are OLDER -> prepend
    # concatenation of every slice equals the unlimited merge: no gap, no overlap
    assert [it["html"] for it in acc] == [it["html"] for it in full]


def test_history_straddling_group_not_duplicated(dash):
    # interleaved emits make group g1's ops non-contiguous (id1, id3) around
    # group g2 (id2); a tail of 1 block puts g1's newest op in the initial
    # window and its older op in history — the group straddles the boundary but
    # each op item appears exactly once across the slices.
    A.session_start({"session_id": "lz3", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("lz3")
    O.emit(log, O.label("g1 head", (1, 2, 3), g="g1"))
    O.emit(log, O.label("g2 head", (1, 2, 3), g="g2"))
    O.emit(log, O.gut("g1 more", (1, 2, 3), g="g1"))
    _, _, oldest, initial = DS.merged_backlog("lz3", "lz3", blocks=1)
    assert oldest > 0
    _, older = DS.history("lz3", "lz3", oldest, 10)
    ini_g1 = [it for it in initial if it["g"] == "g1"]
    old_g1 = [it for it in older if it["g"] == "g1"]
    assert ini_g1 and old_g1                        # g1 straddles the boundary
    # union carries both g1 ops exactly once (no duplicated card body)
    allg1 = [it["html"] for it in ini_g1 + old_g1]
    assert len(allg1) == 2 and len(set(allg1)) == 2
    assert any("g1 more" in h for h in allg1) and any("g1 head" in h for h in allg1)


def test_http_history_endpoint(dash):
    ids = _blocks("lz4", 5)
    d = _get_json(dash + "/api/session/lz4/history?before=%d&blocks=2" % ids[3])
    # before block 3's op id: the previous 2 blocks (1 and 2), newest cursor at
    # block 1's op id (block 0 still older).
    texts = [it["html"] for it in d["items"]]
    assert len(texts) == 2
    assert "block 1" in texts[0] and "block 2" in texts[1]
    assert d["oldest"] == ids[1]
    # before=0 is the exhausted signal (no older content)
    assert _get_json(dash + "/api/session/lz4/history?before=0&blocks=2") \
        == {"oldest": 0, "items": []}


def test_http_history_negative_blocks_does_not_crash(dash):
    # a negative ?blocks made _cut_blocks return len(entries) and _snap index
    # entries[len] → IndexError → 500. Now clamped positive: a clean 200.
    ids = _blocks("hnb1", 4)
    d = _get_json(dash + "/api/session/hnb1/history?before=%d&blocks=-1" % ids[3])
    assert isinstance(d["items"], list)       # 200, not a 500 IndexError
