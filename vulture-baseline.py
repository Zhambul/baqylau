# Vulture baseline — findings that exist today, kept here so `make lint` is
# green. Shrink-only: delete the code, then delete its line. Nothing should be
# added here — a new finding means new dead code, and the gate should fail.
#
# `make deadcode-backlog` prints the same list with this file switched off.
#
# What is left is two kinds, and they are NOT the same debt:
#
#   unused method (2) — product code that ONLY tests/ reaches. The scan omits
#     tests/ from its paths precisely so these surface instead of looking
#     alive. Each is a real question: is the method the product's, or the
#     test's? Answering it means deleting the method or using it.
#
#   unused variable (85) — response-model fields (dataclass / pydantic). These are
#     NOT dead: they are consumed by serialization vulture cannot follow — the
#     codec's `getattr(value, field.name)` fan-out (domain/codec.py,
#     dashboard/services/) and the JS frontend. Adding a field to a response
#     model trips this gate until its name is listed here.
#
# Everything else vulture found was deleted; the source is in
# deadcode-attic.md, verbatim, if a call turns out to have been missed.
#
# Names match globally, not per-site: a line here silences that name
# everywhere. Each carries the site it came from as a comment.

recorded  # unused variable (api/common/models/replies/recorded_response.py:6)
expires_in  # unused variable (api/application/models/files/dictation_grant_response.py:7)
is_image  # unused variable (api/application/models/files/upload_response.py:10)
control_names  # unused variable (api/application/models/harnesses/harness_description_response.py:11)
supports_terminal_input  # unused variable (api/application/models/harnesses/harness_description_response.py:13)
day_of_week  # unused variable (app/services/insights.py:32)
active_session_count  # unused variable (app/services/insights.py:47)
finished_session_count  # unused variable (app/services/insights.py:48)
last_session_at  # unused variable (app/services/insights.py:63)
daily_sessions  # unused variable (app/services/insights.py:64)
total_session_count  # unused variable (app/services/insights.py:70)
daily_sessions  # unused variable (app/services/insights.py:71)
hourly_sessions  # unused variable (app/services/insights.py:72)
last_seven_days  # unused variable (app/services/insights.py:73)
last_thirty_days  # unused variable (app/services/insights.py:74)
all_time  # unused variable (app/services/insights.py:75)
dirty  # unused variable (core/repository.py:14)
RESUMABLE_SCAN  # unused variable (dashboard/config.py:16)
ESCALATION_DELAY_SECONDS  # unused variable (dashboard/config.py:114)
NOTIFY_TELEGRAM_ALWAYS  # unused variable (dashboard/config.py:118)
notifications_muted  # unused variable (dashboard/services/workspace.py:32)
tasks_hidden  # unused variable (dashboard/services/workspace.py:33)
question_id  # unused variable (api/sessiondata/models/entry.py:114)
ended_at  # unused variable (repository/model/audit.py:53)
_.payload_json  # unused method (domain/codec.py:209)
resumed_from  # unused variable (domain/events.py:60)
prompt_message_id  # unused variable (domain/events.py:121)
final_message_id  # unused variable (domain/events.py:126)
previous_path  # unused variable (domain/events.py:257)
line_start  # unused variable (domain/events.py:258)
line_end  # unused variable (domain/events.py:259)
tool  # unused variable (domain/events.py:271)
selection_id  # unused variable (domain/values.py:32)
ACCOUNT_CONFIG_DIRECTORY  # unused variable (harness/impl/claude_code/account.py:10)
SUPPORTED_SHELLS  # unused variable (harness/impl/claude_code/account.py:12)
KINDS  # unused variable (harness/impl/claude_code/canonical/transcript.py:291)
TITLE_SCAN  # unused variable (harness/impl/claude_code/canonical/transcript.py:496)
TITLE_TAIL_B  # unused variable (harness/impl/claude_code/canonical/transcript.py:500)
SCREEN_LIMIT  # unused variable (harness/impl/claude_code/controls/screen_driver.py:9)
TAIL_SCAN_BYTES  # unused variable (harness/impl/claude_code/model.py:30)
MODEL_LADDER  # unused variable (harness/impl/claude_code/model.py:194)
KINDS  # unused variable (harness/impl/codex/canonical/rollout.py:971)
BRIEF_MAX_LINES  # unused variable (harness/impl/codex/canonical/rollout.py:1064)
BRIEF_MAX_B  # unused variable (harness/impl/codex/canonical/rollout.py:1065)
TITLE_HEAD_LINES  # unused variable (harness/impl/codex/canonical/title.py:32)
STATE_DB_TTL_S  # unused variable (harness/impl/codex/canonical/title.py:36)
APPROVE_OPTIONS  # unused variable (harness/impl/codex/controls/plandialog.py:46)
efforts  # unused variable (harness/models/catalog.py:31)
minimum_prompt_count  # unused variable (harness/models/catalog.py:38)
restored_text  # unused variable (harness/models/controls.py:186)
restored_text  # unused variable (harness/models/controls.py:196)
target_account_id  # unused variable (harness/models/controls.py:202)
resets_at  # unused variable (harness/models/usage.py:15)
resets_at  # unused variable (harness/models/usage.py:25)
switchable  # unused variable (harness/models/usage.py:33)
scheduling_allowed  # unused variable (harness/models/usage.py:37)
authentication_error  # unused variable (harness/models/usage.py:39)
DELETE_WINDOW_SECONDS  # unused variable (notify/channels/telegram.py:52)
_.current_session  # unused method (terminal/adapter.py:102)
to_bottom  # unused variable (terminal/models/viewport.py:37)
up_lines  # unused variable (terminal/models/viewport.py:38)
