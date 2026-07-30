# plugins/claude_code/nested.py — WHO launched a session's background jobs and
# monitors, recovered from Claude Code's own launch hook payloads.
#
# The read model needs to attribute every nested stream (a background job, a
# Monitor run) to the agent that started it, and to name the COMMAND behind it.
# The authoritative source is the tailer's own audit `streams.agent_id`
# (hookkit.stream_env's CLAUDE_STREAM_AGENT) — this is the SECOND source, for the
# two things that row cannot serve:
#
#   * HISTORY. Every stream row written before the stamp carries agent_id ''
#     whoever launched it, so a parked session could never be partitioned.
#   * The missing COMMAND. A subagent's bg job paints its `code` op under the
#     tool_use_id while its stream row is keyed by the backgroundTaskId, so
#     core.copy.group_commands misses and the job rendered with a blank command;
#     a subagent's MONITOR is absent from the main transcript entirely, so
#     plugins.monitors had no command for it either.
#
# (The audit-query helpers it borrows — `sessionapi.db_rows` / `in_clause` /
# `sid_chain` — are core's PUBLIC read-only query surface; the tool-specific
# half is the query TEXT below, which is what moved.)
#
# Both are recoverable from the audit `hook_events` table, whose PostToolUse
# payload carries agent_id, the task id, the tool_use_id and the command
# TOGETHER. That query is the reason this module exists in a PLUGIN: it is
# Claude Code's hook vocabulary end to end — the hook NAME (`PostToolUse`), the
# TOOL names (`Bash`/`Monitor`) and four JSON paths into a Claude payload shape
# (`$.tool_response.backgroundTaskId`, `$.tool_response.taskId`,
# `$.tool_use_id`, `$.tool_input.command`) — and it used to sit in
# core/sessionapi.py, where it was the single deepest tool-specific leak in a
# module whose header calls itself tool-agnostic. A host with a different hook
# vocabulary (codex writes none of these rows) now DECLINES the fan-out instead
# of being silently answered "{}" by Claude's SQL.
#
# What stayed in core is the composition: sessionapi.nested_owners() memoizes
# this per sid (OWNERS_TTL_S) and every reader — jobs(), monitor_streams(), the
# badge counts — goes through that one door, unchanged.
#
# Extraction runs in SQLite (json_extract) rather than Python so a busy session's
# large payloads are never pulled across — only the six small columns.
from core import sessionapi as API

# Claude Code's launch-hook coordinates: the hook that reports a completed tool
# call, and the two built-in tools whose response can carry a NESTED stream's id
# (Bash with `run_in_background` → backgroundTaskId; Monitor → taskId).
HOOK = "PostToolUse"
TOOLS = ("Bash", "Monitor")


def nested_owners(sid):
    """`{task_id: {"agent_id", "tool_use_id", "command", "description"}}` for
    one session's background jobs and monitors, chain-aware (a sid fork's
    pre-adoption rows live under the OLD sid). {} when the audit is unavailable
    or this session launched none.

    Every row without a task id is skipped — that is a plain FOREGROUND call,
    which owns no nested stream."""
    chain = API.sid_chain(sid)
    q = ("SELECT agent_id,"
         " json_extract(payload,'$.tool_response.backgroundTaskId'),"
         " json_extract(payload,'$.tool_response.taskId'),"
         " json_extract(payload,'$.tool_use_id'),"
         " json_extract(payload,'$.tool_input.command'),"
         " json_extract(payload,'$.tool_input.description')"
         " FROM hook_events WHERE hook=? AND tool_name IN (%s)"
         " AND session_id IN (%s) ORDER BY id"
         % (API.in_clause(len(TOOLS)), API.in_clause(len(chain))))
    out = {}
    for aid, btid, mtid, tuid, cmd, desc in API.db_rows(
            API.audit_db(), q, (HOOK,) + TOOLS + tuple(chain)):
        task = btid or mtid
        if not task:
            continue                      # a plain foreground call — no nested stream
        out[task] = {"agent_id": aid or "", "tool_use_id": tuid or "",
                     "command": cmd or "", "description": desc or ""}
    return out
