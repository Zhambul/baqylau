# Codex capabilities coverage

This document is the living coverage record for Codex. Update the relevant row
in the same change that adds, removes, or changes Codex support.

The purpose is not to mirror every Codex transport record into the canonical
timeline. The purpose is to make an explicit decision for every known Codex
capability:

- translate user-observable semantics into the shared canonical vocabulary;
- keep transport or duplicate evidence raw by design;
- place application and connection state outside the session transcript; or
- mark the capability unsupported until its complete behavior is implemented.

Codex-specific discovery, parsing, correlation, and vocabulary mapping must
remain in `plugins/codex`. The domain, projections, HTTP API, SSE transport,
dashboard, and terminal frontend must never inspect Codex record types or tool
names.

## Active implementation scope

The current implementation priority is deliberately narrow. Cover these
capabilities next:

| Capability | Required result |
|---|---|
| Interactive process input | **Covered.** `write_stdin` input, returned output, and completion remain attached to the shell operation that owns the live process. |
| Goals | **Covered.** Native goal notifications produce `GoalChanged`; goal-management tool calls do not create duplicate operations. |
| Permissions and approvals | Command, file-change, general permission, and MCP elicitation requests and responses use the shared attention lifecycle. |
| Incoming actor messages | Receiving-side plaintext completes the originating `ActorMessageSent` and appears in the correct actor transcript exactly once. |
| Review mode | Entering and leaving review mode produce the smallest shared session-mode facts required by the existing frontends. |
| Sleep and background delay | Waiting is correlated with the operation or actor that is waiting and never becomes a decorative generic operation. |
| Skills invoked by Codex | Actual skill invocation uses a shared skill operation lifecycle; skill inventory remains application state. |
| Session title changes | Native title changes produce `SessionTitleChanged` with the correct origin. |

All other capabilities that are not already covered are **low priority** for
now. They remain in this document so native behavior is not forgotten, raw
evidence is not discarded, and later work starts from an explicit semantic
decision. Low priority does not permit a generic fallback: an unmapped
user-observable action must still fail translation clearly.

Priority and coverage are independent. An active item remains **Partial** or
**Missing** until it satisfies the complete coverage rule below.

## Status vocabulary

| Status | Meaning |
|---|---|
| **Covered** | Raw evidence is audited, canonical semantics are complete, shared consumers use them, and focused tests prove the behavior. |
| **Partial** | Some semantics exist, but correlation, lifecycle, content, a consumer, or focused verification is incomplete. |
| **Missing** | The native capability exists but has no accepted canonical implementation. |
| **Raw only** | The record is intentionally retained in raw audit and produces no canonical event. |
| **Application state** | The capability belongs to an explicit application model rather than the session transcript. |
| **Out of scope** | Baqylau deliberately does not expose the capability. The reason must remain in the row. |

A row may be changed to **Covered** only when all of these statements are true:

- the authoritative native source is identified;
- exact raw input is committed to audit before translation;
- translation produces the accepted canonical events or an explicit raw-only
  decision;
- failures are recorded as translation failures rather than generic operations;
- dashboard and terminal behavior comes from shared projections where the
  capability is visible;
- copy and expansion content is resolved from stored event-time content;
- focused parser, translator, projection, API/SSE, and presenter tests exist as
  applicable;
- verification does not launch Kitty panes or spend model tokens.

## Translation rules

- Native structured records are authoritative. Do not infer structured tool
  activity by parsing JavaScript when Codex emits a native item for it.
- One semantic action has one canonical lifecycle even if Codex writes it into
  several native registers. Duplicate registers remain raw with a documented
  decision.
- Unknown tools and unknown semantic variants are translation failures. There
  is no generic tool or generic operation presentation fallback.
- Tool-name normalization belongs to the Codex adapter. Frontends receive only
  shared categories, names, content, outcomes, and relationships.
- Large binary values are stored as resources and referenced by canonical
  content. They are not embedded repeatedly in events or SSE payloads.
- Event-time command, output, content, and diffs are persisted. A later click
  must not inspect the current filesystem to reconstruct an earlier action.
- Raw and canonical evidence are both audited. A raw-only decision is not a
  license to discard the native record.

## Transcript item coverage

This matrix follows the structured item vocabulary exposed by the installed
Codex app-server protocol. A protocol item may be sourced from rollout records,
hooks, the app server, or another explicitly named Codex source.

| Capability | Authoritative native source | Status | Canonical decision | Work required before covered |
|---|---|---|---|---|
| User message | `userMessage` and the non-duplicate message register | **Covered** | `MessageCreated(role="user")` | Keep the duplicate-register decision pinned by tests. |
| Agent message | `agentMessage` | **Partial** | `MessageCreated(role="assistant")`; receiving-side actor messages also complete `ActorMessageSent.content` | Correlate receiving-side actor messages without duplicating ordinary assistant messages. |
| Reasoning | `reasoning` | **Partial** | `ReasoningCreated` when plaintext or a summary exists | Explicitly classify encrypted reasoning without a summary as raw only and test both paths. |
| Command execution | `commandExecution` plus its structured continuation/result records | **Partial** | `OperationStarted`, `OperationProgressed`, `OperationFinished` | Make the native command item authoritative and preserve background-process identity through waits and input. |
| File change | `fileChange` or the authoritative resolved patch record | **Partial** | File operation lifecycle plus `FileAccessed` with stored diff/content | Select and test one authoritative source for each Codex protocol shape; classify duplicate file records as raw only. |
| Web search | `webSearch` | **Partial** | Shared search operation | Cover every supported action and preserve result content without Codex-specific rendering. |
| MCP tool call | `mcpToolCall` | **Missing** | Mapped shared operation lifecycle | Translate structured arguments, progress, result, failure, and duration through a strict Codex-owned vocabulary map. |
| Dynamic tool call | `dynamicToolCall` | **Missing** | Mapped shared operation lifecycle | Consume the structured record and remove JavaScript-source inference as the semantic source. |
| Image generation | `imageGeneration` | **Partial** | Shared media operation with a stored resource reference | Consume native completion/failure, persist the generated resource, and avoid canonical base64 payloads. |
| Image view | `imageView` | **Partial** | Shared file/media-read operation | Consume the native item and reference the viewed resource consistently. |
| Plan | `plan` and plan-tool lifecycle | **Partial** | Plan attention or shared task facts according to the native action | Separate a proposed plan awaiting attention from an update to task state; cover resolution. |
| Context compaction | `contextCompaction`, compaction boundary, and hooks | **Partial** | `CompactionStarted`, `CompactionFinished` | Correlate start and finish and document which duplicate notices remain raw. |
| Hook prompt | `hookPrompt` | **Missing** | `MessageCreated(role="system", phase="synthetic")` when user-observable | Preserve content and apply the shared web-visible, lead-terminal-hidden system-message policy. |
| Permission request | `requestPermissions` | **Missing** | `AttentionRequested(type="permission")`, then `AttentionResolved` | Cover command, file, and tool permissions with stable attention identity. |
| Skill invocation | `skill` | **Missing** | Shared skill operation lifecycle | Define the semantic fields, content references, outcome, and strict name mapping. |
| Sleep or delay | `sleep` | **Missing** | Progress/state on the operation or actor that is actually waiting | Define correlation; do not create a decorative standalone block for internal delay. |
| Subagent activity | `subAgentActivity` | **Partial** | Actor lifecycle, assignment lifecycle, and actor messages | Consume every supported native activity state and select one authoritative source when collaboration records duplicate it. |
| Collaboration tool call | `collabAgentToolCall` | **Partial** | Actor lifecycle, assignment lifecycle, and actor messages | Prefer the structured item over backward scanning of function calls and preserve available prompt/model/effort fields. |
| Enter review mode | `enteredReviewMode` | **Missing** | Explicit shared session-mode fact if it changes user-observable state | Add the smallest session-mode abstraction needed by both frontends. |
| Exit review mode | `exitedReviewMode` | **Missing** | Explicit shared session-mode fact if it changes user-observable state | Pair it with the entered state and verify ordering. |
| System error | `systemError` | **Missing** | Canonical system/error fact and operational audit record | Define user-visible content, severity, ownership, and terminal/dashboard policy. |

## Exposed tool and action coverage

Tool availability is dynamic because Codex can gain tools through plugins and
MCP. This table records the known semantic families, not a permanent allowlist
of transport names. Any newly observed name must be added here or mapped to an
existing family explicitly.

| Tool or action family | Status | Canonical decision | Work required before covered |
|---|---|---|---|
| Shell execution | **Partial** | Shared shell operation lifecycle | Use authoritative structured execution state and complete background correlation. |
| Apply patch and file editing | **Partial** | Shared file-edit operation plus immutable `FileAccessed` facts | Pin diffs, content, line metadata, outcome, copy content, and duplicate-source rules for every supported record shape. |
| Process input through `write_stdin` | **Covered** | `OperationInputProvided` records non-empty input; returned output uses `OperationProgressed`; native command completion uses `OperationFinished`, all with the original shell `OperationId` | Empty polls remain raw-audited and explicitly nonsemantic. The shared operation projection means terminal and dashboard retain one unchanged command block. |
| Yielded execution wait | **Missing** | Continuation of the operation represented by the yielded cell | Map `wait` separately from actor waiting and finish the original operation exactly once. |
| Web search | **Partial** | Search operation | Normalize the native variants through the shared vocabulary. |
| Web fetch, open, click, find, and screenshot | **Partial** | Network, search, or media operation according to semantics | Cover native extension/browser records and store usable output/content references. |
| Weather, finance, sports, and time lookup | **Partial** | Shared search or network operations with intuitive names | Pin the mapping and result presentation without transport vocabulary in frontends. |
| Image query | **Partial** | Shared search/media operation | Verify structured results and copy content. |
| View image | **Partial** | Shared file/media-read operation | Complete native item handling and resource identity. |
| Generate or edit image | **Partial** | Shared media operation | Persist resources and consume native completion/failure. |
| Update plan | **Covered** | `TaskListChanged` and `TaskChanged` snapshot the native ordered checklist; plan-mode proposals remain attention requests | Stable identity is actor-scoped while displayed labels remain native short references. The shared projection drives both frontends. |
| Create, read, and update goal | **Covered** | `GoalChanged` for native state changes | Native notifications are authoritative; goal-management calls are raw-audited but never duplicated as decorative operations. |
| Request user input | **Partial** | `AttentionRequested` and `AttentionResolved` | Translate the answer/result record so attention never remains pending after a response. |
| Read MCP resources | **Missing** | Shared file-read, network, or resource operation selected by resource semantics | Cover `read_mcp_resource` through native MCP records. |
| List MCP resources | **Missing** | Shared resource-discovery operation when user-visible | Cover `list_mcp_resources`; raw-only is acceptable only when it is internal discovery. |
| List MCP resource templates | **Missing** | Shared resource-discovery operation when user-visible | Cover `list_mcp_resource_templates`; distinguish internal discovery from transcript activity. |
| Node/browser runtime evaluation | **Missing** | Strictly mapped browser, network, shell, or workspace operation | Consume native MCP records for runtime evaluation and reject unmapped semantic actions. |
| Runtime module-path changes and reset | **Missing** | Application state or explicit workspace operation according to effect | Decide visibility and audit the state change; do not expose raw MCP names to frontends. |
| Plugin-provided tools | **Missing** | Existing shared operation categories with plugin/Codex-owned mappings | Add an extension registration contract and hard-fail tools without a semantic mapping. |

## Actor capability coverage

All shared names use `actor`. No canonical event, API field, projection, or
frontend label may use `child` or `peer` as the semantic noun. `child` remains
valid only as an `ActorRole` value.

| Codex capability | Status | Canonical events | Work required before covered |
|---|---|---|---|
| Start actor with `spawn_agent` | **Partial** | `ActorStarted`, `ActorAssignmentStarted` | Make the structured collaboration item authoritative and preserve the real actor name and assignment brief when available. |
| Send actor message with `send_message` | **Partial** | `ActorMessageSent` | Correlate receiving-side plaintext content and prove exactly-once ordering. |
| Start or resume assignment with `followup_task` | **Partial** | `ActorAssignmentStarted`; `ActorMessageSent` only if Codex also sends a distinct message | Represent repeated assignments without inventing a new actor and preserve the brief. |
| Wait for actors with `wait_agent` | **Raw only** | None | Keep it raw unless it causes a user-observable actor or assignment state change. It must never render as “operation operation.” |
| Interrupt actor with `interrupt_agent` | **Partial** | `ActorAssignmentFinished`, then `ActorFinished` only when the actor actually ends | Correlate interruption outcome and reason without prematurely finishing a reusable actor. |
| List actors with `list_agents` | **Raw only** | None | Actor state already comes from lifecycle facts. Do not render a bookkeeping operation. |
| Receive actor message | **Partial** | Complete the originating `ActorMessageSent.content`; render the receiving message in the correct actor transcript | Resolve sender identity, ordering, and deduplication from structured receiving-side records. |
| Finish assignment | **Partial** | `ActorAssignmentFinished` | Preserve outcome, result, and reason at the native completion time, independently of when the lead actor processes it. |
| Finish actor | **Partial** | `ActorFinished` | Emit only when the actor itself ends; do not infer it from one assignment or a lead-agent message. |

## Attention and control coverage

| Capability | Native source | Status | Canonical or application decision | Work required before covered |
|---|---|---|---|---|
| Question | Request-user-input item and result | **Partial** | `AttentionRequested`, `AttentionResolved` | Translate the result and preserve answers, feedback, edited state, and outcome. |
| Command approval | App-server approval request and response | **Missing** | Shared attention lifecycle | Add stable operation correlation and web control. |
| File-change approval | App-server approval request and response | **Missing** | Shared attention lifecycle | Add stable file-operation correlation and web control. |
| General permission approval | App-server permission request and response | **Missing** | Shared attention lifecycle | Preserve requested permissions and the final decision. |
| MCP elicitation | MCP elicitation request and response | **Missing** | Shared question/permission attention lifecycle | Map form fields and response without exposing MCP transport shapes to frontends. |
| Plan approval and feedback | Plan request and response | **Partial** | Shared plan attention lifecycle | Translate approve, reject, discuss, and change-request outcomes. |
| Interrupt turn | Turn-aborted record and control result | **Partial** | `TurnAborted` plus affected operation/assignment outcomes | Verify exact ordering and distinguish a turn interruption from actor termination. |

## Session and runtime lifecycle coverage

| Capability | Status | Canonical or application decision | Work required before covered |
|---|---|---|---|
| Start session | **Covered** | `SessionStarted`, lead `ActorStarted` | Keep hook and rollout startup facts idempotent. |
| Resume session | **Partial** | `SessionStarted.resumed_from` | Populate lineage from the authoritative native source. |
| Fork session | **Missing** | New `SessionStarted` with explicit ancestry | Distinguish a user fork from a spawned actor rollout. |
| Finish or close session | **Partial** | `SessionFinished`, terminal cleanup | Cover every native close path without duplicate finishes. |
| Archive session | **Missing** | Application/session catalog state unless transcript semantics require more | Add only when Baqylau exposes archive control. |
| Unarchive session | **Missing** | Application/session catalog state | Add only with archive control. |
| Delete session | **Missing** | Application/session catalog state and explicit destructive control | Define deletion scope and audit before exposing it. |
| Session title change | **Missing** | `SessionTitleChanged` | Consume native name updates and distinguish custom, automatic, and summary origins. |
| Working-directory change | **Partial** | `SessionWorkingDirectoryChanged` | Verify native changes after startup and projections. |
| Model change and reroute | **Partial** | `ModelChanged` | Preserve previous/current model and an accurate shared reason. |
| Effort change | **Partial** | `EffortChanged` | Preserve previous/current effort and reason. |
| Goal update and clear | **Covered** | `GoalChanged` | Active, paused, blocked, limited, completed, and cleared states use the shared goal projection. |
| Token usage | **Covered** | `UsageReported` | Keep duplicate cumulative and per-turn sources explicitly classified. |
| Context-window usage | **Covered** | `ContextReported` | Keep it tied to the model/window that produced it. |
| Compaction lifecycle | **Partial** | `CompactionStarted`, `CompactionFinished` | Correlate boundaries and token values. |
| Review-mode lifecycle | **Missing** | Shared session-mode state | Add only the mode fact needed by shared consumers. |

## Application-state and transport coverage

These capabilities must not be forced into transcript operations. They belong
to named application services and may be delivered beside canonical session
activity over SSE.

| Capability | Status | Decision | Work required |
|---|---|---|---|
| MCP server inventory and connection state | **Application state** | Explicit MCP application snapshot | Represent startup, progress, failure, and authentication state outside session activity. |
| Plugin inventory | **Application state** | Explicit extension catalog | Refresh without adding transcript rows. |
| Skill inventory and changes | **Application state** | Explicit skill catalog | Keep inventory changes separate from actual skill invocation. |
| Model catalog and availability | **Application state** | Harness catalog snapshot | Keep native identifiers inside the Codex plugin boundary. |
| Thread status and settings | **Application state** | Session/application snapshot | Canonical events remain the source for semantic changes. |
| Remote-control connection | **Application state** | Connection/control snapshot | Do not represent transport connection as an operation. |
| MCP OAuth state | **Application state** | Attention plus connection state | Attention covers user action; application state covers connection progress. |
| Real-time conversation and audio | **Out of scope** | No implementation until Baqylau deliberately supports this surface | Preserve raw evidence only if it enters an observed source. |
| Codex cloud-task control | **Out of scope** | No implementation until explicitly requested | Do not expand the local session model speculatively. |
| Doctor, login, logout, sandbox, and configuration commands | **Out of scope** | CLI administration remains outside a monitored session | A shell invocation may still appear as an ordinary shell operation. |

## Deliberate raw-only records

| Record family | Decision | Reason |
|---|---|---|
| Encrypted reasoning without plaintext summary | **Raw only** | Baqylau must not pretend it can recover unavailable semantic content. |
| Duplicate message, reasoning, operation, or completion register | **Raw only** | The selected authoritative register already produces the canonical fact. |
| World-state snapshot | **Raw only** | Internal execution state is not transcript semantics. |
| Inter-actor transport metadata | **Raw only** | Actor lifecycle and messages come from semantic records. |
| Streaming token deltas | **Raw only** | Final semantic content and usage facts are authoritative. |
| Actor list and actor wait bookkeeping | **Raw only** | They query or wait on state without changing it. |
| Tool discovery performed only to select a tool | **Raw only** | Internal discovery is not a user-visible action. Actual invoked tools remain semantic. |
| Transport buffering and retry notices | **Raw only** | Operational diagnostics and connection state own this information. |

## Change procedure

When Codex emits a previously unseen record, tool, action, or state:

- preserve the exact native record in raw audit;
- add or update its row here before choosing a presentation;
- decide whether it is canonical, application state, raw only, or out of scope;
- if canonical, map it to existing shared vocabulary or deliberately extend the
  domain for semantics that are not Codex-specific;
- add strict parser and translator coverage in `plugins/codex`;
- add shared projection and frontend tests only when consumers should expose it;
- record translation failures for unknown semantic variants;
- remove any superseded inference path rather than retaining a compatibility
  fallback;
- update the row to **Covered** only after the full acceptance rule above is
  satisfied.

The tracker is complete only when every observed Codex capability has an
explicit row and no production path silently ignores an undecided semantic
record.
