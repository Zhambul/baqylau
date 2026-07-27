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


# --- the PROVIDER surface ------------------------------------------------------
# The optional functions a plugin may expose, and the arity each fan-out calls
# it with. A plugin implements as many as it has something to say about
# (claude_code 19 of 20, codex 2, otel 1) and the fan-outs below skip the rest —
# which is the whole point of an optional surface, and also its hazard: a
# provider whose name is misspelled, or whose signature drifts from its
# fan-out's call, is not an error anywhere. It is simply never found, and the
# feature silently degrades to "no plugin answered" — the one failure mode a
# duck-typed registry cannot report on its own.
#
# So the surface is DECLARED. This table is what `plugins.py` promises to call;
# `tests/test_l1_contracts.py` checks it against reality in both directions:
# every name a fan-out reaches for is listed here, and every provider a plugin
# actually defines matches the listed arity. frontends/ has had this since it
# had a `Frontend` base class and a contract test — plugins are the same problem
# and had neither.
#
# `min_args` is the smallest number of positional arguments a fan-out ever
# passes; a provider may accept more only with defaults (context() takes an
# optional `main=`).
PROVIDERS = {
    "on_session_start": 3,   # (log, cwd, sid)      — attach watchers at SessionStart
    "census": 1,             # (log)                — the scoreboard ✉ row
    "agent_usage": 2,        # (sid, agent_id)      — one agent's token rollup
    "monitors": 1,           # (sid)                — the monitors read model
    "session_title": 1,      # (transcript_path)    — the display title
    "title_and_rename": 1,   # (transcript_path)    — title + the tail rename
    "set_session_title": 2,  # (transcript_path, name) — the rename write
    "accounts": 0,           # ()                   — the switcher registry
    "account_alias": 1,      # (slug)               — its launch command word
    "model_windows": 0,      # (cache=)             — per-model weekly usage
    #                          (keyword-only at the call site, hence 0)
    "migration_target": 3,   # (cur_slug, cur_model, manual) — the ⇆ picker
    "launch_argv": 2,        # (words, cmd)         — the login-shell wrapper
    "slash_commands": 1,     # (cwd)                — the "/" menu's vocabulary
    "config_dirs": 1,        # (cwd)                — the .claude/ ancestor walk
    "effort_default": 2,     # (cwd, slug)          — the resolved effort level
    "context": 1,            # (transcript_path)    — ctx saturation
    "goal": 1,               # (transcript_path)    — the active /goal
    "prompts": 1,            # (transcript_path)    — human prompts, capped
    "conversation": 3,       # (sid, pos, agent_id) — ONE identity's records
    "ask_preamble": 2,       # (sid, tool_use_id)   — the ask card's preamble
}


def provider(plugin, method):
    """One plugin's implementation of a DECLARED provider, or None when it has
    none. The single door every fan-out goes through, so an undeclared name
    cannot be reached by accident — a typo in a fan-out raises here instead of
    silently finding nothing on every plugin, which is exactly the failure the
    PROVIDERS table exists to convert into noise."""
    if method not in PROVIDERS:
        raise KeyError("undeclared plugin provider: %r (see PROVIDERS)" % method)
    return getattr(plugin, method, None)


def _not_none(got):
    return got is not None


def _first(method, *args, default=None, truthy=False, accept=None, **kwargs):
    """FIRST-plugin-wins fan-out primitive: iterate all_plugins(), skip those
    missing `method`, call it, and return the first usable answer; `default`
    when none does. Exceptions propagate (the fan-out callers are read-side
    tools, not hooks); the per-function docstrings own the exact contract.

    What counts as "usable" is the one thing that varies, so it is a parameter:
      `truthy=False` (the norm)  the first result that is `not None` — '' / []
                                 / () ARE answers;
      `truthy=True`              the first TRUTHY result — the empty default is
                                 then never an answer;
      `accept=<pred>`            an explicit predicate, for a result shape where
                                 neither of those means what it looks like.
                                 title_and_rename returns a PAIR, and a tuple is
                                 always truthy — so its "did any plugin
                                 recognize this file" test is `any(got)`, and it
                                 hand-rolled the whole loop to say so."""
    ok = accept or (bool if truthy else _not_none)
    for p in all_plugins():
        fn = provider(p, method)
        if fn is None:
            continue
        got = fn(*args, **kwargs)
        if ok(got):
            return got
    return default


def _concat_unique(method, key, *args):
    """CONCAT-and-dedup fan-out primitive: concatenate every plugin's
    `method(*args)` (each a list, or None), preserving first-seen order and
    dropping later items whose `key(item)` already appeared. Plugins missing
    the method are skipped. Same read-side exception contract as _first()."""
    out, seen = [], set()
    for p in all_plugins():
        fn = provider(p, method)
        if fn is None:
            continue
        for item in fn(*args) or []:
            k = key(item)
            if k not in seen:
                seen.add(k)
                out.append(item)
    return out


def on_session_start(log, cwd, sid):
    """SessionStart fan-out: each plugin may attach its watchers to the
    starting host session (codex spawns its discovery watcher). A plugin
    failure is audited and never blocks the host's SessionStart — same
    hooks-must-never-fail invariant as everything else."""
    for p in all_plugins():
        fn = provider(p, "on_session_start")
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
    (parts, ops) — the census fragments for the row, and any mirror paint ops the
    tick produced (team-mail arrivals/reads). Ops, not raw events: their glyphs
    and colours are the producing plugin's vocabulary (read back by the web
    mirror's classifier), so the pane renderer only emits what it is handed.
    Exceptions propagate — the one caller (claude-scorebar.py) already wraps each
    tick in an audited try/except, and swallowing here would hide which provider
    froze the row."""
    parts, ops = [], []
    for p in all_plugins():
        fn = provider(p, "census")
        if fn is None:
            continue
        ps, o = fn(log)
        parts += list(ps)
        ops += list(o)
    return parts, ops


def agent_usage(sid, agent_id):
    """Per-agent usage fan-out (docs/dashboard.md *Agent scope*): the first
    plugin that recognizes (sid, agent_id) returns that agent's token rollup +
    model as {"model", "usage"}; None when no plugin does. claude_code folds the
    agent's transcript (transcript.agent_usage); codex deliberately declines —
    a run's tokens are folded from its rollout and priced at its footer, so
    there is nothing for the web to re-price. Exceptions propagate, same
    contract as census(): the caller is the read-side dashboard, not a hook, and
    swallowing here would hide which provider broke."""
    return _first("agent_usage", sid, agent_id)


def monitors(sid):
    """Monitors read-model fan-out (docs/dashboard.md, *Monitors tab*): the first
    plugin that recognizes `sid` returns the list of its Monitor tool runs
    (command/description/lifetime + events, merging transcript + audit streams
    state); None when none does. claude_code:
    plugins/claude_code/transcript.session_monitors. codex has no monitors (the
    Monitor tool is a Claude Code concept), so the fan-out finds no provider on
    it and moves on. Same exception contract as activity(): the callers are
    read-side tools, not hooks."""
    return _first("monitors", sid)


def session_title(transcript_path):
    """Display title for a session, resolved from its transcript/rollout path
    (path-keyed, unlike the sid-keyed fan-outs: the dashboard's list view
    already holds each row's path — 50 session_row() round-trips per poll
    would be waste). First non-empty wins; '' when no plugin recognizes the
    file. Same exception contract as census()/activity()."""
    return _first("session_title", transcript_path, default="", truthy=True)


def title_and_rename(transcript_path):
    """(title, tail_rename) fan-out (path-keyed like session_title): the display
    title plus the `agent-name` /rename record STILL inside the transcript's
    title tail-window ('' when the rename scrolled out, or was never set). The
    first plugin that RECOGNIZES the file answers; ('', '') when none does. The
    dashboard reconciles its durable web-rename override against tail_rename so a
    rename that fell out of the 64KB tail no longer 'rolls back' to the auto
    ai-title (docs/dashboard.md, *Web rename*).

    The one fan-out with an explicit `accept`: the result is a PAIR, and every
    tuple is truthy, so `truthy=True` would accept ('', '') from the first
    plugin asked and never reach the second. `any` is the real test — a plugin
    recognized the file iff it produced at least one of the two names."""
    return _first("title_and_rename", transcript_path,
                  default=("", ""), accept=any)


def set_session_title(transcript_path, name):
    """Session-rename fan-out (path-keyed like session_title — the write half
    of that read): the first plugin that OWNS the file appends its naming
    record and returns True; None when no plugin recognizes the path (the
    dashboard then 409s — e.g. a codex rollout, which must never receive a
    Claude `agent-name` record). Exceptions (OSError from the append)
    propagate — the caller is the dashboard's control plane, not a hook."""
    return _first("set_session_title", transcript_path, name)


def accounts():
    """The launchable subscription accounts for the dashboard's new-session
    picker (plugins.claude_code.account.registry): one entry per switcher
    account, [{slug, label, alias}, …] (no synthetic default — the plain-claude
    login duplicates one of these). Concatenated across plugins,
    first slug wins (claude_code is the only provider). Same exception contract
    as census()/activity(): the caller is the read-side dashboard, not a hook."""
    return _concat_unique("accounts", lambda a: a.get("slug"))


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
        fn = provider(p, "model_windows")
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
    return _first("account_alias", slug)


def migration_target(cur_slug, cur_model, manual=False, explain=None):
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
    `explain`, when a dict, is filled with the pick's full decision trace so a
    REFUSAL is reconstructible from the audit (see account.pick_target /
    docs/relimit.md *Audit trail* — the manual twin of the automatic
    `relimit-pick` row). First plugin that recognizes the request wins. Same
    exception contract as census()/activity(): the caller is the dashboard's
    control plane, not a hook."""
    return _first("migration_target", cur_slug, cur_model, manual, explain=explain)


def launch_argv(words, cmd="claude"):
    """The argv that launches a session command in a fresh terminal tab, via
    the user's interactive login shell (the dashboard's web launch — see
    plugins.claude_code.account.launch_argv, the owner). First plugin that
    provides one wins; the bare command as a last resort (a frontend exec'ing
    it directly loses aliases/PATH, but nothing better exists without a
    provider)."""
    return _first("launch_argv", words, cmd, default=[cmd, *words])


def slash_commands(cwd):
    """Slash-command fan-out for the web composer's "/" menu (cwd-keyed like
    session_title is path-keyed — the caller already holds the session's cwd):
    concatenates every plugin's [{name, desc, src}, …], first occurrence of a
    name wins (claude_code is the only provider today). Same exception
    contract as census()/activity(): the caller is the read-side dashboard,
    not a hook."""
    return _concat_unique("slash_commands", lambda c: c.get("name"), cwd)


def config_dirs(cwd):
    """Config-dir fan-out (cwd-keyed like slash_commands): every plugin's
    "directories holding project-level config for this cwd", nearest-first,
    order preserved across plugins, dedup. Consumers layer their own files
    over these — the dashboard's per-project dictation keyterms rides it
    (docs/dashboard.md *Web dictation*). Same exception contract as
    census()/activity(): the caller is the read-side dashboard, not a hook."""
    return _concat_unique("config_dirs", lambda d: d, cwd)


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
    return _first("effort_default", cwd, slug, default="", truthy=True)


def context(transcript_path, main=False):
    """Context-saturation fan-out (path-keyed like session_title — the
    dashboard's rows already hold each transcript path): the first plugin that
    recognizes the file returns {"used", "window", "pct", "model"} for its
    most recent turn — how full the context window is; None when no plugin
    does (a fresh transcript, a codex rollout — no codex provider yet).
    main=True marks a HOST session's main transcript (the claude_code provider
    skips sidechain records there). Same exception contract as
    census()/activity(): the callers are read-side dashboards, not hooks."""
    return _first("context", transcript_path, main)


def goal(transcript_path):
    """Active-`/goal` fan-out (path-keyed like context — the dashboard's rows
    already hold each transcript path): the first plugin that recognizes the
    file returns {"condition", "met"} for the session's pending autonomous goal
    (Claude Code's `/goal` built-in), or None when there's no active goal / no
    plugin speaks the file. Read-side like context() (no hook fires for /goal),
    same exception contract as census()/activity(): the callers are read-side
    dashboards, not hooks."""
    return _first("goal", transcript_path)


def prompts(transcript_path):
    """Human-prompt-count fan-out (path-keyed like context/goal): the first
    plugin that finds prompts in the file returns how many the USER typed,
    capped at a handful; None when no plugin does — a file no parser speaks, or
    one with nothing in it yet. Backs the dashboard's ⊜ compact gate, which is
    why the None means "don't conclude anything" rather than zero: the count
    only ever argues for disabling a button. Same exception contract as
    census()/activity(): the callers are read-side dashboards, not hooks."""
    return _first("prompts", transcript_path)


def conversation(sid, pos=0, agent_id=""):
    """Conversation records from byte `pos` for the dashboard's merged mirror
    stream: (records, new_pos) from the first plugin that recognizes the sid,
    None otherwise. `agent_id` picks WHOSE — the session's own main thread by
    default, a subagent/teammate's when named — so agent scope merges prose into
    an agent's mirror through this same call (docs/dashboard.md *Agent scope*).
    Records carry the tool_use `anchor` the dashboard interleaves on. Same
    exception contract as census()."""
    return _first("conversation", sid, pos, agent_id)


def ask_preamble(sid, tool_use_id):
    """Claude's prose lead-in to a pending AskUserQuestion (the text framing the
    question, shown on the dashboard's ask card): the string from the first
    plugin that recognizes the sid, None otherwise. "" when the plugin owns the
    sid but found no prose. Same exception contract as conversation()."""
    return _first("ask_preamble", sid, tool_use_id)
