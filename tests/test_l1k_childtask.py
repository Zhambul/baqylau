# L1k — the CHILD-TASK model (core/childtask.py) and the codex half that fills it.
#
# The bug this file exists for, measured in session 019fb66b-12a0 (2026-07-31):
# a codex child's report reached its parent at 04:25:26.784, the parent printed
# its final answer at 04:25:28.901, and only THEN did the child emit its own
# `final_answer` (04:25:29.322) and `task_complete` (04:25:29.422). The web stream
# merges by timestamp, so the child's `Agent finished` card sorted after the
# answer it had contributed to. Time cannot decide that; the stream has to know
# which task belongs to which turn.
#
# What is pinned here:
#   · the model's own vocabulary — the task key, the two endpoint steps, and the
#     two questions every consumer asks (`ends_turn` / `final_turn`);
#   · the codex child stream's per-TASK rules: `phase: "final_answer"` IS the
#     result, an ordinary message is not, a second task gets a second result, and
#     a rollout with no phase at all still resolves through the old inference;
#   · that a child learns its PARENT turn from the replayed prefix — the only
#     place it appears.
# The web's ordering pass over these stamps is pinned in
# tests/test_l0_dash_conversation.py (the merge's own file); the two hosts'
# identical launch/result presentation in tests/test_l1h_child_agent_parity.py.
import json
import sys

from conftest import REPO

if REPO not in sys.path:
    sys.path.insert(0, REPO)

from core import childtask as CT
from core import ops as O
from core import render as R
from core import slots as SL
from core import state as S
from plugins.codex import rollout as RO
from plugins.codex import stream as ST

BRIEF = "run a subagent to get a weather in bali"
PARENT = "019fb66b-13a6-7e03-acba-bd68278c6163"      # the parent's turn id
CHILD = "019fb66b-325d-7271-adff-114373383273"       # …and the child task's own
FORK_ISO, FORK = "2026-07-31T04:25:06.388Z", 1785471906
MID = "I'll check the live weather for Denpasar."
ANSWER = "Denpasar live weather: shower, 29C."


# --------------------------------------------------------------- the vocabulary

def test_the_task_key_names_a_task_not_an_agent():
    """One child, two tasks -> two keys. This is the whole reason the model is not
    just the agent id: a codex child handed a follow-up task, or a teammate
    re-tasked by mail, would otherwise fold both results into one card."""
    assert CT.key("a1", "t1") != CT.key("a1", "t2")
    assert CT.key("a1", "t1") == CT.key("a1", "t1")
    # either half may be missing — a host with no task id still gets a per-agent
    # key (the old one-task-per-child behaviour), and only a total absence of
    # identity stamps nothing at all
    assert CT.key("a1", "") and CT.key("", "t1")
    assert CT.key("", "") == ""


def test_stamp_validates_and_the_two_consumer_questions_answer_off_it():
    st = CT.stamp("a1#t1", CT.STEP_END, PARENT)
    op = O.label("⇠ result", O.SLATE, ctask=st)
    assert CT.of(op) == {"id": "a1#t1", "step": CT.STEP_END, "turn": PARENT}
    assert CT.ends_turn(op) == PARENT             # "this closes a task in PARENT"
    # a START endpoint is not an ordering anchor — only a completion can be late
    assert CT.ends_turn(O.label("⇢ prompt", O.SLATE,
                                ctask=CT.stamp("a1#t1", CT.STEP_START,
                                               PARENT))) == ""
    # …and an unknown step / a task-less call writes NOTHING, so a producer
    # mistake degrades to "no ordering hint" instead of a stamp nobody can trust
    assert CT.stamp("a1#t1", "middle") is None
    assert CT.stamp("", CT.STEP_END) is None
    assert CT.of(O.label("x", O.SLATE)) is None
    assert CT.of(O.label("x", O.SLATE, ctask=CT.stamp("a1#t1", "middle"))) is None
    # a task whose parent turn is unknown (every Claude agent) stamps the
    # endpoints and answers "" — the ordering rule is then inert by construction
    assert CT.ends_turn(O.label("⇠ result", O.SLATE,
                                ctask=CT.stamp("a1#t1", CT.STEP_END))) == ""


def test_final_turn_is_the_turns_answer_and_nothing_else():
    assert CT.final_turn({"kind": "message", CT.REC_TURN: PARENT,
                          CT.REC_FINAL: 1}) == PARENT
    assert CT.final_turn({"kind": "message", CT.REC_TURN: PARENT}) == ""
    assert CT.final_turn({"kind": "message", CT.REC_FINAL: 1}) == ""
    assert CT.final_turn({}) == "" and CT.final_turn(None) == ""


# ------------------------------------------------- the codex child's own stream

def _ev(typ, ts=None, **kw):
    return {"type": "event_msg", "timestamp": ts, "payload": {"type": typ, **kw}}


def _prefix(parent_turn=PARENT):
    """The replayed-parent PREFIX every codex child rollout opens with: the
    child's session_meta, the parent's last human turn (which is the brief), and
    the parent's own task_started — whose `started_at` predates the fork and whose
    turn id is therefore the PARENT's."""
    return [
        {"type": "session_meta", "timestamp": FORK_ISO,
         "payload": {"thread_source": "subagent", "timestamp": FORK_ISO,
                     "source": {"subagent": {"thread_spawn": {}}}}},
        {"type": "response_item", "payload": {
            "type": "message", "role": "user",
            "content": [{"type": "input_text", "text": BRIEF}]}},
        _ev("task_started", started_at=FORK - 8, turn_id=parent_turn),
    ]


def _run(tmp_path, monkeypatch, records, label="bali_weather", aid="c1"):
    """Drive plugins/codex/stream.Renderer in the SUBAGENT register over a real
    rollout FILE (parser included, gate included) and return (renderer, ops)."""
    monkeypatch.setenv("CLAUDE_CODEX_SUBAGENT", "1")
    monkeypatch.setattr(O, "_SRC", "sub:" + aid, raising=False)
    monkeypatch.setattr(O, "_SRC_INIT", True, raising=False)
    log = str(tmp_path / ("claude-mirror-ct-%s.log" % label))
    roll = tmp_path / ("rollout-%s.jsonl" % label)
    roll.write_text("".join(json.dumps(r) + "\n" for r in records),
                    encoding="utf-8")
    ST._init(["claude-codex-stream.py", log,
              ",".join(str(x) for x in SL.SUB_PALETTE[0]), str(roll), "-", label])
    assert ST.REGISTER == ST.REG_SUBAGENT
    rd = ST.Renderer()
    rd.fork_epoch = RO.subagent_fork_epoch(str(roll))
    rd.sub_open = rd.fork_epoch is None
    for line in roll.read_text(encoding="utf-8").splitlines():
        rec = RO.parse_line(line)
        if rec is not None:
            rd.feed_rollout(rec)
    _last, ops = S.ops_after(log, 0)
    return rd, ops


def _heads(ops):
    """Every block HEADER as (marker text, endpoint step, task id)."""
    out = []
    for op in ops:
        if op.get("t") != "label":
            continue
        ct = CT.of(op) or {}
        out.append((R.strip_ansi(op.get("s") or ""), ct.get("step", ""),
                    ct.get("id", "")))
    return out


def _body(ops, marker):
    """The body text behind the block whose header carries `marker`."""
    for i, op in enumerate(ops):
        if op.get("t") == "label" and marker in R.strip_ansi(op.get("s") or ""):
            for nxt in ops[i + 1:]:
                if nxt.get("t") == "gut":
                    return R.strip_ansi(nxt.get("s") or "")
    return ""


def test_a_final_answer_message_is_the_result_and_the_rest_stay_intermediate(
        tmp_path, monkeypatch):
    """The measured session's own record order: an intermediate note, then the
    `final_answer`, then task_complete. The phase decides — so the ⇠ result card
    is the answer and the note before it stays a ✎ message, where the old code
    waited for task_complete and promoted whatever was pending."""
    rd, ops = _run(tmp_path, monkeypatch, _prefix() + [
        _ev("task_started", started_at=FORK, turn_id=CHILD),
        _ev("agent_message", ts="2026-07-31T04:25:12.179Z", message=MID,
            phase="commentary"),
        _ev("agent_message", ts="2026-07-31T04:25:29.322Z", message=ANSWER,
            phase=RO.PHASE_FINAL),
        _ev("task_complete", ts="2026-07-31T04:25:29.422Z", turn_id=CHILD,
            started_at=FORK, completed_at=FORK + 23, last_agent_message=ANSWER),
    ])
    task = CT.key("c1", CHILD)
    assert _heads(ops) == [("⇢ prompt", CT.STEP_START, task),
                           ("✎ message", "", ""),
                           ("⇠ result", CT.STEP_END, task)]
    assert _body(ops, "⇠ result") == ANSWER
    assert _body(ops, "✎ message") == MID
    # …and BOTH endpoints name the parent turn, which is the fact the web orders on
    ends = [CT.ends_turn(op) for op in ops if CT.ends_turn(op)]
    assert ends and set(ends) == {PARENT}
    # the result card's duration is measured to the MESSAGE's own clock, not to
    # now (a rollout replayed from disk would otherwise report the file's age)
    note = next(op["note"] for op in ops if "⇠" in (op.get("s") or ""))
    assert note == 'Agent "bali_weather" finished · 23.3s'
    assert rd.result_sent and rd.pending_msg is None


def test_a_trailing_message_after_the_answer_is_not_a_second_result(
        tmp_path, monkeypatch):
    """"Do not treat an ordinary message as a final result": once codex has named
    the answer, anything after it is a ✎ message — including a message still
    pending when task_complete lands, which used to become the result."""
    _rd, ops = _run(tmp_path, monkeypatch, _prefix() + [
        _ev("task_started", started_at=FORK, turn_id=CHILD),
        _ev("agent_message", message=ANSWER, phase=RO.PHASE_FINAL),
        _ev("agent_message", message="one more thought", phase="commentary"),
        _ev("task_complete", turn_id=CHILD, completed_at=FORK + 23,
            last_agent_message=ANSWER),
    ])
    marks = [h[0] for h in _heads(ops)]
    assert marks == ["⇢ prompt", "⇠ result", "✎ message"]
    assert marks.count("⇠ result") == 1


def test_a_second_task_of_one_child_gets_its_own_result(tmp_path, monkeypatch):
    """A codex child can be handed a FOLLOW-UP task on the same rollout. Two
    tasks, two ids, two result cards — grouping by agent id would have merged
    them, which is why the model keys on the task."""
    _rd, ops = _run(tmp_path, monkeypatch, _prefix() + [
        _ev("task_started", started_at=FORK, turn_id=CHILD),
        _ev("agent_message", ts="2026-07-31T04:25:29.000Z", message=ANSWER,
            phase=RO.PHASE_FINAL),
        _ev("task_complete", turn_id=CHILD, completed_at=FORK + 23,
            last_agent_message=ANSWER),
        # …and a second task, in a LATER parent turn
        _ev("task_started", started_at=FORK + 60, turn_id="turn-2"),
        {"type": "event_msg", "payload": {"type": "user_message",
                                          "message": "now check Ubud"}},
        _ev("agent_message", ts="2026-07-31T04:26:26.000Z", message="Ubud: 26C.",
            phase=RO.PHASE_FINAL),
        _ev("task_complete", turn_id="turn-2", completed_at=FORK + 80,
            last_agent_message="Ubud: 26C."),
    ])
    results = [(h[1], h[2]) for h in _heads(ops) if h[0] == "⇠ result"]
    assert results == [(CT.STEP_END, CT.key("c1", CHILD)),
                       (CT.STEP_END, CT.key("c1", "turn-2"))]
    # each result names ITS OWN task, and the second one's duration is that task's
    # alone (started 60s in, answered 20s later), not the run's 80s — measured to
    # the answering message's own clock, which is what keeps a rollout replayed
    # from disk from timing itself to now
    notes = [op["note"] for op in ops
             if op.get("t") == "label" and "⇠" in (op.get("s") or "")]
    assert notes[0].endswith("· 23.0s") and notes[1].endswith("· 20.0s")


def test_a_pre_phase_rollout_still_resolves_through_the_old_inference(
        tmp_path, monkeypatch):
    """A rollout written before `phase` existed (and before `turn_id`): the LAST
    held message at task_complete is still the result, the endpoints are still
    stamped (by task ORDINAL, since there is no turn to name), and no parent turn
    is claimed — so the web simply does not reorder."""
    _rd, ops = _run(tmp_path, monkeypatch, [
        {"type": "session_meta", "timestamp": FORK_ISO,
         "payload": {"thread_source": "subagent", "timestamp": FORK_ISO,
                     "source": {"subagent": {"thread_spawn": {}}}}},
        {"type": "response_item", "payload": {
            "type": "message", "role": "user",
            "content": [{"type": "input_text", "text": BRIEF}]}},
        _ev("task_started", started_at=FORK - 8),          # no turn_id anywhere
        _ev("task_started", started_at=FORK),
        {"type": "event_msg", "payload": {"type": "agent_message",
                                          "message": MID}},
        {"type": "event_msg", "payload": {"type": "agent_message",
                                          "message": ANSWER}},
        _ev("task_complete", completed_at=FORK + 23),
    ])
    assert _heads(ops) == [("⇢ prompt", CT.STEP_START, CT.key("c1", "1")),
                           ("✎ message", "", ""),
                           ("⇠ result", CT.STEP_END, CT.key("c1", "1"))]
    assert _body(ops, "⇠ result") == ANSWER
    # stamped (header AND body, both endpoints) but naming no parent turn
    assert [CT.ends_turn(op) for op in ops if CT.of(op)] == [""] * 4


def test_last_agent_message_is_only_a_last_resort(tmp_path, monkeypatch):
    """codex's own `last_agent_message` fills in for a stream that never SAW the
    message record (a tailer that joined mid-run) — and only then: a run whose
    message was already painted must not have it repainted as a result."""
    # never saw the message -> the result comes from the completion record
    _rd, ops = _run(tmp_path, monkeypatch, _prefix() + [
        _ev("task_started", started_at=FORK, turn_id=CHILD),
        _ev("task_complete", turn_id=CHILD, completed_at=FORK + 23,
            last_agent_message=ANSWER),
    ], label="joined-late")
    assert [h[0] for h in _heads(ops)] == ["⇢ prompt", "⇠ result"]
    assert _body(ops, "⇠ result") == ANSWER
    # …but a message already on screen as a ✎ (flushed by the block after it) is
    # NOT repainted: no result card, exactly as this run rendered before
    _rd2, ops2 = _run(tmp_path, monkeypatch, _prefix() + [
        _ev("task_started", started_at=FORK, turn_id=CHILD),
        {"type": "event_msg", "payload": {"type": "agent_message",
                                          "message": ANSWER}},
        {"type": "event_msg", "payload": {"type": "agent_reasoning",
                                          "text": "checking once more"}},
        _ev("task_complete", turn_id=CHILD, completed_at=FORK + 23,
            last_agent_message=ANSWER),
    ], label="already-shown")
    assert [h[0] for h in _heads(ops2)] == ["⇢ prompt", "✎ message",
                                            "⋯ reasoning"]


def test_the_child_learns_its_parent_turn_from_the_replayed_prefix(
        tmp_path, monkeypatch):
    """The prefix is the ONLY place a child can read the turn that spawned it —
    its own records name only its own turn. The gate drops those records; this one
    fact is taken on the way past."""
    rd, _ops = _run(tmp_path, monkeypatch, _prefix("parent-XYZ") + [
        _ev("task_started", started_at=FORK, turn_id=CHILD),
        _ev("agent_message", message=ANSWER, phase=RO.PHASE_FINAL),
    ])
    assert rd.parent_turn == "parent-XYZ" and rd.cur_turn == CHILD
    # a rollout that is not a child's has no prefix and no parent turn to find
    rd2, _o2 = _run(tmp_path, monkeypatch, [
        _ev("task_started", started_at=FORK, turn_id=CHILD),
        _ev("agent_message", message=ANSWER, phase=RO.PHASE_FINAL),
    ], label="no-prefix")
    assert rd2.parent_turn == "" and rd2.sub_open is True
