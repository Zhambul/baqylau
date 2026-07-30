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
# (claude_code 23 of 26, codex 12 of 26, otel 1 of 26) and the fan-outs below skip the rest —
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
    "owns": 1,               # (path)               — is this file ours to read?
    #                          The gate on every PATH-KEYED row below (see _owns)
    "host": 0,               # ()                   — the HostControl adapter
    #                          (hosts/host_named/host_of — the control interface)
    "session_title": 1,      # (transcript_path)    — the display title
    "title_and_rename": 1,   # (transcript_path)    — title + the tail rename
    "renameable": 1,         # (transcript_path)    — can it be /rename'd?
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
    "model_fallback": 2,     # (transcript_path, pos) — the refusal-fallback scan
    "prompts": 1,            # (transcript_path)    — human prompts, capped
    "conversation": 3,       # (sid, pos, agent_id) — ONE identity's records
    "ask_preamble": 2,       # (sid, tool_use_id)   — the ask card's preamble
    "pending_dialog": 1,     # (sid)                — a host's OPEN modal (ask)
    "usage_windows": 0,      # ()                   — a host's rate-limit windows
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


def _first(method, *args, default=None, truthy=False, accept=None, skip=None,
           **kwargs):
    """FIRST-plugin-wins fan-out primitive: iterate all_plugins(), skip those
    missing `method`, call it, and return the first usable answer; `default`
    when none does. Exceptions propagate (the fan-out callers are read-side
    tools, not hooks); the per-function docstrings own the exact contract.

    `skip(plugin)` drops a plugin before it is even asked — the door
    _first_path() below hangs the OWNERSHIP gate on; None asks everyone.

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
        if skip is not None and skip(p):
            continue
        fn = provider(p, method)
        if fn is None:
            continue
        got = fn(*args, **kwargs)
        if ok(got):
            return got
    return default


def _owns(plugin, path):
    """May `plugin` be asked about `path` at all? True when it DECLARES `owns`
    and claims the file — and also true when it declares NO `owns`, because a
    plugin that has never said which files are its own must keep being asked
    exactly as before (that is what makes the gate below a no-op for everyone
    but the plugin that opted in)."""
    fn = provider(plugin, "owns")
    return True if fn is None else bool(fn(path))


def _first_path(method, path, *args, **kwargs):
    """The PATH-KEYED fan-out primitive: _first(), except a plugin that
    declares `owns` is only asked about a path it OWNS.

    Without the gate, first-plugin-wins means first-PARSER-wins, and a parser
    fed a file from another tool does not fail — it answers. Measured: the
    dashboard's ⊜ compact gate asked `prompts()` about a 429KB CODEX rollout
    and got 8, because prompt_count's over-PROMPT_SCAN_B fast path returns the
    cap for any large file (fail-open by design, and correct — for a Claude
    transcript). The size of an unrelated file decided a Claude-shaped answer.

    The gate is deliberately OPT-IN per plugin rather than "answer only what
    you own": every one of these providers already returns its empty default
    for a file it can't parse, so demanding an `owns` from all of them would be
    a second, redundant statement of the same fact — and a plugin that shipped
    without one would go silent instead of degrading. What ownership buys is
    the case where a parser CAN'T tell: a bounded read, a byte prefilter, a
    fast path over a size limit. Those answer confidently about files they have
    never read, and only a question about the file's SHAPE — its layout, or its
    first record — can tell them apart (plugins/claude_code/transcript.owns)."""
    return _first(method, path, *args, skip=lambda p: not _owns(p, path),
                  **kwargs)


def owns_by(path):
    """WHICH tool owns `path` — the plugin's short name ("claude_code" for a
    Claude transcript, "codex" for a codex rollout), or None when no plugin
    claims it (an unclaimed file — the honest answer, not a wrong one).

    The read fan-outs never need this — they route through _first_path, which
    asks the question per plugin — but a CONTROL-plane caller does: the
    dashboard's resume relaunch builds `claude --resume <sid>`, an argv that is
    only meaningful for a session claude_code owns (docs/dashboard.md *Resume &
    send*). First claim wins, host first, and the corpus test pins that no two
    plugins claim the same file."""
    for p in all_plugins():
        fn = provider(p, "owns")
        if fn is not None and fn(path):
            return p.__name__.rsplit(".", 1)[-1]
    return None


def hosts():
    """The registered HOST tools, for the future new-session tool picker:
    [{name, label, launchable}, …], host first. A plugin is a HOST iff it
    provides `host` (a plugins.host.HostControl adapter); claude_code is the
    only one today, codex's arrives with its own `owns` in a later phase. Same
    read-side exception contract as accounts()."""
    out = []
    for p in all_plugins():
        fn = provider(p, "host")
        if fn is None:
            continue
        h = fn()
        if h is None:
            continue
        out.append({"name": h.name, "label": h.label,
                    "launchable": bool(h.launchable)})
    return out


def host_named(name):
    """The HostControl object of the plugin whose short name is `name`, or None
    (an unknown tool, or a plugin that isn't a host). The one door that turns a
    tool NAME — what owns_by returns — into its control adapter."""
    if not name:
        return None
    for p in all_plugins():
        if p.__name__.rsplit(".", 1)[-1] != name:
            continue
        fn = provider(p, "host")
        return fn() if fn is not None else None
    return None


def host_of(path):
    """The HostControl that OWNS `path` (via owns_by), or None when no plugin
    claims it — an empty/unknown path, or a codex rollout today (codex declares
    no `owns` yet, so "unclaimed" is the honest answer, exactly as for owns_by).
    The CONTROL-plane twin of owns_by: owns_by names the owner, this hands back
    the object that can drive it."""
    return host_named(owns_by(path))


def host_caps(name):
    """The DERIVED capability map {gesture: bool} of the plugin host named
    `name`, or {} when it has none (an unknown tool). The registry-root door
    the dashboard reads instead of touching plugins.host directly — so the
    layering rule (dashboard → registry root, never plugins.<tool>) holds and a
    missing host degrades to every cap absent (the client greys, the guard
    409s)."""
    from plugins.host import caps_of
    return caps_of(host_named(name))


def host_for(sid):
    """The HostControl for a session id — resolves its transcript path through
    the read-side session API first, then host_of. None when the sid is unknown
    or its owner declares no host. Lazy import of core.sessionapi (a read-side
    dependency, not a hook path)."""
    from core import sessionapi as API
    tpath = (API.session_row(sid) or {}).get("transcript_path") or ""
    return host_of(tpath)


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
    file. Same exception contract as census()/activity().

    Path-keyed means OWNERSHIP-GATED (_first_path): a plugin that declares
    `owns` is asked only about its own files."""
    return _first_path("session_title", transcript_path, default="", truthy=True)


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
    recognized the file iff it produced at least one of the two names.
    Ownership-gated like session_title (_first_path)."""
    return _first_path("title_and_rename", transcript_path,
                       default=("", ""), accept=any)


def renameable(transcript_path):
    """Rename-ownership fan-out (path-keyed like session_title): True when some
    plugin owns this transcript as a renameable session — the gate the
    dashboard's LIVE rename asks BEFORE typing `/rename` into the window, since
    a codex standalone host's window carries the same `claude_session` tag
    while its transcript_path is a rollout (it would receive a command it has
    no idea about). The parked path's gate is set_session_title's own None
    return, which shares this predicate. The narrow, RENAME-shaped twin of
    owns(): a plugin may own a file it cannot rename, so the two stay separate
    rows (claude_code's renameable IS its owns today — see transcript.owns)."""
    return _first_path("renameable", transcript_path, default=False, truthy=True)


def set_session_title(transcript_path, name):
    """Session-rename fan-out (path-keyed like session_title — the write half
    of that read): the first plugin that OWNS the file appends its naming
    record and returns True; None when no plugin recognizes the path (the
    dashboard then 409s — e.g. a codex rollout, which must never receive a
    Claude `agent-name` record). Exceptions (OSError from the append)
    propagate — the caller is the dashboard's control plane, not a hook.

    The PARKED half of the rename only: a live session is renamed through
    Claude Code's own `/rename`, which owns the in-memory title this record
    would otherwise be overwritten by (docs/session-naming-findings.md §4).
    Ownership-gated (_first_path) on top of the provider's own predicate — belt
    and braces, because this is the one path-keyed fan-out that WRITES."""
    return _first_path("set_session_title", transcript_path, name)


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


def _named(method, name):
    """The `method` provider FUNCTION of the plugin whose short name is `name`,
    or None (unknown tool / plugin lacks the method). The single-plugin twin of
    host_named — it turns a tool NAME into one of its providers, for a fan-out
    that must route to exactly ONE host rather than concatenate."""
    if not name:
        return None
    for p in all_plugins():
        if p.__name__.rsplit(".", 1)[-1] == name:
            return provider(p, method)
    return None


def slash_commands(cwd, host=None):
    """Slash-command fan-out for the web composer's "/" menu — HOST-SCOPED, not
    concatenated: a session belongs to exactly ONE host, so it is offered that
    host's vocabulary (a codex session shows /plan, /approvals, …; a Claude one
    its own /goal, /rewind, …). `host` is the OWNING tool's short name (from
    owns_by); None defaults to the DEFAULT host (claude_code) — the new-session
    form has no session to own it yet and launches Claude today. An unknown
    host, or one with no slash_commands provider, yields [] (an empty menu is
    the honest answer for a tool with no vocabulary — never another tool's).
    Same read-side exception contract as census()/activity()."""
    fn = _named("slash_commands", host or "claude_code")
    return list(fn(cwd) or []) if fn is not None else []


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
    census()/activity(): the callers are read-side dashboards, not hooks.
    Ownership-gated like session_title (_first_path)."""
    return _first_path("context", transcript_path, main)


def goal(transcript_path):
    """Active-`/goal` fan-out (path-keyed like context — the dashboard's rows
    already hold each transcript path): the first plugin that recognizes the
    file returns {"condition", "met"} for the session's pending autonomous goal
    (Claude Code's `/goal` built-in), or None when there's no active goal / no
    plugin speaks the file. Read-side like context() (no hook fires for /goal),
    same exception contract as census()/activity(): the callers are read-side
    dashboards, not hooks. Ownership-gated like session_title (_first_path)."""
    return _first_path("goal", transcript_path)


def model_fallback(transcript_path, pos=0):
    """Model-refusal-fallback fan-out (path-keyed like context/goal): the
    first plugin that speaks the file scans it FORWARD from byte `pos` and
    returns (last `model_refusal_fallback` record | None, new_pos) — a
    safeguard refusal rerouted the session to a fallback model (docs/
    dashboard.md *Model fallback warning*). (None, pos) when no plugin does
    (a codex rollout has no provider). The caller keeps the position
    checkpoint (dashboard/read/meta.session_fallback) — the record is written
    once mid-file, so a bounded tail probe would miss it. Read-side like
    context()/goal(): no hook fires for the fallback."""
    return _first("model_fallback", transcript_path, pos, default=(None, pos))


def prompts(transcript_path):
    """Human-prompt-count fan-out (path-keyed like context/goal): the first
    plugin that finds prompts in the file returns how many the USER typed,
    capped at a handful; None when no plugin does — a file no parser speaks, or
    one with nothing in it yet. Backs the dashboard's ⊜ compact gate, which is
    why the None means "don't conclude anything" rather than zero: the count
    only ever argues for disabling a button. Same exception contract as
    census()/activity(): the callers are read-side dashboards, not hooks.

    The fan-out ownership gate (_first_path) was written FOR this one: a
    parser's fail-open answer about a file that isn't its own is indistinguish-
    able from a real count, and a 429KB codex rollout measured 8 prompts."""
    return _first_path("prompts", transcript_path)


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


def pending_dialog(sid):
    """A host's OPEN modal dialog for the web question/plan card — the first
    plugin that recognizes the sid returns {"kind", "tool_use_id", …}, None
    otherwise. The Claude ask/plan dialogs ride a hook-stashed kv
    (dashboard/read/session.ask_pending), so claude_code exposes no provider
    here; codex has no such hook (docs/codex.md), so it derives the pending
    request_user_input READ-side from the rollout tail. Read-side like
    conversation(); same exception contract as census()."""
    return _first("pending_dialog", sid)


def usage_windows():
    """A host's own account rate-limit windows — {planType, windows:[{used_pct,
    window_mins, resets_at}]} from the first plugin that has them, None otherwise.
    Claude's per-account caps ride the status-line/model_windows path; codex has
    no status line, so it reads them off `codex app-server`
    account/rateLimits/read (plugins.codex.usage). Read-side; same exception
    contract as accounts() (the caller is the read-side dashboard, not a hook)."""
    return _first("usage_windows")
