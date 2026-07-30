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
# it with. A plugin implements as many as it has something to say about, and the
# fan-outs below skip the rest —
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
# WHICH plugin answers which name is the second half, and it is declared too:
# `tests/test_l1i_host_contract.py`'s coverage MATRIX pins implemented-vs-
# DECLINED per (provider, host), so a decline is a written-down decision with a
# reason rather than an absence nobody notices. A running count in this comment
# used to serve that purpose and was already stale.
#
# `min_args` is the smallest number of positional arguments a fan-out ever
# passes; a provider may accept more only with defaults (context() takes an
# optional `main=`).
PROVIDERS = {
    "on_session_start": 3,   # (log, cwd, sid)      — attach watchers at SessionStart
    "census": 1,             # (log)                — the scoreboard ✉ row
    "agent_usage": 2,        # (sid, agent_id)      — one agent's token rollup
    "runs": 1,               # (sid)                — this host's NESTED runs,
    #                          spliced into sessionapi.agents()
    "nested_owners": 1,      # (sid)                — who launched each bg job /
    #                          monitor, from this host's launch-hook payloads
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
    "effort": 1,             # (transcript_path)    — the session's current effort
    "context": 1,            # (transcript_path)    — ctx saturation
    "goal": 1,               # (transcript_path)    — the active /goal
    "model_fallback": 2,     # (transcript_path, pos) — the refusal-fallback scan
    "prompts": 1,            # (transcript_path)    — human prompts, capped
    "conversation": 3,       # (sid, pos, agent_id) — ONE identity's records
    "ask_preamble": 2,       # (sid, tool_use_id)   — the ask card's preamble
    "pending_dialog": 1,     # (sid)                — a host's OPEN modal (ask)
    "usage_strip": 0,        # (cache=, limit=)     — this host's usage-strip
    #                          rows (keywords at the call site, hence 0)
    "session_usage": 1,      # (sid)                — one session's limit windows
    "session_account": 1,    # (sid)                — …its subscription account
    "session_costs": 1,      # (sid)                — …its token/cost totals
    "tasks": 1,              # (sid[, sdb])         — the pinned task list
    "compacting": 1,         # (sid[, sdb])         — the compaction latch, RAW
    "fg_running": 1,         # (sid[, sdb])         — the in-flight fg command
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


def _first_owner(method, sid, *args, default=None, host=None, **kwargs):
    """The SID-KEYED fan-out primitive, routed by OWNERSHIP: resolve the
    session's transcript path, and ask ONLY the host that owns it — the default
    host when the path is empty/unclaimed (the same fail-OPEN rule
    read/session.session_caps applies, so an unprovable session keeps behaving
    exactly as it does today). `default` when that host has no such provider.

    The sid-keyed twin of _first_path, and it exists for the same reason: these
    providers PARSE, and a parser asked about another tool's session does not
    fail, it answers. A session belongs to exactly ONE host, so first-plugin-wins
    is not merely imprecise here — it is a claim by the wrong host about someone
    else's session (a Claude OTEL sum over a codex run reads a truthful-looking
    zero for work that really happened).

    Costs one session_row() lookup per call; the read model already resolves the
    row for every one of these payload fields, and the audit query is indexed.

    `host` SKIPS that lookup for a caller who already holds the owning host's
    short name. It exists for the SSE tick and nothing else: the tick resolves
    the owner ONCE per slow pass (it needs it for the prompt-bubble command
    vocabulary anyway) and then runs `compacting`/`fg_running` on the FAST
    cadence, where an un-hinted route would add an audit-DB `session_row` walk
    per channel per tick to answer a question that cannot change between them.
    Pass only a name that came from owns_by()/default_host() — an invented one
    routes the read to the wrong host, which is the very failure this primitive
    exists to prevent."""
    if not host:
        from core import sessionapi as API
        tpath = (API.session_row(sid) or {}).get("transcript_path") or ""
        host = owns_by(tpath) or default_host()
    fn = _named(method, host)
    return default if fn is None else fn(sid, *args, **kwargs)


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


def default_host():
    """The DEFAULT host's short name — the tool a session BEHAVES AS when its
    owner cannot be proven, and the tool a launch that names none picks.

    THE one owner of that name. It used to be spelled independently in four
    places (dashboard/read/session.DEFAULT_HOST, read/lists's inline literal,
    http/post/session.DEFAULT_TOOL, and slash_commands' own default below), which
    is three chances for a rename to half-land — and the dashboard tier is not
    supposed to know a host's name at all (docs/styleguide.md *Layering*).

    Derived from the registry rather than authored twice: all_plugins() is
    HOST-FIRST by contract (its docstring), so the first plugin that provides a
    LAUNCHABLE `host` adapter IS the default. The literal below is the last-resort
    answer for a registry with no host at all — a state no build ships, kept only
    so a caller never gets "" for a name it will compare against."""
    for p in all_plugins():
        fn = provider(p, "host")
        if fn is None:
            continue
        h = fn()
        if h is not None and h.launchable and h.name:
            return h.name
    return "claude_code"


def hosts():
    """Every registered HOST tool and its whole new-session VOCABULARY, host
    first — the one payload behind `/api/hosts`, and the reason the page carries
    no per-host table of its own any more.

    Per row: `name`/`label`/`launchable` (the picker), `default` (the tool a
    launch that names none picks — default_host, so the client never spells it),
    `model_choices`/`effort_choices` + `model_default`/`effort_default` (that
    host's menus and their first-ever selections), `model_match` (how a menu row
    matches a RUNNING model id — see HostControl.model_match), `accounts`
    (does this tool have a subscription switcher — DERIVED from the plugin
    providing the `accounts` registry, so the picker's account row follows the
    fact rather than a host name), `attach` (does it have an inline file-mention
    grammar — HostControl.mention; a host without one is handed bare paths) and
    `quick_commands` ([{cmd, min_prompts}] — the QUICK_COMMANDS wire words this
    host actually implements, each with its measured refusal floor, so the
    header greys a button instead of typing a command the TUI will bounce).

    Everything here is DERIVED from the HostControl object plus the plugin's own
    providers; nothing is authored twice. Same read-side exception contract as
    accounts()."""
    dflt = default_host()
    out = []
    for p in all_plugins():
        fn = provider(p, "host")
        if fn is None:
            continue
        h = fn()
        if h is None:
            continue
        out.append({"name": h.name, "label": h.label,
                    "launchable": bool(h.launchable),
                    "default": h.name == dflt,
                    "accounts": provider(p, "accounts") is not None,
                    "attach": bool(h.mention(_MENTION_PROBE)),
                    **host_vocabulary(h)})
    return out


# A path handed to HostControl.mention purely to ask "does this host HAVE a
# mention grammar" — the answer is a rewritten path or "", and no file is
# touched. A probe rather than a second `attaches = True` declaration: the
# grammar is already declared, and two spellings of one fact drift.
_MENTION_PROBE = "/probe"


def host_vocabulary(host):
    """The menu/refusal words ONE host declares, in the shape both wire surfaces
    serve them: `/api/hosts` (per tool, for the new-session form) and the session
    payload (for the session's OWN owner — the ✦/✧ pickers, ⊜ compact's floor,
    the ↶ rewind menu). One builder so the two cannot disagree about a host, and
    so a new word is added in one place.

    `rewind_modes` carries its LABEL beside each mode (rewind_mode_label): the
    words name what that host's checkpoint menu restores, and the client used to
    hold a copy of Claude Code's three."""
    from plugins.host import QUICK_COMMANDS
    return {
        "model_choices": list(host.model_choices()),
        "effort_choices": list(host.effort_choices()),
        "model_default": host.model_default(),
        "effort_default": host.effort_default(),
        "model_match": host.model_match,
        "rewind_modes": [{"mode": m, "label": host.rewind_mode_label(m)}
                         for m in host.rewind_modes()],
        "quick_commands": [{"cmd": c, "min_prompts": host.command_floor(c)}
                           for c, (method, _cap) in QUICK_COMMANDS.items()
                           if host.implements(method)],
    }


def quick_command_caps():
    """{wire word: the capability it rides} over plugins.host.QUICK_COMMANDS —
    the registry-root door for the dashboard's /command guard, which used to
    re-spell the same four rows (and once shipped without `rename`, which is how
    Claude Code's argless `/rename` got pasted into a codex composer)."""
    from plugins.host import QUICK_COMMANDS
    return {cmd: cap for cmd, (_method, cap) in QUICK_COMMANDS.items()}


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


def inert_host():
    """The interface's OWN inert host — a HostControl whose every gesture answers
    `unsupported`. The registry-root door for the one caller that needs a host
    object when the registry could not name one (the dashboard's `_gesture_host`
    last-resort, so a handler that has already audited its intent gets an audited
    409 rather than an AttributeError 500). Not a registered plugin and never a
    default: it is the honest "no host at all"."""
    from plugins.host import HostControl
    return HostControl()


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
    plugin that recognizes (sid, agent_id) returns that agent's token rollup as
    {"model", "usage", "cost"}; None when no plugin does. claude_code folds the
    agent's transcript (transcript.agent_usage) AND prices it with its own table;
    codex deliberately declines — a run's tokens are folded from its rollout and
    priced at its footer, so there is nothing for the web to re-price.

    `cost` is part of the PROVIDER's contract (approximate USD, omitted for an
    unknown model) rather than something the caller adds: the dashboard used to
    price whatever came back with Anthropic's table, which is only correct while
    exactly one plugin answers. Exceptions propagate, same contract as census():
    the caller is the read-side dashboard, not a hook, and swallowing here would
    hide which provider broke.

    Deliberately still `_first` — it is AGENT-keyed, and an agent need not share
    its session's host (a codex run sidecar'd inside a Claude session is a codex
    agent under a claude_code sid). The ownership gate its sid-keyed siblings
    gained in P4 would ask the PARENT's host about a CHILD that isn't its own;
    the agent id already discriminates, since each host recognizes only ids it
    issued. See conversation() — the same rule, argued there in full."""
    return _first("agent_usage", sid, agent_id)


def runs(sid):
    """NESTED-RUN fan-out: every plugin's own child RUNS of a session, in the
    core.sessionapi.agents() row shape ({agent_id, kind, transcript, started_at,
    desc, ended_at, end_reason, tools}), concatenated across plugins with the
    first agent_id winning. [] when no plugin has any.

    codex answers with its sidecar/native rollout runs (plugins/codex/nested.py —
    kind 'codex', minus the standalone host's OWN run, which IS the session);
    claude_code declines, because a Claude subagent is not a "run": it is
    already an audit `streams` row of kind subagent/teammate that agents() reads
    first-hand.

    This exists so that `agents()` — in tool-agnostic core — can splice a host's
    nested children WITHOUT naming one: it used to call a `codex_runs()` defined
    in core, which knew codex's stream kind, its id derivation and its
    self-run drop rule. Same rows, same order, one fan-out."""
    return _concat_unique("runs", lambda r: r.get("agent_id"), sid)


def nested_owners(sid):
    """NESTED-JOB OWNERSHIP fan-out: `{task_id: {"agent_id", "tool_use_id",
    "command", "description"}}` — who launched each of a session's background
    jobs and monitors, and with what command — from the first plugin that
    recognizes the sid; {} when none does.

    The audit `streams.agent_id` stamp is the authoritative source; this is the
    HISTORY fallback (rows written before the stamp) and the only source of the
    launching COMMAND for a nested stream (core.sessionapi.nested_owners
    documents both). Recovering it means reading a HOST's launch-hook payloads —
    its hook name, its tool names, its JSON paths — which is why the query lives
    in the plugin (plugins/claude_code/nested.py) and not in core, where it was
    the deepest tool-specific leak in a module that calls itself tool-agnostic.
    codex writes none of those rows and declines.

    First-plugin-wins is right here rather than a merge: a session belongs to
    exactly ONE host, so a second answer would be about someone else's session.
    core memoizes the result (OWNERS_TTL_S); this fan-out is called once per
    session per window."""
    return _first("nested_owners", sid, default={})


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
    owns_by); None defaults to default_host() — the new-session form has no
    session to own it yet and launches the default tool. An unknown host, or one
    with no slash_commands provider, yields [] (an empty menu is the honest
    answer for a tool with no vocabulary — never another tool's).
    Same read-side exception contract as census()/activity()."""
    fn = _named("slash_commands", host or default_host())
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


def effort(transcript_path):
    """The session's CURRENT reasoning-effort token from its transcript, or "" —
    PATH-keyed and ownership-gated (unlike the cwd-keyed effort_default), so a
    NON-default host's real level comes from its own rollout, never Claude's
    cwd-keyed default (which leaked `high` onto a `low` codex run). codex reads
    its last turn_context (read.codex_effort — no usage record needed); Claude
    provides none here (its effort is a settings fact, so it keeps using
    effort_default). Same read-side exception contract as context()."""
    return _first_path("effort", transcript_path, default="", truthy=True)


def context(transcript_path, main=False):
    """Context-saturation fan-out (path-keyed like session_title — the
    dashboard's rows already hold each transcript path): the first plugin that
    recognizes the file returns {"used", "window", "pct", "model"} for its
    most recent turn — how full the context window is; None when no plugin
    does (a fresh transcript, or a file no parser claims). BOTH hosts provide
    it — codex's answer additionally carries `effort`, its per-turn rollout
    reasoning level, which no Claude transcript has.
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
    context()/goal(): no hook fires for the fallback.

    OWNERSHIP-GATED (_first_path), like every other path-keyed row here — it was
    the one that said "path-keyed like context/goal" and then called `_first`, so
    a plugin was asked about files it does not own. Concretely: claude_code's
    FORWARD scanner read every codex rollout end to end on the `fallback` SSE
    channel, and a rollout that ever quoted the matched token would have been
    reported as a Claude refusal-fallback — the exact fail-open class the gate
    exists to prevent (see _first_path's 429KB-rollout measurement)."""
    return _first_path("model_fallback", transcript_path, pos,
                       default=(None, pos))


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
    stream: (records, new_pos) from the host that OWNS the session, None when it
    has nothing to say. `agent_id` picks WHOSE — the session's own main thread by
    default, a subagent/teammate's when named — so agent scope merges prose into
    an agent's mirror through this same call (docs/dashboard.md *Agent scope*).
    Records carry the tool_use `anchor` the dashboard interleaves on. Same
    exception contract as census().

    THE KEY DECIDES WHO IS ASKED, and this is the one fan-out where both keys
    are in play (P4):

      · no `agent_id` — the question is about the SESSION's own main thread, and
        a session belongs to exactly one host, so it is OWNERSHIP-ROUTED
        (_first_owner). It was `_first`, and that WORKED only because
        claude_code's reader happens to fail CLOSED for a foreign sid (it
        resolves no Claude transcript and returns None, so the fan-out fell
        through to codex — measured on a real standalone codex session: 11
        records either way, and the rendered mirror byte-identical before and
        after). Luck, not a contract: the sibling ask_preamble on the same sid
        answers '' for anyone, a non-None result that WINS.

      · an `agent_id` — the question is about ONE CHILD, and a child need NOT
        share its parent's host. That is the entire premise of the codex plugin's
        original role: a codex run SIDECAR inside a Claude session is a codex
        agent under a claude_code sid. Routing by the session's owner asks Claude
        about a codex rollout, which declines, and the agent's conversation is
        lost (caught by tests/test_l1g_codex_read's sidecar case, which is why
        this branch exists). First-wins is right here AND safe here, because the
        agent id is itself the discriminator — each host recognizes only ids it
        issued, so the wrong host cannot fail open on one it never saw."""
    if agent_id:
        return _first("conversation", sid, pos, agent_id)
    return _first_owner("conversation", sid, pos, agent_id)


def ask_preamble(sid, tool_use_id):
    """A host's prose lead-in to a pending question (the text framing it, shown
    on the dashboard's ask card): the string from the host that OWNS the
    session, None when it has no such provider, "" when it owns the sid but
    found no prose.

    OWNERSHIP-ROUTED for the reason its sibling conversation() documents, and it
    is the concrete instance of that hazard: claude_code answers "" for ANY sid,
    including a codex one — a non-None result, which under first-plugin-wins is
    an answer and ends the fan-out before its owner is asked. Harmless only
    while codex declines the provider; a decline is not a design."""
    return _first_owner("ask_preamble", sid, tool_use_id)


def pending_dialog(sid):
    """A host's OPEN modal dialog for the web question/plan card — the host that
    OWNS the session returns {"kind", "tool_use_id", …}, None otherwise. The
    Claude ask/plan dialogs ride a hook-stashed kv
    (dashboard/read/session.ask_pending), so claude_code exposes no provider
    here; codex has no such hook (docs/codex.md), so it derives the pending
    request_user_input READ-side from the rollout tail. Read-side like
    conversation(); same exception contract as census(); ownership-routed for
    the same reason (and this one PARSES a 256KB rollout tail, so asking the
    non-owner is not merely wrong, it is wasted I/O)."""
    return _first_owner("pending_dialog", sid)


# THE WINDOW-LABEL TABLE — how long a rate-limit window is, spelled the ONE way
# the strip shows it, for every host (docs/dashboard.md *One usage-window
# vocabulary, every host*). It lives beside the vocabulary docstring below
# because it IS part of that vocabulary.
#
# This used to be per HOST: `plugins/claude_code/usage.window_label` said "7d"
# and `plugins/codex/usage.window_label` said "1w" for the very same 10080
# minutes, each "the way its own UI does". That reading was wrong for the one
# surface both feed — the strip is read as a STACK, and its columns are keyed by
# DURATION so codex's weekly bar sits directly under Claude's (*Row alignment*).
# Two spellings of one column is not two vocabularies, it is a column that
# changes its name halfway down. So the DURATION word is shared and a host may
# only name a duration this table does not know.
WINDOW_LABELS = {
    300: "5h",        # 5 hours — Claude's `five_hour`, codex's usual primary
    10080: "7d",      # 7 days  — Claude's `seven_day`, codex's weekly secondary
}


def window_label(mins, fallback=""):
    """A rate-limit window's SHORT display label from its LENGTH IN MINUTES —
    300 → "5h", 10080 → "7d" — the one spelling every host's strip row uses, so
    the same duration lands in the same column whoever reported it.

    `fallback` is the caller's OWN word, used only for a duration this table
    does not name (codex's duration-derived ladder — 1440 → "1d"; a window with
    no readable duration at all). A host that wants to decorate the shared word
    (Claude's per-model cap, "7d fable") builds on the returned string; it does
    not re-spell the duration. Pure — no I/O, safe at import."""
    try:
        mins = int(mins)
    except (TypeError, ValueError):
        return fallback
    return WINDOW_LABELS.get(mins) or fallback


def usage_strip(cache=None, limit=50):
    """THE USAGE-WINDOW VOCABULARY — the one shape every host states its
    rate limits in, and the list page's usage strip, CONCATENATED across hosts
    (each answers for itself; [] from a host with nothing to say).

    One row per thing that has its own limits — for claude_code that is one per
    SUBSCRIPTION ACCOUNT, for codex a single host-wide reading, because that is
    the difference between a tool with an account switcher and one without:

      {
        "host":       the owning plugin's short name — the painter's GROUPING
                      key, so one strip can stack several hosts and each host's
                      columns still line up within its own group,
        "label":      the row's display name ("claude-01" / "codex · plus"),
        "slug":       the switcher account id, "" for a host with no accounts,
        "switchable": may the new-session picker offer this row as an ACCOUNT?
        "plan":       the subscription plan word, "" when the host has none,
        "ts":         when the reading was taken (epoch), None when unknown,
        "windows":    the limits themselves, in display order:
            [{"key":        unique WITHIN this host's rows (a row's own handle
                            on the window; the painter lays its COLUMNS out by
                            `window_mins`, not by this — see window_label),
              "label":      the window's short spelling, from the SHARED
                            duration table (window_label above): the same 10080
                            minutes is "7d" on every host's row, because it is
                            the same column. A host names only what that table
                            does not, and may add its own suffix ("7d fable"),
              "used_pct":   int 0..100, or None when this row has no reading
                            for a window a SIBLING row has (the painter ghosts
                            the column rather than shifting the stack),
              "resets_at":  epoch, or None when the window carries no reset,
              "window_mins": its length,
              "scope":      "account" (its own reset column) or "model" (a
                            per-model cap, which resets on the account-wide
                            window above it and would only repeat it)}],
      }

    A host with an account switcher stamps its picker/limit fields on top of
    this (usage/five_hour_eff/sched_score/sched_ok/limit_hit/logged_out — see
    plugins/claude_code/usage.strip_rows); a host without them serves the honest
    empty, so ONE painter reads every row the same way.

    `cache` is the caller's db_cached() memo dict and `limit` how many recent
    sessions a per-account aggregation may scan — both are hints a host is free
    to ignore. Read-side; same exception contract as accounts() (the caller is
    the read-side dashboard, not a hook)."""
    out = []
    for p in all_plugins():
        fn = provider(p, "usage_strip")
        if fn is None:
            continue
        out += list(fn(cache=cache, limit=limit) or [])
    return out


def session_usage(sid):
    """ONE session's rate-limit reading, from the host that OWNS it
    (_first_owner) — the `windows` list of the usage-strip vocabulary above,
    plus whatever flat fields that host's own snapshot carries; None when it has
    no reading. Claude's is the status-line kv the shim stashed; codex's is its
    rollout's last non-null `rate_limits`. Read-side, no audit rows."""
    return _first_owner("session_usage", sid)


def session_account(sid):
    """ONE session's subscription account as {slug, label}, from the host that
    OWNS it (_first_owner); {} when unknown or when the host has no accounts at
    all. The header's ◈ chip. Read-side, no audit rows."""
    return _first_owner("session_account", sid, default={})


def session_costs(sid):
    """ONE session's token/cost totals, from the host that OWNS it
    (_first_owner): {"tokens": {source: {type: n}}, "cost": {source: usd},
    "total_usd": x}. The empty envelope when no host answers.

    Ownership-routed rather than first-wins because the two hosts BANK their
    spend in different places — Claude Code in the audit `otel` table its
    telemetry receiver fills, codex in its own scoreboard counters, priced at
    read time by the stream that folded them — and each reads ZERO for the
    other's work. A zero is indistinguishable from a cheap session, which is
    exactly the kind of wrong number nobody reports."""
    return _first_owner("session_costs", sid,
                        default={"tokens": {}, "cost": {}, "total_usd": 0.0})


# --- the SESSION-STATE FACETS --------------------------------------------------
# Three things a session is DOING right now, each of which the dashboard used to
# read as a raw kv/hand-off row off the state DB — by NAME, with no host in the
# question. That worked while one host wrote them and read as a silent None for
# the other, which is the shape of a wrong number nobody reports: a codex session
# showed no tasks card (right, for the wrong reason), never breathed its ctx bar
# during a compaction (wrong), and never ticked an elapsed chip on a running
# command (wrong). Ownership-routed (_first_owner) rather than first-wins,
# because these are FACTS ABOUT ONE SESSION and a session has exactly one host.
#
# All three take the caller's already-resolved state-DB path as an optional
# second argument — see read/meta.session_kv for that contract — and `host` as
# the SSE tick's routing hint (see _first_owner).

def tasks(sid, sdb=None, host=None):
    """The session's pinned TASK LIST — a list of task records ({id, subject,
    status, …}, id-sorted) from the host that owns the session, or None when it
    has none (which keeps the card hidden). claude_code re-snapshots Claude
    Code's on-disk task dir into a kv on every task-touching hook
    (task_fmt.tasks); codex DECLINES — no task-list tool appears anywhere in its
    rollout vocabulary, so there is no material and a hidden card is the honest
    answer, not a bug (docs/codex.md). Read-side, no audit rows."""
    return _first_owner("tasks", sid, sdb, host=host)


def compacting(sid, sdb=None, host=None):
    """Is this session COMPACTING right now — the RAW latch `{ts, trigger}` from
    the owning host, or None. Both hosts arm it on their PreCompact hook and
    clear it on PostCompact, into the same kv shape (claude_code
    compact_fmt.compacting, codex facets.compacting), because both fire that
    hook PAIR and nothing else in either tool marks the two minutes a compaction
    takes: no tool call, no reply, no transcript growth, and a tab colour it
    shares with every think.

    RAW on purpose — the TTL that ages an un-cleared latch out lives with the
    reader (dashboard config.COMPACT_MAX_S), not with either producer. A
    compaction that dies on an API error or is interrupted fires no closing hook
    in EITHER tool, the arming process has long exited, and an animation must
    fail OFF; putting the clock in the providers would let two hosts disagree
    about how long a bar may breathe. Read-side, no audit rows."""
    return _first_owner("compacting", sid, sdb, host=host)


def fg_running(sid, sdb=None, host=None):
    """The session's IN-FLIGHT foreground command as {"g", "start_ts"}, or None
    — `g` being the MIRROR BLOCK's copy-group id, which is the whole point: the
    server says WHICH block is running and since when, and the browser ticks the
    ⏱ elapsed chip on it (docs/dashboard.md, *Live command elapsed*).

    The two hosts stamp it from opposite ends, and it could not be otherwise.
    claude_code writes it in the PreToolUse HOOK (cmd_pre.fg_running), where the
    tool_use_id it already stamps on the ▶ header IS the copy group. codex has no
    such id to reuse — measured 2026-07-31: its hook's `tool_use_id` is
    `exec-<uuid>`, the rollout's exec record's `call_id` is `call_<…>`, and the
    mirror block's copy group is a fresh `ops.new_group()` integer, three
    disjoint id spaces — so a hook-stamped id would name no block and the chip
    would tick on nothing. Its rollout STREAM owns the record instead
    (facets.fg_open/fg_close), the one place that holds the group id and the
    command's start together. Same record either way, same reader.

    Read-side, no audit rows (the WRITES are audited by their producers)."""
    return _first_owner("fg_running", sid, sdb, host=host)
