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
    (stateful inbox polling). Returns (parts, ops): the census fragments, and the
    mirror PAINT OPS for this tick's inbox transitions. The events become ops
    HERE, inside the plugin that produced them, because their glyphs and colours
    are a claude_code vocabulary the web mirror reads back (actclass's `mail`
    class) — the pane renderer that emits them stays tool-agnostic."""
    from plugins.claude_code import msgs
    parts, events = msgs.update_messages(log)
    return parts, msgs.event_ops(events, log)


def agent_usage(sid, agent_id):
    """The per-agent usage provider (plugins.agent_usage fan-out) — one
    subagent/teammate's {model, usage, cost}, folded from its transcript, which
    is what the web's agent scoreboard shows as its Σ and ≈cost. See
    transcript.session_agent_usage.

    The PRICE is folded in HERE, not by the caller. The dashboard used to hand
    whatever any plugin returned to Anthropic's PRICES table (accounting.cost_usd
    — a direct plugins.claude_code reach from the read model), so a future host
    that answered this fan-out would have had its tokens priced in Claude's
    currency without a word of code saying so. The plugin that folded the
    transcript is the one that knows its own price list; `cost` is omitted for an
    unknown/empty model and the client simply drops the ≈cost chip."""
    from plugins.claude_code import accounting, transcript
    tl = transcript.session_agent_usage(sid, agent_id)
    if not tl:
        return tl
    u = tl.get("usage") or {}
    if u:
        tl["cost"] = accounting.cost_usd(
            tl.get("model"), u.get("in", 0), u.get("out", 0), u.get("cache", 0),
            u.get("create", 0), u.get("create_1h", 0))
    return tl


def nested_owners(sid):
    """The nested-job OWNERSHIP provider (plugins.nested_owners fan-out) — who
    launched each of a session's background jobs and monitors, and the command
    behind it, recovered from Claude Code's own PostToolUse hook payloads. The
    Claude-shaped audit SQL that used to sit in core/sessionapi.py. See
    nested.nested_owners.

    NB codex exposes no twin: its dispatch writes none of these hook_events rows
    and it has no bg-job/monitor concept at all, so declining is the honest
    answer rather than an empty implementation."""
    from plugins.claude_code import nested
    return nested.nested_owners(sid)


# NB claude_code exposes no `runs` provider. That fan-out is for a host's own
# NESTED runs; a Claude subagent/teammate is not one — it is already an audit
# `streams` row of kind subagent/teammate that sessionapi.agents() reads
# first-hand, and answering here would list every agent twice.


def monitors(sid):
    """The monitors read-model provider (plugins.monitors fan-out) — every
    Monitor tool run of a session, its command/description/lifetime and events,
    merging the MAIN transcript with the audit streams state. See
    transcript.session_monitors."""
    from plugins.claude_code import transcript
    return transcript.session_monitors(sid)


def owns(path):
    """The ownership provider (plugins.owns / owns_by, and the gate every
    path-keyed fan-out applies through plugins._first_path) — True only for a
    file this plugin genuinely speaks: a Claude Code session transcript or one
    of its agent sidecars. Without it, first-plugin-wins hands a codex rollout
    to a Claude parser whose bounded fast paths answer confidently about a file
    they never read. See transcript.owns."""
    from plugins.claude_code import transcript
    return transcript.owns(path)


def host():
    """The HOST-control provider (plugins.host_named / hosts / host_of) — Claude
    Code's plugins.host.HostControl adapter, which drives every gesture (so its
    derived caps read all-True and the dashboard's _caps_guard never fires for a
    Claude session). Imports `hostctl` (NOT `host` — this provider FUNCTION
    shadows a `host` submodule for `from plugins.claude_code import host`). See
    plugins/claude_code/hostctl.py."""
    from plugins.claude_code import hostctl
    return hostctl.get()


def session_title(transcript_path):
    """The session-title provider (plugins.session_title fan-out) — the head
    summary record / first real prompt of a Claude transcript. See
    transcript.session_title."""
    from plugins.claude_code import transcript
    return transcript.session_title(transcript_path)


def title_and_rename(transcript_path):
    """The title+tail-rename provider (plugins.title_and_rename fan-out) — the
    display title AND whether an `agent-name` /rename is still in the transcript's
    title tail-window, so the dashboard can reconcile its durable web-rename
    override. See transcript.title_and_rename."""
    from plugins.claude_code import transcript
    return transcript.title_and_rename(transcript_path)


def renameable(transcript_path):
    """The rename-ownership provider (plugins.renameable fan-out) — True when
    this plugin owns the file as a Claude session transcript, i.e. the session
    can be renamed through the `/rename` channel at all. See
    transcript.renameable."""
    from plugins.claude_code import transcript
    return transcript.renameable(transcript_path)


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


def goal(transcript_path):
    """The active-`/goal` provider (plugins.goal fan-out) — the session's
    pending autonomous goal from a Claude transcript's tail, as {condition,
    met}; None for files this parser doesn't speak / no active goal. See
    transcript.goal_probe."""
    from plugins.claude_code import transcript
    return transcript.goal_probe(transcript_path)


def prompts(transcript_path):
    """The human-prompt-count provider (plugins.prompts fan-out) — how many
    prompts the user typed into a Claude transcript, capped; None for files this
    parser finds none in. Backs the dashboard's ⊜ compact gate. See
    transcript.prompt_count."""
    from plugins.claude_code import transcript
    return transcript.prompt_count(transcript_path)


def model_fallback(transcript_path, pos=0):
    """The model-refusal-fallback provider (plugins.model_fallback fan-out) —
    the LAST `model_refusal_fallback` system record at or after byte `pos` of
    a Claude transcript, as ({from, to, category, reason, ts} | None,
    new_pos). See transcript.fallback_scan."""
    from plugins.claude_code import transcript
    return transcript.fallback_scan(transcript_path, pos)


def conversation(sid, pos=0, agent_id=""):
    """The conversation provider (plugins.conversation fan-out) for the
    dashboard's merged mirror stream — ONE identity's records: the session's own
    main thread, or a subagent/teammate's when `agent_id` names one. See
    transcript.conversation_for."""
    from plugins.claude_code import transcript
    return transcript.conversation_for(sid, pos, agent_id)


def ask_preamble(sid, tool_use_id):
    """The ask-preamble provider (plugins.ask_preamble fan-out) — Claude's
    prose lead-in to a pending AskUserQuestion, for the web ask card. None when
    this plugin has no transcript for the sid. See transcript.ask_preamble."""
    from plugins.claude_code import transcript
    return transcript.ask_preamble_for(sid, tool_use_id)


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


def usage_strip(cache=None, limit=50):
    """The usage-strip provider (plugins.usage_strip fan-out) — ONE ROW PER
    SUBSCRIPTION ACCOUNT, because that is the unit Claude Code's rate limits are
    per: each row is that account's freshest status-line snapshot in the shared
    usage-window vocabulary, plus the picker's load-balancing signals and its
    limit-hit / logged-out state. See usage.usage_strip (the module that now owns
    every Anthropic window constant core/sessionapi.py used to hold)."""
    from plugins.claude_code import usage
    return usage.usage_strip(cache=cache, limit=limit)


def session_usage(sid):
    """The per-session usage provider (plugins.session_usage fan-out) — the
    session's last status-line rate-limit snapshot, flat window keys AND the
    shared `windows` vocabulary; None when the shim captured none. See
    usage.session_usage."""
    from plugins.claude_code import usage
    return usage.session_usage(sid)


def session_account(sid):
    """The per-session account provider (plugins.session_account fan-out) — the
    subscription account {slug, label} this session runs under. See
    usage.session_account."""
    from plugins.claude_code import usage
    return usage.session_account(sid)


def session_costs(sid):
    """The per-session cost provider (plugins.session_costs fan-out) — the OTEL
    token/cost totals, whose `query_source` taxonomy (main/subagent/auxiliary) is
    Claude Code's own. See usage.session_costs."""
    from plugins.claude_code import usage
    return usage.session_costs(sid)


def launch_argv(words, cmd="claude"):
    """The launch-shell provider (plugins.launch_argv fan-out) — the argv that
    runs an account's launch word through the user's interactive login shell.
    See account.launch_argv (the owner; the dashboard's web launch and the
    rate-limit migration both compose their tab launches through it)."""
    from plugins.claude_code import account
    return account.launch_argv(words, cmd)


def migration_target(cur_slug, cur_model, manual=False, explain=None):
    """The migration-target provider (plugins.migration_target fan-out) — see
    account.pick_target, the owner of the model-downgrade ladder
    (docs/relimit.md). Both the automatic rate-limit path and the manual
    (web-button) migrate run the SAME ladder from `cur_model`; a manual migrate
    only relaxes the % headroom ceiling (an explicit click outranks the refuge
    rule). `explain`, when a dict, is filled with pick_target's full decision
    trace (branch/cur_model/candidates/chosen) so a manual-migrate REFUSAL is
    reconstructible from the audit — the same trace the automatic path records
    as `relimit-pick` (docs/relimit.md *Audit trail*)."""
    from plugins.claude_code import account
    return account.pick_target(
        cur_slug, cur_model, ceiling=None if manual else account.TARGET_MAX_PCT,
        explain=explain)
