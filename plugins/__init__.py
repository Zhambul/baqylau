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
                from core import audit as A
                A.error(log, "plugin on_session_start (%s)" % p.__name__)
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
