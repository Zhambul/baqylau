# plugins/codex/read.py — codex ROLLOUT read models for the web dashboard.
#
# The dashboard's read-side providers a codex run answers (ctx saturation, the
# ⊜ compact gate's prompt count, the scoped mirror's conversation bubbles, the
# pending question card, the saved effort level). Each is the codex twin of a
# plugins/claude_code/transcript.py read model and is wired into the SAME
# registry fan-out (plugins/__init__.py), gated by codex's `owns` — so a Claude
# transcript never reaches here and a codex rollout never reaches a Claude parser
# (plugins._first_path). Read-only: like ctx/goal on the Claude side these add NO
# audit rows (the number is derived, and the rollout it derives from is already
# the durable record).
#
# The PARSE half is plugins/codex/rollout.py (the one owner of the rollout record
# grammar); this module only READS files and maps rollout.parse's typed records
# onto the dashboard's shapes. It is the codex analogue of transcript.py's
# context_probe / prompt_count / conversation_for, kept here (not in rollout.py)
# so rollout.py stays pure/I/O-free.
import json
import os
from datetime import datetime

from core import childtask as CT
from core import tail as TL

from plugins.codex import rollout as RO

# Bounded windows — the same discipline as transcript.py: a saturation/gate read
# must never cost a full multi-MB rollout scan (a `codex exec` rollout runs to
# hundreds of KB), so the tail/head is capped and the size gate fails OPEN.
CTX_TAIL_B = 262144          # tail bytes context() scans for the last token_count
PROMPT_SCAN_B = 256 * 1024   # a rollout bigger than this is `cap` unread (fail-open)
PROMPT_CAP = 8               # stop counting prompts here — the gate never needs more


def _complete_lines(path, pos):
    """Complete text lines from byte `pos`: ([line, …], new_pos). A trailing
    partial line is NOT consumed (new_pos stops before it) so json never sees a
    torn record — the codex twin of transcript._complete_lines (the dependency
    rule forbids importing the Claude module for it)."""
    try:
        with open(path, "rb") as fh:
            fh.seek(pos)
            data = fh.read()
    except OSError:
        return [], pos
    end = data.rfind(b"\n")
    if end < 0:
        return [], pos
    return data[:end].decode("utf-8", "replace").split("\n"), pos + end + 1


def _line_ts(s):
    """The rollout line's ENVELOPE `timestamp` as an epoch float, or None. Codex
    stamps every record's envelope with an ISO-8601 `timestamp`; the conversation
    merge interleaves bubbles into the op stream by it (the op stream carries a
    wall-clock `_ts`), falling back to arrival order when absent."""
    try:
        v = json.loads(s).get("timestamp")
    except Exception:
        return None
    if not isinstance(v, str) or not v:
        return None
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def context(path, main=False):
    """Context saturation for a codex rollout — the LAST `token_count` record's
    `last_token_usage.total_tokens` over `model_context_window`, as {"used",
    "window", "pct", "model", "effort"}; None when the tail holds no usable usage
    (a fresh run, an unreadable file). The codex twin of transcript.context_probe.

    codex's CUMULATIVE `total_token_usage` never resets across a compaction, so
    the ctx bar reads the LAST TURN's total (`last`) — the one figure that
    measures the live occupancy (docs/codex.md *token_count keeps three things*).
    `model` + `effort` are the LAST `turn_context`'s — so a mid-session `/model`
    switch is reflected (the reversed scan finds the newest first). `effort` is
    codex's ONLY effort source (it has no cwd-keyed default, unlike Claude's
    persisted `/effort`); the dashboard prefers it over effort_default. `main` is
    accepted for the provider arity but ignored — codex has no sidechain records
    to skip."""
    lines = TL.tail_lines(path, CTX_TAIL_B)
    if lines is None:
        return None
    used = window = None
    model = effort = ""
    for raw in reversed(lines):
        if used is None and b'"token_count"' in raw:
            try:
                rec = RO.parse(json.loads(raw))
            except Exception:
                rec = None
            if rec and rec["kind"] == "usage" and isinstance(rec.get("last"), dict) \
                    and isinstance(rec.get("window"), int) and rec["window"] > 0:
                tot = rec["last"].get("total_tokens")
                if isinstance(tot, int) and tot > 0:
                    used, window = tot, rec["window"]
        elif not model and (b'"turn_context"' in raw
                            or b'"thread_settings_applied"' in raw):
            # model/effort come from the NEWEST of turn_context (per-turn) OR
            # thread_settings_applied (every picker change) — scanning newest→
            # oldest, the first of either wins, so a /model switch shows at once
            # instead of lagging until the next turn.
            try:
                rec = RO.parse(json.loads(raw))
            except Exception:
                rec = None
            if rec and rec["kind"] in ("turn_context", "settings"):
                model = rec.get("model") or ""
                effort = rec.get("effort") or ""
        if used is not None and model:
            break
    if used is None or not window:
        return None
    return {"used": used, "window": window,
            "pct": min(100, used * 100 // window), "model": model,
            "effort": effort}


def usage(path):
    """The rollout's LAST rate-limit reading — codex's per-SESSION twin of
    Claude's status-line snapshot, as {"planType", "windows": [{used_pct,
    window_mins, resets_at}]}; None when the tail holds none. The same probe
    family as context(): one bounded tail read, newest record wins, no audit
    rows.

    The scan is for the last `token_count` whose `rate_limits` IS NON-NULL, not
    simply the last token_count: the field is nullable and codex emits plenty of
    usage events without it, so "the newest event" and "the newest event that
    says anything about limits" are different records (12/12 carried one in the
    measured rollout, but a run that has not talked to the API since its last
    compaction has trailing events that do not).

    This is what makes a codex session's limit bars real: the ACCOUNT-level twin
    (plugins/codex/usage.usage_windows, over `codex app-server`) answers for the
    list-page strip, but a PARKED session has no app server to ask and its
    rollout is the only surviving record of where its limits stood."""
    lines = TL.tail_lines(path, CTX_TAIL_B)
    if lines is None:
        return None
    for raw in reversed(lines):
        if b'"token_count"' not in raw or b'"rate_limits"' not in raw:
            continue
        try:
            payload = (json.loads(raw).get("payload") or {})
        except Exception:
            continue
        got = RO.rate_limits(payload)
        if got:
            return got
    return None


def codex_effort(path):
    """The LAST `turn_context`'s reasoning-effort token (low/medium/high/xhigh/
    max/ultra), or "" — the newest wins (a mid-session switch is reflected).
    Distinct from context(), which needs a `token_count` to report saturation and
    returns None on a fresh run: the ✦ model gesture needs the effort ALONE, to
    PRESERVE it across a model switch, even before the first usage record."""
    lines = TL.tail_lines(path, CTX_TAIL_B)
    if lines is None:
        return ""
    for raw in reversed(lines):
        # the NEWEST of turn_context (per-turn) or thread_settings_applied (every
        # picker change) — the latter is fresher after a /model switch made
        # without running a turn (the reported `terra high` shown as a stale
        # level from the last turn_context).
        if b'"turn_context"' in raw or b'"thread_settings_applied"' in raw:
            try:
                rec = RO.parse(json.loads(raw))
            except Exception:
                rec = None
            if rec and rec["kind"] in ("turn_context", "settings"):
                return rec.get("effort") or ""
    return ""


def prompts(path, cap=PROMPT_CAP):
    """How many NON-synthetic user turns a codex rollout holds, capped at `cap`;
    None when it has nothing to say. The codex twin of transcript.prompt_count and
    the ⊜ compact gate's codex source — so it FAILS OPEN identically (a rollout
    over PROMPT_SCAN_B is `cap` unread, an unreadable one `cap`, an empty one
    None), because the count only ever argues for DISABLING the button.

    A real user turn is a `response_item/message` with role user that is not
    codex machinery (rollout.is_synthetic — the context blocks codex re-injects
    as user messages every turn). The RESPONSE_ITEM register is used, not the
    event_msg `user_message`, so a post-abort/queued prompt still counts."""
    if not path:
        return None
    try:
        if os.path.getsize(path) > PROMPT_SCAN_B:
            return cap
        with open(path, "rb") as f:
            raw = f.read(PROMPT_SCAN_B)
    except OSError:
        return cap
    n = 0
    for ln in raw.split(b"\n"):
        if b'"message"' not in ln:            # cheap prefilter before the parse
            continue
        try:
            rec = RO.parse(json.loads(ln))
        except Exception:
            continue
        if rec and rec["kind"] == "chat" and rec.get("role") == "user" \
                and not rec.get("synthetic"):
            n += 1
            if n >= cap:
                break
    return n or None


def _rollout_for(sid, agent_id):
    """The rollout path for one identity, or "": a SIDECAR/subagent codex run
    (agent_id names it — resolved off this plugin's own nested.session_runs, whose
    transcript IS the run's rollout), or the STANDALONE host's own session
    rollout (agent_id empty — session_row's transcript_path, gated by owns).
    Lazy import of sessionapi (a read-side dependency, not a hook path)."""
    from core import sessionapi as API
    from plugins.codex import nested
    if agent_id:
        for rec in nested.session_runs(sid):
            if rec.get("agent_id") == agent_id:
                path = rec.get("transcript") or ""
                return path if RO.owns(path) and os.path.isfile(path) else ""
        return ""
    path = (API.session_row(sid) or {}).get("transcript_path") or ""
    return path if RO.owns(path) and os.path.isfile(path) else ""


_SLASH_MAX = 4                  # leading `/word` tokens a re-submission may drop


def _strip_slash(key):
    """A prompt key with its leading SLASH-COMMAND tokens removed — `/plan/plan
    do X` -> `do X`, `do X` -> `do X`.

    codex records a slash-command turn twice (see the note in `_add`), the two
    copies differing only by this prefix; stripping it is what lets them
    de-double. Bounded (`_SLASH_MAX`) and prefix-only: a message that merely
    CONTAINS a slash keeps every word of it.

    The doubled `/plan/plan` spelling is codex's own — the TUI writes the command
    word alongside the expansion — so the tokens are taken as a RUN rather than
    assumed to be one."""
    s = key.lstrip()
    for _ in range(_SLASH_MAX):
        if not s.startswith("/"):
            break
        head = s.split(" ", 1)
        tok = head[0]
        # a bare `/word` (possibly several run together, `/plan/plan`)
        if len(tok) < 2 or not tok[1:].replace("/", "").replace("-", "").isalnum():
            break
        s = (head[1] if len(head) > 1 else "").lstrip()
    return s


# NO `plandecision` twin for codex, and this is the evidence rather than an
# omission. Claude's pair works because ExitPlanMode is a TOOL: its tool_result
# carries the verdict, so approved/changes/rejected is a fact on disk. codex's
# plan mode has no tool and writes NO decision record at all — measured over
# every plan-bearing rollout in the corpus (2026-07-31), the only thing that
# follows a `<proposed_plan>` is either more planning or a developer message
# switching the collaboration mode back to Default. That switch is NOT a
# verdict: it means "we left plan mode", which the user reaches by approving the
# plan AND by simply changing mode, and the two are indistinguishable in the
# file. Claiming `approved` from it would be a verdict pinned on a guess — the
# same failure Claude's own implementation avoids by matching the plan's tool_use
# id and failing QUIET. So the plan bubbles alone until codex records a decision.
def conversation(sid, pos=0, agent_id=""):
    """ONE codex identity's conversation records from byte `pos`, as
    (records, new_pos) — the codex twin of transcript.conversation_for, behind
    plugins.conversation(). None when this plugin has no rollout for the pair
    (the fan-out then asks / has already been answered by the next plugin).

    Maps the RESPONSE_ITEM register (docs/codex.md *Two registers*): a
    non-synthetic `chat` becomes a `prompt` (role user) or `message` (assistant)
    bubble, a `think` (reasoning summary) a `message` bubble — the record shape
    dashboard/read/mirror.conv_items merges (kind/text/anchor/ts). Each carries
    the line's envelope `ts`; `anchor` is None (a codex op's copy-group is a
    random new_group carrying no tool_use id to match on, so the merge places a
    bubble by TIMESTAMP — the codex op stream's `_ts` is real-time-tailed, close
    enough for order).

    THIS is codex sidecar → subagent parity (deliverable C): agent scope drops a
    codex run's PROSE ops (opshtml.actclass.prose_block, codex palette) and takes
    its prose from HERE instead, exactly as a Claude subagent's does — while its
    exec/patch ops stay in the scoped mirror. The STANDALONE main-thread branch
    (agent_id empty) resolves too, but note the fan-out asks claude_code FIRST and
    its rollout parse returns an empty [] (a non-None answer) — which SHADOWS this
    branch on purpose: a standalone codex run already paints its prose into its
    (unstamped) ops, so bubbles here would DOUBLE it. The branch exists for
    direct callers/tests and a future main-view that drops those ops."""
    path = _rollout_for(sid, agent_id)
    if not path:
        return None
    # A SUBAGENT rollout opens with the parent thread's REPLAYED history; skip it
    # so the subagent's bubbles are its OWN turns only (docs/codex.md *Sidecar →
    # subagent parity*) — the same boundary the op stream gates on, applied here as
    # a byte offset (this is a random-access read of a complete file). No-op (0)
    # for the standalone own-run (agent_id empty) and a non-subagent sidecar.
    brief = ""
    if agent_id and pos == 0:
        # …and the BRIEF that prefix holds is exactly what must survive it: the
        # task this child was spawned with (rollout.subagent_brief — the child's
        # own NEW_TASK payload is encrypted, so the prefix's last human turn is
        # the only plaintext statement of it). Read BEFORE the seek, prepended as
        # the first bubble, so an agent scope opens on its brief the way a Claude
        # agent's does. It cannot double with the launch CARD the op stream
        # paints: that card is `bubbled`, which is precisely the producer saying
        # "this content is also a conversation record — drop the op wherever the
        # conversation renders" (core/ops.py), and agent scope does.
        brief = RO.subagent_brief(path)
        pos = RO.subagent_body_offset(path)
    lines, new_pos = _complete_lines(path, pos)
    out = []
    # codex writes each turn in BOTH registers — the event_msg one (user_message
    # →`prompt`, agent_message→`message`, agent_reasoning→`reasoning`) AND the
    # response_item one (`chat` role user/assistant, `think`). Which register a
    # turn lands in is MODE-DEPENDENT: an interactive `codex` (what you launch
    # directly) often writes prose ONLY as event_msg, while `codex exec` writes
    # both — so reading just `chat`/`think` (the old code) returned NOTHING for an
    # interactive session and the web showed no messages. Read BOTH and de-double
    # by text: the same turn in both registers must bubble ONCE (first wins).
    seen = set()
    last_prompt = [""]          # the previous prompt's slash-stripped key
    # The TURN each record belongs to (codex's `turn_id`, off every task_started —
    # "" for a pre-turn_id rollout). The parent half of the child-task model
    # (core/childtask.py): a record stamped with its turn, plus the `final` mark
    # below, is what lets the web's merge place a child's result BEFORE the answer
    # that turn ended on instead of after it. A list because _add closes over it.
    turn = [""]

    def _add(kind, text, ts, final=False):
        body = (text or "").strip()
        key = " ".join(body.split())
        if not body or key in seen:
            return
        if kind == "prompt":
            # THE SLASH-COMMAND RE-SUBMISSION. A `/plan …` turn is recorded
            # TWICE by codex itself, and not as two registers of one turn: it
            # writes the raw submission (`/plan/plan do X`), ABORTS that turn
            # (turn_aborted), injects the mode's developer instructions, and
            # re-submits the SAME prompt with the command stripped (`do X`).
            # The two differ by exactly that prefix, so the text key above can
            # never match them and the message bubbled twice (the reported bug).
            #
            # Matched against the IMMEDIATELY PRECEDING prompt only, never the
            # whole `seen` set: the abort and the re-submission are adjacent by
            # construction, while "/plan do X" typed now and "do X" typed an
            # hour later are two real turns that must both show.
            bare = _strip_slash(key)
            if bare and bare == last_prompt[0]:
                return
            last_prompt[0] = bare
        rec = {"kind": kind, "text": body, "anchor": None, "ts": ts}
        # WHICH TURN this record belongs to, and whether it is that turn's FINAL
        # response (core/childtask.py's REC_TURN / REC_FINAL). Only the turn's
        # answer is an ordering anchor — mid-turn prose must never be moved — so
        # `final` is set from codex's own `phase: "final_answer"` and from nothing
        # else. Written whenever the rollout names a turn; absent (and the whole
        # mechanism inert) on a rollout that does not.
        if turn[0]:
            rec[CT.REC_TURN] = turn[0]
            if final:
                rec[CT.REC_FINAL] = 1
        # the assistant bubble's author — "codex", so the web reply bubble reads
        # "codex" instead of the msg_html default "claude" (a codex session must
        # not attribute its reply to Claude). The `prompt` bubble stays "you".
        # A PLAN is the assistant's too: msg_html words it `<who> ▸ proposes a
        # plan`, so without this a codex plan would read as Claude's.
        if kind in ("message", "plan"):
            rec["who"] = "codex"
        seen.add(key)
        out.append(rec)

    if brief:
        # stamped with the FORK moment (the child session_meta's own timestamp,
        # which subagent_fork_epoch already owns), so the merge places the brief
        # before every turn the child then took
        _add("prompt", brief, RO.subagent_fork_epoch(path))

    for s in lines:
        s = s.strip()
        if not s:
            continue
        try:
            rec = RO.parse(json.loads(s))
        except Exception:
            continue
        if rec is None:
            continue
        k = rec["kind"]
        ts = _line_ts(s)
        if k == "task_started":
            # the turn every record after this one belongs to (see `turn` above)
            turn[0] = rec.get("turn") or turn[0]
            continue
        _final = rec.get("phase") == RO.PHASE_FINAL
        # event_msg register (a turn's only source in interactive mode)
        if k == "prompt":
            _add("prompt", rec.get("text"), ts)
        elif k == "message":
            _add("message", rec.get("text"), ts, _final)
        elif k == "reasoning":
            # a thinking summary is never a turn's answer, whatever follows it
            _add("message", rec.get("text"), ts)
        # response_item register (exec mode + some interactive turns)
        elif k == "chat" and not rec.get("synthetic"):
            role = rec.get("role") or ""
            if role == "user":
                _add("prompt", rec.get("text"), ts)
            elif role == "assistant":
                _add("message", rec.get("text"), ts, _final)
            # developer/system non-synthetic turns are codex machinery — skip
        elif k == "think":
            _add("message", rec.get("text"), ts)
        elif k == "plan":
            # codex's plan-mode proposal (rollout.PLAN_WRAPPER) — its own bubble,
            # the twin of the `plan` record a Claude ExitPlanMode writes. No
            # `plandecision` twin follows it: see the note above the parser.
            _add("plan", rec.get("text"), ts)
    return out, new_pos


def pending_dialog(sid):
    """The codex run's OPEN modal — whichever of two plan-mode surfaces is open
    and NEWEST, else None. Both have no hook (codex fires none for them), so both
    are derived READ-side from the standalone host's own rollout tail (a sidecar's
    surface in agent scope later):

      · a `request_user_input` question with no answer after it →
        {"kind": "ask", "tool_use_id", "questions": [...]} (the web question card)
      · a plan-mode Plan (`item_completed`/`item.type == "Plan"`) not yet decided
        → {"kind": "plan", "plan": <markdown>, "plan_id"} (the web plan card)

    A Plan is DECIDED — no longer pending — once a NEW turn starts or you reply
    after it (`task_started` / `user_message` newer than the Plan): clicking
    "implement" opens a fresh default-mode turn, and typing a follow-up starts a
    turn of its own. Scanning newest→oldest, the first OPEN modal wins, so an ask
    raised after a plan (both plan-mode) shadows it, matching the screen. `plan_id`
    lets the driver pair the decision; `tool_use_id` pairs an ask answer. The card
    endpoints verify the live screen anyway, so a stale tail can never mis-drive."""
    path = _rollout_for(sid, "")
    if not path:
        return None
    lines = TL.tail_lines(path, CTX_TAIL_B)
    if lines is None:
        return None
    answered = set()
    plan_decided = False           # a task_started / user_message newer than a Plan
    for raw in reversed(lines):
        # An answer (function_call_output) for a call id closes that ask; scan
        # newest→oldest and return the first ask still open.
        if b'"function_call_output"' in raw or b'"custom_tool_call_output"' in raw:
            try:
                cid = (json.loads(raw).get("payload") or {}).get("call_id")
            except Exception:
                cid = None
            if cid:
                answered.add(cid)
            continue
        # A new turn or your own message (newer than any Plan we'll reach below)
        # means an earlier plan was decided/superseded — mark it so a Plan record
        # reached afterwards is not offered.
        if b'"task_started"' in raw or b'"user_message"' in raw:
            plan_decided = True
            continue
        if b'"item_completed"' in raw:
            if plan_decided:
                continue
            try:
                rec = RO.parse(json.loads(raw))
            except Exception:
                rec = None
            if rec and rec["kind"] == "plan":
                from plugins.codex import plandialog as PD
                return {"kind": "plan", "plan": rec["text"],
                        "plan_id": rec.get("id") or "",
                        # the approve rows the card shows as buttons — static
                        # (the picker is pure TUI, not in the rollout), and
                        # re-verified against the live screen at decide time.
                        "options": [dict(o) for o in PD.APPROVE_OPTIONS]}
            continue
        if b'"request_user_input"' not in raw:
            continue
        try:
            o = json.loads(raw)
        except Exception:
            continue
        rec = RO.parse(o)
        if not rec or rec["kind"] != "ask":
            continue
        cid = (o.get("payload") or {}).get("call_id") or ""
        if cid in answered:
            continue
        return {"kind": "ask", "tool_use_id": cid, "questions": rec["questions"]}
    return None


# NOTE: codex deliberately provides NO cwd-keyed effort_default (see
# plugins/codex/__init__.py) — a global-config read would leak into Claude
# sessions. A codex session's effort is a per-turn rollout fact, surfaced by
# context() from turn_context.effort.
