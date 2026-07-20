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


def monitors(sid):
    """The monitors read-model provider (plugins.monitors fan-out) — every
    Monitor tool run of a session, its command/description/lifetime and events,
    merging the MAIN transcript with the audit streams state. See
    transcript.session_monitors."""
    from plugins.claude_code import transcript
    return transcript.session_monitors(sid)


def session_title(transcript_path):
    """The session-title provider (plugins.session_title fan-out) — the head
    summary record / first real prompt of a Claude transcript. See
    transcript.session_title."""
    from plugins.claude_code import transcript
    return transcript.session_title(transcript_path)


def set_session_title(transcript_path, name):
    """The session-rename provider (plugins.set_session_title fan-out) — append
    the `agent-name` naming record to a Claude session transcript; None for
    files this plugin doesn't own (e.g. a codex rollout). See
    transcript.set_session_title."""
    from plugins.claude_code import transcript
    return transcript.set_session_title(transcript_path, name)


def context(transcript_path, main=False):
    """The context-saturation provider (plugins.context fan-out) — the last
    assistant record's usage in a Claude transcript's tail, as {used, window,
    pct, model}; None for files this parser doesn't speak. See
    transcript.context_probe."""
    from plugins.claude_code import transcript
    return transcript.context_probe(transcript_path, main=main)


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


def config_dirs(cwd):
    """The config-dir provider (plugins.config_dirs fan-out): every `.claude`
    directory that applies to `cwd`, nearest-first, ending at the user config
    dir — model.claude_dirs with env_pin=False, because the caller resolves
    ARBITRARY sessions' cwds (same reasoning as slash_commands). Consumers
    layer their own per-project files over these dirs; the walk itself stays
    owned by model.py."""
    from plugins.claude_code import model
    return model.claude_dirs(cwd, env_pin=False)


def effort_default(cwd, slug=""):
    """The saved-effort provider (plugins.effort_default fan-out) — the merged
    settings' `effortLevel` resolved for the session's cwd AND account (`slug`
    → that account's config dir; each subscription account has its own
    settings.json). The TUI persists every `/effort <level>` there
    (docs/dashboard.md, *Web quick commands*), so this tracks the last applied
    effort; a session-only override isn't readable anywhere (see model.py's
    header). None when unset."""
    from plugins.claude_code import account, model
    return model.settings_field("effortLevel", start=cwd or None,
                                env_pin=False,
                                config=account.config_dir_for(slug)) or None


def accounts():
    """The account-registry provider (plugins.accounts fan-out) — the plain
    default plus the switcher's accounts.tsv rows. See account.registry."""
    from plugins.claude_code import account
    return account.registry()


def account_alias(slug):
    """The account-validation provider (plugins.account_alias fan-out) — a
    chosen slug → its launch command word, or None if unknown. See
    account.alias_for."""
    from plugins.claude_code import account
    return account.alias_for(slug)


def model_windows(cache=None):
    """The per-model weekly-usage provider (plugins.model_windows fan-out) —
    {slug: {seven_day_<model>: used%, …_reset: epoch}} from the OAuth /usage
    endpoint (the caps the tokenless status-line can't see). See
    model_usage.windows_by_slug."""
    from plugins.claude_code import model_usage
    return model_usage.windows_by_slug(cache=cache)


def launch_argv(words, cmd="claude"):
    """The launch-shell provider (plugins.launch_argv fan-out) — the argv that
    runs an account's launch word through the user's interactive login shell.
    See account.launch_argv (the owner; the dashboard's web launch and the
    rate-limit migration both compose their tab launches through it)."""
    from plugins.claude_code import account
    return account.launch_argv(words, cmd)


def migration_target(cur_slug, cur_model, manual=False):
    """The migration-target provider (plugins.migration_target fan-out) — see
    account.pick_target, the owner of the model-downgrade ladder
    (docs/relimit.md). Both the automatic rate-limit path and the manual
    (web-button) migrate run the SAME ladder from `cur_model`; a manual migrate
    only relaxes the % headroom ceiling (an explicit click outranks the refuge
    rule)."""
    from plugins.claude_code import account
    return account.pick_target(
        cur_slug, cur_model, ceiling=None if manual else account.TARGET_MAX_PCT)
