# harness/impl/codex/canonical/events.py — the codex rollout's `event_msg` register.
#
# codex's OWN digested UI stream: the half of the rollout a mirror paints
# (`prompt`/`message`/`reasoning`). The response_item twin — the model-API record
# the conversation is rebuilt from on resume — lives in items.py; the two are
# deliberately not unified (docs/codex.md *Two registers*), which is what keeps a
# mirror from painting every message and every think twice.
#
# One parser per `payload.type`, registered in EVENTS at the bottom; rollout.py
# dispatches through it. An unknown type is not an error — the grammar is
# VERSION-FRAGILE (verified drift across codex 0.95 → 0.144), so a missing key
# means None, never an exception.
from typing import Any

from harness.impl.codex.canonical.vocabulary import empty_record, strip_input_wrapper

# The PHASE codex stamps on an assistant message. `final_answer` is the one that
# matters: it is codex SAYING this message is the turn's answer, which is what
# tells a child's stream that this message is its RESULT rather than one more
# intermediate note (`commentary` is the other measured value). Absent on older
# rollouts — "" then, and the result falls back to the pre-phase inference.
PHASE_FINAL = "final_answer"

# The `item_completed` item types whose content reaches us through ANOTHER
# register and is therefore read there, not here (measured against codex-cli
# 0.147.0: `UserMessage` and `AgentMessage` items are completed for the same prose
# the `response_item/message` records already carry, `Reasoning` for the think the
# `reasoning` records carry — and `_ev_user_message` / `_ev_agent_message` /
# `_ev_agent_reasoning` already de-double the event_msg spellings of the same).
# A CLOSED list on purpose — see _ev_item_completed.
COVERED_ITEMS = ("UserMessage", "AgentMessage", "Reasoning")


def _ev_token_count(p: dict[str, Any]) -> dict[str, Any] | None:
    # Cumulative usage snapshot (info is null on rate-limit-only events).
    # `last_token_usage` + `model_context_window` ride along: the CUMULATIVE
    # total never resets across a compaction, so only the last turn's total
    # over the window measures ctx saturation.
    info = p.get("info")
    if not isinstance(info, dict):
        info = {}
    u = info.get("total_token_usage")
    if not isinstance(u, dict):
        return None
    last = info.get("last_token_usage")
    win = info.get("model_context_window")
    return {"kind": "usage", "usage": u,
            "last": last if isinstance(last, dict) else None,
            "window": win if isinstance(win, int) else None}


def _ev_thread_goal_updated(p: dict[str, Any]) -> dict[str, Any] | None:
    goal = p.get("goal")
    if not isinstance(goal, dict):
        return None
    return {
        "kind": "goal",
        "objective": goal.get("objective"),
        "status": goal.get("status"),
        "reason": goal.get("reason"),
    }


def _ev_thread_goal_cleared(p: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "goal", "objective": None, "status": "cleared", "reason": None}


def rate_limits(p: dict[str, Any]) -> dict[str, Any] | None:
    """A `token_count` payload's `rate_limits` block, normalized to codex's
    windows shape — {"planType", "windows": [{used_pct, window_mins,
    resets_at}]} — or None when the event carries none (the field is NULLABLE)
    or names no window.

    Deliberately NOT part of the `usage` record and NOT in the `EVENTS` table:
    codex emits a token_count with `info: null` on a RATE-LIMIT-ONLY event, which
    _ev_token_count drops entirely because it has no total_token_usage to report.
    The limits ride a different, independently-nullable field of the same event,
    so they are read on their own (harness/impl/codex/read.usage — the bounded tail
    probe for the last event that HAS them).

    Measured shape (rollout 019fb363, 2026-07-30): snake_case `used_percent` /
    `window_minutes` / `resets_at` (epoch seconds) / `plan_type`, `secondary`
    null on a plan with one window. That is the same information the app server
    returns in camelCase (harness/impl/codex/usage._normalize), and both are mapped
    here to ONE codex-internal shape so a single strip mapper serves both."""
    rl = p.get("rate_limits")
    if not isinstance(rl, dict):
        return None
    wins = []
    for key in ("primary", "secondary"):
        w = rl.get(key)
        if not isinstance(w, dict):
            continue
        wins.append({"used_pct": w.get("used_percent"),
                     "window_mins": w.get("window_minutes"),
                     "resets_at": w.get("resets_at")})
    if not wins:
        return None
    return {"planType": rl.get("plan_type") or "", "windows": wins}


def _patch_delta(ch: dict[str, Any]) -> tuple[int, int]:
    """(added, removed) line counts for one patch_apply_end change entry."""
    t = ch.get("type")
    if t == "add":
        return len((ch.get("content") or "").splitlines()), 0
    if t == "delete":
        return 0, len((ch.get("content") or "").splitlines())
    add = rem = 0
    for ln in (ch.get("unified_diff") or "").splitlines():
        if ln.startswith("+") and not ln.startswith("+++"):
            add += 1
        elif ln.startswith("-") and not ln.startswith("---"):
            rem += 1
    return add, rem


def _file_change(item: dict[str, Any]) -> dict[str, Any]:
    """Normalize Codex's authoritative completed FileChange item."""
    files = []
    for path, ch in (item.get("changes") or {}).items():
        if not isinstance(ch, dict):
            continue
        add, rem = _patch_delta(ch)
        change = ch.get("type")
        move_path = ch.get("move_path")
        row = {"path": path, "change": change,
               "added": add, "removed": rem}
        if move_path:
            row.update(path=move_path, previous_path=path, change="move")
        if change in ("update", "move"):
            row["diff"] = ch.get("unified_diff") or ""
        elif change in ("add", "delete"):
            row["content"] = ch.get("content") or ""
        files.append(row)
    return {
        "kind": "patch",
        "success": item.get("status") == "completed",
        "files": files,
    }


def _ev_context_compacted(p: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "compact"}


def _ev_task_started(p: dict[str, Any]) -> dict[str, Any]:
    # `turn_id` is codex's identity for the TURN this task is — the fact the
    # actor-assignment model is built on (core/childtask.py). A child rollout opens by
    # replaying the parent thread, so the task_started records BEFORE the child's
    # own bootstrap carry the PARENT's turn id, which is how a child learns the
    # turn it was spawned in (harness/impl/codex/stream.py). Absent on older rollouts:
    # "" then, and every consumer degrades to its pre-turn behaviour.
    return {"kind": "task_started", "at": p.get("started_at"),
            "turn": p.get("turn_id") or ""}


def _ev_task_complete(p: dict[str, Any]) -> dict[str, Any]:
    # …and the same turn id closing it, plus `last_agent_message`: codex's own
    # statement of what the turn ANSWERED. Kept because it is the FALLBACK result
    # text (harness/impl/codex/stream._ro_task_complete) for a run whose messages
    # carried no `phase` — a pre-phase rollout, or a tailer that joined mid-run
    # and never saw the message record. Never the primary: the `final_answer`
    # phase says which message IS the result, where this only repeats text.
    return {"kind": "task_complete", "at": p.get("completed_at"),
            "turn": p.get("turn_id") or "",
            "last": (p.get("last_agent_message") or "").strip()}


def _ev_thread_settings_applied(p: dict[str, Any]) -> dict[str, Any]:
    """codex's PICKER state: a `thread_settings_applied` fires on EVERY /model
    change (model or reasoning level) — so it is FRESHER than `turn_context`,
    which is written only per-TURN. Its `thread_settings.model` +
    `reasoning_effort` are the current model/effort even before the next turn, so
    the ctx/effort reads take the NEWEST of this and turn_context (else the header
    lagged behind picker changes — a `terra high` run reading a stale `sol high`
    from the last turn_context, docs/codex.md *token_count keeps three things*)."""
    ts = p.get("thread_settings") or {}
    return {"kind": "settings", "model": ts.get("model") or "",
            "effort": (ts.get("reasoning_effort") or "").strip()}


def _ev_item_completed(p: dict[str, Any]) -> dict[str, Any] | None:
    """codex's PLAN-mode plan: an `item_completed` whose `item.type == "Plan"`
    carries the full plan as markdown (`item.text`) with a stable id. This is
    the structured plan the dashboard renders as a plan card (docs/codex.md
    *Plan mode*) — the codex analog of Claude's ExitPlanMode plan text, and the
    signal the pending-plan read keys on. Every OTHER item_completed kind (codex
    also completes messages/reasoning as items) is already covered by its own
    event/response record, so only Plan produces a record here.

    The message items say so EXPLICITLY (`covered_item`) instead of returning
    None: None is reported as `ignored_unknown` — "a type I do not recognise" —
    which is not what we mean about a record whose content we already read from
    another register, and which makes a deliberate decision indistinguishable
    from real drift. Only the item types actually MEASURED as duplicates are
    named; every other type still falls through to None, so the first
    item_completed carrying something new (a todo, a web search) trips the wire
    instead of disappearing into this branch."""
    item = p.get("item") or {}
    if item.get("type") in COVERED_ITEMS:
        return {"kind": "covered_item"}
    if item.get("type") == "FileChange":
        return _file_change(item)
    if item.get("type") == "CommandExecution":
        process_id = item.get("process_id")
        if process_id is None:
            return None
        output = item.get("aggregated_output")
        if not isinstance(output, str):
            output = item.get("formatted_output")
        if not isinstance(output, str):
            stdout = item.get("stdout")
            if not isinstance(stdout, str):
                stdout = ""
            stderr = item.get("stderr")
            if not isinstance(stderr, str):
                stderr = ""
            output = stdout + stderr
        return {
            "kind": "command_completed",
            "process_id": str(process_id),
            "output": output,
            "exit": item.get("exit_code"),
            "item_id": item.get("id") or "",
        }
    if item.get("type") == "SubAgentActivity":
        actor_id = item.get("agent_thread_id") or ""
        agent_path = str(item.get("agent_path") or "")
        if not actor_id:
            return None
        return {
            "kind": "actor_activity",
            "activity": item.get("kind") or "",
            "actor_id": actor_id,
            "actor_path": agent_path,
            "call_id": item.get("id") or "",
            "turn": p.get("turn_id") or "",
            "at": (p.get("started_at_ms") or 0) / 1000 or None,
        }
    if item.get("type") != "Plan":
        return None
    text = (item.get("text") or "").strip()
    return {"kind": "plan", "text": text, "id": item.get("id") or ""} if text \
        else empty_record()


def _ev_turn_aborted(p: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "turn_aborted", "turn": p.get("turn_id") or ""}


def _ev_user_message(p: dict[str, Any]) -> dict[str, Any]:
    # Unwrap an INPUT wrapper here too so a `<task>` that also lands in the
    # event_msg register de-doubles with the response_item one to a single bubble.
    msg = strip_input_wrapper((p.get("message") or "").strip())
    return {"kind": "prompt", "text": msg} if msg else empty_record()


def _ev_agent_reasoning(p: dict[str, Any]) -> dict[str, Any]:
    txt = (p.get("text") or "").strip()
    return {"kind": "reasoning", "text": txt} if txt else empty_record()


def _ev_agent_message(p: dict[str, Any]) -> dict[str, Any]:
    msg = (p.get("message") or "").strip()
    return {"kind": "message", "text": msg,
            "phase": (p.get("phase") or "").strip()} if msg else empty_record()


def _ev_web_search_end(p: dict[str, Any]) -> dict[str, Any] | None:
    """codex's web SEARCH in the event_msg register — and on cli 0.146 the ONLY
    place a search appears at all: the measured child rollout (019fb363-4028…)
    carries five `web_search_end` events and ZERO `web_search_call`
    response_items, so without this handler a codex web search rendered nothing.

    Only a search that NAMES a query yields a record. The same event ALSO fires
    for the web tool's non-search actions (`action.type == "other"` — an
    open/fetch of a previously-found result), where `query` is "" and there is
    nothing to show; four of the five measured events are exactly that. Same
    guard as the response_item twin (items._rsp_web_search_call), which is why
    both can return the one `search` kind.

    If some codex build emits BOTH registers for one search, two records would
    reach the renderer for it; the RENDERER collapses an immediately-repeated
    query (harness/impl/codex/stream.py `_ro_search`) rather than the parser dropping
    one — a parser stays a faithful reader of what the file actually says."""
    q = ((p.get("query") or "").strip()
         or ((p.get("action") or {}).get("query") or "").strip())
    return {"kind": "search", "query": q} if q else None


EVENTS = {"token_count": _ev_token_count,
          "thread_goal_updated": _ev_thread_goal_updated,
          "thread_goal_cleared": _ev_thread_goal_cleared,
          "context_compacted": _ev_context_compacted,
          "task_started": _ev_task_started, "task_complete": _ev_task_complete,
          "thread_settings_applied": _ev_thread_settings_applied,
          "item_completed": _ev_item_completed,
          "turn_aborted": _ev_turn_aborted, "user_message": _ev_user_message,
          "agent_reasoning": _ev_agent_reasoning,
          "agent_message": _ev_agent_message,
          "web_search_end": _ev_web_search_end}
# Two event_msg types stay DELIBERATELY unparsed (they fall through to None like
# any unknown type, and so have no KINDS entry): `sub_agent_activity` (a
# `{kind:"interacted"}` ping about a child thread — the child has its own rollout
# and its own stream, so this would only duplicate) and
# `inter_agent_communication_metadata` (`{trigger_turn:true}` — pure plumbing).
# Both were measured in the real child rollout; noted here so the next reader
# knows they were considered rather than missed.
