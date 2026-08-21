# harness/impl/codex/canonical/events.py — the codex rollout's `event_msg` register.
#
# codex's OWN digested UI stream: the half of the rollout a mirror paints
# (`prompt`/`message`/`reasoning`). The response_item twin — the model-API record
# the conversation is rebuilt from on resume — lives in items.py; the two are
# deliberately not unified (docs/codex.md *Two registers*), which is what keeps a
# mirror from painting every message and every think twice.
#
# One parser per `payload.type`, registered in EVENTS at the bottom; rollout.py
# dispatches through it. Each parser validates the raw payload against the
# DECLARED shape in records.py (`extra="forbid"`) before reading a single field
# of it: a missing/mistyped/EXTRA field raises `pydantic.ValidationError`, which
# propagates out of translation and becomes the `translation_failed` verdict
# (the owner's decision, TASKS.md 2026-08-21). An unrecognised `payload.type`
# never reaches a parser at all — rollout.py's EVENTS.get() answers None for it,
# which is `ignored_unknown`, not a failure: the grammar is VERSION-FRAGILE
# (verified drift across codex 0.95 -> 0.144), and only a KNOWN type whose shape
# no longer matches is drift worth stopping translation for.
from pydantic import JsonValue

from domain.ids import CallId, ShellNativeId
from harness.impl.codex.canonical.records import (
    AgentMessagePayload,
    AgentReasoningPayload,
    CommandExecutionItem,
    CoveredItem,
    EmptyPayload,
    FileChangeEntry,
    FileChangeItem,
    ITEM_COMPLETED_ITEMS,
    ItemCompletedPayload,
    SubAgentActivityItem,
    TaskCompletePayload,
    TaskStartedPayload,
    ThreadGoalUpdatedPayload,
    ThreadSettingsAppliedPayload,
    TokenCountPayload,
    TurnAbortedPayload,
    UserMessagePayload,
    WebSearchEndPayload,
)
from harness.impl.codex.canonical.records import (
    ActorActivityRecord,
    CommandCompletedRecord,
    CompactRecord,
    CoveredItemRecord,
    GoalRecord,
    MessageRecord,
    PatchFile,
    PatchRecord,
    PlanRecord,
    PromptRecord,
    RateLimitsBlock,
    ReasoningRecord,
    RolloutRecord,
    SearchRecord,
    SettingsRecord,
    TaskCompleteRecord,
    TaskStartedRecord,
    TurnAbortedRecord,
    UsageRecord,
)
from harness.impl.codex.canonical.vocabulary import empty_record, strip_input_wrapper

# The PHASE codex stamps on an assistant message. `final_answer` is the one that
# matters: it is codex SAYING this message is the turn's answer, which is what
# tells a child's stream that this message is its RESULT rather than one more
# intermediate note (`commentary` is the other measured value). Absent on older
# rollouts: "" then, and the result falls back to the pre-phase inference.
PHASE_FINAL = "final_answer"

# The `item_completed` item types whose content reaches us through ANOTHER
# register and is therefore read there, not here (measured against codex-cli
# 0.147.0: `UserMessage` and `AgentMessage` items are completed for the same prose
# the `response_item/message` records already carry, `Reasoning` for the think the
# `reasoning` records carry — and `_ev_user_message` / `_ev_agent_message` /
# `_ev_agent_reasoning` already de-double the event_msg spellings of the same).
# A CLOSED list on purpose — records.CoveredItem's Literal names the three,
# and records.ITEM_COMPLETED_ITEMS is the one dispatch table that reads it.


def _ev_token_count(raw: dict[str, JsonValue]) -> UsageRecord | None:
    # Cumulative usage snapshot (info is null on rate-limit-only events).
    # `last_token_usage` + `model_context_window` ride along: the CUMULATIVE
    # total never resets across a compaction, so only the last turn's total
    # over the window measures ctx saturation.
    p = TokenCountPayload.model_validate(raw)
    if p.info is None or p.info.total_token_usage is None:
        return None
    return UsageRecord(
        usage=p.info.total_token_usage,
        last=p.info.last_token_usage,
        window=p.info.model_context_window,
    )


def _ev_thread_goal_updated(raw: dict[str, JsonValue]) -> GoalRecord | None:
    p = ThreadGoalUpdatedPayload.model_validate(raw)
    if p.goal is None:
        return None
    return GoalRecord(objective=p.goal.objective, status=p.goal.status, reason=p.goal.reason)


def _ev_thread_goal_cleared(raw: dict[str, JsonValue]) -> GoalRecord:
    EmptyPayload.model_validate(raw)
    return GoalRecord(objective=None, status="cleared", reason=None)


def rate_limits(raw: dict[str, JsonValue]) -> RateLimitsBlock | None:
    """A `token_count` payload's `rate_limits` block, already normalized to
    codex's windows shape by records.RateLimitsBlock — or None when the event
    carries none (the field is NULLABLE) or names no window.

    Deliberately NOT part of the `usage` record and NOT in the `EVENTS` table:
    codex emits a token_count with `info: null` on a RATE-LIMIT-ONLY event, which
    _ev_token_count drops entirely because it has no total_token_usage to report.
    The limits ride a different, independently-nullable field of the same event,
    so they are read on their own.

    Measured shape (rollout 019fb363, 2026-07-30): snake_case `used_percent` /
    `window_minutes` / `resets_at` (epoch seconds) / `plan_type`, `secondary`
    null on a plan with one window."""
    p = TokenCountPayload.model_validate(raw)
    rl = p.rate_limits
    if rl is None or (rl.primary is None and rl.secondary is None):
        return None
    return rl


def _patch_delta(file_change_entry: FileChangeEntry) -> tuple[int, int]:
    """(added, removed) line counts for one patch_apply_end change entry."""
    if file_change_entry.type == "add":
        return len((file_change_entry.content or "").splitlines()), 0
    if file_change_entry.type == "delete":
        return 0, len((file_change_entry.content or "").splitlines())
    added = removed = 0
    for line in (file_change_entry.unified_diff or "").splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return added, removed


def _file_change(file_change_item: FileChangeItem) -> PatchRecord:
    """Normalize Codex's authoritative completed FileChange item."""
    files = []
    for path, entry in (file_change_item.changes or {}).items():
        added, removed = _patch_delta(entry)
        change = entry.type
        final_path, previous_path, final_change = path, None, change
        if entry.move_path:
            final_path, previous_path, final_change = entry.move_path, path, "move"
        diff = entry.unified_diff or "" if final_change in ("update", "move") else None
        content = entry.content or "" if final_change in ("add", "delete") else None
        files.append(PatchFile(
            path=final_path, change=final_change, added=added, removed=removed,
            previous_path=previous_path, diff=diff, content=content,
        ))
    return PatchRecord(success=file_change_item.status == "completed", files=tuple(files))


def _ev_context_compacted(raw: dict[str, JsonValue]) -> CompactRecord:
    EmptyPayload.model_validate(raw)
    return CompactRecord()


def _ev_task_started(raw: dict[str, JsonValue]) -> TaskStartedRecord:
    # `turn_id` is codex's identity for the TURN this task is — the fact the
    # actor-assignment model is built on (core/childtask.py). A child rollout opens by
    # replaying the parent thread, so the task_started records BEFORE the child's
    # own bootstrap carry the PARENT's turn id, which is how a child learns the
    # turn it was spawned in. Absent on older rollouts: "" then, and every
    # consumer degrades to its pre-turn behaviour.
    p = TaskStartedPayload.model_validate(raw)
    return TaskStartedRecord(at=p.started_at, turn=p.turn_id or "")


def _ev_task_complete(raw: dict[str, JsonValue]) -> TaskCompleteRecord:
    # …and the same turn id closing it, plus `last_agent_message`: codex's own
    # statement of what the turn ANSWERED. Kept because it is the FALLBACK result
    # text for a run whose messages carried no `phase` — a pre-phase rollout, or
    # a tailer that joined mid-run and never saw the message record. Never the
    # primary: the `final_answer` phase says which message IS the result, where
    # this only repeats text.
    p = TaskCompletePayload.model_validate(raw)
    return TaskCompleteRecord(at=p.completed_at, turn=p.turn_id or "",
                               last=(p.last_agent_message or "").strip())


def _ev_thread_settings_applied(raw: dict[str, JsonValue]) -> SettingsRecord:
    """codex's PICKER state: a `thread_settings_applied` fires on EVERY /model
    change (model or reasoning level) — so it is FRESHER than `turn_context`,
    which is written only per-TURN. Its `thread_settings.model` +
    `reasoning_effort` are the current model/effort even before the next turn, so
    the ctx/effort reads take the NEWEST of this and turn_context (else the header
    lagged behind picker changes — a `terra high` run reading a stale `sol high`
    from the last turn_context, docs/codex.md *token_count keeps three things*)."""
    p = ThreadSettingsAppliedPayload.model_validate(raw)
    ts = p.thread_settings
    return SettingsRecord(model=(ts.model if ts else None) or "",
                           effort=((ts.reasoning_effort if ts else None) or "").strip())


def _ev_item_completed(raw: dict[str, JsonValue]) -> RolloutRecord | None:
    """codex's PLAN-mode plan: an `item_completed` whose `item.type == "Plan"`
    carries the full plan as markdown (`item.text`) with a stable id. This is
    the structured plan the dashboard renders as a plan card — the codex analog
    of Claude's ExitPlanMode plan text, and the signal the pending-plan read
    keys on. Every OTHER item_completed kind (codex also completes
    messages/reasoning as items) is already covered by its own event/response
    record, so only Plan produces a record here.

    The message items say so EXPLICITLY (`covered_item`) instead of returning
    None: None is reported as `ignored_unknown` — "a type I do not recognise" —
    which is not what we mean about a record whose content we already read from
    another register, and which makes a deliberate decision indistinguishable
    from real drift. Only the item types actually MEASURED as duplicates are
    named; every other type still falls through to None, so the first
    item_completed carrying something new (a todo, a web search) trips this
    check instead of disappearing into this branch."""
    p = ItemCompletedPayload.model_validate(raw)
    raw_item = p.item
    item_type = raw_item.get("type") if raw_item else None
    item_model = ITEM_COMPLETED_ITEMS.get(item_type) if isinstance(item_type, str) else None
    if item_model is None:
        return None
    item = item_model.model_validate(raw_item)
    if isinstance(item, CoveredItem):
        return CoveredItemRecord()
    if isinstance(item, FileChangeItem):
        return _file_change(item)
    if isinstance(item, CommandExecutionItem):
        return _command_execution(item)
    if isinstance(item, SubAgentActivityItem):
        return _subagent_activity(item, p)
    # item: PlanItem — the only member left in records.ItemCompletedItem.
    text = (item.text or "").strip()
    return PlanRecord(text=text, id=item.id or "") if text else empty_record()


def _command_execution(command_execution_item: CommandExecutionItem) -> CommandCompletedRecord | None:
    if command_execution_item.process_id is None:
        return None
    output = command_execution_item.aggregated_output
    if output is None:
        output = command_execution_item.formatted_output
    if output is None:
        output = (command_execution_item.stdout or "") + (command_execution_item.stderr or "")
    return CommandCompletedRecord(
        process_id=ShellNativeId(str(command_execution_item.process_id)), output=output,
        exit=command_execution_item.exit_code, item_id=command_execution_item.id or "",
    )


def _subagent_activity(
    sub_agent_activity_item: SubAgentActivityItem, item_completed_payload: ItemCompletedPayload,
) -> ActorActivityRecord | None:
    actor_id = sub_agent_activity_item.agent_thread_id
    if not actor_id:
        return None
    at = (item_completed_payload.started_at_ms or 0) / 1000 or None
    return ActorActivityRecord(
        activity=sub_agent_activity_item.kind or "", actor_id=actor_id,
        actor_path=sub_agent_activity_item.agent_path or "",
        call_id=CallId(sub_agent_activity_item.id or ""), turn=item_completed_payload.turn_id or "", at=at,
    )


def _ev_turn_aborted(raw: dict[str, JsonValue]) -> TurnAbortedRecord:
    p = TurnAbortedPayload.model_validate(raw)
    return TurnAbortedRecord(turn=p.turn_id or "")


def _ev_user_message(raw: dict[str, JsonValue]) -> RolloutRecord:
    # Unwrap an INPUT wrapper here too so a `<task>` that also lands in the
    # event_msg register de-doubles with the response_item one to a single bubble.
    p = UserMessagePayload.model_validate(raw)
    msg = strip_input_wrapper((p.message or "").strip())
    return PromptRecord(text=msg) if msg else empty_record()


def _ev_agent_reasoning(raw: dict[str, JsonValue]) -> RolloutRecord:
    p = AgentReasoningPayload.model_validate(raw)
    txt = (p.text or "").strip()
    return ReasoningRecord(text=txt) if txt else empty_record()


def _ev_agent_message(raw: dict[str, JsonValue]) -> RolloutRecord:
    p = AgentMessagePayload.model_validate(raw)
    msg = (p.message or "").strip()
    return MessageRecord(text=msg, phase=(p.phase or "").strip()) if msg else empty_record()


def _ev_web_search_end(raw: dict[str, JsonValue]) -> SearchRecord | None:
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
    reach the renderer for it; a presenter collapses an immediately-repeated
    query rather than the parser dropping one — a parser stays a faithful
    reader of what the file actually says."""
    p = WebSearchEndPayload.model_validate(raw)
    q = (p.query or "").strip() or ((p.action.query if p.action else None) or "").strip()
    return SearchRecord(query=q) if q else None


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
