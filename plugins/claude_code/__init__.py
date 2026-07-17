# plugins/claude_code/ — the Claude Code adapter (the host tool).
#
# Hook handlers (cmd_pre/cmd_fmt/file_fmt/subagent_fmt/monitor_fmt/task_fmt/
# stop_fmt), the detached streamers (stream/substream), the tab-state dispatch
# (tabstatus), the pane/session lifecycle (split), and the Claude-specific
# knowledge modules: hookkit (payload harness), accounting (usage/pricing),
# tools (built-in tool payload shapes), model (model/effort/window resolution),
# msgs (agent-team message tracker). Entry scripts at the repo root are thin
# shims into these modules — the entry FILENAMES are the audit vocabulary.


def census(log):
    """The agent-team message census for the scoreboard's ✉ row — see msgs.py
    (stateful inbox polling; returns (parts, events))."""
    from plugins.claude_code import msgs
    return msgs.update_messages(log)


def activity(sid, agent_id=None):
    """The drill-down activity provider (plugins.activity fan-out) — the
    full-fidelity timeline of a session's main thread or one subagent/teammate,
    parsed from its transcript. See transcript.py."""
    from plugins.claude_code import transcript
    return transcript.activity(sid, agent_id)
