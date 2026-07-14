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
                 agent_type=None, error=None):
    d = base(s, "PostToolUseFailure" if failure else "PostToolUse",
             tool_name="Monitor",
             tool_input={"command": command, "description": description},
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


def notification(s, message="Claude needs your permission to use Bash"):
    return base(s, "Notification", message=message)


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
