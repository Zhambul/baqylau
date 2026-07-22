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


def activity_since(sid, agent_id, pos):
    """LIVE drill-down fan-out (docs/dashboard.md): the incremental companion
    to activity() — the first plugin that recognizes (sid, agent_id) returns
    (entries, resolutions, new_pos) from byte cursor `pos`; None when none
    does. claude_code: plugins/claude_code/transcript.timeline_since over the
    agent's (or, with agent_id=None, the session's main) transcript. codex has
    NO incremental provider yet (its rollout renderer lacks the parse split),
    so a codex run's drill-down stays fetch-once — the fan-out simply finds no
    activity_since on that plugin and moves on. Same exception contract as
    activity(): the callers are read-side tools, not hooks."""
    for p in all_plugins():
        fn = getattr(p, "activity_since", None)
        if fn is None:
            continue
        got = fn(sid, agent_id, pos)
        if got is not None:
            return got
    return None


def monitors(sid):
    """Monitors read-model fan-out (docs/dashboard.md, *Monitors tab*): the first
    plugin that recognizes `sid` returns the list of its Monitor tool runs
    (command/description/lifetime + events, merging transcript + audit streams
    state); None when none does. claude_code:
    plugins/claude_code/transcript.session_monitors. codex has no monitors (the
    Monitor tool is a Claude Code concept), so the fan-out finds no provider on
    it and moves on. Same exception contract as activity(): the callers are
    read-side tools, not hooks."""
    for p in all_plugins():
        fn = getattr(p, "monitors", None)
        if fn is None:
            continue
        got = fn(sid)
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


def title_and_rename(transcript_path):
    """(title, tail_rename) fan-out (path-keyed like session_title): the display
    title plus the `agent-name` /rename record STILL inside the transcript's
    title tail-window ('' when the rename scrolled out, or was never set). The
    first plugin that RECOGNIZES the file answers; ('', '') when none does. The
    dashboard reconciles its durable web-rename override against tail_rename so a
    rename that fell out of the 64KB tail no longer 'rolls back' to the auto
    ai-title (docs/dashboard.md, *Web rename*)."""
    for p in all_plugins():
        fn = getattr(p, "title_and_rename", None)
        if fn is None:
            continue
        title, named = fn(transcript_path)
        if title or named:
            return title, named
    return "", ""


def set_session_title(transcript_path, name):
    """Session-rename fan-out (path-keyed like session_title — the write half
    of that read): the first plugin that OWNS the file appends its naming
    record and returns True; None when no plugin recognizes the path (the
    dashboard then 409s — e.g. a codex rollout, which must never receive a
    Claude `agent-name` record). Exceptions (OSError from the append)
    propagate — the caller is the dashboard's control plane, not a hook."""
    for p in all_plugins():
        fn = getattr(p, "set_session_title", None)
        if fn is None:
            continue
        got = fn(transcript_path, name)
        if got is not None:
            return got
    return None


def accounts():
    """The launchable subscription accounts for the dashboard's new-session
    picker (plugins.claude_code.account.registry): one entry per switcher
    account, [{slug, label, alias}, …] (no synthetic default — the plain-claude
    login duplicates one of these). Concatenated across plugins,
    first slug wins (claude_code is the only provider). Same exception contract
    as census()/activity(): the caller is the read-side dashboard, not a hook."""
    out, seen = [], set()
    for p in all_plugins():
        fn = getattr(p, "accounts", None)
        if fn is None:
            continue
        for a in fn() or []:
            if a.get("slug") not in seen:
                seen.add(a.get("slug"))
                out.append(a)
    return out


def model_windows(cache=None):
    """Per-account, per-MODEL weekly usage windows for the dashboard's usage
    strip: {slug: {seven_day_<model>: used%, …_reset: epoch}}, merged across
    plugins (a slug's dicts combine; first value wins on a key clash). These are
    the caps the tokenless status-line can't see (the /usage OAuth endpoint —
    plugins.claude_code.model_usage.windows_by_slug); the dashboard layers them
    onto account_usage's tokenless snapshot. Same read-side exception contract
    as accounts(); {} when no plugin provides them / the feature is off."""
    out = {}
    for p in all_plugins():
        fn = getattr(p, "model_windows", None)
        if fn is None:
            continue
        for slug, wins in (fn(cache=cache) or {}).items():
            dst = out.setdefault(slug, {})
            for k, v in (wins or {}).items():
                dst.setdefault(k, v)
    return out


def account_alias(slug):
    """Validate a chosen account slug → its launch command word, or None when
    unknown (the dashboard then 400s). First plugin that recognizes the slug
    wins. See plugins.claude_code.account.alias_for."""
    for p in all_plugins():
        fn = getattr(p, "account_alias", None)
        if fn is None:
            continue
        got = fn(slug)
        if got is not None:
            return got
    return None


def migration_target(cur_slug, cur_model, manual=False):
    """The account-migration target for a rate-limited session leaving
    `cur_slug` while running `cur_model` (a model.family word)
    (plugins.claude_code.account.pick_target, docs/relimit.md *Model-downgrade
    ladder*): the best-headroom account for the highest model on the
    fable→opus→sonnet ladder that any account can still serve, or None when
    nothing qualifies. Returns {"slug","alias","model","eff"} — `model` is the
    chosen family (the caller downgrades only when it differs from `cur_model`).
    manual=True is the dashboard's ⇆ migrate button — it drops the 90% refuge
    ceiling (an explicit click outranks the refuge rule); it runs the SAME
    ladder (model-scoped limit-hits are handled per-rung, not waved through).
    First plugin that recognizes the request wins. Same exception contract as
    census()/activity(): the caller is the dashboard's control plane, not a
    hook."""
    for p in all_plugins():
        fn = getattr(p, "migration_target", None)
        if fn is None:
            continue
        got = fn(cur_slug, cur_model, manual)
        if got is not None:
            return got
    return None


def launch_argv(words, cmd="claude"):
    """The argv that launches a session command in a fresh terminal tab, via
    the user's interactive login shell (the dashboard's web launch — see
    plugins.claude_code.account.launch_argv, the owner). First plugin that
    provides one wins; the bare command as a last resort (a frontend exec'ing
    it directly loses aliases/PATH, but nothing better exists without a
    provider)."""
    for p in all_plugins():
        fn = getattr(p, "launch_argv", None)
        if fn is None:
            continue
        got = fn(words, cmd)
        if got is not None:
            return got
    return [cmd, *words]


def slash_commands(cwd):
    """Slash-command fan-out for the web composer's "/" menu (cwd-keyed like
    session_title is path-keyed — the caller already holds the session's cwd):
    concatenates every plugin's [{name, desc, src}, …], first occurrence of a
    name wins (claude_code is the only provider today). Same exception
    contract as census()/activity(): the caller is the read-side dashboard,
    not a hook."""
    out, seen = [], set()
    for p in all_plugins():
        fn = getattr(p, "slash_commands", None)
        if fn is None:
            continue
        for c in fn(cwd) or []:
            if c.get("name") not in seen:
                seen.add(c.get("name"))
                out.append(c)
    return out


def config_dirs(cwd):
    """Config-dir fan-out (cwd-keyed like slash_commands): every plugin's
    "directories holding project-level config for this cwd", nearest-first,
    order preserved across plugins, dedup. Consumers layer their own files
    over these — the dashboard's per-project dictation keyterms rides it
    (docs/dashboard.md *Web dictation*). Same exception contract as
    census()/activity(): the caller is the read-side dashboard, not a hook."""
    out, seen = [], set()
    for p in all_plugins():
        fn = getattr(p, "config_dirs", None)
        if fn is None:
            continue
        for d in fn(cwd) or []:
            if d not in seen:
                seen.add(d)
                out.append(d)
    return out


def effort_default(cwd, slug=""):
    """Saved-effort fan-out (cwd-keyed like slash_commands — the caller
    already holds the session's cwd; `slug` is the session's stashed
    subscription-account slug, resolving WHICH user-level settings apply):
    the first plugin that knows a saved effort level returns it
    ("low"…"max"); "" when none does. Backs the dashboard's effort
    quick-button label: per-session effort is readable from no transcript,
    but every `/effort <level>` saves itself as the settings default, so the
    saved value IS the last applied one. Same exception contract as
    census()/activity(): the caller is the read-side dashboard, not a hook."""
    for p in all_plugins():
        fn = getattr(p, "effort_default", None)
        if fn is None:
            continue
        got = fn(cwd, slug)
        if got:
            return got
    return ""


def context(transcript_path, main=False):
    """Context-saturation fan-out (path-keyed like session_title — the
    dashboard's rows already hold each transcript path): the first plugin that
    recognizes the file returns {"used", "window", "pct", "model"} for its
    most recent turn — how full the context window is; None when no plugin
    does (a fresh transcript, a codex rollout — no codex provider yet).
    main=True marks a HOST session's main transcript (the claude_code provider
    skips sidechain records there). Same exception contract as
    census()/activity(): the callers are read-side dashboards, not hooks."""
    for p in all_plugins():
        fn = getattr(p, "context", None)
        if fn is None:
            continue
        got = fn(transcript_path, main)
        if got is not None:
            return got
    return None


def goal(transcript_path):
    """Active-`/goal` fan-out (path-keyed like context — the dashboard's rows
    already hold each transcript path): the first plugin that recognizes the
    file returns {"condition", "met"} for the session's pending autonomous goal
    (Claude Code's `/goal` built-in), or None when there's no active goal / no
    plugin speaks the file. Read-side like context() (no hook fires for /goal),
    same exception contract as census()/activity(): the callers are read-side
    dashboards, not hooks."""
    for p in all_plugins():
        fn = getattr(p, "goal", None)
        if fn is None:
            continue
        got = fn(transcript_path)
        if got is not None:
            return got
    return None


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


def ask_preamble(sid, tool_use_id):
    """Claude's prose lead-in to a pending AskUserQuestion (the text framing the
    question, shown on the dashboard's ask card): the string from the first
    plugin that recognizes the sid, None otherwise. "" when the plugin owns the
    sid but found no prose. Same exception contract as conversation()."""
    for p in all_plugins():
        fn = getattr(p, "ask_preamble", None)
        if fn is None:
            continue
        got = fn(sid, tool_use_id)
        if got is not None:
            return got
    return None
