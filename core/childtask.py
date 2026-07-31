# core/childtask.py — the CHILD-TASK model, host-neutral.
#
# A child agent's stream has two ENDPOINTS a reader cares about: the task it was
# handed (its launch card) and what it returned (its result card). core/
# agentblocks.py already owns how those two blocks LOOK for every host; this
# module owns WHICH TASK they are about, which is a different fact and the one
# every consumer of the merged stream was missing.
#
# Why it exists (the bug it was written for): a codex child's task_complete can
# land AFTER the parent has already printed its final answer — measured, session
# 019fb66b-12a0 (2026-07-31): the child's message reached the parent at
# 04:25:26.784, the parent answered at 04:25:28.901, and only then did the child
# emit its own `final_answer` (04:25:29.322) and `task_complete` (04:25:29.422).
# The web stream merges ops and conversation records by TIMESTAMP
# (dashboard/read/mirror._merge_order), so the child's `Agent finished` card
# sorted after the answer it had contributed to — chronologically true and
# semantically backwards, because the child's work is part of the turn the answer
# CLOSES. Time alone cannot fix that; the stream needs to know which task belongs
# to which turn. So:
#
#   · a child TASK has an identity of its own (`key`) — NOT just the agent's.
#     A child can be handed a SECOND task later (codex's follow-up task, a
#     resumed teammate), and grouping by agent id would merge two results into
#     one. One task, one result card, one completion.
#   · its two endpoint blocks carry that identity plus which endpoint they are
#     (`STEP_START` / `STEP_END`) — the op field `FIELD`, stamped by
#     core/agentblocks.AgentStream through `stamp()`.
#   · a task also names the PARENT TURN it was started in, when its host can say
#     so (`turn`), and the parent's own conversation records name the turn they
#     belong to plus whether they ARE that turn's final response (`rec_turn` /
#     `rec_final`). Those two halves are what the ordering rule joins on.
#
# WHO FILLS THE PARENT TURN. codex writes a turn id on every `task_started` /
# `task_complete` and a child rollout opens by replaying the parent thread, so a
# codex child knows the turn that spawned it (plugins/codex/stream.py) and a
# codex host's conversation records carry `turn`/`final` off the same records plus
# the `phase: "final_answer"` an assistant message wears (plugins/codex/read.py).
# Claude Code records NO turn id anywhere — a turn is delimited by the
# prompt/reply chain and nothing on disk names it — so its child tasks carry the
# identity and the endpoints but no parent turn, and the ordering rule below is
# simply inert for them. That is not a gap to fill with a guess: a Claude Task's
# tool_result is delivered to the lead BEFORE the lead can write the reply that
# uses it, so the inversion this module exists for cannot arise there through the
# hook path. A host that CAN name its turns gets the ordering; one that cannot
# keeps the presentation, unchanged.
#
# The op field is `ctask` and deliberately not `task`: in this repo's op
# vocabulary a "task" is already a TaskCreate/TaskUpdate todo row (core/ops.py's
# ACT_TASK), and two meanings on one key is how a reader ends up reading the
# wrong table.
#
# Stdlib-only leaf, like core/paths.py: producers (plugins), the web read model
# and the page's own reconcile all speak this one vocabulary, so it may import
# nothing of theirs.

# The op field carrying the stamp (see stamp() / of()).
FIELD = "ctask"

# The two ENDPOINTS of one child task — a closed vocabulary, like core/ops.py's
# ACTS. `start` is the launch card (the brief it was handed), `end` the result
# card (what it returned). Everything in between is just the child working and
# belongs to no endpoint.
STEP_START = "start"
STEP_END = "end"
STEPS = (STEP_START, STEP_END)

# The conversation-record fields the ordering rule reads on the PARENT side:
# which turn a record belongs to, and whether it is that turn's FINAL response
# (the reply that closes it — codex's `phase: "final_answer"`). Named here rather
# than in each host's record builder for the same reason `FIELD` is: the merge
# reads them from records two different plugins produce.
REC_TURN = "turn"
REC_FINAL = "final"


def key(agent_id, task_id):
    """The TASK identity — `<agent id>#<task id>`, stable for as long as that one
    task runs and different for the next task the same child is handed.

    Both halves are the caller's own vocabulary (a codex turn id, a Claude
    agent's streamer generation); this only spells the pair. Either half may be
    empty — a host with no task id of its own still gets a per-agent key, which
    is exactly the old (one task per child) behaviour — and "" only when there is
    no identity at all, which stamps nothing."""
    a = str(agent_id or "").strip()
    t = str(task_id or "").strip()
    if not (a or t):
        return ""
    return "%s#%s" % (a, t)


def stamp(task, step, turn=""):
    """The op field's value for one endpoint block: which task, which endpoint,
    and the parent turn it was started in ("" when the host cannot name one).

    None for a call with no task identity or an unknown step — the same
    drop-rather-than-write rule core/ops.py's `act` follows, so a producer
    mistake degrades to the previous behaviour (no reordering) instead of
    writing a stamp no consumer can trust."""
    if not task or step not in STEPS:
        return None
    o = {"id": str(task), "step": step}
    if turn:
        o["turn"] = str(turn)
    return o


def of(op):
    """An op's child-task stamp as a dict, or None. The one reader — the field
    name and the shape live here, so no consumer spells either."""
    v = op.get(FIELD) if isinstance(op, dict) else None
    return v if isinstance(v, dict) and v.get("id") else None


def ends_turn(op):
    """The PARENT TURN this op closes a child task in, or "" — i.e. "this block
    is a child task's result, and the task belonged to that turn". The whole
    consumer side of the ordering rule is this question plus final_turn()."""
    t = of(op)
    if not t or t.get("step") != STEP_END:
        return ""
    return str(t.get("turn") or "")


def final_turn(rec):
    """…and the other half: the TURN a conversation record is the FINAL RESPONSE
    of, or "". A record that names no turn, or names one without being its final
    response, is not an ordering anchor — mid-turn prose must never be moved."""
    if not isinstance(rec, dict) or not rec.get(REC_FINAL):
        return ""
    return str(rec.get(REC_TURN) or "")
