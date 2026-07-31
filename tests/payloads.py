# payloads.py — synthetic hook payload builders, one per docs/wiring.md row.
#
# Shapes mirror what Claude Code actually sends (the empirically-confirmed
# fields are documented in docs/streaming.md — updatedInput, backgroundTaskId +
# backgroundedByUser, stoppedByUser, "[Request interrupted by user]").
# Every builder takes the Session fixture object for the identity fields.
import os


def base(s, event, **over):
    d = {"session_id": s.sid, "transcript_path": s.transcript, "cwd": s.cwd,
         "hook_event_name": event, "pid": os.getpid()}
    d.update(over)
    return d


def session_start(s, source="startup"):
    return base(s, "SessionStart", source=source)


def user_prompt(s, text="do the thing"):
    return base(s, "UserPromptSubmit", prompt=text)


def pre_bash(s, cmd, tid="toolu_001", run_in_background=False, agent_id=None,
             description=""):
    d = base(s, "PreToolUse", tool_name="Bash", tool_use_id=tid,
             tool_input={"command": cmd, "run_in_background": run_in_background,
                         "description": description})
    if agent_id:
        d["agent_id"] = agent_id
    return d


def post_bash(s, cmd, tid="toolu_001", stdout="ok\n", stderr="",
              duration_ms=1234, failure=False, interrupted=False,
              run_in_background=False, background_task_id=None,
              backgrounded_by_user=False, agent_id=None, error=None):
    tr = {"stdout": stdout, "stderr": stderr, "interrupted": interrupted}
    if background_task_id:
        tr["backgroundTaskId"] = background_task_id
        if backgrounded_by_user:
            tr["backgroundedByUser"] = True
    d = base(s, "PostToolUseFailure" if failure else "PostToolUse",
             tool_name="Bash", tool_use_id=tid, duration_ms=duration_ms,
             tool_input={"command": cmd, "run_in_background": run_in_background},
             tool_response=tr)
    if failure and error:
        d["error"] = error
    if agent_id:
        d["agent_id"] = agent_id
    return d


# The two tool_response shapes a Bash call that never RAN comes back with — both
# measured verbatim out of the audit's own payloads (2026-07-31). Neither fires a
# PostToolUse, which is the whole reason claude-cmd-blocked.py exists; they are
# here as DATA rather than as a matcher, because the product deliberately keys on
# the unconsumed fg-live record instead of on this wording.
BLOCKED_BY_HOOK = ('PreToolUse:Bash hook error: '
                   '[python3 "$CLAUDE_PROJECT_DIR/.claude/hooks/block.py"]: '
                   "Blocked: don't pipe `adapters logs` output into grep/rg")
REJECTED_BY_USER = ("The user doesn't want to proceed with this tool use. "
                    "The tool use was rejected.")


def post_batch(s, calls, agent_id=None):
    """PostToolBatch — fires once a whole tool batch has resolved and carries
    EVERY call of it with its `tool_response`, INCLUDING the ones that never ran.
    `calls` is [(tool_use_id, command, tool_response), …]; a plain string response
    is what a never-ran call carries, a dict what a real run produces."""
    d = base(s, "PostToolBatch", tool_calls=[
        {"tool_name": "Bash", "tool_use_id": tid,
         "tool_input": {"command": cmd}, "tool_response": resp}
        for tid, cmd, resp in calls])
    if agent_id:
        d["agent_id"] = agent_id
    return d


def post_file(s, tool="Edit", path=None, patch=None, failure=False, agent_id=None,
              tid="toolu_001", old_string="old line", new_string="new line\nmore"):
    path = path or os.path.join(s.cwd, "example.py")
    tr = {"file": {"filePath": path}}
    ti = {"file_path": path}
    if tool in ("Edit", "MultiEdit"):
        # diff counts come from the INPUT's old/new strings (plugins/claude_code/tools.diff_counts)
        ti.update(old_string=old_string, new_string=new_string)
    elif tool == "Write":
        ti["content"] = new_string
    if patch is not None:
        tr["structuredPatch"] = patch
    elif tool in ("Edit", "Write", "MultiEdit"):
        tr["structuredPatch"] = [{"oldStart": 1, "oldLines": 1, "newStart": 1,
                                  "newLines": 2, "lines": ["-old", "+new", "+more"]}]
    d = base(s, "PostToolUseFailure" if failure else "PostToolUse",
             tool_name=tool, tool_use_id=tid, tool_input=ti, tool_response=tr)
    if agent_id:
        d["agent_id"] = agent_id
    return d


def post_monitor(s, description="watch the build", command="tail -f build.log",
                 task_id="mon-0001", failure=False, agent_id=None,
                 agent_type=None, error=None, persistent=None, timeout_ms=None,
                 ws_url=None):
    ti = {"description": description}
    if ws_url:                       # a WebSocket monitor has no command
        ti["ws"] = {"url": ws_url}
    else:
        ti["command"] = command
    if persistent is not None:
        ti["persistent"] = persistent
    if timeout_ms is not None:
        ti["timeout_ms"] = timeout_ms
    d = base(s, "PostToolUseFailure" if failure else "PostToolUse",
             tool_name="Monitor", tool_input=ti,
             tool_response={} if failure else {"taskId": task_id})
    if failure and error:
        d["error"] = error
    if agent_id:
        d["agent_id"] = agent_id
        d["agent_type"] = agent_type or "general-purpose"
    return d


def pre_task(s, description="explore the codebase", agent_id=None,
             tool_name="Task"):
    d = base(s, "PreToolUse", tool_name=tool_name,
             tool_input={"description": description,
                         "prompt": "go look at things",
                         "subagent_type": "Explore"})
    if agent_id:
        d["agent_id"] = agent_id
    return d


def subagent_start(s, agent_id="agent-0001", agent_type="Explore"):
    return base(s, "SubagentStart", agent_id=agent_id, agent_type=agent_type)


def subagent_stop(s, agent_id="agent-0001", agent_type="Explore", **over):
    return base(s, "SubagentStop", agent_id=agent_id, agent_type=agent_type, **over)


def task_created(s, task_id="1", subject="Fix the thing"):
    return base(s, "TaskCreated", task_id=task_id, task_subject=subject,
                task_description=subject)


def task_completed(s, task_id="1", subject="Fix the thing"):
    return base(s, "TaskCompleted", task_id=task_id, task_subject=subject,
                task_description=subject)


def post_task_update(s, task_id="1", status="in_progress", tid="toolu_tu1",
                     agent_id=None):
    """PostToolUse(TaskUpdate) — shape per the live capture 2026-07-18: a
    status flip fires NO dedicated hook, only this tool event (task_fmt's kv
    snapshot trigger)."""
    d = base(s, "PostToolUse", tool_name="TaskUpdate", tool_use_id=tid,
             tool_input={"taskId": task_id, "status": status},
             tool_response={"success": True, "taskId": task_id,
                            "updatedFields": ["status"]})
    if agent_id:
        d["agent_id"] = agent_id
    return d


def post_sendmessage(s, to="team-lead", summary="Money-cycle dedup complete",
                     message="Complete. core.money_cycle now owns the cycle.",
                     agent_id=None, agent_type=None, msg_id="msg-0001",
                     success=True, event="PostToolUse"):
    """PostToolUse(SendMessage) — shape per the live capture 2026-07-27 (2.1.220):
    tool_input {to, summary, message} and tool_response {success, msg_id, routing}.
    A TEAMMATE's send carries agent_id/agent_type; the lead's carries neither, and
    mail_fmt handles BOTH (the one formatter that does not skip agent events)."""
    d = base(s, event, tool_name="SendMessage", tool_use_id="toolu_sm1",
             tool_input={"to": to, "summary": summary, "message": message},
             tool_response={"success": success,
                            "message": "Message sent to %s's inbox" % to,
                            "msg_id": msg_id,
                            "routing": {"sender": agent_type or "main",
                                        "target": "@" + to, "summary": summary}})
    if agent_id:
        d["agent_id"] = agent_id
        d["agent_type"] = agent_type or "teammate"
    return d


def post_skill(s, skill="slack", args="read https://slack/archives/p178",
               agent_id=None, success=True, event="PostToolUse",
               tid="toolu_sk1"):
    """PostToolUse(Skill) — shape per the live capture 2026-07-27 (2.1.220, 294 of
    them in the audit): tool_input {skill, args} and tool_response {success,
    commandName, allowedTools}. Note what is NOT there: the skill's BODY. Claude Code
    injects the loaded SKILL.md into the conversation as a user-shaped turn, so the
    args are the only content a mirror row can put behind a click."""
    d = base(s, event, tool_name="Skill", tool_use_id=tid,
             tool_input={"skill": skill, "args": args},
             tool_response={"success": success, "commandName": skill,
                            "allowedTools": ["Bash"]})
    if agent_id:
        d["agent_id"] = agent_id
    return d


def post_tool(s, tool="WebFetch", ti=None, tr=None, agent_id=None,
              event="PostToolUse", tid="toolu_wf1"):
    """PostToolUse(<any tool with no formatter of its own)> — the generic family
    the exclusion matcher routes to tool_fmt.py.

    Shapes per the live audit (measured 2026-07-31 over 474k hook_events): every
    one of them carries a FLAT dict on both sides — WebFetch
    `{url, prompt}` → `{bytes, code, codeText, result, durationMs, url}`,
    WebSearch `{query}` → `{query, results, durationSeconds, searchCount}`,
    ToolSearch `{query, max_results}` → `{matches, query,
    total_deferred_tools}` — and the request's FIRST field is the one that names
    it, which is what the one-liner shows."""
    if ti is None:
        ti = {"url": "https://docs.claude.com/en/docs/claude-code/hooks",
              "prompt": "find the updatedInput contract"}
    if tr is None:
        tr = {"bytes": 807, "code": 200, "codeText": "OK",
              "result": "The PreToolUse hook may return updatedInput.",
              "durationMs": 1533,
              "url": "https://docs.claude.com/en/docs/claude-code/hooks"}
    d = base(s, event, tool_name=tool, tool_use_id=tid,
             tool_input=ti, tool_response=tr)
    if agent_id:
        d["agent_id"] = agent_id
    return d


def notification(s, message="Claude needs your permission to use Bash"):
    return base(s, "Notification", message=message)


def pre_ask(s, questions, tid="toolu_ask1", agent_id=None):
    """PreToolUse(AskUserQuestion) — shape per the live capture 2026-07-18:
    tool_input carries `questions` [{question, header, options[{label,
    description}], multiSelect}], and tool_use_id keys the pending stash."""
    d = base(s, "PreToolUse", tool_name="AskUserQuestion", tool_use_id=tid,
             tool_input={"questions": questions})
    if agent_id:
        d["agent_id"] = agent_id
    return d


def post_ask(s, questions, answers, tid="toolu_ask1"):
    """PostToolUse(AskUserQuestion) — fires ONLY on a real submit (declines
    fire nothing); tool_input gains `answers` {question: label|", "-joined|
    free text} + `annotations`."""
    ti = {"questions": questions, "answers": answers, "annotations": {}}
    return base(s, "PostToolUse", tool_name="AskUserQuestion",
                tool_use_id=tid, tool_input=ti,
                tool_response={"questions": questions, "answers": answers})


def stop(s, failure=False):
    return base(s, "StopFailure" if failure else "Stop")


def session_end(s, reason="other"):
    return base(s, "SessionEnd", reason=reason)


# --- OpenTelemetry OTLP/JSON metrics (the OTEL cost pipeline, plugins/otel/) -----

def _otlp_dp(attrs, val):
    a = [{"key": k, "value": ({"stringValue": v} if isinstance(v, str)
                              else {"intValue": v})} for k, v in attrs.items()]
    return {"attributes": a,
            ("asInt" if isinstance(val, int) else "asDouble"): val}


def otlp_metrics(sid, tokens=(), costs=()):
    """An OTLP/JSON ExportMetricsServiceRequest body Claude Code would POST to the
    receiver. `tokens` = [(query_source, type, value), …] for claude_code.token.usage;
    `costs` = [(query_source, usd), …] for claude_code.cost.usage."""
    metrics = []
    if tokens:
        metrics.append({"name": "claude_code.token.usage", "sum": {"dataPoints": [
            _otlp_dp({"session.id": sid, "query_source": qs, "type": t}, v)
            for qs, t, v in tokens]}})
    if costs:
        metrics.append({"name": "claude_code.cost.usage", "sum": {"dataPoints": [
            _otlp_dp({"session.id": sid, "query_source": qs}, v)
            for qs, v in costs]}})
    return {"resourceMetrics": [{"scopeMetrics": [{"metrics": metrics}]}]}
