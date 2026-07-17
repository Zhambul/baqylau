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


def activity_since(sid, agent_id, pos):
    """The LIVE drill-down provider (plugins.activity_since fan-out) —
    incremental timeline entries + cross-increment tool resolutions from byte
    cursor `pos`. See transcript.activity_since."""
    from plugins.claude_code import transcript
    return transcript.activity_since(sid, agent_id, pos)


def session_title(transcript_path):
    """The session-title provider (plugins.session_title fan-out) — the head
    summary record / first real prompt of a Claude transcript. See
    transcript.session_title."""
    from plugins.claude_code import transcript
    return transcript.session_title(transcript_path)


def conversation(sid, pos=0):
    """The main-thread conversation provider (plugins.conversation fan-out)
    for the dashboard's merged mirror stream. See transcript.conversation."""
    from plugins.claude_code import transcript
    return transcript.conversation_for(sid, pos)


def slash_commands(cwd):
    """The slash-command provider (plugins.slash_commands fan-out) — the CLI
    built-ins + the cwd's discovered .claude commands/skills, for the web
    composer's "/" menu. See slashcmds.py."""
    from plugins.claude_code import slashcmds
    return slashcmds.slash_commands(cwd)
