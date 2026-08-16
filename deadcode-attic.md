# Dead-code attic

Definitions vulture found unreferenced — no caller in the product, none in
`tests/` either — lifted out of the tree verbatim on 2026-08-16 (tree at
d8f9056 plus the terminal refactor). Each block below is the exact source as it
stood, including its decorators and the comment block above it.

This file exists so a false positive costs a copy-paste, not an
archaeology session. Vulture matches names, so anything reached only by a
string — a getattr, a registry key, a JS caller naming a route — can land here
wrongly. If something turns out to have been live, paste it back at the site
named in its heading and delete the entry.

Imports the removed code needed were pruned separately (ruff F401); a restore
may need one added back.

## `rgb_css` — dashboard/ansi.py:242

```python
def rgb_css(color, fallback=(120, 132, 158)):
    try:
        red, green, blue = color
        return "rgb(%d,%d,%d)" % (int(red), int(green), int(blue))
    except Exception:
        return "rgb(%d,%d,%d)" % fallback
```

## `mark_terminal` — dashboard/notify/presence.py:157

```python
def mark_terminal():
    """Record that you are AT THE TERMINAL right now — the terminal's analog of
    a browser's /api/presence beat. It cannot beat for itself (nothing runs in
    the terminal to POST for it), so the notifier POLLS the frontend's
    `app_focused` and calls this; the stamp is otherwise an ordinary device
    presence and competes with the browsers on plain recency."""
    _DEVICE_SEEN[TERMINAL] = time.monotonic()
```

## `config_dir_for` — plugins/claude_code/account.py:44

```python
def config_dir_for(account_id: str) -> str | None:
    if not account_id or not VALID_ACCOUNT_ID.fullmatch(account_id):
        return None
    directory = os.path.join(ACCOUNT_CONFIG_DIRECTORY, account_id)
    return directory if os.path.isdir(directory) else None
```

## `launch_argv` — plugins/claude_code/account.py:60

```python
def launch_argv(arguments: list[str], command: str = DEFAULT_COMMAND) -> list[str]:
    shell = os.environ.get("SHELL") or "/bin/zsh"
    if os.path.basename(shell) not in SUPPORTED_SHELLS:
        shell = "/bin/zsh"
    return [shell, "-lic", f'{command} "$@"', command, *arguments]
```

## `_lead_actor` — plugins/claude_code/canonical.py:257

```python
def _lead_actor(session_id: SessionId) -> ActorId:
    return ActorId(f"{session_id}:lead")
```

## `agent_def_file` — plugins/claude_code/model.py:168

```python
def agent_def_file(atype):
    """The DEFINITION file for an agent type, if any. Identity is the frontmatter
    `name:` (docs); fall back to the filename stem. Project defs shadow user defs.
    Searches agents across ALL ancestor .claude dirs (claude_dirs), not just
    os.getcwd()/.claude: a teammate/subagent frequently runs in a subdirectory or
    a git worktree where <cwd>/.claude is absent OR is a stub without agents/
    (e.g. a task's db/.claude), which would otherwise miss the def and drop
    `effort:`/`model:` to the session/user default. Nearest-first, ~/.claude last."""
    roots = [os.path.join(c, "agents") for c in claude_dirs()]
    stem_hit = None
    for r in roots:
        if not os.path.isdir(r):
            continue
        for dp, _dirs, files in os.walk(r):
            for f in files:
                if not f.endswith(".md"):
                    continue
                p = os.path.join(dp, f)
                if fm_field(p, "name") == atype:
                    return p
                if os.path.splitext(f)[0] == atype and stem_hit is None:
                    stem_hit = p
    return stem_hit
```

## `session_model` — plugins/claude_code/model.py:248

```python
def session_model(tpath):
    """The model VERSION the parent session runs (e.g. "claude-opus-4-8"), from
    the last assistant turn in its transcript. Gives a precise version for agents
    that INHERIT, before the agent's own first turn reveals it. Tail-scan only
    (TAIL_SCAN_BYTES — see its comment)."""
    lines = tail_lines(tpath, TAIL_SCAN_BYTES)
    if lines is None:
        return None
    last = None
    for line in lines:
        if b'"assistant"' in line and b'"model"' in line:
            try:
                m = (json.loads(line).get("message") or {}).get("model")
            except Exception:
                continue
            if m:
                last = m
    return last
```

## `parent_resolved_model` — plugins/claude_code/model.py:268

```python
def parent_resolved_model(tpath, agent_id):
    """The authoritative resolved model (carrying [1m]) is recorded in the PARENT
    transcript on the agent's Task result — but only at completion. Best-effort:
    scans tpath for the agentId; None if not written yet (callers fall back)."""
    try:
        hit = None
        with open(tpath, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if agent_id not in line or "resolvedModel" not in line:
                    continue
                try:
                    tur = (json.loads(line).get("toolUseResult") or {})
                except Exception:
                    continue
                if tur.get("agentId") == agent_id and tur.get("resolvedModel"):
                    hit = tur["resolvedModel"]
        return hit
    except Exception:
        return None
```

## `parent_tool_result` — plugins/claude_code/model.py:289

```python
def parent_tool_result(line, tool_use_id):
    """Whether this raw PARENT-transcript JSONL line carries the tool_result that
    resolves `tool_use_id` (the agent's Task/Agent call, from its meta.json
    `toolUseId`) — and if so, its is_error flag: True == the user REJECTED /
    cancelled the call ("The user doesn't want to proceed with this tool use").
    Returns None when the line isn't that result.

    This is the authoritative "the subagent is done" signal for the cases the
    hooks miss: a rejected or otherwise-abandoned Task fires NO SubagentStop and
    leaves meta.json WITHOUT `stoppedByUser`, so the substream's usual end signals
    never come. The parent transcript still records the Task's tool_result the
    instant the call resolves (completed, rejected, or cancelled) — an EVENT, not
    an idle timeout, so watching for it recovers the gap without the backstop that
    false-positived on long thinks.

    EXCEPTION — the async-launch ack: an ASYNC (background) agent's Task resolves
    IMMEDIATELY with a synthetic "Async agent launched successfully" tool_result
    (is_error absent) that means "launched", NOT "finished" — the agent then runs
    for minutes producing its whole transcript. Treating that ack as resolution
    ended the streamer ~2s in with 0 lines rendered (the agent's work never
    reached the mirror). So the ack is NOT a resolution: return None for it and
    let the streamer tail on to the authoritative SubagentStop sentinel."""
    if not tool_use_id or tool_use_id not in line:
        return None
    try:
        content = (json.loads(line).get("message") or {}).get("content")
    except Exception:
        return None
    if not isinstance(content, list):
        return None
    for b in content:
        if (isinstance(b, dict) and b.get("type") == "tool_result"
                and b.get("tool_use_id") == tool_use_id):
            if not b.get("is_error"):
                txt = b.get("content")
                if isinstance(txt, list):
                    txt = " ".join(x.get("text", "") for x in txt
                                   if isinstance(x, dict))
                if isinstance(txt, str) and "launched successfully" in txt:
                    return None   # async launch ack — not a real resolution
            return bool(b.get("is_error"))
    return None
```

## `effort_config` — plugins/claude_code/model.py:354

```python
def effort_config(def_file):
    """Configured effort in the documented precedence (model-config docs: "The
    environment variable takes precedence over all other methods … Frontmatter
    effort … overriding the session level but not the environment variable"):
    env > agent-def frontmatter `effort` > session `effortLevel`. "" when none —
    callers fall to model_default_effort on the model actually running."""
    return ((os.environ.get("CLAUDE_CODE_EFFORT_LEVEL") or "").strip()
            or def_field(def_file, "effort") or settings_field("effortLevel") or "")
```

## `model_default_effort` — plugins/claude_code/model.py:364

```python
def model_default_effort(model):
    if not model:
        return ""
    m = model.lower()
    if "opus-4-7" in m:
        return "xhigh"
    if any(t in m for t in ("opus-5", "opus-4-8", "opus-4-6", "sonnet-5",
                            "sonnet-4-6", "fable-5")):
        return "high"
    return ""                                # models without adaptive reasoning
```

## `ladder_from` — plugins/claude_code/model.py:418

```python
def ladder_from(fam):
    """The MODEL_LADDER suffix starting at family `fam` — the models to try,
    best first, once `fam`'s quota is gone (("opus", "sonnet") from "opus"). ()
    when `fam` is not a ladder rung (haiku / None / unknown): the caller then
    keeps the current model rather than inventing a downgrade."""
    return MODEL_LADDER[MODEL_LADDER.index(fam):] if fam in MODEL_LADDER else ()
```

## `clip_screen` — plugins/claude_code/screen_driver.py:12

```python
def clip_screen(screen: str, limit: int = SCREEN_LIMIT) -> str:
    if not screen or len(screen) <= limit:
        return screen
    half = limit // 2
    return (
        screen[:half]
        + f"\n…[{len(screen) - limit} chars elided]…\n"
        + screen[-half:]
    )
```

## `original_command` — plugins/claude_code/shell.py:73

```python
def original_command(command: str) -> str:
    """Remove exactly the foreground copy wrapper produced by this module."""
    if not command.startswith("{ "):
        return command
    match = _COPIED_OUTPUT_SUFFIX.search(command)
    return command[2:match.start()] if match else command
```

## `statement_directories` — plugins/claude_code/shell.py:81

```python
def statement_directories(
    command: str,
    initial_directory: str,
) -> tuple[tuple[str, str | None], ...]:
    """Return each shell statement with its statically known directory."""
    statements = _statements(command)
    rows = []
    for index, statement in enumerate(statements):
        directory, known = _working_directory(
            statements[:index],
            initial_directory,
            expand_home=True,
        )
        rows.append((statement, directory if known else None))
    return tuple(rows)
```

## `cmp_key` — plugins/claude_code/suggestion.py:132

```python
def cmp_key(s):
    """A box-text COMPARISON key: every whitespace character removed. Stronger
    than `norm` on purpose — a wrapped box is captured as separate lines that
    join with NO separator, so a box read and the same text from the transcript
    agree on their words but not on the spaces between them. Dropping
    whitespace altogether is what makes post_interrupt's restore check survive
    a wrap (docs/dashboard.md, *Interrupt*); nothing else may compare box text
    by hand."""
    return re.sub(r"\s+", "", s)
```

## `probe` — plugins/claude_code/suggestion.py:176

```python
def probe(fe, win, sid=""):
    """The audited screen probe: capture the ANSI viewport and parse the ghost
    suggestion. None on any failure (audited) or when there is no suggestion."""
    return probe_box(fe, win, sid)[0]
```

## `mail_send` — plugins/claude_code/transcript.py:163

```python
def mail_send(inp):
    """A SendMessage tool_use INPUT -> (recipient, message text) — the shape of an
    OUTGOING piece of team mail as it appears in a transcript.

    One owner because two presenters read it: the substream paints the `✉ to <peer>`
    block from it, and conversation() surfaces the same call as a `sendmsg` record
    (the web's message bubble, which is where the UNCAPPED text comes from).

    `message`/`content` may be a plain string OR a structured content block (dict, or
    a list of them), so it is normalised through result_text — a raw .strip() on a
    dict is what crashed the streamer mid-run once, dropping the agent's un-bumped
    token tail. The recipient is what the SENDER typed, which can differ from the
    recipient's inbox name ("main" vs "team-lead"); mail_fmt.py's note on joining by
    msg_id applies here too."""
    if not isinstance(inp, dict):
        return "?", ""
    to = inp.get("to") or inp.get("recipient") or "?"
    text = result_text(inp.get("message") or inp.get("content")
                       or inp.get("summary") or "")
    return str(to), text
```

## `input_summary` — plugins/claude_code/transcript.py:185

```python
def input_summary(inp):
    """Compact "key: value" view of a tool's input, so the REQUEST is visible
    (e.g. a WebSearch query, a WebFetch url)."""
    if not isinstance(inp, dict) or not inp:
        return ""
    lines = []
    for k, v in inp.items():
        vs = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
        lines.append(f"{k}: {vs}")
    return "\n".join(lines)
```

## `strip_reminders` — plugins/claude_code/transcript.py:209

```python
def strip_reminders(text):
    """`text` with Claude Code's injected <system-reminder> blocks removed. Empty
    in, empty out; a text that is ONLY reminders becomes ''."""
    if not text:
        return text
    return _REMINDER_TAG.sub("", _REMINDER.sub("", text)).strip()
```

## `_resumes_turn` — plugins/claude_code/transcript.py:325

```python
def _resumes_turn(text):
    """Whether this INJECTED user turn resumed a turn Claude Code had already
    ended (see _RESUMES_TURN). Callers must have established `meta` first — the
    marks are read only on a turn the human did not type."""
    return any(p.match(text or "") for p in _RESUMES_TURN)
```

## `agent_paths` — plugins/claude_code/transcript.py:497

```python
def agent_paths(parent_tpath, agent_id):
    """(jsonl, meta_json) for a subagent of the session whose PARENT transcript
    is parent_tpath — the <base>/subagents/agent-<id>.{jsonl,meta.json} layout
    (the one owner of that derivation; substream._init binds through it)."""
    base = parent_tpath[:-6] if parent_tpath.endswith(".jsonl") else parent_tpath
    subdir = os.path.join(base, AGENT_SUBDIR)
    return (os.path.join(subdir, "agent-%s.jsonl" % agent_id),
            os.path.join(subdir, "agent-%s.meta.json" % agent_id))
```

## `title_and_rename` — plugins/claude_code/transcript.py:703

```python
def title_and_rename(path):
    """(session_title, tail_rename): the display title AND the `agent-name`
    /rename record STILL PRESENT in the transcript's title tail-window ('' when
    it has none — never renamed, OR the rename has scrolled out beyond
    TITLE_TAIL_B in a long session while Claude Code kept re-emitting `ai-title`
    near EOF). The dashboard reconciles its DURABLE web-rename override against
    the second value: a rename that fell out of the tail no longer 'rolls back'
    to the auto ai-title, yet a FRESH in-tail rename (a terminal /rename, a
    re-rename) still supersedes the stored override (docs/session-naming-findings.md,
    *Fallbacks*; docs/dashboard.md, *Web rename*). One _title_records read — the
    ladder is shared with session_title()."""
    named, ai = _title_records(path)
    return _title_from_ladder(path, named, ai), named
```

## `_lead_actor` — plugins/codex/canonical.py:199

```python
def _lead_actor(session_id: SessionId) -> ActorId:
    return ActorId(f"{session_id}:lead")
```

## `usage_split` — plugins/codex/rollout.py:342

```python
def usage_split(u):
    """The ONE total_token_usage → (fresh_in, out, cached, total_in) mapping:
    codex's cumulative input_tokens INCLUDES the cached share, so fresh billed
    input is input - cached. The stream footer's rollup/fold calls this;
    re-encoding the arithmetic per-site is banned (styleguide single-owner
    rule)."""
    tin = int(u.get("input_tokens") or 0)
    tcache = int(u.get("cached_input_tokens") or 0)
    tout = int(u.get("output_tokens") or 0)
    return max(tin - tcache, 0), tout, tcache, tin
```

## `subagent_brief` — plugins/codex/rollout.py:1080

```python
def subagent_brief(path):
    """The BRIEF a codex subagent was spawned with — the text behind its launch
    card's click — or "" when the file offers none.

    WHERE THE BRIEF ACTUALLY IS, measured on the real cli 0.146 child rollout
    (019fb363-4028…): NOT in the child's own NEW_TASK record. codex delivers the
    task as a `response_item/agent_message` whose plaintext is only the envelope
    (`Message Type: NEW_TASK / Task name: /root/bali_weather / Sender: /root /
    Payload:`) — the payload itself is an `encrypted_content` part and cannot be
    read here at all. What IS in plaintext is the fork PREFIX: a subagent rollout
    opens by replaying the parent thread, and the last REAL HUMAN turn in that
    replay is the task the parent was working on when it spawned the child ("run
    a subagent to get a weather in bali"). That is the closest available
    statement of why the child exists, so that is what this returns.

    The team-scaffolding message ("You are an agent in a team of agents…", 2.1KB
    of spawn_agent/concurrency-slot instructions) is deliberately NOT it, and
    needs no preamble-stripping heuristic to exclude: it is role=developer —
    codex's SYSTEM channel — and contains no task text whatsoever, so the
    structural `is_synthetic` rule already drops it, along with
    `<environment_context>` and every other `<tag>` injection. A `<task>…</task>`
    INPUT wrapper (how codex delivers an UNencrypted task) is kept and reduced to
    its inner text by the shared strip_input_wrapper, which `_rsp_message` has
    already applied to these records.

    Reads the `chat` register (response_item), the complete resume-restored one
    (module header). Bounded by BRIEF_MAX_LINES/BRIEF_MAX_B and fail-open: "" for
    a non-subagent rollout, an unreadable file, or a prefix whose bootstrap never
    arrives. The caller CAPS the text (core/agentblocks takes it capped)."""
    fork_epoch = subagent_fork_epoch(path)
    if fork_epoch is None:
        return ""
    brief = ""
    try:
        read = 0
        with open(path, encoding="utf-8") as fh:
            for n, ln in enumerate(fh):
                read += len(ln)
                if n >= BRIEF_MAX_LINES or read > BRIEF_MAX_B:
                    break
                try:
                    rec = parse(json.loads(ln))
                except Exception:
                    continue                    # a torn/foreign line is not a brief
                if is_child_bootstrap(rec, fork_epoch):
                    break                       # the prefix ends here
                if (rec and rec["kind"] == "chat" and rec["role"] == "user"
                        and not rec["synthetic"]):
                    brief = rec["text"]         # the LAST one before the bootstrap
    except Exception:
        return ""
    return (brief or "").strip()
```

## `state_sig` — plugins/codex/title.py:67

```python
def state_sig():
    """A freshness stamp for the codex state INDEX — "<mtime>:<size>" of the
    resolved `state_<N>.sqlite`, or "" when there is none.

    This is what makes a codex rename VISIBLE on the web: the name lives in the
    index's `threads.title`, so renaming leaves the rollout byte-identical and
    the read model's (path, size) title memo would serve the old name forever
    (dashboard/read/cache.size_cached's `sig`). A stat is deliberately the whole
    test — it is coarse (any thread's rename re-computes every codex session's
    title) and that is the right trade for a memo whose miss costs one small
    query, where the alternative is a per-uuid read on every tick."""
    db = _state_db_cached()
    if not db:
        return ""
    try:
        st = os.stat(db)
    except OSError:
        return ""
    return "%d:%d" % (st.st_mtime_ns, st.st_size)
```

## `title_and_rename` — plugins/codex/title.py:165

```python
def title_and_rename(path):
    """(title, tail_rename) — the display title plus any rename record still in a
    reconcilable window. codex keeps the name in its state index, NOT in the
    rollout, so there is no in-file rename to reconcile: tail_rename is always ""
    and the dashboard's durable web-rename override stands unchallenged. Behind
    plugins.title_and_rename."""
    return session_title(path), ""
```

## `require_event` — runtime/canonical_store.py:172

```python
    def require_event(self, event_id: CanonicalEventId) -> StoredCanonicalEvent:
        stored_event = self.event(event_id)
        if stored_event is None:
            raise CanonicalEventStoreError(f"unknown canonical event: {event_id}")
        return stored_event
```

## `hosting_session` — terminal/adapter.py:105

```python
    def hosting_session(self, excluding_session_id: SessionId) -> SessionId | None:
        """Another session already displayed in this tab, if any — a tab hosts
        one session's panes, so a second would fight it for the space."""
        for window in self._tab_windows(None):
            hosted = window.tags.get(ACTIVITY_PANE_TAG) or window.tags.get(SESSION_WINDOW_TAG)
            if hosted and hosted != str(excluding_session_id):
                return SessionId(hosted)
        return None
```

## `scroll` — terminal/contract.py:133

```python
    def scroll(self, request: ViewportScrollRequest) -> ViewportScrollResponse:
        """One scroll gesture. A low-latency path for it is the
        implementation's own optimisation, not a second method here."""
```

## `scroll` — terminal/impl/kitty/plugin.py:295

```python
    def scroll(self, request) -> ViewportScrollResponse:
        if request.to_bottom and not self._to_bottom(request.window_id):
            return ViewportScrollResponse(False, "terminal scroll failed")
        if request.up_lines and not self._up(request.window_id, request.up_lines):
            return ViewportScrollResponse(False, "terminal scroll failed")
        return ViewportScrollResponse(True)
```

## `scroll` — terminal/impl/null.py:95

```python
    def scroll(self, request) -> ViewportScrollResponse:
        return ViewportScrollResponse(False, NO_TERMINAL)
```

## `def_field` — plugins/claude_code/model.py:167

```python
def def_field(def_file, field):
    """A frontmatter field from an agent definition; "inherit"/unset -> None so
    resolution falls through to what the agent actually ran / the session default."""
    v = fm_field(def_file, field) if def_file else None
    return None if (not v or v == "inherit") else v
```

## `settings_field` — plugins/claude_code/model.py:174

```python
def settings_field(field, start=None, env_pin=True, config=None):
    """A field from the merged settings (project overriding global). Layered
    across ALL ancestor .claude dirs (claude_dirs, nearest-first) for the same
    subdir/worktree reason as agent_def_file — else a teammate in a subdirectory
    skips the project settings and falls straight through to ~/.claude. First
    non-empty wins; settings.local.json shadows settings.json per dir.
    `start`/`env_pin`/`config` pass through to claude_dirs — an out-of-process
    reader (the dashboard) resolves for a SESSION's cwd and account config
    dir, not its own (same reason slashcmds passes start/env_pin)."""
    paths = []
    for c in claude_dirs(start=start, env_pin=env_pin, config=config):
        paths += [os.path.join(c, "settings.local.json"),
                  os.path.join(c, "settings.json")]
    for p in paths:
        try:
            with open(p, encoding="utf-8") as fh:
                v = json.load(fh).get(field)
            if v:
                return v
        except Exception:
            pass
    return None
```

## `probe_box` — plugins/claude_code/suggestion.py:165

```python
def probe_box(fe, win, sid=""):
    """ONE capture, BOTH readings: (ghost, typed) — the faint suggestion and the
    user's own real text. Exactly one of them can be non-None (they partition
    the box's content by intensity), and the two callers want opposite halves:
    the ghost feeds the composer's placeholder, the typed text feeds the
    terminal→web draft sync (docs/dashboard.md, *Terminal draft sync*). They run
    on the same SSE tick, so sharing the capture keeps that one screen read per
    tick instead of two.

    The typed half distinguishes "" (a box we READ and it is empty) from None
    (we could not read one — dead window, unreachable terminal, no box on screen).
    The sync treats an empty box as a signal and an unreadable one as no news,
    so collapsing the two would clear a draft every time a session's window
    goes away. (None, None) on any failure (audited)."""
    try:
        screen = fe.get_text(win, ansi=True)
    except Exception:
        A.error(sid, "dashboard suggestion probe", {"win": win})
        return None, None
    if not screen:
        return None, None
    return parse(screen), (typed(screen) or "")
```

## `session_title` — plugins/claude_code/transcript.py:630

```python
def session_title(path):
    """Best-effort display TITLE for a session transcript — what the terminal tab
    (Claude Code's OSC title) and the `claude --resume` picker show: the last
    `agent-name` (a /rename custom name — never clobbered by auto titles), else
    the last `ai-title`, else the LAST `summary` record in the head window,
    else the first line of the first REAL user prompt (isMeta rows and
    `<command-*>`/`<local-command-*>` wrappers are plumbing, not prompts), else
    — for a short slash-command session with none of the above — the `/command`
    that started it (docs/session-naming-findings.md, *Fallbacks*). '' when
    unreadable / nothing found."""
    named, ai = _title_records(path)
    return _title_from_ladder(path, named, ai)
```

## `_state_db_cached` — plugins/codex/title.py:40

```python
def _state_db_cached():
    """`_state_db()` behind a small per-directory TTL memo.

    The RESOLUTION is a glob of ~/.codex plus a regex per candidate, and the
    only thing that can change its answer is codex shipping a HIGHER-numbered
    index file — which happens on a codex upgrade, not during a page tick. The
    plain resolver stays the one that knows HOW (and the tests drive it), this
    is only about how often. It matters because `title_sig` runs on every title
    lookup — per codex session card, per slow tick — where the un-memoised glob
    would be the most expensive thing in a path that exists to be cheap.

    Keyed on `_CODEX_DIR` and read at CALL time, never at import: the tests
    monkeypatch that global, and a key that captured it would serve one test's
    directory to the next. A NEGATIVE answer is never cached — "there is no
    index yet" is the one answer that flips on its own (a first codex run, a
    fixture that writes the file after the first read), and a miss is a glob of
    a directory we just found nothing in."""
    hit = _STATE_DB.get(_CODEX_DIR)
    now = time.time()
    if hit and hit[0] > now:
        return hit[1]
    db = _state_db()
    if db:
        _STATE_DB[_CODEX_DIR] = (now + STATE_DB_TTL_S, db)
    return db
```

## `session_title` — plugins/codex/title.py:137

```python
def session_title(path):
    """Display title for a codex rollout: threads.title from the state index,
    else the first real user prompt in the rollout head, else "". Behind
    plugins.session_title."""
    return _thread_title(_thread_uuid(path)) or _first_prompt(path)
```

## `_to_bottom` — terminal/impl/kitty/plugin.py:295

```python
    def _to_bottom(self, window_id):
        if self.remote.raw("scroll-window", {"amount": ["end", None],
                                             "match": match.window(window_id)}) is True:
            return True
        return self.remote.run("scroll-window", "--match", match.window(window_id), "end") == 0
```

## `_up` — terminal/impl/kitty/plugin.py:301

```python
    def _up(self, window_id, lines):
        # The raw path is fire-and-forget and runs INSIDE the mirror's render
        # freeze bracket, where a subprocess would outlive the freeze window.
        if self.remote.raw("scroll-window", {"amount": [-float(lines), "l"],
                                             "match": match.window(window_id)}) is True:
            return True
        # `N-` = scroll up N lines (kitten @ scroll-window's amount grammar).
        return self.remote.run("scroll-window", "--match", match.window(window_id),
                               f"{int(lines)}-") == 0
```

## `fm_field` — plugins/claude_code/model.py:149

```python
def fm_field(path, field):
    """Scalar field from a markdown file's YAML frontmatter (the first
    --- ... --- block); None when absent/unreadable."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            if fh.readline().strip() != "---":
                return None
            for line in fh:
                if line.strip() == "---":
                    break
                k, sep, v = line.partition(":")
                if sep and k.strip() == field:
                    return v.strip().strip('"\'') or None
    except Exception:
        return None
    return None
```

## `_title_records` — plugins/claude_code/transcript.py:520

```python
def _title_records(path):
    """(agent_name, ai_title) — the LAST naming record of each kind in the tail
    window (docs/session-naming-findings.md): `agent-name`/`agentName` is the
    /rename custom name, `ai-title`/`aiTitle` the auto title Claude Code's OSC
    tab title mirrors. '' / '' when absent or unreadable."""
    lines = tail_lines(path, TITLE_TAIL_B)
    if lines is None:
        return "", ""
    named, ai = "", ""
    for raw in lines:
        if b'"agent-name"' not in raw and b'"ai-title"' not in raw:
            continue
        try:
            o = json.loads(raw)
        except Exception:
            continue
        if o.get("type") == "agent-name":
            named = o.get("agentName") or named
        elif o.get("type") == "ai-title":
            ai = o.get("aiTitle") or ai
    return named, ai
```

## `_title_from_ladder` — plugins/claude_code/transcript.py:593

```python
def _title_from_ladder(path, named, ai):
    """The display title given the tail's (named, ai): the /rename `agent-name`
    beats everything, then `ai-title`, then the head-window summary / first real
    prompt / opening `/command` fallbacks. Split out of session_title so
    title_and_rename() can reuse the SAME ladder without a second _title_records
    read (styleguide single-owner: the ladder lives here, once)."""
    if named or ai:
        return named or ai
    summary, prompt, cmd = "", "", ""
    try:
        with open(path, encoding="utf-8") as fh:
            for i, raw in enumerate(fh):
                if i >= TITLE_SCAN or prompt:
                    break
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    o = json.loads(raw)
                except Exception:
                    continue
                t = o.get("type")
                if t == "summary":
                    summary = o.get("summary") or summary
                elif t == "user" and not o.get("isMeta"):
                    c = (o.get("message") or {}).get("content")
                    if isinstance(c, str):
                        s = c.strip()
                        if s and not s.startswith("<"):
                            prompt = s.split("\n", 1)[0][:200]
                        elif s and not cmd:      # the /command that opened it
                            cmd = _command_label(s)
    except OSError:
        return ""
    return summary or prompt or cmd
```

## `_thread_title` — plugins/codex/title.py:62

```python
def _thread_title(uuid):
    """threads.title for `uuid` from the codex state index, or "" (no index, no
    row, an unreadable/other-shaped DB — all degrade to "")."""
    db = _state_db()
    if not db or not uuid:
        return ""
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=1.0)
        try:
            row = conn.execute("SELECT title FROM threads WHERE id=?",
                               (uuid,)).fetchone()
        finally:
            conn.close()
    except Exception:
        return ""
    return (row[0] or "").strip() if row and row[0] else ""
```

## `_first_prompt` — plugins/codex/title.py:80

```python
def _first_prompt(path):
    """The first real user prompt in a rollout's head, one line, capped — the
    fallback title when the state index has none. Bounded to TITLE_HEAD_LINES."""
    from plugins.codex import rollout as RO
    import json
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for i, raw in enumerate(fh):
                if i >= TITLE_HEAD_LINES:
                    break
                raw = raw.strip()
                if not raw or ('"user_message"' not in raw and '"message"' not in raw):
                    continue
                try:
                    rec = RO.parse(json.loads(raw))
                except Exception:
                    continue
                if not rec:
                    continue
                if rec["kind"] == "prompt" and rec["text"].strip():
                    return rec["text"].strip().split("\n", 1)[0][:200]
                if rec["kind"] == "chat" and rec.get("role") == "user" \
                        and not rec.get("synthetic") and rec["text"].strip():
                    return rec["text"].strip().split("\n", 1)[0][:200]
    except OSError:
        return ""
    return ""
```

## `tail_lines` — plugins/claude_code/transcript.py:507

```python
def tail_lines(path: str, byte_count: int) -> list[bytes] | None:
    """Read complete records from the bounded tail of a Claude transcript."""
    try:
        with open(path, "rb") as transcript_file:
            transcript_file.seek(0, os.SEEK_END)
            size = transcript_file.tell()
            transcript_file.seek(max(0, size - byte_count))
            lines = transcript_file.read().split(b"\n")
    except OSError:
        return None
    return lines[1:] if size > byte_count else lines
```

## `_command_label` — plugins/claude_code/transcript.py:543

```python
def _command_label(s):
    """The `/slash-command [args]` that STARTED a session, as ONE line — a title
    can't carry the newlines a multi-line argument has, so the args' whitespace
    is collapsed (the prompt fallback takes its first line for the same reason).
    '' when the content carries no command name. session_title's last-resort
    fallback, below summary/prompt (a slash command is less descriptive than a
    typed prompt, but beats a bare sid)."""
    name, args = _command_parts(s)
    if not name:
        return ""
    if not args:
        return name[:200]
    return ("%s %s" % (name, " ".join(args.split())))[:200]
```
