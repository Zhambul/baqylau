# plugins/ — one directory per agent tool (docs/architecture.md).
#
# A plugin is the adapter between ONE agent tool's own signals (hook payloads,
# transcripts, sidecar files, rollout logs) and the core runtime (paint ops,
# scoreboard counters, slots, tab states, audit). Plugins import core/ and
# frontends/; they never import each other. Adding support for another tool
# means adding a sibling directory here plus (if it needs them) thin entry
# scripts at the repo root — nothing in core/ changes.
#
#   claude_code/  the HOST tool: Claude Code hook handlers, streamers, tab
#                 dispatch, transcript accounting. Its session (session_id) is
#                 the key everything else renders into.
#   codex/        a SECONDARY source: discovers codex runs on disk and streams
#                 them into the hosting session's mirror.


def all_plugins():
    """The registered agent-tool plugins, host first. Adding a tool = adding
    its directory and listing it here. `otel` is not an agent tool but a
    cross-cutting subsystem (the per-machine OTLP cost receiver); it rides the
    on_session_start fan-out and exposes no census, so the getattr guards below
    skip it cleanly."""
    from plugins import claude_code, codex, otel
    return [claude_code, codex, otel]


def on_session_start(log, cwd, sid):
    """SessionStart fan-out: each plugin may attach its watchers to the
    starting host session (codex spawns its discovery watcher). A plugin
    failure is audited and never blocks the host's SessionStart — same
    hooks-must-never-fail invariant as everything else."""
    for p in all_plugins():
        fn = getattr(p, "on_session_start", None)
        if fn is None:
            continue
        try:
            fn(log, cwd, sid)
        except Exception:
            try:
                from core.noaudit import load_audit
                load_audit().error(log, "plugin on_session_start (%s)" % p.__name__)
            except Exception:
                pass


def census(log):
    """Scoreboard census fan-out (the ✉ row): concatenates every plugin's
    (parts, events). Exceptions propagate — the one caller (claude-scorebar.py)
    already wraps each tick in an audited try/except, and swallowing here would
    hide which provider froze the row."""
    parts, events = [], []
    for p in all_plugins():
        fn = getattr(p, "census", None)
        if fn is None:
            continue
        ps, ev = fn(log)
        parts += list(ps)
        events += list(ev)
    return parts, events


def activity(sid, agent_id=None):
    """Drill-down fan-out (docs/sessionapi.md): the first plugin that
    recognizes (sid, agent_id) returns its FULL-FIDELITY activity timeline;
    None when no plugin does. claude_code: plugins/claude_code/transcript.
    timeline over the agent's — or, with agent_id=None, the session's main —
    transcript. codex: plugins/codex/rollout.timeline over the run's native
    rollout (agent_id = the sessionapi.codex_aid identity; with
    agent_id=None a standalone codex session's own rollout). Exceptions
    propagate, same contract as census(): the callers are read-side tools
    (dashboards/CLIs), not hooks, and swallowing here would hide which
    provider broke."""
    for p in all_plugins():
        fn = getattr(p, "activity", None)
        if fn is None:
            continue
        got = fn(sid, agent_id)
        if got is not None:
            return got
    return None


def session_title(transcript_path):
    """Display title for a session, resolved from its transcript/rollout path
    (path-keyed, unlike the sid-keyed fan-outs: the dashboard's list view
    already holds each row's path — 50 session_row() round-trips per poll
    would be waste). First non-empty wins; '' when no plugin recognizes the
    file. Same exception contract as census()/activity()."""
    for p in all_plugins():
        fn = getattr(p, "session_title", None)
        if fn is None:
            continue
        got = fn(transcript_path)
        if got:
            return got
    return ""


def conversation(sid, pos=0):
    """Main-thread conversation records from byte `pos` for the dashboard's
    merged mirror stream: (records, new_pos) from the first plugin that
    recognizes the sid, None otherwise. Records carry the tool_use `anchor`
    the dashboard interleaves on (docs/dashboard.md). Same exception contract
    as census()/activity()."""
    for p in all_plugins():
        fn = getattr(p, "conversation", None)
        if fn is None:
            continue
        got = fn(sid, pos)
        if got is not None:
            return got
    return None
