# harness/impl/codex/canonical/rollout.py — codex ROLLOUT-record dispatch.
#
# The parse half of the codex stream's parse/paint split — the same shape as
# harness/impl/claude_code/canonical/transcript.py (docs/sessionapi.md). This module and its
# three register modules are the ONE owner of the codex rollout record shapes
# (styleguide single-owner table): the
# `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` event grammar — turn_context /
# event_msg / response_item / top-level discrimination, the exec-arguments
# decode, the patch-change line counts, the exec-output exit extraction, the
# synthetic-message vocabulary, and the cumulative total_token_usage field
# mapping (usage_split).
#
#   events.py      the `event_msg` register — codex's own digested UI stream.
#   items.py       the `response_item` register + the custom-tool argument
#                  grammar only that register speaks.
#   vocabulary.py  which text is codex machinery and which is a real turn.
#   HERE           path ownership, the TOP-LEVEL register, the kind dispatch,
#                  and a subagent rollout's replayed-parent prefix.
#
# PRESENTERS (a record's consumers — there may be MORE THAN ONE; the grep
# contract in tests/test_l1f_codex_rollout.py pins only that no presenter
# re-walks the raw grammar, not that a single one exists):
#
#   harness/impl/codex/stream.py Renderer.feed_rollout — the mirror's CAPPED,
#       styled paint (byte-identical to the pre-split renderer; the e2e
#       codex suite is the equivalence pin). It dispatches on a `kind`
#       TABLE and silently ignores every kind it has no handler for, so a
#       record added here for another presenter never changes the mirror.
#   a dashboard `conversation` provider (a later phase) — the codex run's
#       web bubbles, off the `chat`/`think` register below.
#
# There was a third — an uncapped drill-down timeline behind plugins.activity()
# — and it is gone with that fan-out: a codex run's web view is the mirror it
# already paints, scoped.
#
# TWO REGISTERS, deliberately not unified (docs/codex.md *Two registers*): a
# codex rollout says most things TWICE — once as an `event_msg` (codex's own
# digested UI stream) and once as a `response_item` (the model-API record the
# conversation is rebuilt from on resume). The MIRROR paints the event_msg
# register (`prompt`/`message`/`reasoning`); a CONVERSATION presenter reads the
# response_item register (`chat`/`think`), which is the complete, in-order,
# resume-restored one and the ONLY source of a post-abort / queued prompt.
# Giving the second register its own kinds is what keeps the mirror from
# painting every message and every think twice.
#
# parse(o) takes one DECODED rollout object and returns a typed record
# (None = nothing renderable — unknown types fall through silently, exactly
# as the pre-split renderer did; the grammar is VERSION-FRAGILE — verified
# drift across codex 0.95 → 0.144 — so an unknown type/payload.type must
# always be None, never an exception):
#   {"kind": "turn_context", "model": str, "effort": str}
#   {"kind": "usage", "usage": dict,      cumulative total_token_usage snapshot
#    "last": dict|None, "window": int|None}   last turn's usage + ctx window
#   {"kind": "patch", "success": bool,
#    "files": [{"path", "change", "added", "removed", "diff"?/"content"?}, …]}
#   {"kind": "compact"} | {"kind": "task_started", "at": …, "ts": …}
#   {"kind": "task_complete", "at": …, "ts": …} | {"kind": "turn_aborted"}
#   {"kind": "prompt" | "reasoning" | "message", "text": str}   (never empty)
#   {"kind": "search", "query": str}
#   {"kind": "exec", "cmd": str, "call_id": str, "ts": str|None}
#   {"kind": "tool", "name": str, "args": str, "call_id": str}   a NON-shell
#    tool call through the same `exec` custom tool (`tools.web__run({…})`)
#   {"kind": "exec_result", "exit": str|int|None, "output": str,
#    "call_id": str, "process_id": str|None, "running": bool, "ts": str|None}
#   {"kind": "stdin", "text": str, "call_id": str, "process_id": str}
#   {"kind": "command_completed", "process_id": str, "output": str,
#    "exit": int|None}
#   {"kind": "chat", "role": str, "text": str, "synthetic": bool}
#   {"kind": "think", "text": str}                      (never empty)
#   {"kind": "patch_call", "patch": str, "call_id": str}
#   {"kind": "ask", "call_id": str, "questions": [{"id", "header", "question",
#                                  "options": [{"label", "description"}]}]}
#   {"kind": "compact_boundary", "message": str, "replaced": int,
#    "window_id": …, "previous_window_id": …}
# parse_line(s) wraps json.loads: {"kind": "bad", "raw": s} for a complete
# line that isn't JSON. parse_line/parse are pure (no I/O, no state), and so is
# owns() (a filename/layout test — the codex twin of transcript.owns). The ONLY
# functions here that touch a file are the two SUBAGENT head-readers at the
# bottom (subagent_fork_epoch / subagent_body_offset): a subagent rollout's
# replayed-parent PREFIX is a fact about the file's shape, not about one record,
# so it cannot be answered from a parsed line — each is bounded and fails open.
import dataclasses
import os
from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ValidationError

from harness.impl.codex.canonical.events import CodexEventType, EVENTS, parse_event
from harness.impl.codex.canonical.items import CodexResponseType, RESPONSES, parse_response
from harness.impl.codex.canonical.records import (
    BadRecord,
    CompactBoundaryRecord,
    CompactedDocument,
    CompactedPayload,
    EventDocument,
    EmptyRecord,
    ExecRecord,
    ExecResultRecord,
    MessageRecord,
    ItemCompletedHeaderPayload,
    ItemCompletedType,
    InterAgentCommunicationMetadataDocument,
    PayloadHeaderDocument,
    ResponseDocument,
    RolloutDocument,
    RolloutHeader,
    RolloutInput,
    RolloutRecord,
    SessionMetaPayload,
    SessionMetaSource,
    TaskCompleteRecord,
    TaskStartedRecord,
    TurnContextPayload,
    TurnContextDocument,
    TurnContextRecord,
    WorldStatePayload,
    WorldStateDocument,
    WorldStateRecord,
)

# The canonical codex ROLLOUT path layout (docs/codex.md): a `rollout-*.jsonl`
# file under a `.../sessions/YYYY/MM/DD/` tree (`~/.codex/sessions/…` in
# production; the `sessions` ancestor is the stable, non-$HOME-pinned part a
# fixture reproduces). `owns()` recognises one by that FILENAME PREFIX + one
# ancestor dir — the single-owner codex path recogniser (docs/styleguide.md
# single-owner table), the codex twin of harness/impl/claude_code/canonical/transcript.owns.
# Deliberately a PURE filename/layout test: a rollout's records are the grammar
# above, but ownership must be answerable once per session per poll WITHOUT
# opening the file, and the `rollout-` stem is codex-specific (a Claude transcript
# is a bare `<uuid>.jsonl`, an agent sidecar `agent-<id>.jsonl`), so the two
# vocabularies cannot collide. The `sessions/` ancestor keeps a stray
# `rollout-*.jsonl` elsewhere from being claimed.


def owns(path: str) -> bool:
    """Is `path` a codex rollout this plugin SPEAKS — the `owns` provider behind
    plugins._first_path (the ownership gate on every path-keyed read fan-out) and
    plugins.owns_by / host_of (the dashboard's host attribution)? True for a
    `rollout-*.jsonl` under a `sessions/` tree, False for everything else — a
    Claude transcript above all (its parsers fail OPEN on a file they never fully
    read, so an ungated fan-out would hand a codex rollout to a Claude parser; the
    same reason claude_code grew `owns`). An empty path (codex sent no
    transcript_path in its SessionStart payload) is not ours — session_caps then
    keeps the session on the empty-path default rather than attributing it here."""
    if not path or not path.endswith(".jsonl"):
        return False
    if not os.path.basename(path).startswith("rollout-"):
        return False
    return "sessions" in os.path.normpath(path).split(os.sep)[:-1]


# --- the TOP-LEVEL register (neither event_msg nor response_item) ----------------

def _turn_context(turn_context_payload: TurnContextPayload) -> TurnContextRecord:
    p = turn_context_payload
    # `reasoning_effort` moved under collaboration_mode.settings in 0.14x; the
    # bare top-level `effort` is the older (and still emitted) spelling.
    settings_effort = p.collaboration_mode.settings.reasoning_effort if (
        p.collaboration_mode and p.collaboration_mode.settings
    ) else None
    effort = settings_effort or p.effort
    return TurnContextRecord(model=p.model, effort=effort)


def _top_compacted(compacted_payload: CompactedPayload) -> CompactBoundaryRecord:
    p = compacted_payload
    # The TOP-LEVEL compaction record (distinct from the event_msg
    # `context_compacted` notice the mirror paints as ⟳): it is the boundary
    # itself, and `message` is usually "" because the summary is encrypted.
    # `replacement_history` — the entire rewritten conversation — is
    # deliberately NOT carried, only its length: a record shape must not be a
    # megabyte.
    hist = p.replacement_history
    return CompactBoundaryRecord(
        message=p.message or "", replaced=len(hist) if hist is not None else 0,
        window_id=p.window_id, previous_window_id=p.previous_window_id,
    )


def _top_world_state(_world_state_payload: WorldStatePayload) -> WorldStateRecord:
    # A large periodic state snapshot (open files, shell sessions, todos).
    # Explicitly ignored: nothing in it is renderable.
    #
    # It returns a RECORD rather than None, and that is the whole point of this
    # function: `parse` has exactly two outcomes, a record or None, and None is
    # what the translator reports as `ignored_unknown` — "a type I do not
    # recognise". A deliberate ignore that reports itself that way is
    # indistinguishable from real drift, which is precisely what this table
    # existed to prevent. The kind produces no canonical events, so the verdict
    # is `ignored_nonsemantic` with this kind named in its reason: recognised,
    # and carrying nothing.
    return WorldStateRecord()


class CodexTopLevelType(StrEnum):
    TURN_CONTEXT = "turn_context"
    COMPACTED = "compacted"
    WORLD_STATE = "world_state"
    INTER_AGENT_COMMUNICATION_METADATA = "inter_agent_communication_metadata"


_TOP: Mapping[CodexTopLevelType, type[BaseModel]] = {
    CodexTopLevelType.TURN_CONTEXT: TurnContextDocument,
    CodexTopLevelType.COMPACTED: CompactedDocument,
    CodexTopLevelType.WORLD_STATE: WorldStateDocument,
    CodexTopLevelType.INTER_AGENT_COMMUNICATION_METADATA: (
        InterAgentCommunicationMetadataDocument
    ),
}

# Record kinds that carry the RECORD's `timestamp` as a separate `ts` string.
# Three families: the task lifecycle records whose OWN timestamp field is absent
# in many codex versions (task_started/task_complete), the exec pair — a codex
# exec record carries no duration of its own, so the standalone command block
# times itself from the exec's `ts` to its exec_result's `ts` (the elapsed on
# `■ finished · Ns`, harness/impl/codex/stream.py) — and an assistant `message`, whose
# clock a child's RESULT card needs: a `final_answer` message ENDS the task
# (harness/impl/codex/stream.py paints the ⇠ card there, ~100ms before task_complete),
# so without it the card's `· 23.0s` would be measured to `time.time()` and a
# rollout being replayed from disk would report the age of the file. `ts` is
# always the ISO record string, never folded into the numeric `at` a task
# duration subtracts.


def _stamp(rec: RolloutRecord | None, timestamp: str | None) -> RolloutRecord | None:
    if rec is None:
        return rec
    ts = timestamp
    # One isinstance branch per kind, rather than one check against their
    # union: dataclasses.replace's stub wants the concrete dataclass type,
    # not a Union, so this cannot collapse to one isinstance(rec, (A, B, …))
    # call the way a `kind` string comparison could.
    if isinstance(rec, TaskStartedRecord):
        return dataclasses.replace(rec, ts=ts)
    if isinstance(rec, TaskCompleteRecord):
        return dataclasses.replace(rec, ts=ts)
    if isinstance(rec, ExecRecord):
        return dataclasses.replace(rec, ts=ts)
    if isinstance(rec, ExecResultRecord):
        return dataclasses.replace(rec, ts=ts)
    if isinstance(rec, MessageRecord):
        return dataclasses.replace(rec, ts=ts)
    return rec


# The COMPLETE set of record kinds parse()/parse_line() can return — the ONE
# owner of the codex rollout KIND vocabulary (docs/styleguide.md single-owner
# table; docs/codex.md *Kind drift contract*). Hand-maintained rather than
# derived: the kind a handler returns is NOT its registry key
# (events._ev_user_message → "prompt", events._ev_context_compacted →
# "compact"), so it can't be read off the EVENTS/RESPONSES/_TOP tables. But a new
# or renamed kind can never drift past a renderer SILENTLY:
# tests/test_l1f_codex_rollout.py pins every kind here to be EITHER rendered
# (stream.Renderer._RO) OR explicitly ignored (stream.IGNORE_KINDS), and every
# rendered/ignored kind to be a real member here — so adding a parser kind fails
# the suite until someone decides render-vs-ignore. `bad` is parse_line's
# non-JSON record.
#
# Not a vocabulary of OURS for the enum sweep (TASKS.md item 4b): every member
# names a record SHAPE this translator recognises, the same role the `kind`/
# `type` Literal tags in records.py play — and those stay Literals for the
# same reason. A closed set of record shapes, not a verdict this codebase
# hands out.
KINDS = frozenset({
    "turn_context", "usage", "patch", "compact", "task_started",
    "task_complete", "turn_aborted", "prompt", "skill", "reasoning", "message",
    "search", "exec", "exec_result", "stdin", "command_completed", "chat", "think", "patch_call",
    "ask", "plan", "settings", "compact_boundary", "tool",
    "actor_activity", "collaboration_call", "task_list", "goal", "goal_tool",
    "tool_batch",
    "unmapped_tool", "bad",
    # The RECOGNISED-AND-CARRYING-NOTHING kinds. They exist so that a deliberate
    # ignore is a verdict of its own (`ignored_nonsemantic`, naming the kind)
    # rather than the `ignored_unknown` that real drift must stay alone in:
    # `world_state` is a state snapshot with nothing renderable in it,
    # `covered_item` an `item_completed` for content another register already
    # delivered (events.COVERED_ITEMS), and `empty` a record of a type we parse
    # whose text is absent (vocabulary.empty_record).
    "world_state", "covered_item", "empty",
})


def parse(o: Mapping[str, object]) -> RolloutRecord | None:
    """Compatibility boundary for callers that already decoded a line."""
    return parse_line(RolloutInput(root=o).model_dump_json())


def parse_line(s: str) -> RolloutRecord | None:
    """One rollout JSONL line -> a typed record; BadRecord(raw=s) when the
    line isn't JSON at all (the stream keeps its own json.loads so its
    malformed-line audit contract stays where it was)."""
    try:
        header = RolloutHeader.model_validate_json(s)
    except ValidationError:
        return BadRecord(raw=s)
    if header.type == "event_msg":
        payload_type = PayloadHeaderDocument.model_validate_json(s).payload.type
        try:
            event_type = CodexEventType(payload_type or "")
        except ValueError:
            return None
        if event_type not in EVENTS:
            return None
        if event_type is CodexEventType.ITEM_COMPLETED:
            item_header = RolloutDocument[ItemCompletedHeaderPayload].model_validate_json(s).payload
            try:
                ItemCompletedType((item_header.item.type if item_header.item else None) or "")
            except ValueError:
                return None
        event_document = EventDocument.model_validate_json(s)
        return _stamp(parse_event(event_document.payload), event_document.timestamp)
    if header.type == "response_item":
        payload_type = PayloadHeaderDocument.model_validate_json(s).payload.type
        try:
            response_type = CodexResponseType(payload_type or "")
        except ValueError:
            return None
        if response_type not in RESPONSES:
            return None
        response_document = ResponseDocument.model_validate_json(s)
        return _stamp(parse_response(response_document.payload), response_document.timestamp)
    try:
        top_type = CodexTopLevelType(header.type or "")
    except ValueError:
        return None
    if top_type not in _TOP:
        return None
    if top_type is CodexTopLevelType.TURN_CONTEXT:
        return _turn_context(TurnContextDocument.model_validate_json(s).payload)
    if top_type is CodexTopLevelType.COMPACTED:
        return _top_compacted(CompactedDocument.model_validate_json(s).payload)
    if top_type is CodexTopLevelType.INTER_AGENT_COMMUNICATION_METADATA:
        InterAgentCommunicationMetadataDocument.model_validate_json(s)
        return EmptyRecord()
    return _top_world_state(WorldStateDocument.model_validate_json(s).payload)


# --- subagent rollout: skip the replayed-parent PREFIX ---------------------------
# A codex SUBAGENT run (cli 0.146+, `collaboration.spawn_agent`) writes its OWN
# rollout that OPENS with a burst replaying the PARENT thread's history as of the
# fork — two `session_meta` records (the child's `thread_source=="subagent"`, then
# the parent's), the parent's replayed turn(s), then the child's own work. Left
# in, that prefix DOUBLES the parent's prose/exec into the subagent's scoped
# mirror + bubbles (docs/codex.md *Sidecar → subagent parity*). The reliable
# boundary (verified on cli 0.146): a parent's replayed `task_started` carries a
# `started_at` from BEFORE the fork, while the CHILD's OWN bootstrap `task_started`
# carries `started_at >= the fork` (the child `session_meta`'s own timestamp).
# The bootstrap task_started ITSELF is the child's first own record — it is the
# child's turn/assignment start, and classifying it as replay eats the canonical
# `actor.assignment_started` (measured, session 01a00a31-3a90: the started card
# never painted). Everything before it is the replayed prefix.

def subagent_fork_epoch(path: str) -> int | None:
    """int(the child `session_meta` timestamp) for a SUBAGENT rollout, else None
    (a normal rollout / unreadable head). A subagent rollout's first session_meta
    has `thread_source == "subagent"` (or a `source.subagent.thread_spawn`)."""
    try:
        with open(path, encoding="utf-8") as fh:
            line = fh.readline()
        header = RolloutHeader.model_validate_json(line)
        if header.type != "session_meta":
            return None
        rollout_document = RolloutDocument[SessionMetaPayload].model_validate_json(line)
        metadata = rollout_document.payload
        source = metadata.source if isinstance(metadata.source, SessionMetaSource) else None
        spawn = source.subagent.thread_spawn if source and source.subagent else None
        if metadata.thread_source != "subagent" and spawn is None:
            return None
        ts = metadata.timestamp or rollout_document.timestamp or ""
        return int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp())
    except Exception:
        return None


def is_child_bootstrap(rec: RolloutRecord | None, fork_epoch: int | None) -> bool:
    """True for the child's OWN bootstrap `task_started` (`at >= fork_epoch`) —
    the FIRST child-own record; the replayed-parent prefix is everything before
    it. `fork_epoch` None => never."""
    if fork_epoch is None or not isinstance(rec, TaskStartedRecord):
        return False
    at = rec.at or 0
    return isinstance(at, (int, float)) and at >= fork_epoch


# How far into a subagent rollout's HEAD a head-reader will read before giving
# up. The replayed-parent prefix is short (13 records in the measured run), but a
# fork of a long conversation replays more, and this runs in a tailer's startup
# path — so both a line and a byte ceiling, generous enough that only a
# pathological file hits one, and hitting one just means no brief.
BRIEF_MAX_LINES = 500
BRIEF_MAX_B = 4 << 20


def subagent_body_offset(path: str) -> int:
    """Byte offset of the first CHILD-OWN record in a subagent rollout — the
    child's bootstrap task_started itself (its turn/assignment start), skipping
    the replayed-parent prefix before it. 0 for a normal rollout OR when the
    boundary isn't found (fail-open: show everything, never an empty scope)."""
    fork_epoch = subagent_fork_epoch(path)
    if fork_epoch is None:
        return 0
    try:
        off = 0
        with open(path, "rb") as fh:
            for raw in fh:
                try:
                    rec = parse_line(raw.decode("utf-8", "replace"))
                except Exception:
                    off += len(raw)
                    continue
                if is_child_bootstrap(rec, fork_epoch):
                    return off          # the child's turns begin HERE
                off += len(raw)
    except Exception:
        pass
    return 0
