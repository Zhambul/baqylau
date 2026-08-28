# Vulture allowlist — names reached by a protocol, never by a caller in this
# repo. Permanent: every entry below is a framework/stdlib contract, not debt.
# (Dead code that merely HASN'T been deleted yet lives in vulture-baseline.py.)
#
# Nothing here is imported or executed; vulture parses the file and counts each
# name as one use. Framework CALL SITES that ride a decorator (FastAPI routes,
# pydantic validators) are handled by --ignore-decorators in the Makefile.

# http.server dispatches these by name on the handler class.
_.log_message
_.do_POST

# HTTPServer.server_bind() assigns them; the stdlib reads them back.
_.server_name
_.server_port

# sqlite3.Connection attribute — set to shape rows, never read by us.
_.row_factory

# inspect/FastAPI contract: `singleton` rewrites the signature the framework
# reads off a provider, so the attribute is assigned here and read by
# `inspect.signature` — never by a caller of ours.
_.__signature__

# The account pair a launched CLI carries in its environment. Read by a program
# that CANNOT import this one — `client/_http.py`, which imports nothing of ours
# by design — so the only reference inside the import graph is the module that
# owns the concept and validates what comes back.
# tests/test_canonical_clients.py::test_the_http_module_matches_the_daemon pins the two
# copies to each other, so this is a contract with a reader, not dead code.
SLUG_VARIABLE
LABEL_VARIABLE

# --- response-model fields reached only by serialization ------------------------
# Each of these is a frozen-dataclass field the browser reads, carried out by
# `dashboard.render.serialize.json_ready`'s `getattr(value, field.name)` fan-out.
# Vulture cannot follow that, and the three below stopped looking used only
# because the last non-serialization reader of the NAME went away with the code
# this refactor deleted. They belong here rather than in the shrink-only
# baseline: a serialized field is a framework contract, not debt.
new_session_drafts  # dashboard/services/preferences.py
error_id         # audit/models.py
completed        # api/sessiondata GoalResponse; read by HTTP model serialization

# `notify/presence.py`'s RouteDecision/RouteCandidate: read whole by
# `dataclasses.asdict(decision)` (notify/notifier.py, the `notify-route`
# audit row) rather than by field name, so vulture never sees the read.
age_s               # RouteCandidate
target_label        # RouteDecision
subscription_count  # RouteDecision

# anyio's capacity limiter: we ASSIGN the pool size the policy asked for and
# anyio reads it back when it hands out worker threads (api/app.py's lifespan).
# The only reference of ours is the assignment, which is what a framework
# attribute looks like from inside our own graph.
_.total_tokens

# Naming jobs are invoked through the repository Protocol. Vulture keeps the
# concrete and Protocol declarations separate from those attribute call sites,
# even though mypy verifies both implementations against the same contract.
_.enqueue
_.register_running
_.claim_next
_.complete
_.fail

# The session renamer calls this method through the terminal Protocol.
_.rename_session_tab

# pydantic's own config attribute (harness/impl/codex/canonical/records.py,
# harness/impl/codex/usage.py): pydantic's metaclass reads `model_config` off
# every BaseModel subclass to build its validator. We only ever ASSIGN it —
# the same "framework attribute" shape as `_.total_tokens` above.
_.model_config
clientInfo  # codex usage JSON-RPC request field, read by pydantic serialization
jsonrpc     # codex usage JSON-RPC envelope field, read by pydantic serialization
account_name  # Claude status-line field read by pydantic serialization

# Closed boundary and audit models. These fields are read by Pydantic when it
# writes JSON or by the remote API that reads that JSON.
why             # rejected input and pane-command audit detail
rate            # dictation audit rate
payload_base64  # raw-event audit output
payload_bytes   # harness audit input size
ttl_seconds     # Deepgram grant response
control         # control audit gesture name
ms              # control audit duration
channel         # notification retraction audit
age_seconds     # notification retraction audit
retractable     # Telegram send audit
typ             # VAPID JWT header
alg             # VAPID JWT header
exp             # VAPID JWT claim
no_response     # kitty remote-control request

# FileMarker is compared as one dataclass value. Dataclass equality reads these
# generated fields, which static name analysis cannot see.
inode        # harness/file_tail.py FileMarker
modified_at  # harness/file_tail.py FileMarker

# --- FOREIGN payload fields validated but never read -----------------------
# harness/impl/codex/canonical/records.py / usage.py declare codex's OWN JSON
# shapes with `extra="forbid"` so an unrecognised field fails translation
# (TASKS.md, the owner's 2026-08-21 decision) — which means every field codex
# actually sends must be declared, not only the ones our own logic reads. Each
# name below is a REAL field of a foreign record with no reader today: kept
# for the day one exists, not debt to pay down.
window_minutes    # records.py RateLimitWindow
plan_type         # records.py RateLimitsBlock
originator        # records.py SessionMetaPayload
item_id           # records.py CommandCompletedRecord (the codex item's own id)

# The same shape, all found by fixing the live e2e drift (a real codex
# session's rollout carries these; test-drift's kept-dir interpretations
# named each one) — declared to keep `extra="forbid"`, not read by anything.
rate_limit_reached_type      # records.py RateLimitsBlock
spend_control_reached        # records.py RateLimitsBlock
limit_id                     # records.py RateLimitsBlock
limit_name                   # records.py RateLimitsBlock
individual_limit             # records.py RateLimitsBlock
credits                      # records.py RateLimitsBlock
has_credits                  # records.py RateLimitCredits
unlimited                    # records.py RateLimitCredits
balance                      # records.py RateLimitCredits
cache_write_input_tokens     # records.py TokenUsageBlock
reasoning_output_tokens      # records.py TokenUsageBlock
threadId                     # records.py GoalBlock / ThreadGoalUpdatedPayload
collaboration_mode_kind      # records.py TaskStartedPayload
duration_ms                  # records.py TaskCompletePayload / TurnAbortedPayload
time_to_first_token_ms       # records.py TaskCompletePayload
developer_instructions       # records.py CollaborationModeSettings / TurnContextPayload
model_provider_id            # records.py ThreadSettingsBlock
service_tier                 # records.py ThreadSettingsBlock
approval_policy               # records.py ThreadSettingsBlock / TurnContextPayload
approvals_reviewer            # records.py ThreadSettingsBlock / TurnContextPayload
personality                   # records.py ThreadSettingsBlock / TurnContextPayload
reasoning_summary             # records.py ThreadSettingsBlock
active_permission_profile     # records.py ThreadSettingsBlock
permission_profile            # records.py ThreadSettingsBlock / TurnContextPayload
secs                          # records.py DurationBlock
nanos                         # records.py DurationBlock
parsed_cmd                    # records.py CommandExecutionItem
current_date                  # records.py TurnContextPayload
timezone                      # records.py TurnContextPayload
sandbox_policy                # records.py TurnContextPayload
user_instructions              # records.py TurnContextPayload
truncation_policy              # records.py TurnContextPayload
realtime_active                # records.py TurnContextPayload
file_system_sandbox_policy     # records.py TurnContextPayload
workspace_roots                # records.py TurnContextPayload
comp_hash                      # records.py TurnContextPayload
multi_agent_version            # records.py TurnContextPayload / SessionMetaPayload
multi_agent_mode                # records.py TurnContextPayload
trigger_turn                    # records.py InterAgentCommunicationMetadataPayload
first_window_id                 # records.py CompactedPayload
window_number                   # records.py CompactedPayload
commit_hash                     # records.py SessionMetaGit
repository_url                  # records.py SessionMetaGit
cli_version                     # records.py SessionMetaPayload
model_provider                  # records.py SessionMetaPayload
base_instructions               # records.py SessionMetaPayload
history_mode                    # records.py SessionMetaPayload
history_base                    # records.py SessionMetaPayload
end_ordinal_exclusive           # records.py SessionMetaHistoryBase
end_byte_offset                 # records.py SessionMetaHistoryBase
git                             # records.py SessionMetaPayload
dynamic_tools                   # records.py SessionMetaPayload
agent_nickname                  # records.py SessionMetaPayload / ThreadSpawn
agent_role                      # records.py ThreadSpawn
forked_from_id                  # records.py SessionMetaPayload
subagent_history_start_ordinal  # records.py SessionMetaPayload
encrypted_content               # records.py ReasoningPayload
namespace                       # records.py FunctionCallPayload
images          # records.py UserMessagePayload
local_images    # records.py UserMessagePayload
text_elements   # records.py UserMessagePayload
audio           # records.py UserMessagePayload
local_audio     # records.py UserMessagePayload
memory_citation # records.py AgentMessagePayload
results         # records.py WebSearchEndPayload
completed_at_ms # records.py ItemCompletedPayload
thread_id       # records.py ItemCompletedPayload
create_time     # records.py ChatMessageMetadata
sender_thread_id   # records.py CollabAgentToolCallItem
receiver_thread_ids  # records.py CollabAgentToolCallItem
receiver_agents      # records.py CollabAgentToolCallItem
agents_states        # records.py CollabAgentToolCallItem
image_url            # records.py ContentPart
queries              # records.py WebSearchAction / WebSearchCallAction
author               # records.py CompactedHistoryItem
workdir              # records.py ExecArguments
yield_time_ms         # records.py ExecArguments / StdinArguments
max_output_tokens    # records.py ExecArguments / StdinArguments
tty                  # records.py ExecArguments
login                # records.py ExecArguments
sandbox_permissions  # records.py ExecArguments
justification        # records.py ExecArguments
prefix_rule          # records.py ExecArguments

# A TypedDict field read only through dict-literal construction and ["check"]
# subscripts (askdialog_screen.rows / its dialog callers) — the ANNOTATION is
# the only bare-name mention, which is what an unused variable looks like to
# vulture. The field is the checkbox state the multi-select dialog reads.
check  # harness/impl/claude_code/controls/askdialog_screen.py Row

# Three more TypedDict fields with the same shape as `check` above: the typed
# migration gave dict-shaped returns their real field types, and each field's
# only bare-name mention is its annotation — readers go through ["…"]
# subscripts and dict literals, which vulture cannot connect to the name.
access_token  # dashboard/dictate.py GrantResponse
alias         # harness/impl/claude_code/account.py AccountRecord
decided       # harness/impl/claude_code/controls/plandialog.py Decided

# harness/impl/claude_code/canonical/records.py — the Claude Code transcript/
# hook/tool-call FOREIGN models. Every name below is a declared, corpus-
# observed field nothing in this package reads (the same "declared as far as
# reality allows, not only as far as a reader needs" stance codex's own
# records.py section above takes): `extra="forbid"` demands them of every
# record Claude Code actually sends, not only the ones a translator uses.
caller                          # ToolUseBlock / ToolCallNative
is_error                        # ToolResultBlock
thinking                        # ThinkingBlock
from_                           # FallbackBlock / Origin
stop_reason                     # MessageObject
stop_sequence                   # MessageObject
stop_details                    # MessageObject
container                       # MessageObject
context_management              # MessageObject
diagnostics                      # MessageObject
senderTaskId                     # Origin
parentUuid                        # UserRecord / AssistantRecord / SystemRecord / AttachmentRecord
sessionId                          # UserRecord / AssistantRecord / SystemRecord / AttachmentRecord / TitleRecord
gitBranch                          # UserRecord / AssistantRecord / SystemRecord
entrypoint                         # UserRecord / AssistantRecord / SystemRecord
userType                           # UserRecord / AssistantRecord / SystemRecord
agentId                            # UserRecord / AssistantRecord / SystemRecord
isSidechain                        # UserRecord / AssistantRecord / SystemRecord / AttachmentRecord
isVisibleInTranscriptOnly          # UserRecord
permissionMode                     # UserRecord
promptId                           # UserRecord
promptSource                       # UserRecord
sourceToolAssistantUUID            # UserRecord
sourceToolUseID                    # UserRecord
toolDenialKind                      # UserRecord
turnCompanion                       # UserRecord
userFeedback                        # UserRecord
imagePasteIds                       # UserRecord
queueSkipAttachments                # UserRecord task-notification delivery flag
isAbortedMidStream                  # AssistantRecord
isApiErrorMessage                   # AssistantRecord
apiErrorStatus                      # AssistantRecord
errorDetails                        # AssistantRecord
requestId                           # AssistantRecord / SystemRecord
attributionAgent                    # AssistantRecord
attributionPlugin                   # AssistantRecord
attributionSkill                    # AssistantRecord
quotaLimits                         # AssistantRecord
logicalParentUuid                   # SystemRecord
toolUseID                           # SystemRecord
toolUseId                           # SystemRecord / AgentMetaFile
stopReason                          # SystemRecord
hasOutput                           # SystemRecord
hookAdditionalContext               # SystemRecord
hookCount                           # SystemRecord
hookErrors                          # SystemRecord
hookInfos                           # SystemRecord
preventContinuation                 # SystemRecord
preventedContinuation               # SystemRecord
durationMs                          # SystemRecord / GoalStatusAttachment
messageCount                        # SystemRecord
pendingBackgroundAgentCount         # SystemRecord
session_title                      # HookPayload

# terminal/models/values.py WindowInfo is built from the terminal protocol.
# Consumers use the tab-level focus values. This native pane-level field stays
# in the closed terminal shape and is read through dataclass serialization.
is_active_in_tab
fallbackModel                       # SystemRecord
originalModel                       # SystemRecord
persistedAsDefault                  # SystemRecord
apiRefusalCategory                  # SystemRecord
apiRefusalExplanation               # SystemRecord
direction                           # SystemRecord
refusedUserMessageUuid              # SystemRecord
trigger                             # SystemRecord / HookPayload
iterations                          # GoalStatusAttachment
sentinel                            # GoalStatusAttachment
source_uuid                         # QueuedCommandAttachment
old_string                         # FileArguments
new_string                         # FileArguments
replace_all                        # FileArguments
offset                             # FileArguments
max_results                        # SearchArguments
allowed_domains                    # SearchArguments
discard_changes                    # WorktreeArguments
team_name                          # AssignmentArguments / HookPayload
isolation                          # AssignmentArguments
annotations                        # QuestionArguments
planFilePath                       # PlanArguments
backgroundedByUser                  # ToolResponse
agent_transcript_path               # HookPayload
permission_mode                     # HookPayload
headUuid                           # PreservedCompactSegment
anchorUuid                         # PreservedCompactSegment / PreservedCompactMessages
tailUuid                           # PreservedCompactSegment
uuids                              # PreservedCompactMessages
allUuids                           # PreservedCompactMessages
postTokens                         # CompactMetadata
cumulativeDroppedTokens            # CompactMetadata
preCompactDiscoveredTools          # CompactMetadata
preservedSegment                   # CompactMetadata
preservedMessages                  # CompactMetadata
filePath                           # ToolResponseFile
numLines                           # ToolResponseFile
startLine                          # ToolResponseFile
totalLines                         # ToolResponseFile
originalWidth                      # ToolResponseImageDimensions
originalHeight                     # ToolResponseImageDimensions
displayWidth                       # ToolResponseImageDimensions
displayHeight                      # ToolResponseImageDimensions
truncatedByTokenCap                # ToolResponseFile
originalSize                       # ToolResponseFile
dimensions                         # ToolResponseFile
token_budget                       # codex GoalArguments
tool_calls                          # HookPayload
is_interrupt                        # HookPayload
stop_hook_active                    # HookPayload
last_assistant_message              # HookPayload
seconds_since_last_response         # HookPayload
context_tokens                      # HookPayload
prompt_cache_likely_expired         # HookPayload
estimated_cache_write_usd           # HookPayload
background_tasks                    # HookPayload
session_crons                       # HookPayload
final                               # HookPayload
notification_type                   # HookPayload
permission_suggestions              # HookPayload
custom_instructions                 # HookPayload
compact_summary                     # HookPayload
load_reason                         # HookPayload
memory_type                         # HookPayload
teammate_name                       # HookPayload
task_subject                        # HookPayload
task_description                    # HookPayload
agentType                          # AgentMetaFile
customAgentType                    # AgentMetaFile
isFork                              # AgentMetaFile
parentAgentId                       # AgentMetaFile
planModeRequired                    # AgentMetaFile
spawnDepth                          # AgentMetaFile
stoppedByUser                       # AgentMetaFile
teamName                            # AgentMetaFile
worktreeBranch                      # AgentMetaFile
worktreeCleanlyRemoved              # AgentMetaFile
worktreePath                        # AgentMetaFile
activeForm                          # TaskFile
blockedBy                           # TaskFile
promptText                          # HookSummaryInfo
limit_dollars                       # Claude live usage foreign response
used_dollars                        # Claude live usage foreign response
remaining_dollars                   # Claude live usage foreign response
is_enabled                          # Claude live usage foreign response
monthly_limit                       # Claude live usage foreign response
used_credits                        # Claude live usage foreign response
currency                            # Claude live usage foreign response
decimal_places                      # Claude live usage foreign response
disabled_reason                     # Claude live usage foreign response
user_disabled                       # Claude live usage foreign response
spend_limit_reached                 # Claude live usage foreign response
credits_ever_enabled                # Claude live usage foreign response
daily                               # Claude live usage foreign response
weekly                              # Claude live usage foreign response
surface                             # Claude live usage foreign response
severity                            # Claude live usage foreign response
amount_minor                        # Claude live usage foreign response
exponent                            # Claude live usage foreign response
used                                # Claude live usage foreign response
cap                                 # Claude live usage foreign response
auto_reload                         # Claude live usage foreign response
disclaimer                          # Claude live usage foreign response
can_purchase_credits                # Claude live usage foreign response
can_toggle                          # Claude live usage foreign response
seven_day_oauth_apps                # Claude live usage foreign response
seven_day_opus                      # Claude live usage foreign response
seven_day_sonnet                    # Claude live usage foreign response
seven_day_cowork                    # Claude live usage foreign response
seven_day_omelette                  # Claude live usage foreign response
tangelo                             # Claude live usage foreign response
iguana_necktie                      # Claude live usage foreign response
omelette_promotional                # Claude live usage foreign response
nimbus_quill                        # Claude live usage foreign response
cinder_cove                         # Claude live usage foreign response
amber_ladder                        # Claude live usage foreign response
extra_usage                         # Claude live usage foreign response
spend                               # Claude live usage foreign response
member_dashboard_available          # Claude live usage foreign response
total_cost_usd                      # Claude live usage foreign response
total_api_duration_ms               # Claude live usage foreign response
total_duration_ms                   # Claude live usage foreign response
total_lines_added                   # Claude live usage foreign response
total_lines_removed                 # Claude live usage foreign response
model_usage                         # Claude live usage foreign response
pct                                 # Claude live usage foreign response
request_count                       # Claude live usage foreign response
behaviors                           # Claude live usage foreign response
agents                              # Claude live usage foreign response
mcp_servers                         # Claude live usage foreign response
week                                # Claude live usage foreign response
rate_limits_available               # Claude live usage foreign response
hasCredits                          # Codex rate-limit foreign response
limitId                             # Codex rate-limit foreign response
limitName                           # Codex rate-limit foreign response
individualLimit                     # Codex rate-limit foreign response
spendControlReached                 # Codex rate-limit foreign response
rateLimitReachedType                # Codex rate-limit foreign response
resetType                           # Codex rate-limit foreign response
grantedAt                           # Codex rate-limit foreign response
expiresAt                           # Codex rate-limit foreign response
availableCount                      # Codex rate-limit foreign response
rateLimitsByLimitId                 # Codex rate-limit foreign response
rateLimitResetCredits               # Codex rate-limit foreign response
tokensUsed                          # Codex goal foreign response
timeUsedSeconds                     # Codex goal foreign response
createdAt                           # Codex goal foreign response
updatedAt                           # Codex goal foreign response
content_item_kinds                  # Codex chat metadata foreign response
supports_native_initial_naming      # HarnessInfo capability field
thinking_tokens                    # UsageOutputTokensDetails
web_search_requests                # UsageServerToolUse
web_fetch_requests                 # UsageServerToolUse
ephemeral_1h_input_tokens           # UsageCacheCreation
ephemeral_5m_input_tokens           # UsageCacheCreation
output_tokens_details              # MessageUsage
server_tool_use                    # MessageUsage
cache_creation                     # MessageUsage / UsageIteration
inference_geo                      # MessageUsage
speed                              # MessageUsage
screen_error                       # TerminalWindowDiagnosticResponse
toolName                          # Claude PermissionRule foreign field
ruleContent                       # Claude PermissionRule foreign field
attributionMcpServer              # Claude AssistantRecord foreign field
attributionMcpTool                # Claude AssistantRecord foreign field

# Pydantic serializes these Python-side names through their camelCase aliases;
# the receiver reads the JSON names, so no Python attribute read exists here.
permission_decision                 # HookSpecificOutput.permissionDecision
updated_input                       # HookSpecificOutput.updatedInput
hook_specific_output                # HookReply.hookSpecificOutput
updated_permissions                 # ChromePermissionDecision.updatedPermissions

# --- StrEnum members reached only by validation/serialization -------------------
# A closed vocabulary's member is a complete listing of what a stored value or a
# wire field may BE, not a set of branches our own code has to take — pydantic
# constructs the member from a stored or posted string, and a member nothing
# ever branches on by name is still a real, round-tripped value. Framework
# contract, not debt, the same reasoning as the response-model fields above.
AUTOMATIC_FALLBACK                  # domain/values.py ModelChangeReason
ERROR                               # domain/values.py ProgressStream
ACTOR                               # domain/values.py UsageScope
TURN                                # domain/values.py UsageScope
OPERATION                           # domain/values.py UsageScope
VERBOSE                             # domain/preferences.py ViewMode
FOCUS                               # domain/preferences.py ViewMode
ANSWER                              # AnswerDecision, OptimisticActionKind (harness/models/controls.py, api/telemetry)
TRANSPORT                           # api/telemetry/models/client_failure_request.py ClientFailureKind
HTTP                                # api/telemetry/models/client_failure_request.py ClientFailureKind
COMPOSER                            # api/telemetry/models/optimistic_action_request.py OptimisticActionKind
CLOSE                               # api/telemetry/models/optimistic_action_request.py OptimisticActionKind
SHOWN                               # api/telemetry/models/optimistic_action_request.py OptimisticActionPhase
RECONCILED                          # api/telemetry/models/optimistic_action_request.py OptimisticActionPhase
DROPPED                             # api/telemetry/models/optimistic_action_request.py OptimisticActionPhase
STALE                               # api/telemetry/models/optimistic_action_request.py OptimisticActionPhase
GPT_5_6_SOL                         # harness/impl/codex/model.py CodexModel
GPT_5_6_TERRA                       # harness/impl/codex/model.py CodexModel
GPT_5_6_LUNA                        # harness/impl/codex/model.py CodexModel
GPT_5_5                             # harness/impl/codex/model.py CodexModel
GPT_5_4                             # harness/impl/codex/model.py CodexModel
GPT_5_4_MINI                        # harness/impl/codex/model.py CodexModel
GPT_5_3_CODEX_SPARK                 # harness/impl/codex/model.py CodexModel
FABLE                               # harness/impl/claude_code/model.py ClaudeCodeModel
OPUS                                # harness/impl/claude_code/model.py ClaudeCodeModel
SONNET                              # harness/impl/claude_code/model.py ClaudeCodeModel
HAIKU                               # harness/impl/claude_code/model.py ClaudeCodeModel
CLAUDE_FABLE_5                      # harness/impl/claude_code/model.py ClaudeCodeModel
CLAUDE_OPUS_5                       # harness/impl/claude_code/model.py ClaudeCodeModel
CLAUDE_OPUS_4_8                     # harness/impl/claude_code/model.py ClaudeCodeModel
CLAUDE_SONNET_5                     # harness/impl/claude_code/model.py ClaudeCodeModel
CLAUDE_HAIKU_4_5                    # harness/impl/claude_code/model.py ClaudeCodeModel
CLAUDE_HAIKU_4_5_20251001           # harness/impl/claude_code/model.py ClaudeCodeModel
LOW                                 # harness/impl/codex/model.py CodexEffort
MEDIUM                              # harness/impl/codex/model.py CodexEffort
HIGH                                # harness/impl/codex/model.py CodexEffort
XHIGH                               # harness/impl/codex/model.py CodexEffort
MAX                                 # harness/impl/codex/model.py CodexEffort
ULTRA                               # harness/impl/codex/model.py CodexEffort
FALLBACK_MESSAGE                    # records.py UsageIterationType
STANDARD                            # records.py UsageServiceTier / UsageSpeed
NOT_AVAILABLE                       # records.py UsageInferenceGeo
