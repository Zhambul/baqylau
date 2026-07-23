# The web dashboard (`dashboard/` + `bin/claude-dashboard.py`)

A localhost web UI over the whole session estate: every session (live and
parked) with its mirror stream, scoreboard stats, agents, costs and errors —
plus the two things a terminal pane can't give you: **drill-down into any
agent's full activity timeline** and **toast/OS notifications across all
sessions** when a session starts asking you something or finishes its turn.

It is a CONSUMER, not a producer — read-only **except the control plane** (the
write endpoints below): everything it *shows* comes through
`core/sessionapi.py` (the one read-side door — [sessionapi.md](sessionapi.md))
and `plugins.activity()`. It writes no session state directly; its only state
writes are its own singleton pid-lock and audit rows. The control plane does
not write session state either — it drives the TERMINAL (types into a window /
opens a tab) through the `Frontend` interface, and Claude Code's own hooks then
produce the resulting state. See *Control plane (web writes)* below.

```
bin/claude-dashboard.py     the CLI: serve | start | stop | status | open
dashboard/server.py         HTTP + SSE + the notification watcher
dashboard/opshtml.py        paint ops -> HTML (the web presenter)
dashboard/static/           the single-page app (vanilla JS/CSS, no build step)
```

`./bin/claude-dashboard.py` (default verb `open`) starts the server if needed
and opens `http://127.0.0.1:8377` (`CLAUDE_DASH_PORT` overrides).

## Placement: a fourth dependency tier

`dashboard/` sits ABOVE core/plugins/frontends: it imports `core/`, the
`plugins` registry root (for `activity()`), AND `frontends/` (for the control
plane — the top consumer tier reaches the terminal the same way the bin/ entry
scripts do), and nothing imports it back except its bin/ entry and the tests. It
cannot live in `plugins/` — plugins never import each other, and the dashboard
needs the cross-plugin registry — and it isn't a `frontends/` terminal either
(the Frontend interface is about terminal control; the dashboard *uses* it but
has no panes of its own). The precedent is the bin/ renderers, which already sit
at this height; `dashboard/` is that tier made importable so the server is
testable in-process. It reaches a terminal ONLY through `frontends.get()` and
the `Frontend` interface — never a kitty-only attribute (the frontends contract
grep test enforces this).

## Server design (each choice rejects a specific trap)

Decisions inherited from the sessionapi design review (docs/sessionapi.md's
"web dashboard notes", now implemented):

- **Read-only except the control plane, 127.0.0.1 only.** The page shows raw
  command output and transcripts; it must never sit on a routable interface. The
  GET surface is pure read of SESSION state (the ⧉ copy endpoint *returns* text;
  the browser owns its clipboard) — but the two mirror-block gesture reads (⧉
  copy, click-to-view expand) leave their OWN audit rows (`web-copy`/`web-view`
  `state_files`), because they call `core/copy.collect`/`view_payload` DIRECTLY
  and so bypass every audit row the terminal's `claude-copy.py` entry writes for
  the same click; without them a web copy/expand was an audit blind spot next to
  a fully-traced terminal one. The only STATE writes are the control-plane POSTs
  (*Control plane (web writes)* below), which type into / launch a terminal and
  are guarded against the browser cross-origin vector.
- **`ThreadingHTTPServer` + per-request fresh `mode=ro` reads** — NOT the OTLP
  receiver's single-threaded request loop: sqlite connections are
  thread-affine, and concurrent SSE streams need concurrent handlers. No
  connection is shared across requests; every read goes through the API's
  `*_at()`/fresh-conn paths. In particular ops are read via `ops_at()` on the
  RESOLVED DB path (live or parked) — never `ops_after()`, whose live-path
  `connect()` would CREATE the DB and fake the session-alive signal for a
  parked session. (Same reason the click-to-view endpoint reads through
  `kv_at()`, the read-only twin added for it.)
- **Singleton + explicit lifecycle** — a `core/locks.py` pid-lock on
  `paths.DASH_DB` with the port bind as the second guard; started/stopped
  explicitly by the CLI. Deliberately NOT the OTLP receiver's 900s idle-exit +
  respawn-on-SessionStart: that lifecycle is correct for a receiver that only
  matters while sessions emit metrics, and wrong for a dashboard that must be
  up precisely when you're browsing PARKED sessions at midnight.

  **Opt-in auto-start is one-way.** With `CLAUDE_DASHBOARD_AUTOSTART=1` set
  (docs/wiring.md), a hosted SessionStart also makes a spawn-if-not-running
  attempt (`plugins/claude_code/split._maybe_autostart_dashboard`, alongside the
  OTLP fan-out in `cmd_open`): a cheap `locks.lock_holder` + `pid_alive` check —
  never a port bind from a hook — and, only when nothing is up,
  `core/spawn.spawn_detached` of `claude-dashboard.py serve`. This changes only
  the *start* trigger; the explicit-lifecycle story above is otherwise unchanged
  — there is still no idle-exit and no auto-STOP (you stop it with
  `claude-dashboard.py stop`). The dashboard's own singleton lock + port-bind
  second guard make a lost race harmless (a loser exits with an audited
  lock-denied/port-busy row), so spawning from every session is safe. OFF by
  default: with the env unset the gate returns before touching anything and
  audits nothing — the OTLP receiver's telemetry-gate precedent. The decision is
  audited on the `pane_events` row `cmd_open` already writes (`dash-autostart:
  spawned` / `already running (pid N)` / `spawn failed`).
- **gzip in one place.** `_send` — the single non-SSE response path — gzips its
  body (`Content-Encoding: gzip`, recomputed `Content-Length`, `Vary:
  Accept-Encoding`) when the client offers gzip and the body clears `GZIP_MIN`
  (~1KB); everything routed through it is text (JSON/HTML/CSS/JS/plain). SSE is
  never compressed — it holds the response open and writes incremental frames
  through its own `_sse_*` writers, so buffering it through gzip would break the
  stream.
- **Poll-path reads are memoized by change fingerprint.** The 1s global SSE
  tick rebuilds `sessions_payload` (≤`SESSIONS_LIMIT` rows) and the accounts
  strip re-scans the same DBs — uncached, that opened ~50 sqlite connections
  per tick (~55ms) for data that almost never changes. Two memo dicts
  (`_STATS`, `_ACCT`) key on `_db_sig`: the `(mtime_ns, size)` stat of the
  state-DB file AND its `-wal` sidecar. The WAL half is load-bearing — a live
  writer appends to the WAL without touching the main file until checkpoint,
  so a `(path, size)` key (the `_TITLES` pattern, fine for append-only
  transcripts) would serve stale numbers for exactly the sessions that are
  moving. The sig is taken *before* the read, so a racing write can only make
  a cached value newer than its sig (re-read next tick), never staler. Each of
  these memos (and the `(path, size)` ones — `_TITLES`, `_CTX`, `_GIT`,
  `_DIRTY`) is a `sessionapi.BoundedLRU(MEMO_CAP)`, not a bare `dict`: the value
  side is freshness-checked but the KEY set is one entry per session/transcript/
  cwd ever seen, so an unbounded `dict` grew for the whole life of the days-long
  singleton server. The LRU caps it well above the live working set
  (`SESSIONS_LIMIT` sessions + their agents), so active sessions never thrash
  and only paths that scrolled out of discovery age out — re-derivable, so a
  re-seen path just re-reads once.
  The other historical poll-path sink was `sessionapi.sid_chain()`'s adopt-map
  scan on every audit-backed read — fixed at the source with the audit index
  `ix_state_act` (docs/sessionapi.md, *Fork-aware queries*), which took
  `/api/session` from 300–1000ms to ~25ms.
- **Audit shape**: `start` spawns `serve` through `core/spawn.spawn_detached`
  (the `A.spawn` row), and `serve()` runs inside `core.tail.stream_lifecycle`
  (kind `dashboard`) — the server's lifetime is a `streams` row whose
  `end_reason` says how it exited (`stopped` / `port-busy` / lock-denied
  errors ride `A.error`; a crash closes the row as `crash` with a traceback in
  `errors`). Request-handler failures audit once per request via `A.error`
  with the path.

## The web presenter (`opshtml.py`)

The third presenter over vocabularies owned elsewhere (the parse/paint
precedent): `core/ops.py` owns the op shapes; `claude-mirror.py` paints them
to ANSI at pane width; `opshtml.py` renders them to HTML. Width-dependent
layout deliberately does NOT port — wrapping, gutter repetition, rule length
and chip truncation are CSS facts in a browser (`pre-wrap`, `border-left`,
block elements, `text-overflow`), so each op maps to a structured block and
`codefmt.render` runs at an effectively-unwrapped width (`CODE_W`).

**Main-agent-only (the op `src` stamp).** The web stream shows the MAIN
agent's activity only, unlike the terminal mirror, which paints everything.
`core/ops.py` stamps every op with its producer source (`src`:
`sub:<agent_id>` / `team:<agent_id>` / `codex:<label>`; absent = the main
session) — an ambient per-process value, because every detached streamer
serves exactly one source: the substream calls `set_src` at init, which also
exports `$CLAUDE_OPS_SRC` so the fg/bg/monitor tailers it spawns inherit the
stamp through `stream_env`'s environ copy; the codex watcher sets the env on
SECONDARY-source spawns only (a STANDALONE codex host's own rollout is the
main agent — stamping it would blank that session's web mirror); the one
in-hook-process producer of agent ops (a subagent's monitor header,
`monitor_fmt`) passes the explicit `emit(src=)` kwarg. `op_items` drops
stamped ops (and `server._cut_blocks` skips them when sizing the backlog
window, so "newest N blocks" means N *visible* blocks). What survives of an
agent is the lead's own record of it — the `subagent_fmt` launch header and
finish chip — PLUS the two endpoints of the subagent's own contribution: its
`⇢ prompt` and `⇠ result` blocks. Those are the one exception to the
main-agent-only rule: the substream stamps them `web=1` (a keep-on-the-web
override, `core/ops.py`'s "web" field) so `op_items` keeps them despite the
`src` stamp, while everything in between (its messages, commands, file ops)
stays drill-down only. The full detail lives in the per-agent drill-down
(`plugins.activity()`), which reads transcripts, not ops. Why surface just
those two: a subagent reads on the dashboard as "here's what I asked it, here's
what it gave back," without the wall of intermediate work the terminal pane
shows inline. A surfaced prompt/result chip opens with the agent's label (not a
`▶▷◉■` command glyph), so the client's heuristic classifier files it under the
`agents` filter, same as before. Why filter at render, not at write: the
terminal mirror must keep painting everything (same ops table, two presenters),
and the stamp doubles as provenance in the audit's op rows. Pre-stamp history
(parked DBs) has no `src` and renders as before — the client's heuristic
`agents` filter chip still covers those.

**Security — the `neutralize()` analog.** Op text is raw command output
(attacker-adjacent bytes; the `@kitty-cmd` replay incident is the terminal
form of this bug class). Every character is `html.escape`d inside
`ansi_html()`; input first passes `render.neutralize()` so only the two
sanctioned survivors — SGR styling and OSC 8 hyperlinks — are ever
*interpreted*, exactly mirroring the terminal renderer. SGR runs become
inline-styled `<span>`s (truecolor verbatim; 256/16-color mapped); a
`claude-copy:///<key>/<gid>/<what>` OSC 8 link becomes
`<a class="cc" data-cc=…>` which the app intercepts — copy verbs call
`/api/session/<sid>/copy/<gid>/<what>` (served by the SAME `core/copy.collect`
the terminal click handler uses — one owner of "what does ⧉cmd copy") and put
the result on the clipboard; the `view` verb fetches the rendered
`view:<gid>` stash from `/view/<gid>` and toggles it inline, the web twin of
click-to-view. Any other `http(s)` URL becomes a plain `target=_blank` anchor;
**any other scheme is dropped to the link's plain escaped label with no
anchor** — the same `http(s)`-only gate `_md_inline` applies, because OSC 8 is
one of neutralize()'s two survivors and raw output could otherwise print
`\x1b]8;;javascript:…` (or `data:`) and mint a clickable href in the dashboard
origin (an XSS-on-click the terminal, having no `href`, can't have).

**Markdown for conversation text** (`opshtml.md_html`). Assistant messages,
user prompts and teammate mail are markdown in practice, so the dashboard
renders them as markdown instead of a flat `<pre>` — a small dependency-free
subset (headings, bold/italic, inline & fenced code, un/ordered lists,
blockquotes, `http(s)` links, rules, pipe tables, paragraphs). Two rules
dictate the shape. The **no-build/no-deps rule** rules out a markdown library,
so it is hand-rolled (~200 lines). The **escape rule** (the `neutralize()`
analog) rules out any "escape later" design: block *structure* is detected on
the raw lines (the sigils `#-*>`` ``[]()|` are ASCII and emit nothing
themselves), but every fragment that reaches the page is `html.escape`d at its
leaf — `_md_inline`
escapes before layering emphasis, and a fenced block is highlighted through the
single lexer owner (`render.lexer` via `coderender.render_code`) to ANSI and
then `ansi_html` (which escapes), falling back to plain escaped text when
pygments/the lexer is absent. So `<script>` survives as escaped text in every
context, and a `javascript:` link renders as literal text (only `http(s)` URLs
become anchors). Bare `http(s)://` URLs in prose are **autolinked** — people
paste URLs without `[label](…)` dressing, and a dead URL in a message bubble
is exactly the thing you want to click. `_md_inline` stashes both link kinds
(markdown links and autolinks) as placeholders before the emphasis pass, so a
URL's `_`/`*` can never be chewed into `<em>`/`<strong>` (emphasis *around* a
URL, and inside a markdown label, still renders) and the autolink pass can
never re-match inside an already-built `href`; `_trim_url` peels the
sentence's trailing punctuation (`.`,`)` only while unbalanced — a wiki-style
`…/Foo_(bar)` survives — and the `&lt;`/`&gt;` of a raw `<…>` wrapper) off
the match, and URLs inside code spans stay literal text. Malformed markdown
never raises — the outer guard returns
escaped plain text. Pipe tables are the one block needing **two-line
lookahead** (a header row with a `|` over a `|---|`-shaped delimiter row with
the *same* cell count — the GFM rule; a mismatch stays a paragraph), checked
both in the main loop and in the paragraph accumulator so a table directly
under a text line isn't swallowed into it; delimiter colons map to a closed
alignment-class vocabulary (`ta-c`/`ta-r`), body rows pad/truncate to the
header width, `\|` is a literal pipe, and the accepted subset limitation is
that a bare `|` inside a backtick code span still splits the cell. Wide tables
scroll horizontally inside their own `.md-tbl` wrapper instead of stretching
the bubble. The timeline endpoints (`/activity`, `/agent`) add an
`html` field to message/prompt/teammsg entries *additively* (the raw `text`
stays), and `app.js` uses it via `innerHTML` (server-escaped by construction),
falling back to `pre(text)` when absent.

**Recap bubbles** (Claude Code's away-summary). Claude Code writes a one-line
summary of what happened while you were away — automatically after ~3 min idle,
or on demand via `/recap` — as a `type=system` `subtype=away_summary` transcript
line whose plain-string `content` is the summary (disable with
`CLAUDE_CODE_ENABLE_AWAY_SUMMARY=0` or `/config`). `transcript.parse_line`
surfaces it as a `recap` record (a sibling of `compact`, the other system
subtype it reads), stripping the trailing `(disable recaps in /config)` hint
(a terminal-only menu, noise in the web bubble) and dropping a hint-only /
empty summary entirely. Both read models carry it: the merged mirror
`conversation()` emits a `recap` bubble (`opshtml.msg_html` — an `↩ recap`
label, cyan, no rewind ↶ since it isn't a re-runnable prompt), and the
drill-down `timeline()` a `recap` entry (`app.js timelineEntry` — a `k-recap`
chip). No new audit wiring: a recap is derived from the already-audited session
transcript (its path is in the audit `sessions` row), like every other
conversation record.

**Rich tool rendering** (`opshtml.tool_html` / `tool_output_html`). A tool entry
in the drill-down timeline used to dump its input as raw JSON; the presenter now
renders the well-known built-in tools structurally, reusing the single owners of
their payload shapes rather than re-encoding them: a **Bash** command through
`codefmt.render` → `ansi_html` (the same `_code_block` the `code` op uses) with a
dim description; an **Edit/MultiEdit/NotebookEdit** input as a line-numbered
red/green diff via `plugins.claude_code.tools.diff_rows` (empty result dict → its
difflib fallback over the input strings), with the `replace_all` flag shown; a
**Write** as a file headline plus content highlighted through `coderender` when
the extension maps to a lexer, capped at `WRITE_CAP` lines with an elision note;
a **Read** as `streamfmt.file_line`'s `verb(name)[ extent]` one-liner (extent from
`tools.read_extent`); and **Grep/Glob/WebFetch/WebSearch/Task/SendMessage** as a
definition list of their fields (long values first-lined). Unknown tools return
`None`, so the timeline keeps its escaped-JSON fallback. The enrichment is the
same additive post-processing markdown uses (`server._mdify`): tool entries gain
`input_html` and — only where it differs from a plain `<pre>` (Bash output, which
may carry ANSI) — `output_html`; raw `input`/`output` stay untouched, and `app.js`
falls back to the JSON dump / plain `<pre>` when a field is absent. Escape-first
throughout — every leaf rides `ansi_html` or `html.escape`, so a `<script>` in an
`old_string` survives as escaped text.

**Why not an xterm.js embed** (the Hermes harness does one): the mirror's
content is not a pty — it's a structured op stream that reflows. An embedded
terminal would need a server-side repaint-to-ANSI at the browser's column
width on every resize (re-implementing claude-mirror.py per client), and adds
the project's first frontend build dependency. Structured HTML + CSS gets
reflow for free and keeps the no-build rule.

## Endpoints

| Route | Returns |
|---|---|
| `/` `/static/<name>` | the app (whitelist — no path resolution on user input) |
| `/api/sessions` | discovery list + per-row stats + tab state + `ctx` (context saturation, below) + `git` (branch/worktree/root/dirty, below) |
| `/api/session/<sid>` | overview: `session()` + error count + `ctx` + `git`; agent rows carry their own `ctx` |
| `/api/session/<sid>/ops?after=N` | `{last, html: […]}` server-rendered ops |
| `/api/session/<sid>/history?before=<opid>&blocks=N` | the previous `N` stream blocks OLDER than op id `before` (lazy backlog): `{oldest, items}`, `oldest` the next cursor (0 = exhausted) |
| `/api/session/<sid>/backlog` | the initial newest-`TAIL_BLOCKS` slice (`merged_backlog`): `{last, mpos, oldest, items}` — the gzip-able GET twin of the SSE fresh-connect backlog; the page fetches this first, then connects the session SSE with the cursors (*Lazy backlog* below) |
| `/api/session/<sid>/activity` | main-thread timeline (`plugins.activity(sid)`) |
| `/api/session/<sid>/agent/<aid>` | one agent's timeline (carries a `pos` byte cursor for the live SSE) |
| `/api/session/<sid>/errors` | swallowed-exception rows |
| `/api/accounts` | `[{slug, label, alias, usage}, …]` — the launchable subscription accounts (`plugins.accounts`) plus each one's freshest captured usage: every status-line rate-limit window (the 5h/7d pair, aggregated across sessions, served EFFECTIVE — a rolled-over window reads 0 with no reset) PLUS per-model weekly windows fetched from the OAuth `/usage` endpoint and merged in (`plugins.model_windows`, *Per-model usage bars*); each row also carries `five_hour_eff`, `sched_score` (weekly-quota perishability), and `sched_ok` (5h safety gate) for the new-session default-account picker (*Default account*); backs the new-session picker and the top usage strip |
| `/api/commands?cwd=<dir>` | the "/" menus: `[{name, desc, src}, …]` — CLI built-ins + the directory's discovered `.claude` commands/skills (`plugins.slash_commands`); cwd-keyed, not sid-keyed — the new-session form completes for a directory with no session yet (non-directory → built-ins + user-level) |
| `/api/resumable?cwd=<dir>&limit=25&q=<text>` | the new-session **resume picker**'s rows (`resumable_payload`): the directory's sessions (canon-cwd-scoped, newest-first, `limit` clamped to `RESUMABLE_MAX`), each `{sid, title, last_active, live, model, effort, account{slug,label}}` — enough to reuse a session's model/effort on resume (*Resume picker* below); `q` filters by title+sid across the directory's WHOLE history (discovery scans up to `RESUMABLE_SCAN`, not just the newest — the client can't); blank/unknown cwd → `[]` |
| `/api/session/<sid>/view/<gid>` | rendered click-to-view stash (HTML); leaves a `web-view` `state_files` row (`gid`/`ok`) — the web twin of the terminal ⧉view toggle's audit |
| `/api/session/<sid>/copy/<gid>/<what>` | copy text (`core/copy.collect`); leaves a `web-copy` `state_files` row (`gid`/`what`/`chars`) — the web twin of the terminal `copy` row (the dashboard calls `collect()` directly, bypassing `claude-copy.py`'s audit) |
| `/api/dictate` | `{available}` — Deepgram key-file probe; the page renders mic buttons iff true (*Web dictation* below) |
| `POST /api/dictate/token` | **control plane:** `{"sample_rate"}` → `{token, expires_in, ws_url}` — a ~30s Deepgram grant JWT + the fully-assembled live-listen URL; the browser connects to Deepgram DIRECTLY (*Web dictation* below); 400 bogus rate, 501 no key, 502 grant failed |
| `/api/dirs/hidden` | `{group_key: hidden_at_epoch}` — the directories the `✕` hid from the list (the durable prefs store, `prefs.hidden_dirs()`); the page seeds `S.hidden` from this on load (*Hidden directories* below) |
| `POST /api/dirs/hide` | **control plane:** `{"cwd"}` (the group key `group_dir\|\|cwd`) → stamp `time.time()` into the hidden-dirs prefs and return `{ok, hidden}` (the full map); the group vanishes until a session started after now shows up in it (*Hidden directories* below); 400 non-string key; **409 when the directory has an active (live) session** |
| `POST /api/session/<sid>/message` | **control plane:** `{"text", "attachments"?, "clear_draft"?}` → type it (+ Enter) into the session's kitty window (`Frontend.paste_text`); `attachments` are `@`-mention paths prepended to the text (*Web attachments* below); replies `{ok, queued, tab}` — `queued: true` when the send landed mid-turn in Claude Code's own message queue (`QUEUE_TABS`); 409 headless, 400 empty, 503 no terminal |
| `POST /api/session/<sid>/command` | **control plane:** `{"cmd", "arg"?}` → the scoreboard's quick-command row (*Web quick commands* below): a FIXED vocabulary of the TUI's own slash commands — `compact` (argless), `model` (arg: `_MODEL_ARG_OK`), `effort` (arg: `EFFORTS`) — pasted like a composer send; model/effort auto-answer the TUI's switch-confirm menu (`dashboard/confirmdialog.py`, non-queued only); replies `{ok, queued, tab, confirm?}`; 400 off-vocabulary, 409 headless or a dialog open (red tab), 503 no terminal |
| `POST /api/session/<sid>/stop` | **control plane:** close the session's kitty tab (`Frontend.close_tab` — a graceful stop: Claude Code exits on the HUP and SessionEnd runs the normal lifecycle); 409 headless, 503 no terminal |
| `POST /api/upload` | **control plane:** `{"sid"?, "name", "mime", "data"(base64)}` → stage the bytes under `paths.UPLOADS_DIR/<sid\|staging>/` and return `{path(abs), name, mime, is_image}`; the composer injects `path` as an `@`-mention (*Web attachments* below). JSON+base64 (no multipart), cap raised to `UPLOAD_MAX`; 400 bad base64, 413 oversize |
| `POST /api/clientlog` | **frontend audit** (audit-only, no terminal write): `{"client", "conn"{online,view,es,conn}, "events":[{t,sid,ev,…}]}` → one `web-client` `state_files` row per event, scoped to each event's own `sid` (*Frontend audit (clientlog)* below); the browser reporting the transport + connection + JS-error timeline the server can't see; ≤`CLIENTLOG_MAX` events, scalars only; 400 non-list events |
| `POST /api/sessions/new` | **control plane:** `{"cwd", "account"?, "resume"?, "continue"?, "model"?, "effort"?, "prompt"?, "attachments"?}` → launch `<account-alias> [--resume sid \| --continue] [--model m] [--effort e] [prompt]` in a new tab at `cwd` (`Frontend.launch_tab`); `account` is a switcher slug → its vetted alias command word (default `claude`); responds `{ok, win}` — `win` the new tab's window id when the terminal reported one (the page's exact jump-match key, "" otherwise) — and starts the `_launch_wake` SSE hurry-up watch; 400 bad cwd/model/effort/resume/account, 503 no terminal |
| `POST /api/session/<sid>/rename` | **control plane:** `{"name"}` → append the `agent-name` naming record to the session's transcript (`plugins.set_session_title` — the `/rename` channel, docs/session-naming-findings.md) and, when a live window exists, `Frontend.set_tab_title` (*Web rename* below); works for live AND parked sessions; replies `{ok, title, tab_retitled}`; 400 empty name, 409 no transcript / unsupported (a codex rollout), 502 append failed |
| `POST /api/session/<sid>/…` | **control plane**, each with its own section below: `interrupt` (Esc in the session's window), `rewind` (mid-turn cancel-edit, the double-Esc), `rewind-to` (*Web rewind* — the full checkpoint restore), `answer` (*Web ask* — AskUserQuestion; a `chat`+`message` body routes a typed preview-question answer through "chat about this" then delivers the text) + `ask-draft` (persist the unsubmitted ask selections, no terminal write), `composer-draft` + `composer-queue` (persist the unsent message / pending ⧗ chips, no terminal write — *Web composer draft* / *Web composer queue*), `hint-audit` (audit-only beacon for the optimistic composer bubble's lifecycle — a `web-hint` state_files row, no terminal write, no session state — *Optimistic composer bubble*), `plan-options` + `plan-decision` (*Web plan mode* — ExitPlanMode), `notify` (`{"muted"}` → opt this session in/out of the deferred Telegram alert, a prefs write, no terminal — *Telegram alerts* below) |
| `/events` | global SSE: a `hello` (the server's `BOOT_ID` — the EventSource auto-reconnects across a server restart, and a changed boot id tells an OPEN page its loaded JS may be stale; the client toasts "dashboard updated — refresh", click to reload. Twice a redeploy shipped under an open page and its old handlers running against the new server read as a product bug), then a full `sessions` snapshot on connect + on membership/order change, `sessions-delta` `{rows}` for content-only changes (paused-blind per-row diff, wire-stripped rows — *The list renders once, then patches* below) + `notify` toasts |
| `/events/session/<sid>?after=N&mpos=M` | per-session SSE: `ops`/`msgs`/`stats`/`agents`/`costs`/`ctx`/`git`/`title`/`running`/`tab`/`errors`/`monitors`/`jobs`/`memory`/`ask`/`ask-draft`/`plan`/`tasks`/`composer-draft`/`composer-queue`, each on change; a fresh connection's first `ops` event is the merged backlog, tail-limited, carrying `oldest` (see below) |
| `GET /api/session/<sid>/monitors` | the session's Monitor tool runs (command/description/lifetime + events, merging transcript + audit streams state) for the monitors tab (*Monitors tab*) |
| `GET /api/session/<sid>/jobs` | the session's background Bash jobs (command + lifecycle state, merging audit streams + ops) for the jobs tab (*Jobs tab*); output via the `/copy/<task>/out` endpoint |
| `GET /api/session/<sid>/memory` | the memory-wiki notes the session touched (`{path, name, verb, agent, count, ts}`, from the `memory` kv) for the memory tab (*Memory tab*) |
| `GET /api/session/<sid>/note?path=<abs>` / `?stem=<stem>` | one memory-wiki note rendered for the viewer (`{name, frontmatter, html, backlinks, missing}`); path-traversal-guarded to `~/wiki/01` (*Memory tab*) |
| `/events/agent/<sid>/<aid>?pos=N` | one agent's LIVE timeline SSE: `entries` (new increment entries) + `resolve` (cross-increment tool results), from byte cursor `N` (see below) |

SSE is plain polling server-side (`TICK_S` per session, `GLOBAL_TICK_S`
global) pushed over a held response — no websockets dependency, and
`EventSource` gives the client reconnect for free (the app reconnects with
`?after=<last seen op id>` so nothing repeats).

## Cache-busting (`?v=<BOOT_ID>`)

Static responses are sent `Cache-Control: no-store`, but that only asks a
browser not to cache — it can't EVICT bytes already cached, and a remote client
(mobile Safari especially, or a CDN in front — the public origin is a Cloudflare
tunnel) can keep serving a stale `app.js`/`style.css` across a dashboard restart.
That is exactly the "does NOT hot-reload" hazard (CLAUDE.md): a fix shipped, the
origin served it, the phone kept the pre-fix bytes (the *memory wikilinks don't
follow on mobile* report was really this — the fix was live at the origin the
whole time). So `static()` rewrites the sub-resource URLs in `index.html` to
`/static/app.js?v=<BOOT_ID>` / `/static/style.css?v=<BOOT_ID>` (`BOOT_ID` is
bumped every server start). `index.html` is itself `no-store` AND is the main
document a reload always refetches, so each restart hands the browser fresh
`?v=` URLs that nothing (browser or CDN, which key by full URL incl. query) has
cached. The `?v=` is a cache key only — `do_GET` parses the path (query
stripped), so `static()` still serves the same file. A hard reload is no longer
required for a remote page to pick up new JS/CSS; a normal reload suffices.

## Control plane (web writes)

The dashboard was born read-only; these POST endpoints deliberately break
that charter so you can drive a session from the browser: **message a running
session**, **interrupt its turn** (an Escape key press), **close one** (its
whole tab), **launch a new one** (fresh or `--resume`, *Resume picker* below),
and **rename one**. All but one reach
the TERMINAL through the `Frontend` interface (`send_text` / `send_key` /
`launch_tab`, over
the same silenced `kitten @` machinery the tab painter uses), and Claude Code's
own hooks then produce whatever state results. The ONE exception that writes
session state is `rename` (*Web rename* below): a single atomic O_APPEND line
into the session's transcript JSONL — the same record Claude Code's own
`/rename` writes, through the record shape's owner
(`plugins/claude_code/transcript.set_session_title`), never a re-encoding.
The dashboard stays a consumer of
session data; it is now also a driver of the terminal.

**The threat: drive-by RCE via the browser.** These endpoints type into a
terminal, so an unprotected one is remote code execution triggered by any web
page you happen to have open. A malicious page cannot reach a routable
interface (we bind 127.0.0.1 only), but it CAN aim a **simple** cross-origin
`POST` at `http://127.0.0.1:8377` from the victim's own browser — no preflight,
no read of the response needed, the type-into-terminal side effect is the whole
attack. So the defense makes every control-plane POST a **non-simple** request
the browser must preflight, and we never let the preflight pass
(`dashboard/server.py` `_post_guard`):

- **JSON content type required** (`Content-Type: application/json`) — a simple
  request can only be `text/plain` / form encodings, so this alone forces a
  preflight; a wrong type is `415`.
- **A custom header (`X-Claude-Dash: 1`) OR a present, allowlisted `Origin`** —
  the caller must prove same-origin one of two ways, because a cross-origin page
  can forge NEITHER. The header is what a `<form>` or a simple `fetch` cannot set
  (forcing the preflight below); the Origin-allowlist alternative exists so
  `navigator.sendBeacon` — which physically cannot set a custom header — is
  accepted for the close (see *Close via sendBeacon*). A request with neither is
  `403`. A cross-origin request always carries its real (non-allowlisted) Origin,
  so it can never satisfy the Origin branch, and it can't set the header branch —
  the Origin allow-list is therefore the actual CSRF gate; the header was always
  belt-and-suspenders.
- **We answer `OPTIONS` with a bare `501`** (no `do_OPTIONS`, so no
  `Access-Control-Allow-*` headers ever) — a forced preflight therefore fails
  and the browser never sends the real POST. Same-origin requests never
  preflight, so the dashboard's own page is unaffected.
- **Origin allow-list** — any `Origin` header present and not
  `http://127.0.0.1:<port>` / `http://localhost:<port>` is `403`.
  `CLAUDE_DASH_ORIGINS` (comma-separated full origins) EXTENDS the set for a
  proxied deployment (docs/remote.md) — it never replaces the local ones and
  is not an exposure switch: the bind stays `127.0.0.1`.
- **`CLAUDE_DASH_READONLY=1`** kills the control plane outright (every POST
  `403` before any other guard) — for remote-viewing days when the hands
  should stay home.
- **Body cap** (`POST_MAX`, 64 KiB) and a JSON-object check; a guard rejection
  closes the connection (an unread body would desync HTTP keep-alive).

`POST /api/session/<sid>/message` `{"text"}` resolves the session's
`kitty_window_id` (`sessionapi.session_row`) and, when it has one,
`Frontend.send_text(win, text)` types the text plus a carriage return.
**Windowed sessions only:** a headless / `claude daemon run` session has no
window (same scoping as tab colours and toasts), so it returns `409` — the
composer is disabled with a hint for it. When it CAN send, the composer takes
focus as the mirror view opens, so typing works immediately without a click
(safe because every document-level gesture — Esc, the ⌃ readline keys, ⌃⇧←/→
— is focus-independent; autofocus only redirects plain typing). Empty text is
`400`. The text rides
kitten's `--stdin` verbatim (no shell, no escape interpretation). **The Enter
is a separate second `send-text` call** (`SEND_ENTER_GAP_S`, 150 ms, after the
message write — `frontends/kitty.py kitten_send_text`): appended to the same
write, Claude Code's chunk-based paste detection sometimes coalesced text+CR
into one stdin read and treated the CR as a pasted *newline* — the message sat
in the terminal's draft with a trailing blank line, never submitted, and only
sometimes (whether the TUI's event loop picked the bytes up in one read or two
is scheduling). A gap-separated CR always arrives as its own read = a real
Enter keypress; both writes must succeed for the send to report `ok`.

**Queued messages.** Claude Code natively queues a message typed while a turn
is running and delivers it when the turn ends — a composer send rides exactly
that (it types into the TUI either way), so the *mechanics* need nothing from
us. The *feedback* does: a mid-turn message reaches the transcript only at
delivery, so from the page it would just vanish for minutes. The endpoint
therefore reports which case happened — the response carries `queued` (tab
state at send time ∈ `QUEUE_TABS` = `thinking`/`working`/`executing`) and
`tab`, and the same tab state rides the `web-send` audit row. The page shows a
queued send as a ⧗ chip under the composer (and the send button reads
"queue" while busy — a cosmetic client-side mirror of `QUEUE_TABS`; the
server's verdict is the chip authority). A chip is removed when its prompt
record actually arrives in the stream — `_conv_items` items additively carry
`kind` and, for prompts, the raw `text`, and `drainQueue` matches on exact
text — because the transcript is the ONE delivery signal: tab transitions are
useless (green flips busy again the instant a queued prompt starts
processing), and the chip's ✕ only hides it (the message is already in the
TUI's queue; the web cannot unqueue it). The subtlety that made this silently
break (2026-07-20): a queued message, when delivered, is written to the
transcript ONLY as a `queued_command` **attachment** record (`{"type":
"attachment", "attachment": {"type": "queued_command", "commandMode":
"prompt", "prompt": …}}`), NEVER as the plain `user` string an idle-typed
prompt produces — so `transcript.parse_line` dropped it, the mirror never
showed the delivered message, AND the chip never drained (the "stuck queued
message, missing from the transcript" report). The fix surfaces that
attachment as a `{"kind": "prompt"}` record (only `commandMode == "prompt"` —
the harness's `task-notification` re-injections use the same attachment but
are not user turns), so both the bubble and the drain work.
`awaiting-command` (red) is
deliberately NOT in `QUEUE_TABS`: a dialog is up and typed text goes to the
DIALOG, not the input box — a send then is neither immediate nor queued, and
claiming "queued" would be a lie.

**Interrupt flips the button out of "queue" immediately** (2026-07-20). Claude
Code fires NO hook on interrupt, so after an Esc the tab can sit stale-busy —
especially from `executing`, where not even the escape-recheck spawns (it only
covers magenta `thinking`/`working`). The composer then kept reading "queue"
even though the turn had ended and a plain send is what would happen. So
`interruptSession`, on a successful interrupt of a `BUSY_TABS` tab,
optimistically drives `composerMode`/`cancelMode`/`stopMode`/`quickMode` to the
your-turn state (`awaiting-response`) — the button reads "send" at once (and
■ stop / ⊘ cancel grey out, since the turn just ended). This is only a
client-side hint: the escape-recheck's green (or the next prompt's tab event)
is what reconciles the real state, and if the turn actually kept going that
next `tab` event flips the button right back to "queue". Terminal-side Esc
still has no signal at all (the known no-hook gap), so its stale-busy label
only clears on the next real hook.

**A red `awaiting-command` tab refuses every Esc-sending gesture** (2026-07-20).
Red means a MODAL DIALOG is open — AskUserQuestion, ExitPlanMode, or a
permission prompt — and an Escape there DECLINES/dismisses the dialog, it does
not interrupt a turn. A `cancel-edit` gesture (double-Esc) once landed its
Esc-Esc on an open ask and killed the very answer the user was giving through
the web ask card (the tab read "User declined to answer questions", and the
web answer then hit "no question dialog on screen"). So `awaiting-command` is
DELIBERATELY excluded from the server's `BUSY_TABS` and the page's `CANCEL_TABS`,
and `post_interrupt`, `post_rewind` (cancel-edit) AND `post_rewind_to` all bail
with a 409 *"a dialog is open — answer it first"* (`_dialog_open_guard`,
mirroring post_command's own red-tab refusal). Client-side the ■ stop / ⊘ cancel
buttons disable on red, and the keyboard Esc gesture / ↶ rewind button swallow
themselves with a toast pointing at the card. The ask / plan / confirm cards are
the response path; the 409 is the authoritative backstop for a stale page that
still believes the tab is cancelable.

**Web attachments (images/screenshots + files).** The composer and the
new-session prompt accept attachments the way the Claude Code TUI does — paste a
screenshot (`onpaste` over `clipboardData.items` of kind `file`), drag-drop
files onto the composer/prompt box, or the 📎 picker. Claude Code has NO CLI
flag or stdin channel for images, so the mechanism reuses its ONE native path:
an `@path` mention in the message text, which Claude Code itself resolves and
attaches (an image becomes an image content block). The dashboard's job is only
to get the bytes onto disk and put the path in the message:

- The browser base64-encodes the file and POSTs `POST /api/upload` (`{sid?,
  name, mime, data}` → `{path, name, mime, is_image}`). Transport is
  JSON+base64, NOT multipart — it keeps the whole `_post_guard` browser-vector
  defense (same-origin + `X-Claude-Dash` + read-only switch) with no boundary
  parser to write. `_post_guard(max_bytes=)` raises the cap to `UPLOAD_MAX`
  (~14 MiB, a base64-inflated 10 MB image — Claude's per-image ceiling) for this
  one endpoint; every other POST keeps the tiny 64 KiB `POST_MAX`. Bad base64 is
  a 400; an oversize `Content-Length` is refused by the guard (413 / a reset,
  the `_reject` close-without-drain contract).
- `post_upload` writes the bytes under `paths.UPLOADS_DIR`
  (`~/.claude/baqylau-uploads/<sid>/`, or a shared `staging` bucket for the
  new-session form which has no sid yet) — durable ~/.claude, OUTSIDE any repo
  working tree, so an uploaded screenshot never dirties `git status`. The
  filename is slugged to a basename (a `../` name can't escape the dir), prefixed
  with a uuid. Every write is a `web-upload` `state_files` row (`ok`, `bytes`,
  `name`, `mime`); a write/decode failure adds an `A.error`. `serve()` best-effort
  prunes attachments older than a week (`_prune_uploads`) — the bytes are only
  needed until Claude Code has read them.
- On send, the composer prepends the vetted paths as leading `@path` mentions
  (`_with_attachments`: `"@p1 @p2\n" + text`, paths-then-text like the TUI's
  paste-then-type order) and rides the existing transport — `paste_text` for a
  live send (`post_message` accepts `attachments`), the launch argv for a new /
  parked-resume launch (`post_new_session` accepts `attachments` too). A message
  with attachments but no text is valid (the mention alone). `web-send` /
  `web-launch` rows carry the attachment count.
- **Security:** `_attachment_paths` accepts a path ONLY if it resolves inside
  `UPLOADS_DIR` and exists — a page cannot smuggle an arbitrary filesystem path
  into an `@`-mention. A rejected path is silently dropped; if nothing valid is
  left and there's no text, the send is a 400.
- The browser shows pending attachments as removable chips above the input
  (image thumbnail from a local `URL.createObjectURL`, no server round-trip;
  📄 + filename otherwise); an in-flight upload dims the chip and a send waits on
  it. Attachments are NOT persisted into the `composer-draft` kv, so a reload
  drops the pending chips (the staged files themselves survive on disk until the
  prune) — a deliberate scope limit; the draft machinery stays text-only.

**Resume & send (a parked session's composer).** A parked session's composer
is NOT disabled — everything passive works exactly like live (typing, the
"/" menu, dictation; all free drafts), and the one send button, relabeled
**"resume & send"**, is the single door from parked to live. Pressing it
POSTs the existing `/api/sessions/new` with `{cwd, resume: <sid>, prompt:
<text>, account: <the session's own statusline-stashed slug>}` — so the
message rides the LAUNCH ARGV (`claude --resume <sid> "<text>"`) and Claude
Code consumes it at startup itself. Why not enable the /message endpoint and
deliver after waking: "kitty tab exists" ≠ "the TUI's input is ready", and
text typed into a half-started TUI gets eaten (the same class of race the
bracketed-paste and DRAFT_CLEAR_GAP_S notes above exist for) — argv delivery
has no readiness window at all. The armed `armJump(cwd, sid)` watch then
follows the revived session (SessionStart under the OLD sid, adopt-fork
after — the *jump* section's resume case), the toast says "resuming
session", and on ANY failure (dead cwd → 400, no terminal → 503) the draft
stays in the box — nothing is lost on a failed wake. A launch that POSTs OK
but never produces a session (claude fails to boot after the command returned,
so no SessionStart, so the watch never hits) is the one case the success path
can't see: the composer disables on send and would stay dead forever (the
success branch has no `finally`). The watch's `onfail` closes it — fired by
`jumpFail` when the 120 s `JUMP_TIMEOUT_MS` elapses with no arrival, it
re-enables the composer, re-stashes the draft, and toasts *resume timed out*
(guarded on still being that session's composer, so a user who navigated away
mid-wait is never yanked). The heavyweight action
stays deliberate by wording alone: from the iPad, "resume & send" opens a
real kitty tab on the laptop — the label is the consent. Reused, not new:
the launch is the form's own audited `web-launch` path (the row carries
`resume` + `account` + `ok`), so there is no new server surface and no new
audit row kind. **Live-session guard:** `post_new_session` REFUSES a
`resume: <sid>` whose sid already has a live tab (`fe.window_for_session`, a
fresh kitten scan) — a second `claude --resume <sid>` would run a duplicate
process against the SAME transcript (two tabs, interleaved writes). It's a
409 (`{error, sid, win}` — the page can focus/message the existing tab) with
a `web-launch` `ok: false` row carrying the live `win`. The page only issues
a resume-launch when it thinks a session is parked, but a STALE page — e.g.
after the dashboard restarts and the browser's SSE drops, so its live/parked
snapshot freezes — can misjudge a live session; this is the server-side
backstop (the observed bug: a restart mid-session, then messaging spawned a
duplicate tab per send). Fresh launches are unaffected.
Headless-live sessions (live, no window) stay disabled —
they aren't asleep, resume is the wrong medicine — and their mic button is
now honestly `disabled` (dim, inert) instead of a live-looking button that
swallowed clicks (`ta.disabled` guard in `dictation.start`).
**Gone-transcript guard:** a parked session whose transcript `.jsonl` no
longer exists on disk CANNOT be resumed — `claude --resume <sid>` finds no
conversation and the freshly launched tab exits at once, a silent dead tab the
user reads as "resume did nothing" (observed on a short slash-command
aggregator session whose file was never persisted, 2026-07-21). Two layers
close it: (1) `session_payload` stamps `transcript_missing` (the session's
KNOWN transcript path — its audit row — is absent on disk; an empty/unknown
path is NOT flagged, we can't prove it's broken), and the composer disables the
door with "this session's transcript is gone — it can't be resumed" instead of
offering a button that dies; (2) `post_new_session` is the authoritative
backstop — a `resume: <sid>` whose known transcript path is absent 410s
(`{error, sid}`) with a `web-launch` `ok: false`, `why: transcript missing`
row, before any tab is launched. The account is irrelevant to this check: the
subscription switcher symlinks every `configs/<slug>/projects` to the shared
`~/.claude/projects`, so all accounts see the same transcript (or its absence).

**The "/" menu** (the composer AND the new-session form's first-prompt box —
one shared `slashMenu` helper in app.js). A leading `/` with no whitespace yet
opens a Claude-Code-style completion menu over `GET /api/commands?cwd=…` —
the composer keys it to the session's cwd (fetched once per view), the form
to whatever directory is currently typed (cached per dir). ↑/↓ move, Tab
completes, Enter completes a *partial* token but sends/launches when the
token already IS the selection (a fully-typed `/compact` goes through on one
Enter — both boxes pass `enterSends: !IS_IPAD`, so on an iPad Enter always
completes and never falls through to a send), Esc closes. The menu drops BELOW its host box, never upward over
the stats row. The list is
`plugins.slash_commands(cwd)` → `plugins/claude_code/slashcmds.py`: a curated
`BUILTINS` snapshot of the CLI's built-in commands plus the session cwd's
discovered custom entries — `commands/**/*.md` (subdirectory-namespaced,
`gh/fix.md` → `/gh:fix`) and `skills/*/SKILL.md` from every ancestor
`.claude/` (`model.claude_dirs` with `env_pin=False`: the lookup is for an
ARBITRARY session's cwd, and a dashboard that happened to be spawned from
inside some session must not have `$CLAUDE_PROJECT_DIR` pin every lookup to
that project). Descriptions come from the file's frontmatter `description:`,
else its first body line. Dedup is by name, built-ins first (the TUI resolves
those names to itself regardless of what a same-named custom file claims),
then nearest-first (a project command shadows a user-level one). **The TUI
stays authoritative**: sending just types `/name …` into the terminal and
Claude Code's own palette parses and executes it — the menu only has to be
good enough to complete against, never to validate, so `BUILTINS` drifting
behind the CLI is harmless (an un-listed command still types fine).

**Both message boxes share the Claude Code input UX**: Enter sends (the
composer) / launches (the form's first prompt), Shift+Enter inserts a
newline — **except on an iPad**, where Enter is always a newline and only
the send/launch button submits (`IS_IPAD` in app.js: iPadOS Safari
masquerades as desktop Safari — identical User-Agent, `MacIntel` platform —
so detection is client-side by necessity, `platform === "MacIntel" &&
maxTouchPoints > 1` being the one remaining tell; Macs report 0 touch
points, iPads 5. The placeholders drop the Enter hints there too) — and the
textarea auto-grows with its content (`autoGrow`), capped
at `GROW_CAP` = 40% of the viewport (mirrored as `max-height: 40dvh` in CSS —
dynamic vh, so an open on-screen keyboard shrinks the cap with the layout)
so a long paste can't swallow the page. Every dashboard text box (the two
message boxes plus the directory and filter fields — one delegated document
listener over `textarea`/`input[type=text]`) also gets the kitty/shell
readline editing keys: **⌃W** deletes the word left of the cursor (or the
selection), **⌃A** jumps to the start of the current line, **⌃E** to its
end. Ctrl is free real estate in a macOS browser (the browser's own
accelerators live on ⌘), matching is on `e.code` so a non-QWERTY layout
can't move the keys, and ⌃W dispatches an `input` event so `autoGrow` and
the suggest/filter `oninput` hooks see the edit.

**The form's first-prompt clears the instant you launch** (`go()` in app.js).
The message rides the launch argv, so on submit the prompt box is emptied
OPTIMISTICALLY (before the POST resolves) rather than left looking un-sent
through the kitten-slow launch round-trip — the "I started the session but my
message stayed in the input box" report (2026-07-20). The form tears down on
success anyway (`closeNewSession` empties `#modal`), so this only matters for
the in-flight window and the failure path: on ANY launch failure the captured
text is restored verbatim into the box (and the form stays open, submit
re-enabled) so a retry keeps it — the same optimistic-clear-restore-on-failure
shape the composer's own send uses.

**⌃⇧←/→ cycle through live sessions** — kitty's next/previous-tab keys,
mirrored: the cycle is the LIVE sessions ordered oldest-first (creation
order, the same order kitty's tab bar holds them in), wrapping at the ends;
from the list view or a parked session (nowhere in the cycle) → enters at
the oldest live session and ← at the newest. Works with focus anywhere,
including a text box — macOS claims ⌃←/→ for Spaces but nothing claims
⌃⇧←/→, so the only thing shadowed is a selection gesture that already
lives on ⌥⇧/⌘⇧.

## Web composer draft (`POST /api/session/<sid>/composer-draft`)

**The unsent message survives a device switch, a reload, or leaving and
coming back.** The composer text used to be purely browser-local — the moment
you jumped to another device, reloaded, or navigated to a different session
and back, a half-typed message was gone. Now every edit debounce-POSTs the box
to `POST /api/session/<sid>/composer-draft`, which writes the `composer-draft`
kv (`{text, origin}`; a pure state write via `ST.kv_set_at`, types NOTHING
into the terminal — distinct from `/message`, which sends). The session
snapshot carries `composer_draft` and the SSE emits a `composer-draft` event
on change (slow cadence — a draft is convenience state, nobody is blocked on
it, unlike the ask/plan dialogs), so `buildComposer` SEEDS the box from it on
open and an already-open composer on another device tracks the edits live
(`applyComposerDraft`). It reuses the *Web ask* draft machinery verbatim: each
page stamps its writes with the per-load `origin` (`CLIENT_ID`) and ignores
the SSE echo of its OWN `origin`, and `applyComposerDraft` never yanks text
out from under an ACTIVE local edit (the box holding focus is skipped, its
`ses.meta.composer_draft` still updated so the next remote change applies once
it blurs) — last-writer-wins, right for a shared draft.

Unlike the ask draft there is **no `tool_use_id` / turn-boundary lifecycle** —
a message draft has no natural expiry, so it lives until **sent or
overwritten** (that IS the "come back and it's still there" the feature is
for). `send()` clears it immediately (both the /message path and the parked
*resume & send* path — `clearComposerDraft`, so the adopted resumed session
doesn't re-show the just-sent text) and re-persists it on a send FAILURE (the
box keeps its text, so a reload must not lose it). An emptied box POSTs empty
text, which clears the stash; `_composer_draft` treats a blank stash as None so
the card clears everywhere. Works for LIVE and PARKED sessions alike —
`state_db_for` resolves the parked copy — since the composer itself is usable
in both. Best-effort throughout: a failed save retries on the next edit and the
local box keeps its text.

**The clear must win a race with an in-flight save** (added 2026-07-19, from a
"dictated a message, sent it, but the draft didn't clear" report). The rapid
per-keystroke saves dictation produces and the `clearComposerDraft` on send are
independent POSTs with no ordering guarantee over a tunnel — an old save
landing *after* the clear would resurrect the just-sent draft. Each write now
carries a wall-clock `seq` (`Date.now()` at dispatch); the server DROPS a write
whose `seq` is older than what's stored (a `composer-draft` state_files row
`action: stale`), and the clear keeps a seq'd **empty-text tombstone** (not a
delete) so its `seq` survives to reject a later straggler. Writes without a
`seq` (seq 0) skip the guard — last-writer-wins, as before.

The seq compare-and-set is **atomic** — one `BEGIN IMMEDIATE` transaction
(`ST.kv_cas_seq_at`, the seq-guarded sibling of `kv_set_at`), NOT a read
(`kv_at`) followed by a separate write. The dashboard is a
`ThreadingHTTPServer`, so the racing save and clear can land in two CONCURRENT
worker threads, not just out of order over the tunnel; with the guard's read
and its write in separate statements, both threads read the same older stored
row, both pass the `seq < stored` check, and then race the write — the
lower-seq save committing LAST resurrects the just-sent draft. This is exactly
what happened to a *queued* send (added 2026-07-22, from a "queued a message,
the box cleared, but coming back the message was in the queue AND the box
again" report): the debounced save (a lower `seq`) and the clear-on-send (a
higher `seq`) hit the server together and BOTH audited as writes — the tell of
a passed read on both — with the stale save landing last. Folding the check and
the write into one BEGIN-IMMEDIATE transaction serializes the two threads, so a
lower-seq write can never straddle a higher-seq one.

## Web composer queue (`POST /api/session/<sid>/composer-queue`)

A message sent while a turn is running lands in Claude Code's OWN message queue
and delivers at the turn boundary; the page shows it meanwhile as an amber **⧗
queued prompt bubble PINNED at the top of the transcript** — the `.pinq` pane
that `buildQueuePin` stacks ABOVE the newest-first stream inside the transcript
column (`.scol`), so incoming activity can never bury it (the newest-first stream
prepends new rows below it, never above). It looks like the delivered prompt
bubble it will become — same `.msg.prompt` shape, minus the rewind ↶ (a
not-yet-delivered prompt isn't re-runnable) — plus a `⧗ queued` badge and a `✕`
to drop a stale marker. It stays pinned until `drainQueue` matches its prompt
arriving in the stream (the only true delivery signal), at which point the pinned
bubble is removed and the delivered prompt appears in the stream itself.

*(This replaced the earlier narrow ⧗ **chip** row under the composer — the
message reads as a real, prominent transcript entry now, pinned on top until
sent, rather than a cramped tag detached from the conversation.)*

The queue used to be purely browser-memory, so a reload lost it (the "shown in
the queue but gone even from the queue after refresh" report, 2026-07-19) —
alarming, since you couldn't tell whether the message was still coming. The page
now mirrors the whole list to the `composer-queue` kv on every mutation (a queued
send, a delivery drain, a ✕-remove) via `POST /api/session/<sid>/composer-queue`
(`{items:[{text}], origin}`; a pure state write, no terminal keys). The session
snapshot carries `composer_queue`, the SSE emits a `composer-queue` event on
change (slow cadence, convenience state like the draft), `buildQueuePin` seeds
`ses.queue` from it on open (only when the in-memory queue is empty), and
`applyComposerQueue` adopts a peer's update (own-`origin` echo ignored). An
empty list deletes the stash. This is display persistence only — the message
itself lives in the TUI's queue regardless; the pinned bubble just stops vanishing.

`drainQueue` matches a delivered prompt against a queued entry **tolerantly** —
exact text, or (attachments prepend leading `@path` mentions + `\n`) the
delivered prompt ends with the queued suffix — the same match `drainPending`
uses, so a queued message *with attachments* (delivered as `@path\n<text>`) still
drains instead of sticking.

**Delivered entries are also reconciled server-side, or a persisted one stuck
forever.** `drainQueue` only reconciles NEW stream items, never the
already-loaded backlog. So if the client that persisted an entry closed or
reloaded *before* its message was delivered, every later page load re-seeded it
from the kv (`buildQueuePin`), found the delivered prompt already sitting in the
backlog, and had no fresh item to drain it against — a ⧗ queued bubble stuck
forever even though Claude Code received and answered the message (the "still
shows as queued after it was delivered" report). `_composer_queue` now drops any
entry whose prompt already appears among the transcript's delivered prompts
(`_delivered_prompts` / `_chip_delivered`, the same tolerant match) before it
ever seeds the page. Read-only — the server can't rewrite the kv (`mode=ro`), so
the stale rows are pruned by the client's next `saveQueue` once this filtered
list seeds it.

**Sends into an open modal are refused.** A message pasted while an
AskUserQuestion / ExitPlanMode dialog is up goes INTO the dialog, not the
queue, and is lost (perturbing the dialog too). `post_message` now checks
`_ask_pending`/`_plan_pending` and returns a 409 `modal` with no paste,
pointing the user at the ask/plan card above; the composer keeps its text. Once
the dialog resolves, the send goes through normally.

## Optimistic UI & the web-hint audit

The dashboard's write actions are **optimistic**: the page reflects the action
the instant you take it (a greyed stand-in / greyed card) and reconciles to the
REAL confirmation when it arrives async over SSE — rather than blocking on the
POST, or (the old way) claiming done the moment the POST returns, before the
action had actually landed. Four flows share the pattern, and one audit
mechanism (`web-hint` rows, `op` = composer | close | answer | plan): the
composer bubble (below), plus session **close**, ask **answer**, and **plan**
decisions.

- **Close** (`cardClose` / the header ✕): the list-card greys to `closing…`
  (`.scard.closing`, `S.closing`) the instant the confirm fires; `reconcileCloses`
  (run on every `sessions`/`-delta` snapshot, before the re-render) swaps it to
  the parked chip when the sid goes not-live — the true "the tab actually parked"
  signal. A failed POST reverts.
- **Answer** (`submitAsk`) / **Plan** (`submitPlan`): the card is replaced by a
  greyed `pendingCard` (`.askcard.pending` / `.plancard.pending`) and stays until
  the SSE `ask`/`plan` event drops the stash (the answer's/approval's PostToolUse)
  — reconciled in those handlers by `tool_use_id`. `renderAsk`/`renderPlan`
  reassert the greyed state on every render (a stray draft push can't un-grey a
  submitting card); a failed POST clears the pend and rebuilds the live card.

Each flow holds an `optPending(sid, op, id, note)` handle (`ses.askPend` /
`ses.planPend` / `S.closePend[sid]`) that beacons `shown` + arms a stale
watchdog, and `.settle(phase)` on reconcile/failure — the sibling of the
composer's `addPending`, minus the DOM node. The audit is the same `web-hint`
endpoint (`op` distinguishes them); see below.

### Composer bubble

A composer send only reaches the transcript once Claude Code writes the user
prompt record and the server pushes the `msgs` SSE event — a visible lag after
the paste lands (longer still if the turn is busy). To close that gap, `send()`
prepends a GREYED stand-in bubble (`.msg.prompt.pending`, `pendingBubble` —
plain `textContent`, no markdown, no rewind ↶) into the stream the instant it
POSTs, tracked in `ses.pending`. When the matching real prompt arrives,
`drainPending` (called beside `drainQueue` in `appendItems`) removes the
stand-in and the server-rendered bubble takes its place — normal color, full
markdown, rewindable. Matching is on the raw prompt text (exact, or — since
attachments prepend leading `@path` mentions + a newline — the real text ends
with the typed suffix). The stand-in is DOM-only and never persisted (a reload
replays from the real transcript), so a stale one can't leak. `send()` also
removes it directly on a failed POST (nothing was sent) and on a `queued`
verdict (the ⧗ chip owns that case — no double representation). Attachment-only
sends (empty text) get no stand-in: there's nothing to preview.

Because the stand-in is client-only DOM the server can't see it, so a stuck
grey bubble would leave no trace. Each transition therefore beacons a `web-hint`
`state_files` audit row (`hintAudit` → `POST /api/session/<sid>/hint-audit` →
`A.state_file`): `shown` on create, `reconciled` on the swap (carrying `wait_ms`
— the shown→swap latency), `dropped` on queued/send-failed (`reason`), and
`stale` from a ~20s (`STALE_HINT_MS`) client watchdog when a stand-in outlives
the window unreconciled — THE bug signal (the "optimistic composer bubble never
reconciled" anomaly). The endpoint is audit-only: it types nothing and writes no
session state, and never sends the raw prompt text (only `chars` — a length is
enough to correlate with the session's `web-send` row without storing content).
`leaveSession` disarms the watchdog so a deliberate navigate-away doesn't
false-fire `stale`. See the audit-debug skill's stuck-grey-bubble shape.

## Client-observed send failures (`POST /api/session/<sid>/client-fail`)

The `"send failed"` / `"resume failed"` toast is a purely CLIENT-side reaction:
it fires in the composer's `.catch()` whenever the send `fetch` PROMISE rejects.
But `post_message` audits the outcome (`web-send`, `ok`) and returns `200`
*before* that response travels back to the browser. So a response LOST in transit
— a dashboard restart, a tunnel/proxy reset, a dropped connection, a slept laptop
— rejects the page's fetch and toasts a failure **even though the send
succeeded**: the `web-send` row reads `ok: true`, the message really landed in the
TUI, and (if it had an optimistic bubble) it even `reconciled` to gold over SSE.
That combination — a failed toast next to a healthy `web-send ok:true` — was
INVISIBLE to the audit, since server-side auditing happens before the response is
sent and the browser's own view of the outcome was never recorded (the
`web-hint` `send-failed` beacon only fires when there's a bubble, and it rides the
same failed tunnel).

`clientFail(sid, gesture, err, chars)` closes the gap: on a failed send/resume it
beacons what the PAGE saw as a `web-clientfail` `state_files` row (`gesture`
send | resume; `kind` `transport` = the fetch itself rejected — the request or its
response was lost, the audit-blind class — vs `http` = the server returned an
error status, so a paired `web-send ok:false` / `A.error` should exist; `error`
the toast text, `status` on `http`, `chars` the message length). Like `hint-audit`
it types nothing, writes no session state, and is best-effort — it too rides the
tunnel that may have failed, so a MISSING row for a reported failed toast is
itself the signal of a total outage (the user-facing toast is the primary signal;
this is the after-the-fact breadcrumb). Correlate it with the paired `web-send`:
a `web-clientfail kind:transport` next to a `web-send ok:true` at the same second
IS the lost-response case (the message went through — no resend needed); a
`kind:http` points at the server row/`A.error` for the real refusal. See the
audit-debug skill's "failed toast but the message went through" shape.

## Web ghost suggestion (the TUI's "suggested answer", mirrored)

**Claude Code pre-fills a greyish *suggested answer* in its input box when a
turn settles** (e.g. `apply the MODULES filesystem-scan fix`) — right-arrow
accepts it as real input, typing anything replaces it. The web composer now
mirrors it: the suggestion shows as the textarea's grey placeholder, `→` / Tab
accepts it into the box, and typing dismisses it — the same feel as the
terminal.

**Why screen-scrape.** The suggestion is pure TUI state — Claude Code fires
**no hook** for its own input-box suggestion, and it never touches the
transcript (it isn't sent yet). So the only source is the live screen. The
probe (`dashboard/suggestion.py`, sibling of `askdialog.py`/`plandialog.py`)
captures the viewport WITH ANSI (`fe.get_text(win, ansi=True)` — a new `ansi`
flag on the frontend `get_text`, `--ansi` / the raw-socket `ansi` payload
field) and reads the input box, which sits between the two grey divider rules
(`\x1b[38:2:136:136:136m─…`) at the bottom. On the `❯` prompt line, a **ghost
suggestion is rendered with the faint SGR attribute** (`\x1b[22;2m`, param
`2` = dim); REAL typed/queued input is normal weight. So the tell is *all the
input content is faint* — `parse()` returns the faint text (wrapped lines
joined, whitespace-normalized) or `None` when the box is empty or holds
non-faint (real) input. `parse()` is a pure function over the screen string,
unit-tested (`tests/test_l0_dashboard.py`); `probe()` wraps it with the
get-text call and audit-before-swallow (`A.error` on any failure).

**Live-only, ephemeral, gated.** There is no kv and no persistence — a parked
session has no TUI, hence no suggestion. `sse_session` probes on the SLOW
cadence and emits a `suggestion` SSE event only on change, but only when a
suggestion could plausibly be there: the tab is **settled** (`_SUGGEST_TABS` —
green *done* or grey *idle*; a busy/asking tab never shows one), there is no
pending ask/plan modal, and the web composer box is empty (`_composer_draft`
None) — otherwise we don't screen-scrape at all (nothing to surface, and a
probe would fight a draft the user is editing elsewhere). The live window is
resolved through the memoized `claude_session=<sid>` map (`_live_windows`),
never a reused start-time id.

**Frontend: placeholder + accept key.** `applySuggestion` stores the value on
`ses.meta.suggestion`; `syncSuggestion(ta)` borrows the placeholder slot while
the box is empty (`.cinput.hasghost::placeholder` — italic + a touch brighter,
so it reads as a suggestion vs. the static "message this…" hint) and restores
the composer's own default placeholder (`ta.dataset.defph`) otherwise. The
keydown accepts `→` / Tab **only on an empty box with a suggestion** — it fills
`ta.value` (a normal `saveComposerDraft` follows), so it never steals `→` from
caret movement or Tab from the "/" menu (both non-empty). It is a **mirror +
client-side accept**: accepting fills the WEB box only, nothing is written back
to the TUI — a subsequent send pastes over whatever the input holds, as always.

## New-session prefs (`GET`/`POST /api/ns-prefs`)

The new-session form pre-selects the **last-used directory, model, and
effort** (launches are usually the same project on the same settings). This
used to be per-browser `localStorage`, which meant a laptop and an iPad
disagreed and a fresh browser started blank. It now lives on the backend in
the durable **global** prefs store (`dashboard/prefs.py` — a tiny kv table at
`core.paths.DASH_PREFS_DB`, `~/.claude`, shared across every browser/device
pointing at this one dashboard and surviving a reboot). `GET /api/ns-prefs`
returns `{cwd, model, effort}` (`{}` until the first launch); the page primes
`S.nsPrefs` from it at boot so `nsLast()` stays synchronous, and `nsRemember`
POSTs to `/api/ns-prefs` on a successful launch — **exactly where and when it
wrote `localStorage` before; only the storage moved.** `post_ns_prefs`
re-validates `model`/`effort` against the same allowlists `post_new_session`
uses (a bad value is dropped, never stored, so a corrupt pref can't later feed
the launch path). This is the ONE piece of dashboard state that is global
rather than per-session — `dashboard/prefs.py` is its single owner, unlike the
per-session `core/state.py` kv (and unlike the `/tmp` `DASH_DB` lock, it is
durable), and it CREATES its DB on demand (a global prefs DB has no
session-alive meaning, so a reader making it is fine — the opposite of the
per-session rule). These remembered defaults seed a FRESH launch; selecting a
row in the resume picker overrides the model/effort with that session's own
(*Resume picker* above), while the account always re-load-balances.

## Web quick commands (`POST /api/session/<sid>/command`)

The scoreboard's SECOND action row (its own line under
stop/cancel/rewind/close — live-with-window sessions only, like the buttons
above it): **⊜ compact**, **✦ model ▾**, **⚡ effort ▾**. Each just types one
of the TUI's OWN slash commands into the session's window — `/compact`,
`/model <alias>`, `/effort <level>`. The TUI stays authoritative, same
philosophy as the "/" menu: the web never re-implements compaction or model
switching, it only presses the button.

**The switch-confirm menu (`dashboard/confirmdialog.py`).** v2.1.214 applied
`/model`/`/effort` with an argument outright; newer builds (observed live
2026-07-18) interpose a numbered are-you-sure menu when the switch would
invalidate the conversation's prompt cache — "Change effort level? … ❯ 1.
Yes, switch to low / 2. No, go back" — and the command does NOTHING until it
is answered, so the web click looked dead (reported live). The clicked
button IS the user's consent, so after the paste (non-queued only)
`post_command` runs `confirmdialog.confirm`: poll the screen up to
`OPEN_TIMEOUT_S` for the menu, press its own Yes digit, verify it closed.
Detection is by SHAPE, not header text — a ❯-cursored numbered list in the
screen TAIL (`TAIL_LINES`) with one label leading "Yes" and one "No" —
because the model variant's wording is unmeasured and scrollback prose / the
bare composer `❯` must never match (a false press types a digit into the
chat). No menu inside the window is a clean non-event (`confirm: "none"` —
same level, or no cache to invalidate); a menu that stays open after Yes is
`confirm: "failed"` (still 200 — the command WAS typed; the menu is left
open, never Escaped, and the page toasts "answer the confirm dialog in the
terminal"). A QUEUED command (busy tab) gets no confirm watch — the menu
only opens at the turn boundary, minutes away; if it pops unanswered there,
the red-tab notification is the surface. Each attempt is a
`web-command-confirm` state_files row (`{win, cmd, confirm}`), failures also
an `A.error`.

Measured live (v2.1.214, 2026-07-18): `/model <arg>` and `/effort <arg>`
don't just switch the running session — the TUI **also saves the choice as
the user's default for new sessions** ("Set model to Sonnet 5 and saved as
your default…", persisted to settings.json `model`/`effortLevel`). That is
exactly what typing the command in the terminal does, so the buttons inherit
it (the tooltips say so); a "session-only" variant would need the picker
dialog's `s` key and a full screen-driver — deliberately not built while the
argument form (plus the confirm auto-answer above) does the job.

Server side (`post_command`) the vocabulary is CLOSED — `{"cmd": "compact"}`
(argless), `{"cmd": "model", "arg"}` with the arg validated against
`_MODEL_ARG_OK` (`_MODEL_OK`'s one-clean-word alphabet plus the CLI's literal
`[1m]` context suffix, e.g. `sonnet[1m]`), `{"cmd": "effort", "arg"}` against
`EFFORTS`; anything else is `400` and never reaches the terminal (free-form
text is the composer's job — this endpoint exists so a *button* can't be
talked into typing arbitrary bytes). Delivery is exactly a composer send
(live `claude_session` window resolve, bracketed paste + separate CR), so a
mid-turn command lands in Claude Code's message queue and runs at the turn
boundary — the reply carries `queued`/`tab` like `/message` and the page
toasts "queued — runs when the turn ends". The one refusal beyond
post_message's: a RED tab (`awaiting-command` — a modal dialog is up) is a
`409`, because pasted text would land IN the dialog and its digits would
*decide* it; the row's buttons also disable client-side on the same tab state
(`ses.quickMode`, fed by the SSE `tab` event next to `cancelMode`). Every
attempt is a `web-command` state_files row (`{win, cmd, arg, ok, tab}`),
failures also an `A.error`.

The client row (app.js `act2` in `renderSessionChrome`): compact carries the
close button's two-step arm ("compact now?", 4 s) — a misclick would
summarize the conversation out from under you; model and effort open
dropdowns in the new-session form's own picker language
(`.nsdropmenu`/`.nsdropitem` + the anchoring `.qcwrap`/`.qcmenu` classes,
Esc or click-away closes; the model menu marks the current family `.sel`
like `dropdown()` does) listing the form's model aliases (`MODEL_CHOICES` —
fable/opus/sonnet/haiku) and the `EFFORTS` levels. They briefly reused the
rewind menu's `.rwmenu` class, which taught a lesson that outlives the
styling: `closeRewindMenu()` keeps selecting `.rwmenu:not(.qcmenu)` because
the rewind feed-delegation handler runs on every document click and its
click-away branch once removed the quick-command menu in the same click
that opened it (the pickers looked dead) — any future menu sharing that
class needs the same exclusion. The model button's label
shows the session's CURRENT model (`✦ opus-4.8 ▾`) from the ctx probe's
`model` field, refreshed by the same `ctx` SSE event that drives the ctx bar
(`shortModel` in app.js is the display twin of `model.short_model` — the
Python side is the authority). Both labels stay CURRENT: an applied web
switch updates them optimistically (`applyQuickSwitch` — for model a
`pendingModel` override that holds until the ctx probe's family confirms it
on the next assistant turn; the probe's model is stale until then). The
effort label (`⚡ high ▾`) shows the SAVED effort level — session meta
`effort` + the SSE `effort` event, backed by the
`plugins.effort_default(cwd, slug)` fan-out over
`model.settings_field("effortLevel", start=cwd, config=…)`, where `slug` is
the session's statusline-stashed account and `config` its
`account.config_dir_for(slug)` — each subscription account has its OWN
settings.json, so reading the server's ambient config dir would show one
account's effort on another's session. Per-session
effort is readable from no transcript (`plugins/claude_code/model.py`), but
every applied `/effort <level>` — terminal or web — persists itself as the
settings default, so the saved value IS the last applied one (a
terminal-side `/effort` reaches the open page on the SSE slow cadence). The
honest residual: a session started with `--effort X` that never ran
`/effort` shows the saved default, not X — that flag is recorded nowhere
readable.

`POST /api/sessions/new` `{"cwd", "model"?, "effort"?, "prompt"?}` validates
`cwd` is an existing directory (`os.path.isdir`, else `400`), `model` against
`_MODEL_OK` (one clean argv word — an alias like `opus` or a full id like
`claude-fable-5`; the form offers the aliases, the API takes any id) and
`effort` against `EFFORTS` (the CLI's `low`…`max` levels), then
`Frontend.launch_tab(cwd, launch_argv(["--model", m?, "--effort", e?,
prompt?]))` opens a new tab — the flags are just more positional `"$@"` words
ahead of the prompt, so the injection story is unchanged; the session then appears through its
own `SessionStart` (no synthetic row). `kitten @ launch` prints the new
window's id, which `kitten_launch_tab` captures (the ONE launch call whose
stdout isn't silenced) and the response passes through as `win` — the page's
exact match key for the session that boots there, where a cwd heuristic is
ambiguous under two same-directory launches.

**Web launches must not steal macOS focus (and why there is no bounce-back).**
The user is *in the browser* — but a web launch used to make macOS activate
kitty over it. The mechanism, pinned by live measurement (steal transitions
at 2.2s/3.0s/5.8s into the startup — after `claude` boots, never at the tab
launch itself): the plain `--type=tab` launch is innocent; the thieves were
the SessionStart **pane opens**, which passed kitty's `--keep-focus`. That
flag's "restore focus to the previous window" path calls
`focus_os_window(raise=True)` whenever *no kitty OS window is focused* —
i.e. always, when the launch came from a browser — activating the app
(verified against a plain-config kitty 0.45: plain launch leaves the browser
frontmost, `--keep-focus` yanks kitty up). The fix is at the source:
`frontends/kitty.py launch_pane` passes `--keep-focus` **only while kitty is
the frontmost app** (`kitten_app_focused` — that's the case the flag exists
for, keeping the user's cursor in the claude window), and `kitten_launch_tab`
never passes it. `kitten @ focus-window` cannot substitute as a restore — it,
too, raises the OS window of a background kitty
(`set_active_window(switch_os_window_if_needed=True)`).

**Inner focus (which pane), separately from OS focus (which app).** Skipping
`--keep-focus` on a background launch had a cost: the LAST pane split in (the
scoreboard bar) held *inner-tab* focus, so a web-launched tab showed "▪ session"
as its tab title instead of the host's ai-generated summary until the user
manually clicked the host pane. This is fixed WITHOUT re-introducing the app
steal: after opening the panes, `core/hostpane.py open_mirror` hands inner focus
back to the host via `frontends.Frontend.focus_first_pane(anchor)` →
`kitten @ action --match window_id:<host> first_window`. That is an INNER-tab
move (`Tab.nth_window(0)`; `boss.combine` dispatches a tab action to the
*matched* window's tab, never the active one) and it never calls
`focus_os_window`, so a background kitty is **not** raised — the crucial
difference from `focus-window`, whose rc hardcodes
`switch_os_window_if_needed=True`. Group 0 is the host: the tab's first-created
window, before its mirror/scoreboard splits. It runs only when `open_mirror`
actually created a pane (a resume/toggle-while-open where the panes already
exist must not yank a mirror the user is reading) and only with a host `anchor`
to target; the result is audited as a `pane_events` `focus-host` row (`win=`
the anchor). A foreground open is unaffected — `--keep-focus` already kept the
host focused, so the correction is a no-op there.

Two rejected designs, do not re-add: (1) `--keep-focus` on the tab launch —
see above, it *causes* the steal; (2) an **active bounce-back** (watch the
frontmost app, `open -b` the browser back whenever kitty takes over) shipped
2026-07-18 and was reverted the same day — it cannot distinguish kitty
stealing focus from the user *deliberately* switching to kitty inside the
watch window, so it yanked the user back to the browser when they genuinely
wanted the terminal, and the bouncing itself was jarring. What survives is a
**passive steal watch** (`_steal_watch`, a daemon thread; skipped off-mac,
when the frontend has no `app_id()`, or when the terminal was already
frontmost at click time): it captures the frontmost app's bundle id before
the launch (`lsappinfo` — plain LaunchServices, no TCC/automation prompts),
records each transition onto the terminal app for
`STEALWATCH_POLLS × STEALWATCH_POLL_S` (~30s), touches nothing, and writes
one `web-launch-steal-watch` state_files row (`before`/`terminal`/`steals`
= seconds-into-watch offsets; `[]` = clean). A non-empty `steals` on a
current build means some launch path still activates the terminal — that
row names the second it happened. **The argv is NOT a bare `["claude"]`**
— kitty execs launch argv with kitty's OWN environment, and a GUI-launched
kitty has no user PATH (`~/.local/bin` absent → command-not-found → the tab
flashes and closes while `kitten @ launch` still exits 0; this shipped once)
and no shell aliases (`claude` here IS an alias). `launch_argv` therefore runs
`$SHELL -lic 'claude "$@"' claude <prompt?>` — the user's interactive login
shell, i.e. exactly what typing `claude` in a fresh tab does (profile PATH, rc
aliases). Injection safety is preserved: the command string is FIXED and the
prompt rides as a positional `"$@"` arg, never interpolated. Non-POSIX `$SHELL`
(fish) falls back to `/bin/zsh` (`LAUNCH_SHELLS`). The wrapper is OWNED by
`plugins/claude_code/account.launch_argv` (reached via the `plugins.launch_argv`
registry fan-out) — the rate-limit migration (docs/relimit.md) composes the
exact same launch, so the server's `launch_argv` is a thin delegation. The
server may have no resolvable kitty
socket at all (started outside kitty) — `frontends.get(resolve=True).usable()`
is `False`, `_frontend()` returns `None`, and every control-plane endpoint
returns a clean `503`, never a 500 traceback.

**Liveness = an OPEN tab, not a lingering state DB.** A session's `live` flag
is *not* just "its `/tmp` state DB exists" — that only means the session was
never PARKED, and a tab closed WITHOUT a SessionEnd (crash / `kill -9`, or a
leaked test DB) leaves the state DB intact, so the session would masquerade as
running with a `kitty_window_id` that kitty has since REUSED for an unrelated
tab. Both payloads therefore reconcile against `_live_windows()` — one
`kitten @ ls` (memoized `_LIVE_TTL`, 5s) mapping each pane's
`claude_session=<sid>` user-var → its window id, the authoritative "which
sessions have an open tab". The TTL can be that loose because every consumer
of the MAP is read-side (demotion + the stop-button display gate) and
staleness only delays noticing a crashed tab; the control-plane writes never
trust it — each POST re-scans via `window_for_session` at action time. It
started at 0.8s ("bound the calls under the 1s tick"), which made the ~21ms
`kitten @ ls` subprocess the server's single largest recurring cost
(~1.25 spawns/s while any client polled); 5s cuts that 6× for an
imperceptible staleness window.
A state-DB-live session that ever had a window but isn't in that map is demoted
to not-live (and its control plane disabled). When no frontend resolves (map is
`None`) the state-DB signal is kept as-is — we don't mark sessions dead we
can't verify. This is also why the control-plane writes below resolve the
**live** window rather than the stored id.

**Startup grace (why a brand-new session must NOT be demoted).** The demotion
above has a race at the START of a session: the audit `sessions` row (carrying
`kitty_window_id`) is written a beat BEFORE the pane is tagged
`claude_session=<sid>` (`split.cmd_open` runs `A.session_start`, then
`tag_window`), and `_live_windows` is memoized up to `_LIVE_TTL` (5s) on top —
so for a few seconds a fresh launch has a window id but isn't in the tagged-window
map, and the naive demotion flips it to not-live: the card flashes **parked** and
the session-detail header (whose `meta` is fetched ONCE at open — the launch jump
navigates straight into it) *froze* on that reading, leaving the parked chip stuck
on and every live-gated action (stop/cancel/rewind/close/quick-commands) missing —
so the just-launched session couldn't even be closed. Two fixes, both needed:
`_within_live_grace` EXEMPTS a session from the missing-window demotion for
`_LIVE_GRACE_S` (10s) after its `started_at` (covers boot + the memo TTL; a
session that dies within its first 10s only shows live until the next tick past
the grace), and the client's `updateHeadFromList` now re-syncs `meta.live` /
`meta.kitty_window_id` from the authoritative global `sessions` snapshot and
re-renders the header chrome on a real live↔parked flip (skipping a subagent
drill-down and an in-progress rename) — so any later flip (kill, crash, resume)
also stops freezing the header.

`POST /api/session/<sid>/stop` closes the session's whole kitty TAB
(`Frontend.close_tab` → `kitten @ close-tab --match window_id:<win>` — the
main window, mirror pane, and scorebar go together). **The target window is
resolved by the live `claude_session=<sid>` tag (`window_for_session`), NEVER
the audit row's start-time `kitty_window_id`** — that id goes stale (kitty
reuses window ids), and closing by a reused id once closed an unrelated live
tab (a leaked smoke-test session's window id had been reassigned to the user's
own tab). No live tag ⇒ `409`, nothing closed. `post_message` resolves the
same way (typing into a reused id is just as dangerous). This is a **graceful
stop, not a kill**: kitty HUPs the tab's processes and Claude Code exits
cleanly on SIGHUP, firing SessionEnd — so the normal end-of-session lifecycle
(mirror park to `HISTORY_DIR`, audit `sessions` row closed with reason
`other`, no `/tmp` leftovers) runs on its own. Verified empirically
2026-07-18: launched a throwaway session, `close-tab`'d it, and confirmed the
`ended_at`/`end_reason` audit row, the parked state DB, and the clean `/tmp`.
Headless session (no window) is `409` — there is no tab to close. The page
puts a **close** button in the session head (live + windowed only) behind a
two-step confirm (first click arms for 4 s, second fires); on success it
navigates back to the sessions list (the session just ended — staying on its
now-dead view helps nobody; skipped if the user already navigated elsewhere
while the POST was in flight). A parked session shows a **resume** button
there instead, which opens the new-session form preset to `--resume <sid>`.
The same close is reachable from the **sessions list**: a live windowed
session's card carries a corner **✕** (`cardClose`, the slot the parked/gone
chip uses on inactive cards) with the same two-step arm and the same `/stop`
POST — the button lives inside the card's `<a>`, so its clicks
preventDefault/stopPropagation instead of navigating, and success changes no
hash: the card demotes to parked on its own via the SSE `sessions` push.
Unlike the header buttons, the card ✕'s arm and in-flight state live in `S`
(`S.armClose` — one `{sid, until}` slot, a deadline, not a timer handle —
and the `S.closing` sid set), NOT in the button's closure/DOM: the per-tick
`sessions` push rebuilds every changed card wholesale (`patchCards`
`replaceChildren`), and a live card's row — the only kind that shows a ✕ —
changes every tick, so button-held state died within ~1s of arming and the
"close?" confirm was gone before it could be clicked. The constructor
re-derives both states, so a rebuilt (or fully re-rendered) button resumes
the arm with the remaining window; stale disarm timers left on replaced
predecessor buttons no-op via a sid+deadline check. The single slot also
means arming one card steals the arm from any other — one live confirm at a
time. The header close/compact keep closure-local arm state on purpose:
nothing tears the detail view's action row down mid-arm.

`POST /api/session/<sid>/interrupt` presses **Escape** in the session's
window (`Frontend.send_key(win, "escape")` → `kitten @ send-key`) — the TUI's
own interrupt: the current turn stops in place and the session stays up,
which is what a "stop whatever it's doing" button must mean (closing the tab
is the separate close endpoint above). It must be a key EVENT, not
`send_text` bytes: a TUI in the kitty keyboard protocol never sees a raw
`\x1b` byte as the Escape key, and send-key encodes for the window's current
keyboard mode. Same window discipline (live tag, `409` when none) and the
same guard chain. Note `send-key` reports no per-window delivery errors —
rc 0 means kitty accepted the call — so `ok` here is weaker evidence than
send_text's. The page wires it as the **stop** button (■, live + windowed
only, no confirm — it matches pressing Esc in the terminal) and as the
**Esc key** on the session view itself: a document-level fallback that fires
only when no overlay (modal, slash menu, filter, dropdown) claimed the
Escape, so muscle memory from the terminal carries over to the browser.
When the Escape lands on a MAGENTA tab (`thinking`/`working`) the endpoint
also spawns the **`escape-recheck`** tab dispatch (detached
`claude-tab-status.py escape-recheck <log> <transcript> <press-size>`, env
carrying the window id): an Esc that kills a turn mid-think leaves no
signal anywhere (the interrupt-watch KNOWN GAP — docs/tab-colors.md), so
the tab would sit magenta and the dashboard would keep showing busy; a web
interrupt is itself an event, so the recheck flips the dead magenta green
unless any real signal (tab-state movement, or a new `"type":"user"`
transcript record past the press-time size) appears within its 2s grace.

`POST /api/session/<sid>/migrate` — the header's **⇆ migrate** button (right
after ✎ rename; like rename it works live AND parked, and like ■ stop it
fires immediately with no confirm) hands the session to the other
subscription account: the server picks the target
(`plugins.migration_target(manual=True)` — least effective-5h used, active
limit-hit excluded, NO 90% ceiling for a manual click) and spawns the same
detached migrator the automatic rate-limit path uses, in `mode=manual` (bare
`--resume`, no auto-continue nudge). Audited as a `web-migrate` state_files
row carrying a **`pick`** sub-object — `pick_target`'s full decision trace
(`branch`/`cur_model`/per-account `candidates` with each rung/`eff5h`/limit-hit
scope/reject reason/`chosen`), threaded through `plugins.migration_target(…,
explain=)`. This makes a manual-migrate REFUSAL reconstructible from the DB —
the manual twin of the automatic path's `relimit-pick` row, closing the same
subtle gap the first rate-limit-migration investigation hit (a bare "no target"
that couldn't be explained). `409` when no other account qualifies (the `pick`
trace says why each was refused), `404` for a sid this machine has never seen
(the migrator's park check can't tell "parked" from "never existed"). Full
mechanics + the manual/auto differences: docs/relimit.md *Manual migrate*. No-confirm stays (the click IS the intent — docs/relimit.md
*Manual migrate*), but the button DISABLES for the round-trip (`lockDuring` in
app.js): "no confirm" means one deliberate click is enough, not that a
double-tap during the ~1s POST should spawn TWO racing migrators (each closing
the tab and picking a target). The same closure-local in-flight lock guards the
other immediate no-confirm header actions — ■ stop and ⊘ cancel would otherwise
double-send Escape mid-flight; it re-enables on settle (cancel re-derives from
the tab, so an idle turn keeps it disabled). This is button-closure state, not
`S` like the card ✕ (above): nothing tears the detail view's action row down
mid-action, and the Esc-KEY gesture path has its own `escHold` debounce, so the
lock lives on the buttons rather than the shared `interruptSession`/`cancelEdit`
/`migrateSession` functions (which just return their POST promise for it).

`POST /api/session/<sid>/rewind` mirrors Claude Code's double-Esc, whose
MEANING depends on session state — and the endpoint splits on the tab
state at gesture time:

- **MID-TURN** (a `BUSY_TABS` colour — `thinking`/`working`/`executing`/
  `awaiting-bg`, NOT red `awaiting-command`): double-Esc CANCELS the running
  work and restores the last message into the input for editing (removing it
  from the conversation). Mirrored with **two Escape key events**
  `DOUBLE_ESC_GAP_S` (150 ms) apart — measured **3/3 reliable** mid-turn
  on a live session (2026-07-18), unlike the idle menu — plus the same
  magenta `escape-recheck` (that experiment showed the tab stays stuck
  `thinking` after the cancel). Editing then happens in the kitty tab.
  A red `awaiting-command` tab is NEITHER this nor the idle branch: a modal
  dialog is open there, so the endpoint refuses outright (`_dialog_open_guard`
  — see the interrupt section) rather than sending Esc-Esc (which would decline
  the ask) or typing `/rewind` (which would land in the dialog).
- **IDLE**: double-Esc opens the rewind/checkpoint menu (restore code
  and/or conversation, summarize; checkpoints are automatic, one per user
  prompt — code.claude.com/docs/en/checkpointing.md). Mirrored by **typing
  `/rewind`** (documented identical) — NOT synthesized key events:
  measured on a live idle session, two `send-key` Escapes opened the menu
  only ~2/3 of the time at the BEST gap (0.15 s), ~1/3 at 0.5 s, never
  from one batched call, focus irrelevant, while typed `/rewind` opened it
  **every time**. No Escape ⇒ no recheck.

The response's `mode` (`cancel-edit` | `rewind`) tells the page which
meaning fired (its toast differs), and rides the `web-rewind` audit row
(`{win, ok, tab, mode}`). On `cancel-edit` the response also carries
`restored` — the session's last user prompt (`_last_prompt` →
`plugins.conversation`), the message Claude Code puts back into the input.
Same guard chain and window discipline as the other writes. The page now
calls this endpoint only for the MID-TURN meaning (the cancel); its idle
rewind is the full web rewind below — the endpoint's idle branch (type
`/rewind`, navigate in kitty) survives for API callers and tests.

**What the page does on `cancel-edit` — the full loop, no jumping to the
terminal.** It drops the cancelled prompt bubble from the feed (abandoned
— kitty un-renders it too; optimistic, since a mid-turn cancel does NOT
rewrite the transcript, so a full reload re-shows it) and puts `restored`
into the composer for editing. Resending the edit goes through
`/message` with `clear_draft: true` (`ses.clearDraftNext`), because the
TUI input still holds the restored draft: the send kills the line
(`Ctrl+U` to start + `Ctrl+K` to end — cursor-position-independent) and
delivers the edited text as a **bracketed paste** (`Frontend.paste_text`).

The bracketed paste is load-bearing and hard-won. The Claude Code TUI
MANGLES a RAW send into an input whose state just changed: measured live
(2026-07-18), clearing the restored draft and RAW-typing a replacement
nondeterministically dropped 3–9 leading bytes and inserted stray
newlines (`echo REPLACED` arrived as `\n REPLACED`), and a 3-second settle
failed identically — it is NOT a race a gap fixes, it is the TUI reading
fast keystrokes and dropping the leading ones. Wrapping the text in
bracketed-paste escapes (`kitten @ send-text --bracketed-paste=enable`)
makes the TUI read it as ONE atomic paste, which lands clean every time
(verified 3/3 with settled trials). The Enter stays a separate keystroke
OUTSIDE the paste so it still submits. So the reliable boundary is: you
can cancel, edit, and resend entirely from the web — no frontend hop.

Known limit (Claude-Code-imposed): a cancel that ORIGINATES in the kitty
tab (you press Esc-Esc there) can't be reflected on the web, because
Claude Code fires no hook and a mid-thinking cancel writes NOTHING to the
transcript (verified — the same no-signal gap the tab-colour recovery
documents in docs/tab-colors.md). The web mirrors a cancel it TRIGGERED;
it cannot observe one it didn't.

The **`escape-recheck`** that both the interrupt and the mid-turn
cancel-edit spawn watches the transcript for a new `"type":"user"` RECORD,
not raw byte growth: the cancel-edit gesture appends pure METADATA
(`ai-title`, `last-prompt`) right after killing the turn, and a
raw-growth bail false-positived on the gesture's own records — the tab
sat magenta until a later gesture's recheck flipped it (observed live).
Only a user record (a real new prompt, or the `[Request interrupted by
user]` line) means a real signal owns the tab; metadata-only growth is
ignored and the dead magenta still flips.

## Web rename (`POST /api/session/<sid>/rename`)

`{"name"}` renames a session — the ✎ button in the session header's action
row swaps the title into an inline input (Enter submits, Esc/blur cancels;
its keydown handler `stopPropagation`s unconditionally so Esc never leaks to
the document-level interrupt gesture). The mechanism is the one
docs/session-naming-findings.md verified: **append the
`{"type":"agent-name","agentName":…,"sessionId":…}` naming record to the
session's transcript JSONL** via `plugins.set_session_title(tpath, name)` —
a path-keyed fan-out to the record shape's single owner,
`plugins/claude_code/transcript.set_session_title` (grep-test-enforced:
`agentName` appears in no other product module). The record is what Claude
Code's own `/rename` writes: the `--resume` picker reads it on next launch,
`session_title` prefers it over every later auto `ai-title`, and the
`(path, size)` title cache self-invalidates because the append grows the
file — the list card retitles on the next global SSE snapshot and the open
header on the per-session `title` push (below).

Deliberate choices, and why:

- **Live AND parked.** Unlike every other control-plane endpoint, no
  terminal (503) / no window (409) is NOT an error — the append needs no
  terminal, so a parked session (or a dashboard started outside kitty)
  renames fine and only the tab retitle degrades (`tab_retitled: false`).
  The writer refuses paths outside the `~/.claude/projects/<hash>/` layout
  (→ 409 `unsupported session`): a codex standalone host's `transcript_path`
  is a codex ROLLOUT and must never receive a Claude naming record. A
  missing file is never created just to name it (409 `no transcript`).
- **Always append, even mid-turn.** A single atomic O_APPEND line is
  low-risk against Claude Code's own appender (the findings doc §5); gating
  on tab state would make renames randomly fail. The tab state at rename
  time rides the `web-rename` audit row, so a hypothetical torn-line race is
  diagnosable after the fact.
- **The live kitty tab retitles NOW** via the new
  `Frontend.set_tab_title(win, name)` (`kitten @ set-tab-title --match
  window_id:<win>`) — a JSONL append alone doesn't move a live tab (the tab
  mirrors Claude Code's in-memory OSC title, seeded from the JSONL only at
  startup). kitty makes an explicit tab title STICKY: that tab stops
  following the window's OSC titles — i.e. future auto `ai-title` changes —
  for the rest of the session, which is exactly right for a
  deliberately-named session. No raw-socket fast path (deliberately
  different from `set_tab_color`): this is a rare user action, not the
  blocking hook path.
- **Input hygiene:** control bytes are stripped (`_NAME_CTRL`) — the name
  goes verbatim into a `set-tab-title` argument and the picker, the exact
  OSC/CSI injection class `render.neutralize()` exists for — and capped at
  `RENAME_MAX` (120); empty-after-cleaning is 400. A name starting with `-`
  may be eaten by the kitten CLI as a flag (rc≠0 → `tab_retitled: false`);
  the JSONL rename still lands.
- **A durable override defeats the tail-window rollback.** The `agent-name`
  record is written ONCE, but Claude Code keeps re-emitting `ai-title` near
  EOF every few turns; once the rename scrolls more than `TITLE_TAIL_B`
  (64KB) behind EOF, the bounded tail scan no longer sees it and
  `session_title` reverts to the newest `ai-title` — the rename *appears to
  roll back* (the confirmed bug; this WAS a documented "accepted gap"). So the
  rename ALSO stashes a durable, tail-window-proof override in the global
  prefs store (`dashboard/prefs.py` `renamed-title`, `{stem: name}`, keyed by
  the transcript's `.jsonl` stem — adopt/fork-proof, survives park). The
  dashboard's `session_title` wrapper reconciles via
  `plugins.title_and_rename(tpath)` → `(display_title, tail_rename)`: it
  prefers the override ONLY when `tail_rename` is empty (the rename scrolled
  out), so a FRESH in-tail rename — a terminal `/rename`, or renaming again —
  still supersedes it (last rename wins). The transcript append stays the
  canonical channel the `--resume` picker (a full read) reads; the override is
  purely the dashboard-display belt so ITS title never reverts.

Every post-validation attempt is a **`web-rename`** `state_files` row
(`{win, chars, ok, tab, tab_retitled, override?, reason?}` — `override` is
whether the durable prefs override was recorded); an append failure is also
an `A.error`. The per-session SSE stream gained a **`title`** event (slow
cadence, on change, like `ctx`/`git`) — which also means a fresh AUTO
ai-title now live-updates an open session header, not just renames.

## Web rewind (`POST /api/session/<sid>/rewind-to`) — the full thing, no kitty hop

"Rewind to a specific message, choose what to restore" works entirely from
the page: the feed's prompt bubbles ARE the checkpoint list (Claude Code
checkpoints every user prompt), each carries a hover-revealed **↶** button
(picking mode — the idle ↶-button/double-Esc meaning — reveals them all
and makes whole bubbles clickable), and the mode menu on it mirrors Claude
Code's own confirm options (`RW_MODES` ↔ `rewindmenu.MODE_LABELS`:
conversation / code + conversation / code).

**Why drive the TUI menu at all?** A rewind is invisible outside the live
process: it writes NOTHING to the transcript at restore time — the
conversation state changes in memory, the file keeps every record, and
only the NEXT send materializes the fork (a user record whose
`parentUuid` points back at the fork point, the abandoned branch left in
place; verified live 2026-07-18). File snapshots do live on disk
(`~/.claude/file-history/<sid>/<hash>@vN`, mapped by the transcript's
`file-history-snapshot`/`-delta` records keyed to prompt uuids), so CODE
could be restored externally — but conversation could not, and a partial
reimplementation would drift. So `dashboard/rewindmenu.py` drives Claude
Code's own menu in the session's window, with every step verified by
reading the screen back (`Frontend.get_text`), never pressing blind:

- type `/rewind` (the 100%-reliable opener; draft killed first — Ctrl+U/K
  — so a held draft can't corrupt the command), poll until the checkpoint
  list renders (`menu_open`: the `Rewind` header + `Enter to continue`
  footer, anchored at the LAST header occurrence so scrollback can't
  spoof it);
- the list is one entry per LIVE-BRANCH user prompt, oldest first, cursor
  starting on the trailing `(current)` — burst the page's `ups` hint
  (`up`-press distance = newer prompt bubbles + 1) blind, then VERIFY the
  cursor entry against the target text (`entry_matches` — an entry is the
  prompt's first line, truncated to pane width with a trailing `…`, so
  truncation is a prefix match; cursor rows are indented `  ❯ `, which is
  what separates them from column-0 scrollback prompt echoes); a miss
  scans up to the top, then back down through the whole list — so a STALE
  page hint (dead-branch bubbles the menu doesn't list, e.g. after a
  kitty-side rewind the web never saw) self-corrects, and a target that
  is genuinely gone bails;
- Enter, then pick the restore option **by parsed LABEL, never position**:
  the confirm menu's numbering SHIFTS with content (with code changes
  `Restore conversation` is `2.`, without them it is `1.`) — a digit key
  selects immediately. A `both` request at a checkpoint with NO code
  changes **degrades to `Restore conversation`** rather than failing
  (verified against the screen's own "The code will be unchanged." line):
  the code is already in the target state, which is exactly why Claude
  Code omits the code options there — the response and audit row carry
  `degraded: true`, and the page's toast says so. A `code` request there
  still bails (`option`), now with the no-code-changes reason in the
  error;
- poll until the menu is gone. ANY unverified step raises `MenuError`
  (its `.step` names the failing stage: `open`/`find`/`confirm`/`option`/
  `close`) after Escape-closing whatever was open — the session is never
  left sitting inside a menu — and the endpoint returns it as a 409.

The endpoint refuses a BUSY tab outright (409 — mid-turn the gesture
means cancel, and a typed `/rewind` would just queue as a message; stop
or cancel first). Success returns `restored` (the target text) for the
conversation-restoring modes: Claude Code puts the rewound prompt back
into the TUI input, so the page runs the same tail as cancel-edit
(`prefillComposer`) — composer prefilled, next send `clear_draft` — and
`applyRewind` un-renders everything from the target bubble on, matching
what the terminal now shows (optimistic like cancel-edit: the transcript
keeps the dead branch, a full reload re-shows it). A code-only restore
changes no conversation, so nothing is dropped. Every attempt is a
`web-rewind-to` state_files row (`{win, ok, tab, mode, ups, steps,
digit}` on success, `{…, step}` on a bail), failures also an `A.error`.

Verified end-to-end live (2026-07-18): both-mode restore (file reverted +
composer prefilled), conversation-only with a deliberately WRONG hint
(the scan self-corrected; digit resolved to `2` — the label rule doing
real work), code-mode at a no-code-change checkpoint (clean `option`
bail, menus closed), and a nonexistent target (clean `find` bail).

Known limit, same family as the cancel one: a rewind done IN the kitty
tab is invisible to the web until its fork lands (no hook, no transcript
write) — the page keeps showing the dead turns until reload/next-send,
and its `ups` hints go stale, which the text-verified scan absorbs.

Adjacent documented facts the driver leans on or tolerates
(code.claude.com/docs/en/checkpointing.md + changelog; researched
2026-07-18): checkpoints cap at 100 per session (hard-coded — `SCAN_MAX`
mirrors it); file snapshotting can be disabled
(`fileCheckpointingEnabled` / `CLAUDE_CODE_DISABLE_FILE_CHECKPOINTING=1`),
which just makes every checkpoint a "No code changes" entry — a code-mode
request there is the normal `option` bail; and after a `/clear` the menu
grows a `/resume <sid> (previous session)` entry that is not a prompt —
the text scan walks past it like any non-matching entry. There is NO
programmatic restore API to prefer over the menu (no CLI flag, no
external SDK call — the open feature request is anthropics/claude-code
#16976), which is why screen-driving the TUI is not a stopgap but the
only sanctioned path.

The page wires rewind as the **↶ rewind**
button, and the session view's **Esc key** as an ATOMIC gesture: a lone
press is HELD for `ESC_DOUBLE_MS` (450 ms) then classified — single press
→ one `/interrupt` (an Escape key event; busy tab → "interrupted" toast,
idle → "double-press Esc for rewind"), rapid double → the double-Esc
meaning split CLIENT-side by tab state: mid-turn the `/rewind` POST (the
cancel-edit above), idle picking mode (no POST until you pick a message),
with **no separate Escape sent at all**. Streaming the first press immediately
shipped and corrupted the rewind: the in-flight Escape and the `/rewind`
text race through two server threads with variable kitten latency, and
one landed MID-TEXT — the input cleared after "/rewi" and the surviving
"nd" tail was submitted into the chat as a message. Nothing streams until
the gesture is decided, so nothing can interleave; the 450 ms hold on a
real interrupt is imperceptible next to the HTTP+kitten pipeline.
Residual accepted mismatch: a SLOW double-press (>450 ms) is two
interrupts to us, while the TUI's own (flaky) double-Esc detection may
still open the panel on those two Escapes — unavoidable in any design
that must send Escape key events for interrupts.

**The form's pickers are a custom dropdown, not `<select>`** (`dropdown()` in
app.js, `.nsdrop*` styles): Safari ignores most `<select>` styling even with
`appearance: none` and always opens the native white macOS popup for the
option list, which clashes with the theme — the custom control renders both
the closed state and the open list in the page's own cmenu language. It keeps
the old call-site shape (`value` get/set, `fill()` rebuild-preserving-value)
and native-ish keyboard handling (↑/↓/Enter/Space, Esc closes the menu without
closing the modal via `stopPropagation`). The directory field is freeform text
with `suggest()` — the same menu language over the snapshot's distinct cwds —
NOT a `<datalist>`: Safari renders that list in the system style too, and pops
it open on focus, which made the prefilled field look already-clicked. Only a
pointer CLICK on the field (or typing / ArrowDown) opens the menu — never
focus alone, which also fires on the form's own auto-focus — with the value
blank or an exact known cwd it lists EVERYTHING (the picker look, current
value highlighted), while typing filters by substring; Enter picks the
highlighted row, but when that row already IS the value (or nothing is
highlighted) it falls through to launch — so click-pick-Enter and
type-path-Enter both behave. Every picker/input row is a `div`, not a
`<label>` (only the prompt row keeps the label): label activation forwards
any click on the row — its TITLE included — into the field, focusing the
input or toggling the dropdown, and making it impossible to defocus by
clicking beside the field. Opening the form focuses the *prompt* when the
directory is already known (remembered or prefilled), the directory field
only when it's blank. While the form is up the page
behind it is scroll-locked (`body.modal-open` → `overflow: hidden`, set and
released by open/closeNewSession); a panel taller than the viewport scrolls
INSIDE the overlay (`.nsback` is `overflow-y: auto`), never the dashboard.

**The form remembers the last launch** (`claude-dash:ns-last` in
localStorage, written only on a *successful* launch): the directory, model
and effort preselect to their last-used values the next time the form opens
— launches are usually the same project on the same settings. An explicit
prefill (a dir group's "+", a parked session's resume button) still wins over
the remembered directory. Model and effort offer **concrete values only — no
"default" entry**: every launch sends explicit `--model`/`--effort` flags
(first-ever fallbacks `fable`/`high` before anything is remembered). The API
keeps `model`/`effort` optional — absent flags remain valid for other
clients; only the form always sends them.

**Resuming preselects the SESSION's own model/effort, not the last-used
prefs.** When the form opens on a `resume: <sid>` (a parked session's resume
button), a resume should continue where the *session* was, not where the
launcher last was — so a `/api/session/<sid>` fetch overrides the remembered
model/effort defaults with the resumed session's own: its **model** from the
ctx probe (the transcript tail's last assistant turn) and its **effort** from
`effort_default` (the last-applied `/effort` level — the only readable
per-session effort). The fetch is async and **yields to a hand pick** made
while it was in flight (`modelPicked`/`effortPicked`, the same discipline as
`acctPicked`); it only replaces a value still on its default. The account
picker keeps load-balancing — setting the resumed model re-runs `autoAcct`,
so the account is still auto-picked by weekly-quota perishability, skipping any
account the resumed model is limit-blocked on (*Default account* below).

**Resume picker.** The new-session form's conversation source is a **fresh
toggle** (`start`: "fresh conversation" ⇄ "resume a conversation", default fresh
for `+ session`, default resume when reached via a card's `↻ resume`) plus, when
resuming, a searchable/scrollable **resume picker** (`resumePicker`, app.js). It
replaces the old three-way "start from" dropdown: there is **no `--continue`** —
resuming the most-recent row IS "continue" (the picker auto-selects the newest
row on load, and `↻ resume` preselects its own session). Only `claude --resume
<sid>` is emitted; `body.resume` still validates against `_SID_OK` (one clean
argv word) and rides as a positional `"$@"` word ahead of `--model`/`--effort`/
prompt, so the injection story is unchanged (the endpoint still ACCEPTS a
`continue` bool for compatibility, but the form never sends it, and
`resume`+`continue` together still 400s).

The picker's rows come from **`GET /api/resumable?cwd=<dir>&limit=25&q=<text>`**
(`resumable_payload`) — the directory's sessions (canon-cwd-scoped, newest-first,
capped at `RESUMABLE_MAX`), each enriched with what a row shows: `title`,
`last_active`, `live`, the transcript-tail `model`, the SAVED `effort` (resolved
per the session's OWN account config dir, like `session_payload`), and the
`account` `{slug, label}` (its statusline-stashed slug). It is a read-only
endpoint (no state writes → no audit rows, like `/api/session/<sid>`); the browser
side is instead audited via the clientlog channel (`resume.list`/`resume.pick`/
`resume.preview`/`resume.mode`, *Frontend audit* below). Fetched when the form
opens / the toggle flips to resume, and re-fetched (debounced) as the directory
field changes.

**Search is SERVER-SIDE, across the directory's whole history.** The old form's
resume list was a client-side filter over the `S.sessions` snapshot — capped at
~10 rows, so an older session was simply unreachable (the audit's `resume.list`
`n:10` for a 162-session directory is exactly that bug). The picker's search box
instead refetches `/api/resumable` with `?q=` (debounced), and the server scans up
to `RESUMABLE_SCAN` sessions — enough to reach a stale directory that isn't in the
newest `SESSIONS_LIMIT` globally — matching `q` against title + sid and returning
the first `limit`. Discovery is one cheap audit query (+ a per-call canon-cwd
memo, since `realpath` is a syscall per row); the per-row transcript/settings
reads are the real cost, so only matched rows up to `limit` are enriched.

**Selecting a row reuses its model + effort, but NOT its account.** On every
pick, the form sets `model`/`effort` from that row (unless the user hand-picked
them first — `modelPicked`/`effortPicked`), then re-runs `autoAcct` so the
**account keeps load-balancing** by the normal scheduler (*Default account*
below) rather than pinning to whatever the resumed session used. So "continue
where the session was" applies to the model/effort, while the account follows the
same quota-aware logic a fresh launch does.

**Space previews the recent mirror transcript.** With a row highlighted, `Space`
toggles an inline preview panel (`.nspreview`) that fetches the session's recent
mirror tail from **`GET /api/session/<sid>/backlog`** (the newest `TAIL_BLOCKS`
slice — the mirror tab's own on-load call) and renders it with `renderPreview` —
the same server `{g,t,html}` items and block grouping the mirror tab uses, into a
throwaway container (never `S.ses`), blocks FOLDED so it's a compact scannable
peek (command/file/agent blocks collapse to a one-line summary; conversation
messages show inline; click a header to expand). Use `backlog`, NOT
`/history?before=N` — `/history` returns blocks *older than* a cursor, so
`before=0` returns nothing (the "no mirror history" bug the first cut shipped).
For the picker to be keyboard-drivable, selecting a row updates its highlight IN
PLACE (a full repaint would recreate the row element and drop keyboard focus, so
`Space` would land nowhere — the "space did nothing after I clicked" bug); on
open the selected row itself is focused (not the search box, which would pop the
iPad keyboard). The `resume.preview` audit row carries the rendered item count
`n`, so an empty-but-successful preview is distinguishable from a rendered one in
the DB alone (the blind spot that made the first diagnosis need an endpoint repro).

A resumed conversation **forks to a new sid** (CLAUDE.md: resume forks) — but NOT
at launch: SessionStart fires under the OLD sid (restoring its parked DB, so that
sid flips parked→live), and the fork happens at the first event after. The adopt
machinery handles the state hand-off as always; the jump watch must target the
OLD sid (see below — "new sid in the cwd" alone shipped broken once).

**Jump to the new session — and the wait it rides on.** The launch response
carries no session id — none exists yet (the session appears through its own
`SessionStart`; the server deliberately returns no synthetic row, and
inventing one would desync the list). Measured budget from click to
appearance: `kitten @ launch` ~100–200 ms, then **claude's own boot 1.4–2.1 s**
(audit `web-launch` rows joined against the following SessionStart — the
irreducible chunk), then up to a full `GLOBAL_TICK_S` before the sessions
poll notices. Three mechanisms cover it:

*The pending view (`#/launching`).* A form launch navigates IMMEDIATELY to an
optimistic "starting session…" page (spinner, launch dir, account/model/effort
chips, the typed first prompt echoed) instead of idling on the list — the
original design left ~2–3 s of dead air and then yanked the page when the
snapshot landed ("late jumping"). The arrival becomes a swap-in-place
(`jumpHit` uses `location.replace`, so the waiting room never enters history —
back lands on the list). Past `PEND_HINT_MS` the hint escalates with an
elapsed counter (counted from `armedAt`, so leaving/re-entering the room
doesn't reset the clock); the watch's 120 s timeout renders an inline failure
card ("claude may have failed to start") instead of a silent give-up. The
composer's resume-&-send deliberately does NOT open the pending view — the
user is already looking at the session being revived.

*The `wake` fast path (server).* On a successful launch `_launch_wake` (a
daemon thread, `LAUNCHWAKE_POLL_S`/`LAUNCHWAKE_MAX_S`) polls the sessions
head for the launched session — by `kitty_window_id` when the launch reported
one (exact across fresh/resume/continue: the audit's SessionStart upsert
stamps a resumed row's new window too), else a fresh `started_at` in the
launch cwd — and pushes a `wake` `{sid, win, cwd}` into `NOTIFIER`. That both
delivers the sid to every page (the one whose armed watch matches — win,
resumed sid, or cwd — jumps instantly) and unblocks the `sse_global` loops'
queue wait, so the snapshot follows NOW instead of at the next tick. A
timeout pushes nothing — there'd be nothing to jump to.

*The snapshot watch (client fallback — stub terminals, a wake lost to a
reconnect).* `armJump` stashes the known sids, the currently-LIVE sids, the
launched cwd, and the response's `win`; every global `sessions` snapshot AND
`sessions-delta` runs `checkJump` (delta too: a known row flipping
parked→live moves no membership/order, so waiting for full snapshots alone
could miss a resume). A hit is, in priority order: the `win` row (gated on
`live` — a previous terminal RUN's ids restart from 1, so a stale row can
collide), *that* resumed sid coming back to life (matched by sid, not cwd —
you can resume into a different directory), or a cwd-row that is brand-new or
freshly parked→live (`liveAtArm` — a plain "new sid" check misses resume and
continue, which re-animate an EXISTING sid at SessionStart and only fork to a
new one at the first event after; this shipped broken once).

*Navigating away mid-wait must not break the wait.* A user-driven route
change while the watch is armed flips it **quiet** (`route()` — jumpHit's own
navigations never land there armed, it clears `S.jump` before touching the
hash): the watch keeps running, but resolution becomes a clickable "session
started" toast instead of a navigation — yanking the browser away from
wherever the user went is the exact annoyance the pending view removes (this
replaces the old cancel-outright, which orphaned the launch if you peeked at
another session mid-wait). A quiet resolution also stashes `S.jumpDone`, so
browser-back to `#/launching` forwards to the session that arrived meanwhile
(consumed once); re-entering `#/launching` with the watch still armed
un-quiets and re-mounts the pending view. The 120 s timeout still bounds
every path — a launch that never produces a session can't toast or yank
minutes later.

**Audit.** Every attempt lands a `state_files` row: `web-send`
(`{win, chars, ok, tab}` — `tab` is the state at send time, so "my message
vanished" is answerable as "it queued mid-turn"; keyed to the session's
state-DB path) and `web-launch`
(`{cwd, model, effort, resume, cont, account, ok, win}`, no session yet so
log/path are empty) followed by its watcher's one `web-launch-wake`
(`{sid, win, cwd, ok, waited_s}` — found: `waited_s` IS the launch→appearance
latency, the dashboard's own share of "launching felt slow" reconstructible
next to the `web-launch` row; timeout: `ok` false, sid empty),
`web-stop` (`{win, phase, ok}` — `phase` is `attempt`, written BEFORE the
potentially-blocking `close_tab`, then `done` with the `ok` outcome; a lone
`attempt` with no paired `done` means `close_tab` HUNG and never returned — an
unbounded kitten socket connect — so the tab won't close and the client's greyed
'closing…' hangs to its 20s watchdog, the "dashboard close entered but never
completed" anomaly; before the attempt row existed a hung close left NO
server-side trace, only the client's `web-hint op=close … stale`) and
`web-interrupt` (`{win, ok, tab}` —
the tab state at press time says what the Escape landed on). Failure paths
(no window, no terminal, send/launch/close/key returned false) also write an
`A.error` per the audit-before-swallow rule, so a "my message never arrived"
report is answerable from the DB.

**Input-validation rejects are NOT `A.error`s.** A client that sends a bad
field (a partial/non-existent `cwd`, a typo'd `model`/`effort`, a malformed
`resume`, an unknown `account`, a bad quick-`command`, a bad `hide-dir` key —
AND an empty message, an empty rename, a bad upload, a bad rewind `mode`, a
non-string composer draft, a non-list composer queue, a bad `hint-audit`
phase/op, a wrong-count ask answer or draft, an actionless plan decision) gets a
400/4xx and an `ok:False` `state_files` row under the handler's own action
(`web-launch` / `web-command` / `hide-dir` / `web-send` / `web-rename` /
`web-upload` / `web-rewind-to` / `composer-draft` / `composer-queue` /
`web-hint` / `web-answer` / `ask-draft` / `web-plan`) carrying `why:"<reason>"`
plus the offending field
`repr()`'d — the shared `Handler._reject_input` helper. It takes an optional
`log`/`path`, so a SESSION-scoped reject files under THAT session's timeline
(not just the global stream) — without which every empty-message / empty-name /
bad-payload reject was a silent 4xx, the exact class the `web-reject` guard fix
closed one layer down. Deliberately NOT an `errors` row: these are expected
client-input 4xx, not swallowed exceptions (their traceback would be a bare
`NoneType: None`), and `errwatch` surfaces every `session_id=''` `errors` row as
a `⚠ global:` chip in EVERY session's scorebar — so a stray "ba" typed into the
new-session form must not light a warning light that never clears. Genuine
server-side failures (no terminal, launch/grant returned false) stay `A.error` —
those ARE bugs worth the light. (The stash-race 409s — `no pending question` /
`ask expired` on the ask/plan cards — deliberately stay row-less: they fire
legitimately when the dialog was resolved AT THE TERMINAL, and the
`ask-pending`/`plan-pending` stash lifecycle already records that.)

**Guard rejections ARE audited now (`web-reject`).** The above is the
INPUT-validation layer (a handler ran and disliked a field). BENEATH it,
`_post_guard` rejects a POST before ANY handler runs — a missing `X-Claude-Dash`
header, a cross-origin `Origin`, read-only mode, an oversized/malformed body.
Those used to write NOTHING, which was a real blind spot: a browser `/stop`
that produced a client `web-hint op=close` beacon yet **no `web-stop` row** was
indistinguishable between "the POST never left the browser" and "it arrived but
the guard bounced it". `_reject` now writes a `web-reject` `state_files` row
(path = the rejected request path, content `{code, why}`) — audit-only
telemetry, NOT an `errors` row (an expected 4xx, same reasoning as the
input-validation rejects), so it never lights the warning chip. Paired with the
client's `web-clientfail` beacon (which the `close` gesture now also fires on a
failed `/stop` fetch), a stuck close is now fully attributable: a `web-reject`
for the `/stop` path = guard-bounced; a `web-clientfail gesture:close` = the
fetch itself failed/aborted; neither, only the `web-hint` = the POST never left
the page (a rendering/wiring bug, e.g. the launch tag-race below).

### Close via the plain-fetch channel (and why sendBeacon was a regression)

A stuck close was the hardest bug of the lot, and the wrong turn is instructive.
Repeated closes left the SAME shape server-side: the click's `/hint-audit`
beacon arrived (a `web-hint op=close shown` row), then a 20s `web-hint … stale`
and **NO `web-stop`, no `web-reject`** — the `/stop` request never reached the
handler. On the (mistaken) theory that the page's long-lived SSE `EventSource`
streams had starved the browser→proxy fetch connection pool, the close was moved
to **`navigator.sendBeacon`**. That REGRESSED it: `sendBeacon` returns `true`
(queued) so `closeSession` resolved `ok` optimistically, but the queued beacon
was then silently dropped by the tunnel — still no `web-stop`, no `web-reject`,
no fallback, no trace.

What the frontend audit (below) finally proved: the transport that DOES traverse
the tunnel is the plain `fetch` — the `/hint-audit` beacon and the composer's
`/message` both ride it and always land, and every morning-era close (plain
`fetch`, before the sendBeacon change) succeeded; `sendBeacon` is the one that
vanishes. So `closeSession()` (app.js) sends the close over `postJSON` — the
plain-fetch channel (`X-Claude-Dash` header, JSON body) — tagged
`audit:"close"`, with a `CLOSE_POST_MS` (< the 20s watchdog) `AbortController`
timeout so a genuine upstream stall becomes a VISIBLE, retryable, audited
failure (`close.fail kind:transport` + `web-clientfail`) instead of a silent
hang. It is optimistic: the card greys immediately, the sessions poll
(`reconcileCloses`) confirms the park, and a close that didn't land reverts the
card.

`_post_guard` still accepts a header-less POST by **allowlisted Origin** — no
longer for the close (which carries the header again) but for the one legitimate
`sendBeacon` left: the frontend-audit flush on `pagehide` (below). A cross-origin
page can forge neither the header nor an allowlisted Origin, so the Origin
allow-list remains the CSRF gate.

### Frontend audit (clientlog)

The close saga burned several rounds because the server can only ever see a
control request that ACTUALLY ARRIVED — a `/stop` the browser *tried* but that
never reached a handler (dropped by the tunnel, starved of a connection, queued
forever) left no server trace at all, so every diagnosis was a guess. The fix is
a **frontend audit channel**: the browser reports what IT did, and those reports
become audit rows.

- **Client** (`app.js`): `clog(sid, ev, data)` appends an event to a ring
  buffer; `flushClog()` delivers the batch as ONE `POST /api/clientlog` over the
  plain-fetch channel (the one proven to traverse the tunnel — NOT `sendBeacon`,
  the very transport that vanished the close). A `pagehide` /
  `visibilitychange→hidden` does flush via `sendBeacon` (a last-ditch flush as
  the tab goes away is exactly beacon's job, and losing the tail then is fine).
  Every batch carries a `connInfo()` snapshot — `online`, `view`, `es` (SSE
  streams held open — the connection-pool evidence), `conn` (global stream up).
- **The spine is `postJSON`**: a control POST tagged `{audit:"<gesture>"}`
  auto-logs its whole transport lifecycle — `<gesture>.begin` (with `ep`, `es`,
  and gesture-specific `auditData`), `<gesture>.ok` (`ms`, `status`), and
  `<gesture>.fail` (`ms`, `kind` http|transport, `status`/`error`, `aborted` for
  a timeout). Tagged today: `close`, `send`, `command`, `interrupt`, `rename`,
  `migrate`, `rewind`, `rewind-to`, `answer`, `plan`, `new`, `resume-send`. The
  telemetry endpoints themselves (`/clientlog`, `/hint-audit`, `/client-fail`)
  are deliberately untagged — tagging them would recurse.
- **Also captured** (event-driven, never periodic — the ring + server cap bound
  the volume):
  - **SSE health**: `sse.open` / `sse.drop` per stream (global/session/agent) —
    the direct read on connection health.
  - **Uncaught JS**: `js.error` / `js.reject` — a handler throwing used to be a
    silent product bug (this is what caught the real can't-close cause, an
    uninitialized `S.closePend` throwing before `closeSession` ran).
  - **Page + build lifecycle**: one `boot` per load (origin — `127.0.0.1` vs the
    tunnel — + device + viewport + the LOADED build id from the `?v=` on this
    `app.js`); `hello` (the server build the page first connected to); `stale`
    (the server redeployed under an open page — `boot.build` ≠ current = the
    browser is on stale cached JS, the "product bug that was really old code").
  - **Session-view load / the launch tag-race**: `meta.stuck` (the composer + ✕
    close stayed dead because the pane never tagged), `meta.resolved` (the
    self-heal worked after N retries), `meta.fail` (the meta GET rejected);
    `backlog.fail` (the initial stream GET failed → "waiting for activity…").
  - **Launch story** (the client half of `web-launch`/`web-launch-wake`):
    `launch.arm` → `launch.hit` (appeared, with latency) / `launch.timeout`
    (never showed up in time).
  - **Resume picker** (the read-only `/api/resumable` + `/backlog` gestures leave
    no server row, so the browser is the only witness — *Resume picker* above):
    `resume.mode` (`fresh` toggled), `resume.list` (`cwd` + search `q` + row count
    `n` + preselection — a "picker was empty / search didn't find my session"
    report is answerable from this: the `n:10` for a 162-session dir is what
    exposed the client-side-filter scope bug) / `resume.list.fail`, `resume.pick`
    (the chosen sid + the `model`/`effort`/`account` it CARRIED — so a "resumed
    with the wrong model/effort" report is reconstructible), and `resume.preview`
    (`shown`/`cached`/rendered item count `n` — `n:0` IS the "no mirror history"
    empty preview) / `resume.preview.fail`.
  The audit itself is SELF-GUARDING — `clog`/`flushClog` swallow their own
  exceptions and a re-entrancy flag stops a throw-in-a-flush from looping back
  through the `js.error` handler (the one channel that must never raise the very
  error it exists to record).
- **Server** (`post_client_log`): behind `_post_guard`, writes one `web-client`
  `state_files` row per event, scoped to the event's own `sid` (a blank sid is a
  session-less row — a boot, a launch). Bounded by construction: at most
  `CLIENTLOG_MAX` events per batch, only JSON scalars kept (`_clip_scalars`),
  strings capped — a page can't stuff bulk into the audit. Audit-only, always
  200 unless the guard rejects.

This is the general per-gesture transport + connection + error timeline the two
older client beacons sit on top of: `web-hint` tracks OPTIMISTIC-UI lifecycle
(shown/reconciled/stale), `web-clientfail` a single observed gesture failure,
`web-client` the transport truth beneath both. A stuck close is now fully
attributable from the DB alone: a `close.begin` with no `close.ok`/`close.fail`
= the request left but no response came (tunnel/upstream drop — the sendBeacon
failure mode); a `close.fail kind:transport aborted:true` = our timeout fired (a
genuine hang); a `web-reject` on `/stop` = guard-bounced; a paired `web-stop
attempt` with no `done` = `close_tab` itself hung. If a close still stalls with
`close.begin`-only rows through the tunnel while `127.0.0.1:8377` (no proxy
between) closes fine, the bottleneck is the proxy→upstream pool — proxy config,
not an app fix.

**What it actually caught (and why the transport hunt was a red herring).** The
first restart with this audit live immediately produced recurring
`ev:"js.error"` rows — `Uncaught TypeError: Cannot convert undefined or null to
object` at `app.js:878`, firing on EVERY sessions tick — with NO `close.begin`
at all. That is the TRUE "still not closing" root cause: the `S` state object
shipped WITHOUT initializing `closePend`, so `reconcileCloses`'s
`Object.keys(S.closePend)` threw every tick AND the ✕ handler's
`S.closePend[sid] = optPending(...)` threw BEFORE `closeSession` ever ran — so
`/stop` was never sent (only the `web-hint shown`+`stale` from the `optPending`
that evaluated first). It reproduced on the tunnel AND locally because it was
never a transport bug — `closeSession` wasn't reached at all; the whole
sendBeacon-vs-fetch investigation chased a symptom. `S.closePend` is now
initialized (`closePend: {}`), guarded by `test_app_js_initializes_close_state`.
The frontend audit is what surfaced it — an uncaught handler exception was
previously invisible server-side, exactly the blind spot this channel closes.

**The launch tag-race (why a just-launched session's controls were dead).** A
dashboard launch jumps straight to the new sid, but its kitty pane isn't tagged
`claude_session=<sid>` for a moment, so `/api/session` momentarily reports
`live:true` with a BLANK `kitty_window_id` (`session_payload` resolves the
window through `_live_windows`, empty until the pane is tagged — unlike the
sessions LIST, which carries the RAW audit id immediately). The client gates the
composer AND the `✕ close` button on `meta.live && meta.kitty_window_id`, and
that partial meta fails BOTH the send gate (`live && window`) and the resume
gate (`!live`) — so the box locked and the close button never rendered until a
manual reload (the reported bug). The global-poll heal (`updateHeadFromList`) is
meant to repair it but is fragile across the raw-vs-resolved window-id spaces, so
`showSession` now re-fetches meta directly (bounded, `LAUNCH_RESOLVE_TRIES` ×
`LAUNCH_RESOLVE_MS`) until the window resolves — authoritative and
self-healing, no reload needed.

## Web ask (`POST /api/session/<sid>/answer`) — AskUserQuestion from the browser

When Claude asks a question (the AskUserQuestion tool), the session view
grows an **ask card** above the composer mirroring the TUI dialog: one
block per question (the header chip + question text + a dim
"pick one"/"pick any" hint), option buttons whose leading mark makes the
select mode legible at a glance (a radio circle for single-select, a
checkbox square that fills with a ✓ for multiSelect), a free-text "type
your own" input per question (the dialog's "Type something" row) — which
carries a **red (`--ask`) border while it is the ACTIVE answer** and none
otherwise: multiSelect whenever it holds text (additive to any checked
options), single-select only while NO option is selected (typing a custom
answer deselects the options; clicking an option reclaims the answer but
**keeps the typed text** — it sits dormant and borderless, and clicking
back into the field reselects it, no retype). The old option-click USED to
wipe the field (silent data loss); now the text is preserved and submit
sends `other:""` whenever a single-select option is chosen, so the dormant
text can never override the clicked option (`askdialog._answer_question`
gives `other` precedence over `selected`). "Active answer" is derived, not
stored — `hasText && (multiSelect || noOptionSelected)` — a
submit row, and **chat about this** (the dialog's own
decline-and-discuss). Submission is ALWAYS the explicit submit button
(or Enter in a free-text row) — a lone single-select question does NOT
submit on the option click itself. That one-keystroke feel is right for
the TUI's one-key select but wrong for the web: a misclick would fire the
answer with no chance to reconsider, so the card favors
review-before-send (selections stay editable until submitted).

**Claude's context rides on the card.** The AskUserQuestion dialog carries
only the terse question + options, but Claude almost always writes a prose
LEAD-IN first — the "why" framing the choice ("I've traced this all the
way down; there are two separate problems…"). That text is a normal
assistant `message`, so it already shows as a `claude` bubble in the merged
stream — but detached from the card you actually answer from, and easy to
miss. So the ask card now renders it above the questions:
`transcript.ask_preamble(path, tool_use_id)` returns the text block(s) in
the SAME assistant message before the AskUserQuestion tool_use, or (the
common shape, where the tool call stands alone in its own message) the
trailing text of the most recent earlier assistant message in the SAME turn
(a real user prompt resets the turn) — i.e. exactly the last `message` the
stream shows before the question, so card and stream can't disagree (both
walk `parse_line`'s blocks with the same non-empty-text rule). It reaches
the page via `plugins.ask_preamble(sid, tool_use_id)` (the registry fan-out,
same sid resolution as `conversation`), rendered by the server to
`preamble_html` (the msg-bubble `md_html`, escape-first) and enriched onto
the ask payload in `_ask_wire` — kept OUT of `_ask_pending`, which is the
per-tick SSE change-detection poll and must stay a cheap kv read, so the
transcript is touched only when the ask actually changes / on session open.
A pure read-model addition over the already-audited transcript (no new hook,
stream, or state), the same shape as the `question`/`answer` bubbles below.
`""` when Claude asked with no framing text (the card just omits the block);
a failed read degrades to `""` too — it never blocks the question rendering.

**Detection** is a hook stash, because the dialog is otherwise just
pixels: `plugins/claude_code/ask_fmt.py` (routed by the dispatcher on
PreToolUse/PostToolUse(+Failure) matcher `AskUserQuestion`, plus
Stop/StopFailure and UserPromptSubmit) writes the pending ask —
`tool_input.questions` verbatim + `tool_use_id` — to the state DB kv
`ask-pending` on PreToolUse, and clears it on the answer's PostToolUse
or, crucially, at the TURN BOUNDARY: every decline path (Esc in the
terminal, "Chat about this", an Enter on the EMPTY "Type something" row)
resolves the tool as "User declined to answer questions" with **no
closing hook at all** (measured 2026-07-18; 243 PreToolUse vs 230
PostToolUse in the historical audit — the 13 unmatched are declines), so
Stop/UserPromptSubmit are the clear signal. The stash respects the
main-session-only invariant (`agent_id` events ignored) and the
ghost-DB rule (`state.parked()` guard — an unhosted/headless session
gets no stash, and `kv_get` must never create the DB whose existence is
the session-alive signal). Reads are `kv_at` (ro). The session snapshot
carries `ask`, and the session SSE emits an `ask` event on every change
(fast cadence) — the card appears the moment the dialog does and
disappears when ANY answer path resolves it, web or terminal.

**Draft selections survive a device switch.** The in-progress answers
(options clicked, free text typed, nothing submitted yet) are NOT purely
browser-local — that lost them the moment you jumped to another device or
reloaded. On every edit the card debounce-POSTs them to
`POST /api/session/<sid>/ask-draft`, which writes the `ask-draft` kv
(`{tool_use_id, answers:[{selected, other}], origin}`; a pure state write
via `ST.kv_set_at`, guarded to the OPEN ask's `tool_use_id`, types nothing
into the terminal). The session snapshot carries `ask_draft` and the SSE
emits an `ask-draft` event on change, so `renderAsk` SEEDS the card from
it on open (`seedAskAnswers`) and an already-open card on another device
tracks the edits live (`applyAskDraft`). Each page stamps its writes with
a per-load `origin` (`CLIENT_ID`) and ignores the SSE echo of its OWN
`origin`, so a device never clobbers its own typing; a peer's `origin`
differs and IS applied (last-writer-wins, which is right for a shared
draft). `_ask_draft` only returns the draft while it still matches the
open ask — a stale one is ignored — and `ask_fmt.py` clears `ask-draft`
on the SAME boundary as `ask-pending` (its PostToolUse, or the turn
boundary), so it never outlives its question. Best-effort throughout: a
failed save retries on the next edit and the local card keeps its state.

**Answering** drives the TUI's own dialog — `dashboard/askdialog.py`,
the rewindmenu philosophy (screen-verified key events, never a blind
press) but deliberately NOT unified with it: different anatomy, and
OPPOSITE bail semantics — rewindmenu bails by pressing Escape, while
here **Escape declines the whole question set**, so a failed step leaves
the dialog exactly as it was (AskError → 409 with `step`; a retry
re-normalizes). Because Escape is the decline key, the DIALOG itself must
never receive a stray Escape from elsewhere in the dashboard: a
`cancel-edit` gesture once fired its Esc-Esc into an open ask (the tab was
red `awaiting-command`, which the cancel path wrongly treated as a
cancelable mid-turn state), so by the time the user's answer POSTed the
dialog was already declined and `drive` bailed at the very first check with
`AskError("open", "no question dialog on screen")` — the "I answered but it
failed, and the tab said *User declined*" report (2026-07-20). The fix lives
on the gesture side (`_dialog_open_guard`, the interrupt section): no web
interrupt / cancel-edit / rewind sends a key while a red dialog is open.

**The open-check polls (2026-07-22).** `drive`'s first check — is the dialog
on screen at all — used to read `get_text` ONCE with no retry, unlike every
later step (which polls via `_wait` up to `STEP_TIMEOUT_S`). So a capture
taken a beat too early — the dialog still rendering right after a `--resume`
into a fresh kitty window, or a transient blank/partial `get_text` — bailed
immediately with `step: open` on an ask that was genuinely up and never
answered (session `0247ebb2`, 2026-07-21). It now polls like the rest. And
every `AskError` carries the SCREEN it saw (`e.screen`); `post_answer` folds
it (via `_clip_screen`) into the `dashboard answer (<step>)` audit `errors`
row's `screen` field, because the bail otherwise records only its outcome — a
step:open can't be told apart after the fact (dialog too tall for the visible
screen · a `FOOT`/`REVIEW` footer-string drift after a Claude Code upgrade · a
blank capture) without the pixels. `_clip_screen` keeps BOTH ends of a long
capture (head + tail, `SCREEN_CLIP` = 2000) rather than a plain `[-2000:]`
tail: a step:open's discriminator is whether the ☐/☒ chip bar is at the TOP,
so a wide window whose visible screen exceeds the cap must not have that top
truncated away and misread as 'off-screen'.

**The dialog-open detector tolerates the chip bar scrolling off-screen
(2026-07-23).** `dialog_open`/`review_open` isolate the dialog via
`askdialog.region`, which anchors on the LAST `☐`/`☒` header-chip bar and
returns "" when there's none. On a NARROW/SHORT window a tall dialog (several
options with wrapped multi-line descriptions) overflows the visible viewport,
so the chip bar scrolls off the TOP while the footer survives at the bottom —
`get_text` returns only the visible screen, so the bar is simply absent. The
chip-bar-only anchor then returned "" and `drive` false-bailed `step: open` on
a genuinely-open dialog the user was staring at (session `819627e5`,
2026-07-23: a narrow window, the `screen` capture showed options 1–5 + the
`Enter to select … Esc to cancel` footer but no chip bar). `region` now falls
back to the WHOLE screen when there's no chip bar but a dialog footer
(`FOOT`/`REVIEW`) is present — so open-detection AND row/question parsing still
work; the chip-bar path stays primary (it cleanly excludes the transcript
whenever the bar IS visible). A `step: open` whose `screen` shows a footer but
no chip bar on a current build means the fallback regressed.

**The key model was overhauled in v2.1.215 (re-measured 2026-07-19).**
The original v2.1.214 model was *digit-driven* — a digit selected a
single-select option, toggled a multiSelect box, and numbered the "Type
something"/"Chat about this" rows. v2.1.215 rebuilt the dialog: **digits
are now inert**, selection is cursor-driven (move the `❯` with up/down,
press Enter), an option `preview` switches the whole dialog to a
side-by-side layout, and there is a new "Notes: press n" affordance. The
symptom was every web answer to a *multi-question* ask failing with
`question 2 never became current`: the driver pressed the option's digit
(a no-op in v2.1.215), the single-select never auto-advanced, and the
wait for the next question timed out. The measured v2.1.215 model:

- **selection is cursor + Enter, never a digit** — digits do nothing.
  `_cursor_to` walks the `❯` to a target row (normalize to the top, then
  down, screen-verified each step; deliberately walk-based, not index
  arithmetic, because the dialog now has non-cursor rows the parser skips
  — indented descriptions, the "Notes" hint, preview-box lines);
- single-select: Enter on the cursored option selects AND auto-advances;
  the sole question of a one-question ask submits the tool outright (no
  review pane);
- multiSelect: Enter on the cursored option TOGGLES its checkbox — so the
  driver DIFFS the desired selection against the checkboxes the screen
  actually shows (boxes the user pre-toggled in the terminal are
  reconciled, not re-flipped), then it advances by cursoring onto the
  question's own "Next"/"Submit" advance row + Enter (`_advance_multi`,
  screen-verified). **NOT a blind `right`** — see the forward-only note
  below: `right`/`left`/`Tab` don't switch questions at all in this build,
  so the only advance is the "Next" row's Enter. A failed advance bails its
  own step `advance` (not the misleading `question` one tab later);
- TWO layouts: with no `preview` on any option, options carry an indented
  description line and "Chat about this" is NUMBERED; when ANY option has
  a `preview`, the dialog draws a box to the RIGHT of the option rows
  (its text bleeds onto the option lines — `rows()` strips a `\s{2,}` +
  box-drawing-char run off each label), adds a "Notes: press n" hint row,
  and renders "Chat about this" UNNUMBERED. The driver is layout-agnostic
  because it never reads a digit — it cursors + Enters and finds the chat
  row by its label;
- free text: cursor onto the "Type something" row (navigated by its ROW
  NUMBER `len(options)+1`, since the label mutates to the typed text),
  then `send_text` (types the text + a CR): single-select commits it and
  auto-advances; multiSelect commits + checks the custom row — with a
  screen-verified fallback Enter, since whether the CR alone checks it was
  not nailed down;
- **FORWARD-ONLY navigation.** `left`/`right`/`Tab` do NOT switch questions
  in this Claude Code build — they are inert, or caret movement on a focused
  text row (verified live 2026-07-22, session 3fd325d9: `left`/`right`/`Tab`
  from every row left the same question showing). The ONLY way to a later
  question is answering the current one (single-select auto-advance / the
  "Next" row's Enter); there is no back-navigation. So `drive` answers
  whatever question is CURRENTLY on screen, in order, and lets each answer
  move the pane on — it does NOT normalize to question 1 first. The old
  `left`×len normalize assumed back-nav: on a fresh dialog it was a harmless
  no-op, but a dialog already stuck/partway on a LATER question (a prior
  half-answer, or a terminal-side answer) could never be walked back, so the
  very first wait bailed `question 1 never became current` (the 3fd325d9
  RETRY, after the custom-multiSelect advance bug above left the dialog on
  question 2). Starting from the current question also RECOVERS such a
  dialog — the remaining questions get answered forward, earlier ones keep
  whatever already set them. up/down still move the row cursor, except a
  filled custom-text row traps upward movement (edit focus) — `_cursor_to`'s
  down-walk fallback handles that and its normalize-up bails early when up
  stops making progress;
- each question is verified CURRENT by finding its text in the dialog
  region — ALL whitespace stripped from both sides before the substring
  match, because long question text wraps across screen lines and a
  wrap can land mid-word (a hyphenated path); a real 555-char question
  never matched the original exact line-set lookup (the live `question
  1 never became current` bail, 2026-07-18). The review pane is
  excluded explicitly (`current_question` → None on "Review your
  answers") since its answer recap repeats every question's text;
- the review pane ("Review your answers") follows the last question;
  cursor onto the "Submit answers" row + Enter submits. PostToolUse then
  fires with `answers` {question → label, ", "-joined labels (custom text
  joins as a label), or the free text} — verified live for every shape:
  single label, free-text-only, two-question mixed with custom multi text
  (`{"Pick a planet": "Venus", "Pick metals": "Iron, Zinc, titanium"}`),
  and chat-about-this.

**A typed answer on a PREVIEW-layout question is delivered via "Chat about
this"** (2026-07-19, corrected 2026-07-20 after a live re-test). An ask whose
options carry a `preview` renders the side-by-side layout, which has **no
numbered "Type something" row** — a typed answer can't be entered as an option.
But "Chat about this" IS reachable; the driver just couldn't recognize it. The
subtlety (verified by probing the live dialog, arrows only): "Chat about this"
is the row BELOW the last option, reached by `down` from it — and when the
cursor lands there, the preview layout renders `❯` on **both** the last option
AND the Chat row (a highlight bleed). `_cursor_to` read only the FIRST cursor
mark (the option) and so never recognized it had reached Chat, dead-looping
(`cursor never reached Chat row`). The fixes:

- `_cursor_to` now treats a row as reached if **any** cursored row matches the
  target, not just the first — so it recognizes Chat in the two-`❯` state.
  Option targeting is unaffected: the down-from-top walk stops at the clean
  single-`❯` option row before it ever descends into the two-`❯` state (pinned
  by `test_cursor_to_reaches_chat_in_two_cursor_preview_layout`);
- the card detects a preview question (`askHasPreview` — any option with a
  `preview`) and, on a TYPED answer, routes it through "Chat about this" AND
  carries the typed text as `message` in the `/answer` body. `post_answer`
  presses chat, waits for the dialog to close, then delivers the text as a
  normal message (`fe.paste_text`, a `web-send` row `via: ask-chat`) — so the
  custom answer reaches the session. Selecting an *option* still drives
  normally;
- `askdialog._require_type_row` remains a fast-fail belt-and-suspenders
  (`step: type`) for the free-text path, which the card no longer takes on a
  preview question (it routes to chat instead).

The dialog is live TUI pixels with no answer API, so this key model can
only be verified by driving a real dialog and reading the screen back —
which is why a Claude Code version bump can silently break it. The
parsers are pinned against real captures of BOTH layouts in
`test_askdialog_parsers_pin_the_real_screens`; the reactive `_AskFE` fake
models the v2.1.215 key semantics for the end-to-end `post_answer` tests.

The endpoint guards before any key: the body's `tool_use_id` must match
the stash (a STALE card — a newer ask replaced it — is a clean 409
"expired"), the answers list must match the question count, and the
dialog must actually be on screen (`step: open` 409 otherwise — e.g.
answered in the terminal while the card sat open; the SSE clear races
the click). Every attempt is a `web-answer` state_files row
(`{win, ok, chat, tool_use_id}` (+`step` on a bail)), failures also an
`A.error`. The card clears optimistically on 200 and authoritatively via
the SSE `ask` event when the stash drops.

## Web plan mode (`POST /api/session/<sid>/plan-decision`) — ExitPlanMode from the browser

When Claude presents a plan (ExitPlanMode — the "Ready to code? … Would
you like to proceed?" dialog), the session view grows a **plan card**
above the composer: the plan itself rendered as markdown (`plan_html`,
the server-side md_html of the PreToolUse payload's `plan` — the raw
markdown rides the hook, measured 2026-07-18, alongside `planFilePath`),
the dialog's decision buttons, a feedback box mirroring the "Tell Claude
what to change" row, and **keep planning** (the dialog's own Esc).

**Detection** rides the same stash as the ask card:
`plugins/claude_code/ask_fmt.py` is the pending MODAL-DIALOG tracker for
both tools (dispatcher matcher `AskUserQuestion|ExitPlanMode`) — kv
`plan-pending` written on PreToolUse, cleared on the tool's own
PostToolUse(+Failure) and at the turn boundaries, because every plan
decline (terminal Esc, a typed feedback) fires NO closing hook — the
transcript just gains the rejection `tool_result` ("The user doesn't
want to proceed…"). The clears are TOOL-SCOPED: an ExitPlanMode approval
drops only `plan-pending`, never a co-pending ask stash (and vice
versa); the turn boundaries drop both. Snapshot carries `plan`, the
session SSE emits a `plan` event on change.

**The decision buttons come from the live screen** — `POST
/plan-options` (`dashboard/plandialog.options`, read-only, no key
pressed): the labels VARY with the session's permission mode ("Yes, and
bypass permissions" in a bypass session vs "Yes, and auto-accept edits"
elsewhere — measured), and they exist nowhere but the dialog pixels, so
hardcoding them would drift. The card fetches once per render; a parse
failure degrades to the feedback box + "decide in the terminal".

**Deciding** (`POST /plan-decision`, `dashboard/plandialog.py` — third
sibling of rewindmenu/askdialog, same screen-verified philosophy):

- `digit` + `label` — press that decision row, after verifying the
  screen STILL shows that label on that digit (the dialog may have been
  replaced since the options were fetched — label drift is a 409 with
  nothing pressed). A decision digit selects immediately (measured:
  approve fired PostToolUse, flipped the permission mode per the chosen
  option, and executed the plan);
- `feedback` — the "Tell Claude what to change" row: its digit only
  FOCUSES the editable row (measured — unlike the decision rows), typed
  text goes inline and Enter submits the rejection-with-feedback.
  Newlines collapse to spaces (single-line editor; a raw CR mid-text
  would submit early);
- `dismiss: true` — Escape, the TUI's own reject-and-keep-planning.

Bail semantics match askdialog, NOT rewindmenu: a failed step leaves the
dialog exactly as it was (an Escape bail would REJECT a plan the user
may still want to approve) — PlanError → 409 with `step`. An `open` bail
(the dialog is gone while the stash lingers — resolved in the terminal,
the turn-boundary clear not yet fired) **self-heals the stash**
(`_heal_stash` → `state.kv_del_at`, the explicit-path fresh-connection
delete: the request runs on a handler THREAD, where kv_del's cached
connection would silently no-op under sqlite's check_same_thread), so
the page's card clears on the next SSE tick; the same heal applies to
the ask card's `open` bail. Every attempt is a `web-plan` state_files
row (`{win, ok, kind: decide|feedback|dismiss, label, tool_use_id}`,
+`step` on a bail), failures also an `A.error`.

Verified live end-to-end (2026-07-18): feedback → Claude revised the
plan (and the final output honored it), dismiss → rejected in place,
approve by digit+label → PostToolUse + the plan executed; options parsed
from the live dialog exactly; stash lifecycle audited write→remove with
reasons (`answered` / `new prompt` / overwrite-by-revision).

## Web tasks (the pinned tasks card)

The session's native task list (Claude Code's TaskCreate/TaskUpdate
tools) renders as a **tasks card pinned at the very top of the mirror
tab** — above the plan/ask cards and the composer (`buildTasksCard`/
`renderTasks` in app.js, `.taskscard`, amber accent — the mirror's own
task-line colour). Each row is `mark #id subject`: pending `○` (dim
mark), in_progress `▸` (amber mark, bold subject, plus the task's
`activeForm` in amber italic — the same label the TUI spinner shows),
completed `✓` (green mark, the whole row dimmed and the subject
**struck through**). A `⛓ #n` chip marks a task blocked on open
dependencies (`blockedBy`), the header counts `done/total`, and the
full `description` rides each row's hover title. The card hides when
the session has no tasks. Read-only — unlike ask/plan there is no
modal to drive, so there is no POST endpoint.

**Where the data comes from (and why a stash, again).** Task state
DOES live on disk — `<CLAUDE_CONFIG_DIR|~/.claude>/tasks/session-<first
uuid segment of sid>/<id>.json`, one `{id, subject, description,
activeForm, status, blocks, blockedBy}` record per task (measured
2026-07-18) — but Claude Code **deletes the files at session end**, so
reading the dir directly would blank every parked session (and the
dashboard would re-encode a Claude-internal path format). Instead
`plugins/claude_code/task_fmt.py` re-reads the dir on every
task-touching hook and snapshots the full id-sorted list into the state
DB's `tasks` kv, audited as a `tasks` state_files write. The triggers:
`TaskCreated`/`TaskCompleted` (the dedicated events, which also paint
the mirror one-liners) **plus `PostToolUse(+Failure)` of
`TaskCreate|TaskUpdate`** — a status flip (pending→in_progress,
→completed, →deleted) fires NO dedicated hook (measured 2026-07-18), so
the tool event is its only refresh signal. The dir at op time is
authoritative; there is deliberately no clear-on-empty guard (no hook
fires at session-end cleanup, so an empty read always means a truly
empty list). The usual guards apply: `agent_id` events are ignored
(main-session-only), and an unhosted session (no state DB) stashes
nothing — kv_set would CREATE the DB whose existence is the
session-alive signal (this previously bit: the old task_fmt's
unconditional `O.emit` created ghost DBs for headless team sessions).

`session_payload` carries the list as `data["tasks"]` — deliberately
**NOT live-gated** (unlike `ask`/`plan`): the kv survives park, so a
parked session still shows its final task list. The per-session SSE
diff-emits a `tasks` event on the slow cadence (tasks change per-hook,
not per-keystroke; nobody is blocked waiting on this card).

## Web goal (the pinned goal card)

Claude Code's `/goal <condition>` built-in (2.1.139+) puts the session
into an **autonomous mode**: Claude works across turns toward a stated
completion condition until an internal checker confirms it, at which
point the goal auto-clears. The dashboard mirrors the active goal as a
**goal card pinned at the very top of the mirror tab — above the tasks
card** (`buildGoalCard`/`renderGoal` in app.js, `.goalcard`): a 🎯 mark,
the condition text, and an amber **active** state while working; once the
checker reports the condition met the card flips to a green ✓ **achieved**
before it clears. The card hides when there is no active goal. Read-only
— the goal is set/cleared at the terminal (or by typing `/goal` in the
composer, now in the "/" menu), never from this card, so there is no POST
endpoint.

**Detection — read-side, no hook (why it's a transcript scan, not a
stash).** Unlike tasks, **no hook fires** for `/goal` — not on set, met,
or clear (there is no `Goal*` hook event). But the goal is **persisted in
the session transcript**: setting one writes an
`{"type":"attachment","attachment":{"type":"goal_status","condition":…,
"met":…,"sentinel":…}}` line (captured live from a real run, 2.1.217), the
checker re-stamps a fresh `goal_status` each turn, and Claude Code itself
restores the goal from the transcript on resume (`restoreGoalFromTranscript`).
So detection is a **read-side tail scan**, exactly like context saturation
(`transcript.context_probe`): `transcript.goal_probe(path)` reads the same
bounded `CTX_TAIL_B` window and takes the most-recent-record-wins — a
`goal_status` attachment gives `{condition, met}` (an empty condition = a
cleared goal → `None`), and a bare `/goal clear`|`off` command that
post-dates the last attachment ends it (`/goal status` is a query and is
skipped). It surfaces through the `plugins.goal()` fan-out (path-keyed,
sibling of `plugins.context()`).

`session_payload` carries it as `data["goal"]` behind the `session_goal`
`(path, size)` memo (sibling of `session_ctx`), deliberately **NOT
live-gated**: the transcript persists past park (unlike the task files),
so a parked session still shows its final/achieved goal. The per-session
SSE diff-emits a `goal` event on the slow cadence (a goal changes
per-turn, not per-keystroke; nobody is blocked waiting on this card).

**No audit rows (and why that's correct).** This is a pure read-side
transcript derivation — no hook, no detached process, no state/marker
file, no tab-state input — identical in kind to the context-saturation
probe, which also adds no audit rows. The source is already recorded (the
transcript path in the audit `sessions` row), so "why did the goal card
show X" is answerable from the transcript itself. One caveat: like
`context_probe`, the scan only sees the transcript **tail** — an active
goal stays in-window because the checker re-stamps it each turn, but a
goal that goes many turns without a re-stamp could scroll out and the card
would blank (the goal is still active in the TUI; only the mirror loses
sight of it).

## Web dictation (mic → Deepgram → the textarea, live)

A mic button on the **composer** and on the **new-session form's first-prompt
box** (`dictation(ta)` in app.js — one controller per textarea, the same
helper both sites; `.micbtn`, a three-state story in the tab-colour
vocabulary: grey idle → pulsing `--exec` blue while CONNECTING (mic
permission + token mint run CONCURRENTLY — `Promise.allSettled`, so a
granted-after-failure stream is still released and the mic indicator can't
stick on — then the ws handshake + worklet load) → pulsing `--ask` red while
listening; the blue phase is why the delay between click and red reads as
startup, not deadness). Click,
speak, and the transcript splices into the textarea **as you speak** —
interim results land ~100ms behind the voice and are REPLACED in place as
Deepgram firms them up, so the box always shows the current best guess and
you visually validate before sending. On a PARKED session the mic works the
same and everything dictated is a free draft — only the composer's "resume
& send" button wakes anything (*Resume & send* above); on a headless-live
session the button is honestly `disabled`, matching its dead composer.
Engine: **Deepgram Nova-3 streaming**
(`interim_results=true`, `smart_format`), chosen over the free Web Speech API
for accuracy and for **keyterm prompting** — repo jargon ("scorebar",
"tailer") the generic engines mangle.

**The token-grant architecture.** The server's whole role is one trade —
it never sees audio:

- `GET /api/dictate` → `{available}`: a bare key-file probe
  (`dashboard/dictate.py`, the one owner of the dictation vocabulary —
  file locations, grant call, listen-URL assembly). The page probes once
  and renders mic buttons iff true: no key = feature invisible, never a
  dead button.
- `POST /api/dictate/token` (behind `_post_guard` like every control-plane
  write, so `CLAUDE_DASH_READONLY` kills it exactly like the composer it
  feeds) → reads `~/.config/deepgram/api-key`
  (`CLAUDE_DICTATE_KEY_FILE` overrides), trades it via Deepgram's
  `POST /v1/auth/grant` for a **~30s single-purpose JWT**, and returns
  `{token, expires_in, ws_url}` — the listen URL fully assembled
  server-side (model, formatting, one `keyterm=` per vocabulary term).
  The client contributes ONLY its AudioContext sample rate plus an
  optional `cwd` (the composer sends its session's, the new-session form
  its typed dir) that keys the project vocabulary layer. The long-lived
  key never leaves the server process — not in a response, an audit row,
  or an error detail.

**The vocabulary is LAYERED, project-first** (`dictate.keyterms(cwd)`):
each applicable `.claude/` dir's **`deepgram-keyterms`** file —
nearest-first via the same `plugins.config_dirs` walk the "/" menu's
command discovery rides (`model.claude_dirs(env_pin=False)`, the walk's
one owner behind a registry-root door), so a nested worktree inherits its
project's vocabulary and a project file can be COMMITTED and shared —
then the user-global `~/.config/deepgram/keyterms`
(`CLAUDE_DICTATE_KEYTERMS_FILE` overrides). Every file parses the same
(one term per line, `#`-comments and blanks dropped), first occurrence
wins the dedup, and the `KEYTERMS_MAX` cap evicts the FARTHEST layer
first — keyterm biasing degrades with bloat, so when something must fall
off it is never the nearest project's terms. A bogus/missing `cwd`
degrades to global-only (the `/api/commands` contract — arbitrary
sessions' dirs come and go, never an error), and every file is re-read at
mint time, so a vocabulary edit lands on the next mic press.
- The **browser then connects `wss://api.deepgram.com/v1/listen` directly**,
  authenticating with the `['bearer', <jwt>]` WebSocket subprotocol (browsers
  can't set WS headers; this is Deepgram's documented browser pattern). The
  JWT only needs to outlive the handshake — an open session runs past its
  expiry.

Why direct-to-Deepgram instead of proxying: the stdlib
`ThreadingHTTPServer` speaks no WebSocket in either direction — proxying
means hand-rolling RFC 6455 both ways — and a server whose identity is
"read-only over session state" has no business buffering live audio.
Rejected: key-in-the-page (a localhost page is still a page; the key is
long-lived), and Web Speech API (no vocabulary biasing at all, Chrome-only
quality, nothing to keyterm).

**Audio: AudioWorklet → linear16 PCM, not MediaRecorder.** MediaRecorder
was rejected because **iPad Safari emits mp4/AAC, which Deepgram streaming
refuses** — and the iPad (docs/remote.md) is a first-class client. Instead a
~15-line worklet converts Float32→Int16 at the AudioContext's **native**
rate (declared in `sample_rate=` — no resampling code), batched to
4096-sample chunks (~85ms @48k) so the socket sees a sane message rate.
Continuous PCM means silence is still data, so Deepgram's no-audio timeout
never fires and there is no KeepAlive plumbing. Secure-context note: mic
APIs work on `http://127.0.0.1` (localhost is a secure context) and on the
HTTPS remote origin — a plain-http non-localhost origin would refuse
`getUserMedia`, but none exists (the bind never leaves 127.0.0.1).

**The splice (live visual validation).** At mic-start the textarea splits at
the caret into `prefix`/`suffix`; dictated text grows between them as
`committed` (finalized) + `interim` (volatile). Every partial repaints
`prefix+committed+interim+suffix` with the caret pinned after the interim,
and dispatches a real `input` event so `autoGrow` &co stay honest. Typing
mid-dictation **re-anchors**: the shown interim becomes plain text where it
stands, the next `is_final` (which would repeat it) is dropped, and dictation
continues from the new caret. Stop paths: the button, Esc, send/launch (the
visible — validated — text is what sends; a `lastPainted` guard stops the
async close from resurrecting text into a box the post-send clear already
emptied), and view/modal teardown (`leaveSession`/`closeNewSession` →
`stopDictation()` — a mic must never outlive the box it feeds; one mic
page-wide). Stopping sends Deepgram's `{"type":"CloseStream"}` so the last
partial flushes as a final, with a 2s failsafe close, then releases the
tracks (the tab's mic indicator must go off). Deliberately NO auto-stop on
silence — an open mic costs $0.0077/min and auto-stop mid-thought is the
annoying failure mode.

**Audit.** Every mint attempt is a `web-dictate` `state_files` row (no sid —
the new-session form dictates too), `{ok, rate, cwd, keyterms}` on success
(`cwd` + the term count answer "why didn't my project word bias" — an empty
`cwd` there means the sent directory failed the isdir guard),
`{ok: false, why: bad-rate|no-key|grant}` on failure, grant failures also an
`A.error("dashboard dictate (grant failed)")`. "Mic button missing or dead"
triages as: `/api/dictate` says available? → `web-dictate` rows → dictate
errors (the audit-debug skill's bug shape).

## Accounts & usage

The machine juggles several Claude subscriptions through the `claude-subscription`
wrapper (github.com/leegunwoo98/claude-code-account-switcher; the user's `c1`/`c2`
zsh aliases). Each `claude-subscription <slug>` exports `CLAUDE_SUBSCRIPTION_SLUG`
+ `CLAUDE_SUBSCRIPTION_LABEL` and injects that account's keychain token; the plain
`claude` alias is the default account (empty slug). Three surfaces:

**Launch under an account.** The new-session form's account picker is
`plugins.accounts()` (`plugins/claude_code/account.registry` — one entry per
`accounts.tsv` row). There is **no "default" option**: the plain-`claude` login
resolves to whichever account is interactively signed in — a duplicate of one of
the listed accounts — so offering it just yields an unlabeled session that's
really c1 or c2. The chosen slug is resolved server-side to a registry-vetted
command word (`plugins.account_alias`, `account.alias_for`) — the slug, which IS
the `c1`/`c2` alias — and that word replaces `claude` in `launch_argv`'s FIXED
command string. Because it comes only from the registry (never raw client text),
the injection story is unchanged; an unknown slug is a `400`. An *absent* account
field still falls back to plain `claude` (so a machine with no switcher, whose
registry is empty and whose picker row hides, still launches). The account word
rides the same `$SHELL -lic '<word> "$@"'` login shell, so the alias resolves
exactly as typing it in a fresh tab.

### Default account

The picker's **default selection burns PERISHABLE weekly quota first** — the
scheduling objective is *(b) maximise total work extracted across accounts per
week*, not *never hit a wall this session*. Unused weekly (`seven_day`) quota is
wiped at the window's reset whether you spend it or not, so quota that resets
SOON is perishable: leaving it idle wastes it, while an account whose 7d window
resets days out can be conserved (its headroom survives to next week). So the
form preselects the account with the highest **perishability** —
`remaining% / hours-to-7d-reset`: quota still left AND a near reset scores high
(spend it now), the same headroom with a distant reset scores low (save it). The
higher per-session wall risk this accepts is by design: the **automigrate safety
net** (docs/relimit.md) catches a session that then runs into a limit and moves
it to another account, so aggressive burning is safe.

Two server-computed signals ride each `/api/accounts` row (single-owner in
`core/sessionapi`, `app.js` only reads them):

- **`sched_score`** — the perishability above (`sessionapi.sched_score`). A
  rolled-over / unknown-reset 7d window, or no snapshot at all, counts as full
  quota over a full-week horizon: a low BASELINE score, never a spike (so a
  stale/quiet account doesn't get falsely prioritised); an exhausted window
  (0 remaining) scores 0. A reset only seconds away is floored (`SCHED_MIN_HORIZON_H`)
  so it can't produce an unbounded score.
- **`sched_ok`** — a 5h **session-safety gate** (`sessionapi.sched_ok`):
  effective `five_hour` used below `SCHED_5H_GATE` (90%). The picker ranks by
  `sched_score` only among accounts that clear this gate, so it won't open a
  session onto an account already at its 5h wall. (Effective `five_hour` — the
  `five_hour_eff` field, `sessionapi.effective_five_hour` — still means a
  rolled-over or reset-passed 5h window reads 0, and a no-snapshot account reads
  0.) Gate empties the pool → fall back to any open account; all blocked → any.

Only the account-wide `seven_day` window feeds `sched_score`; per-MODEL weekly
caps still HARD-block via `limit_hit` (below), but a soft per-model perishability
tie-break is a deliberate non-goal for now (the tokenless snapshot the migration
picker shares carries no per-model window, and the user's own framing was the
account-wide 7d reset). The migration target picker (`account.pick_target`,
docs/relimit.md) is UNCHANGED — it still picks the least-used-5h refuge; it is
the safety net, not the scheduler, and runs on tokenless snapshots with no 7d
reset to reason about. The suggestion is recomputed when the fresh
`/api/accounts` fetch supersedes the cached list, but a manual pick (the
dropdown's `onpick` hook) always wins and is never overridden. On top of the
perishability rank, the auto-pick **skips any account whose active `limit_hit`
applies to the launch**:
an account-wide stamp always applies; a model-scoped one (`limit_hit.model` —
e.g. a Fable-only limit, docs/relimit.md *Limit scope*) only when that model
is the one selected in the form, which is why flipping the model picker
re-runs the account choice (`model.onpick → autoAcct`; the model picker is
built before the account block for exactly this). The scope match lives
client-side deliberately — the chosen model exists only in the form; the
stamp's `model` field itself is server-parsed (`relimit.limit_model`), never
re-derived from the message. Every account blocked → plain lowest-usage
fallback, and each blocked option carries a `· <model> limit hit` marker in
its dropdown label.

**Which account a chat runs under** is stamped into the session's state DB at
SessionStart (`split.cmd_open` → `state.kv_set("account", account.current())`,
read from the env contract — no token touched) and shown in the session header
(`◈ c2 · claude-01`) and the terminal scoreboard's id row.

**Usage limits (5h / 7d).** Claude Code exposes per-session rate-limit data to
exactly ONE place — the **status-line command's stdin** JSON
(`rate_limits.<window>.{used_percentage,resets_at}`), after each API
response. The capture is GENERIC over windows (`statusline.parse_usage`):
every `rate_limits` entry with a parseable used-% lands in the `usage` kv as
`<window>` + `<window>_reset` — the account-wide `five_hour`/`seven_day` pair
always first, then any model-scoped window sorted by key. As of CLI 2.1.215
only the account-wide pair exists here (verified against live payloads
2026-07-19: the `/usage` screen's per-model weekly bar — e.g. the Fable
cap — has NO statusline counterpart); if Claude Code ever starts
reporting one in the status line (say `seven_day_fable`), it flows through the
kv, the aggregation, and the strip's bars with no code change — but until then
the per-model bars come from the OAuth endpoint instead (*Per-model usage
bars* below). Rate-limit data is NOT in any hook payload, the transcript, or
OTEL (all checked). So the account-wide number is captured by
**wrapping the status line**: `bin/claude-statusline.py`
(`plugins/claude_code/statusline`) becomes `settings.json`'s
`statusLine.command`, with the user's real status-line command (their HUD) as its
argv. It reads the stdin once, stashes `usage` + `account` into the session state
DB (guarded on the DB already existing — never creates it), then runs the HUD with
that same stdin and forwards its output verbatim. **The capture is tokenless and
per-account by construction** — the number came from that session's own token, no
scope, no API call (this is exactly how the switcher's own usage cache is
populated). The shim must never break the status line: every capture failure is
swallowed and the HUD still runs; a delegate crash returns 0. The `settings.json`
edit is one prepended path (backed up to `settings.json.bak-kitty-statusline`);
to revert, drop the shim prefix.

Usage shows in three places: the session header (next to the account chip), the
terminal scoreboard (id row), and a **strip across the top of every dashboard
page** (`#accounts`) — `plugins.accounts()` with usage aggregated per slug (the
freshest snapshot across that account's sessions —
`core/sessionapi.account_usage`, shared with the rate-limit migration's target
picker), polled slowly and hidden until some account has usage. The pill
renders **one bar per captured window** (app.js `usageWindows`/`windowLabel`
— `five_hour` → "5h", `seven_day` → "7d", a future `seven_day_fable` → "7d
fable"), in the served order; the new-session picker's option text joins the
same windows. The served
`usage` is the **effective** snapshot (`sessionapi.effective_usage`): any
window — the 5h/7d pair or a model-scoped one (`sessionapi.usage_windows`,
span fallback `window_span`) — whose reset time has passed (or, reset
unknown, whose snapshot is
older than the window) rolled over — its used% is zeroed and its reset
DROPPED before serving. Without that, an account with no recent session keeps
its last snapshot forever, and app.js's `resetAgo()` renders any past epoch
as `resets now` — a pill that read "5h 29% · resets now" for hours was the
symptom (the client must not fix this itself: the rolled-over arithmetic is
single-owner, server-side). The `web-launch` audit row records the chosen
`account`.

**Per-model usage bars (the OAuth `/usage` fetch).** The `/usage` screen's
third bar — a **weekly per-MODEL cap** (e.g. "Fable") — is exposed by no
tokenless channel; it lives only behind the undocumented OAuth endpoint
`GET https://api.anthropic.com/api/oauth/usage`, which requires a
`user:profile`-scoped token. The switcher's `setup-token`-minted account tokens
are **inference-only** (no `user:profile` → 403; documented limitation of
github `leegunwoo98/claude-code-account-switcher`), so this is the ONE number
baqylau cannot get tokenlessly. `plugins/claude_code/model_usage.py`
(`plugins.model_windows`) PIGGYBACKS on the full-scope OAuth logins Claude Code
stores in the macOS keychain (`Claude Code-credentials[-<hash>]`): read the
access token, refresh it when expired, call the endpoint, and shape each
`weekly_scoped` limit into the SAME `seven_day_<model>` window kv the strip
already paints — so no renderer change, the fable-ready generic pipeline just
lights up. The dashboard's `accounts_payload` MERGES these windows into each
account's `usage` before `effective_usage`; `five_hour_eff`/`limit_hit` stay on
the tokenless snapshot, and a missing/failed fetch simply omits the extra bars.
Design details (docs/relimit.md borrows the same account vocabulary):

- **Account → slug mapping.** The endpoint identifies its account only by
  email, but the switcher slugs carry no email (setup-tokens can't read the
  profile). So the fetched account is matched to a slug by its account-wide
  **7d reset epoch** against each slug's freshest captured usage
  (`account_usage`); the **5h epoch is only a tie-breaker** when two accounts
  share a 7d boundary (that ambiguity is real — a single-signal match
  mis-mapped personal↔work once, 2026-07-19; an unbreakable tie refuses to
  guess). Requiring the 5h epoch to ALWAYS match was the original design and
  the reported 2026-07-20 bug: the captured 5h epoch rolls every 5 hours, so
  after any quiet spell (dashboard just started, no session running under that
  account) the stale 5h reset failed the match and the Fable bar silently
  vanished until a status line re-captured — which is also why a rarely-used
  account showed no bar at all. The 7d epoch is stable for the whole week, so
  one status-line capture per account per week now suffices. No match ⇒ the
  bar just doesn't attach (audited once per process,
  `model_usage._slug_for`).
- **Refresh ownership** (avoids two writers fighting over one rotating refresh
  token). The actively-used login (plain `claude` — e.g. the personal account)
  is kept fresh by Claude Code itself, so baqylau only READS its keychain token.
  A **switcher-only** account's OAuth login is never exercised by Claude Code
  (it runs on the setup-token), so its access token expires in ~8h with nobody
  to refresh it — baqylau becomes its SOLE refresher (`grant_type=refresh_token`
  at `platform.claude.com/v1/oauth/token`), persisting the rotation in its OWN
  keychain entry (`baqylau-model-usage: <service>`), never overwriting Claude
  Code's copy. The per-cycle pick is just "whichever copy is fresher", so the
  personal path never self-refreshes and the work path does. **Coverage is
  therefore limited to accounts with a scoped keychain login** — a
  switcher-only account gets a bar only after one interactive
  `claude auth login` (its refresh token then lets baqylau keep it alive).
- **Tokenless-departure discipline.** This is the sole API call in a
  tokenless-by-design tool, so it is gated (`CLAUDE_MODEL_USAGE=0` disables),
  macOS-only, TTL-cached (60s — the keychain/network work runs at most once a
  minute however often the page polls), and **fail-silent + audited-once**: a
  failure degrades to "no model windows", and its `errors` row is written at
  most once per process (a 60s poll against a down endpoint would otherwise
  trickle a row a minute, which errwatch surfaces as a `⚠` in every session's
  mirror). But an **EXPECTED transient is not audited at all** — a machine
  offline / unreachable endpoint (`urllib.error.URLError`, incl. a wrapped DNS
  `gaierror`) or a rotated/stale-token refresh rejection (its `HTTPError` 4xx
  subclass) is the environmental "endpoint or credential unavailable" outcome
  this optional read is DESIGNED to degrade on, so `_expected_net_error` skips
  the `_audit_once` and it never lights the `⚠` warning light; only a genuinely
  unexpected exception (a `KeyError`/JSON-shape change in our own handling)
  audits, keeping the light meaningful (global-errors skill, 2026-07-22). The
  number is live from an undocumented endpoint — not reconstructible from the
  DB, unlike the tokenless snapshot.

**The "limit hit" pill.** The frozen usage bar UNDERSTATES a blocked account:
Claude Code's status line reports `used_percentage` from the API's utilization
headers, and once requests are REJECTED no update ever reaches 100 — the bar
sits at ~95% at exactly the moment the account stops working (measured
2026-07-19: the status line stamped 95% thirteen seconds AFTER the "You've hit
your session limit" turn; the block signal travels in a separate
`anthropic-ratelimit-unified-status` header the status-line JSON never carries).
So the account pill keys the truth off the EVENT instead: the rate-limit
StopFailure's `limit-hit` kv stamp (docs/relimit.md), served per account as
`limit_hit` while still active (`sessionapi.limit_hit_active` — reset not yet
passed, or, when the reset is unknown, younger than the limit's OWN window: 5h
for an account-wide session limit, one WEEK for a model-scoped cap, which is a
weekly per-model quota) and rendered as a red `limit hit` chip + its reset
countdown next to the usage bars. The stamp's `resets_at` is the real reset
epoch whenever it can be known — the usage snapshot's `five_hour_reset` for an
account-wide limit (or the wall-clock reset named in the message itself,
`relimit.limit_reset`), and a per-model reset for a model-scoped one — but the
snapshot carries NO per-model window today (statusline.parse_usage), so a Fable
cap's reset is unknown and rides the weekly fallback. Sourcing that reset from
`five_hour_reset` (which rolls in hours) was the reported bug where the Fable
chip cleared ~5h in while the weekly limit still bit, reappearing only when a
new chat re-hit it (docs/relimit.md). The weekly fallback OVERSTATES in the
other direction — Anthropic sometimes resets limits mid-week — so
`accounts_payload` lets the LIVE per-model window override a model-scoped
stamp: when the fetched `seven_day_<model>` for that very model reads below
100%, the cap has demonstrably cleared and the pill drops (2026-07-20).
Dashboard-presentation only — core (the relimit target picker) stays tokenless
and keeps the conservative week-long fallback. A
MODEL-scoped stamp (`limit_hit.model`, parsed at stamp time by
`relimit.limit_model` — "You've reached your Fable 5 limit" → `fable`,
docs/relimit.md *Limit scope*) renders as `fable limit hit` (app.js
`limitLabel`): only that model is blocked on the account, and the bare label
overstated it. The stamp is filed under **its own `slug` field**, not the
session's `account` kv:
after a rate-limit migration the adopted session runs under the NEW account
while the stamp in the same (renamed) state DB still describes the old one —
grouping by the session's account put c2's `limit hit` chip on c1's pill and
left the actually-blocked account looking clean (and, worse, let
`account.pick_target` — same aggregation — consider migrating back onto it).

## Context saturation (the ctx bars)

How full each context window is — a filled progress bar in the account-limit
strip's visual language (`ctxBar` in app.js, the `ubar`'s bigger sibling:
`ctx [██████░░░░] 42% · 84k / 200k`), always on its OWN row: under each
session card's stats on the main page, a dedicated row in the session header
(`.big`, live via the `ctx` SSE event), and under every agent card's meta
(rail + agents tab, riding the `agents` event). Accent fill normally, amber at
70%, red at 90% (`.cbar.warn`/`.cbar.hot`).

**One data path, no new store.** The transcript IS the record of occupancy: the
LAST assistant record's usage is exactly what the model saw on the most recent
turn — fresh + cache-write + cache-read input tokens (`model.context_used`, the
one owner of that arithmetic; output tokens are what came back, not context) —
and that record's `model` id sizes the window (`model.context_window`, same
known-1M resolution the substream footer uses). `transcript.context_probe`
reads a bounded tail (`CTX_TAIL_B`, no full-read — a final record buried deeper
than the window just yields no chip), skipping `isSidechain` records for a MAIN
transcript (`main=True` — an inline agent turn's smaller usage would paint a
phantom shrink; an agent's own transcript IS its sidechain turns, so agent
callers keep the default). Exposed as the path-keyed `plugins.context()`
fan-out (like `session_title` — the dashboard's rows already hold every
transcript path: the sessions row's `transcript_path`, the agent row's streams
`src_path`); a codex rollout finds no provider and shows no chip. The server
caches by `(path, size)` (`session_ctx`, the `_TITLES` pattern) so the polls
re-probe only when a transcript grows.

**Why not the state DB / OTEL / the status line:** the scoreboard's `txlast`
carry froze when accounting went OTEL-authoritative (the fold now runs only as
a SessionEnd fallback), OTEL datapoints are per-session sums with no per-request
grouping (occupancy is a *last-request* fact, not a total), and the status-line
stdin carries rate limits, not context. The transcript tail is live, survives
parking (transcripts persist), and covers agents uniformly.

## Agent model·effort (the card's op-tag echo)

Every agent card's meta row carries a `opus-4.8·high` chip (`.amodel`, between
the status chip and the `N events` count) — the web echo of the terminal
mirror's per-op model tag (`substream.op_tag`), so a glance at the rail tells
you *which* model (and reasoning effort) each subagent is burning.

**Free off the ctx probe, no new store.** The model needs no extra read: the
`context_probe` that `agents_ctx` already ran stamps each agent's raw model id
onto `ctx["model"]` (the last assistant turn's `model`), so `agents_model_effort`
just reads that back and shortens it (`model.short_model` — `claude-opus-4-8` →
`opus-4.8`). Effort mirrors the substream's `EFFORT_CFG or model_default_effort()`
resolution: the session's SAVED effort (the same `plugins.effort_default` value
the quick-button shows, resolved once up front and reused for both), else the
running model's default (`model.model_default_effort` — `high` for opus-4.8,
`""` for a model without adaptive reasoning, which then shows model-only). The
one divergence from the terminal tag: a per-agent frontmatter/env effort override
(the substream's higher-precedence source) isn't readable out-of-process here, so
an agent that overrides effort shows the session/default value. Agents with no
ctx yet (husks, not-yet-started) carry no model chip — exactly as their ctx bar
is absent. Rides the same `agents` SSE event, so it appears live and survives
parking.

## Git chips (branch + worktree)

Which checkout a session runs in — `⎇ branch` (accent, a trailing `*` when
the checkout has uncommitted changes) plus `⋔ <name>` (amber)
when the cwd is a linked worktree — on each session card's stats row and the
session header's title line (live via the `git` SSE event on the slow cadence).
`git_info(cwd)` in server.py reads the `.git` files directly, **never a `git`
subprocess for branch/worktree** (this runs per row per poll tick): walk up from the cwd to the
first `.git`; a directory is a main checkout, a file is a linked worktree
(`gitdir: .../worktrees/<name>` — the name is the `⋔` chip) or a submodule
(no `worktrees` segment → no name). A linked worktree's payload also carries
`root` — the MAIN checkout that owns it (`gitdir` is
`<root>/.git/worktrees/<name>`; `null` for a main checkout): that is what
`group_dir` resolves a worktree session to — the list page's grouping key
(*Grouping and titles* below) — and the toast `project` name, so a worktree
session files under its project. HEAD at the resolved
gitdir gives the
branch (`ref: refs/heads/<b>`, or a 7-char sha when detached). The ancestor
walk + gitdir indirection is cached per cwd (in the `_GIT` `BoundedLRU`, so a
days-long server can't accumulate one entry per cwd ever seen — see *Poll-path
reads are memoized*); HEAD itself is re-read on
every call (one tiny file), so a branch switch shows on the next poll and a
removed worktree drops the chip. A cwd outside any checkout carries
`git: null` and no chip renders.

**The dirty `*`** follows the status-line convention (claude-hud, which the
statusline shim wraps): dirty = `git -c core.quotePath=false
--no-optional-locks status --porcelain` printing *anything* — staged,
unstaged, and untracked all count. Worktree/index dirtiness is NOT derivable
from `.git` metadata (detecting it is exactly the index stat-cache walk `git
status` performs), so this is the ONE sanctioned `git` subprocess in the
dashboard (`_git_dirty`): TTL-cached per cwd (`DIRTY_TTL_S` = 10s — bounds it
to one probe per checkout per TTL, not per row per tick; a flip shows within
TTL + one slow tick), `DIRTY_TIMEOUT_S` = 1s so a huge or network-mounted
repo can't stall a poll tick, `--no-optional-locks` so the read-only observer
never touches the index. The payload's `dirty` is three-valued: true/false
from a successful probe, `null` = unknown (no git binary, timeout, or a
broken checkout) — which renders as no marker, same as clean; failures are
cached under the same TTL so a repo that can't answer isn't re-probed every
tick.

## Grouping and titles

The sessions view groups by PROJECT directory — the server's `group_dir`
(app.js keys on it; `group_dir || cwd` for legacy/parked rows that predate it).
`group_dir` is the session's FROZEN original cwd resolved to its linked-worktree
owner: `_group_dir(start_cwd)` in server.py walks the `.git` files (via
`_git_resolve`, never the dirty subprocess) exactly as `git_info` does, but
returns the worktree owner (`root`) or the dir itself. Two consequences:

- A linked-worktree session files under the main checkout that owns it, not its
  worktree dir, so N agents fanned out over `.claude/worktrees/*` of one repo
  stay ONE group (the per-card `⋔` chip is what tells them apart, and the group
  header's "+" launches new sessions at the main checkout).
- Grouping keys off `start_cwd` — the frozen ORIGINAL cwd (audit `sessions`
  column, set once at SessionStart, NEVER re-stamped; added by
  `audit._migrate`) — rather than the live `cwd`, so an agent's mid-session
  `cd`, which `session_paths` folds into the live `cwd` on every event, can NOT
  move a card between groups. The card still SHOWS its live `cwd`; only the
  group key is pinned. *Why not the live cwd:* a `cd` into a subdir, `/tmp`, or
  another repo silently re-aggregated the whole card mid-session — the reported
  bug this pinning fixes. `start_cwd` is server-internal (it only feeds
  `group_dir`) and is stripped from the wire by `_wire_row`.

A parked session whose worktree was since REMOVED degrades to its own
start-cwd-keyed group (`_git_resolve` returns null once the `.git` file is
gone — the branch chip drops the same way). Groups are ordered by their newest session's `started_at`
(app.js `orderKey`), NOT `last_active`: started_at is fixed for the session's
whole life, so the order only moves when a session starts or resumes
somewhere. Sorting groups on `last_active` (transcript mtime, which grows on
every stream write) made two concurrently-live projects leapfrog each other
every SSE tick — and group order is part of `listShape`, so each flip forced
a full list rebuild and the page visibly jolted. The directory name lives on
the group header, so the card itself is titled by the SESSION's name. That name comes
from `plugins.session_title(transcript_path)` — a path-keyed fan-out (the
list view already holds every row's path; 50 sid-keyed `session_row()`
resolutions per poll would be waste). The claude_code provider
(`transcript.session_title`) prefers the transcript's NAMING records
(docs/session-naming-findings.md) — the last `agent-name` (a `/rename` custom
name, never clobbered by auto titles), else the last `ai-title`, the
auto-generated title Claude Code's OSC tab title mirrors — so the dashboard
card matches the kitty tab. Those are re-emitted every few turns and sit
within lines of EOF, so they're scanned from a bounded `TITLE_TAIL_B` tail
window (the one accepted gap: a mid-file `agent-name` in a >64KB transcript
with no later naming record). When neither exists it falls back to the last
`summary` record in the head window (Claude Code prepends them on resume),
else the first line of the first REAL user prompt, which is effectively what
the `claude --resume` picker shows (`history.jsonl` `display`). `isMeta` rows
and `<command-*>`/`<local-command-*>` wrappers are plumbing, never titles. The
server caches titles by `(path, size)` — a title can only change when the
transcript grows. Since the web rename (*Web rename* above) the dashboard
also WRITES the `agent-name` channel (`plugins.set_session_title` →
`transcript.set_session_title`, the same module that parses it), and the
rename lands at EOF so the tail window always sees it initially. Agent cards
follow the same rule: the Task description
(`desc` from the state DB's agents table) IS the agent's name; the raw
`agent_id` drops to the subtitle.

**The `transcript_path` the title keys off must stay fresh.** Claude Code
RELOCATES a session's transcript when its cwd moves to another project
directory (measured 2026-07-18 via `EnterWorktree`: the file moves to the
worktree cwd's `projects/` slug dir, and every later hook payload carries the
new path). The audit `sessions` row is written at SessionStart, so without a
refresh it points at a dead file for the rest of the session — `session_title`
swallows the `getsize` OSError and the card/header silently show NO name (the
e7192407 shape), and the ctx probe, git chips (cwd), web rename, and rewind's
transcript checks break the same way. The fix lives at the WRITE side, not
here: the hook dispatcher calls `A.session_paths(payload)` on every event
(docs/wiring.md), which folds a changed cwd/`project_slug`/`transcript_path`
back into the sessions row and audits the move as a `session-paths`
`state_files` row. A read-time fallback in the dashboard was rejected: it
would fix the title while leaving every other consumer of the row (sessionapi,
the CLI, future tooling) stale.

**The time chip is recency, not age.** Every time-flavored thing on the list
— the card's "2m ago" chip, the 3d archive boundary, the resume
dropdown's "· 2m ago" — keys off `last_active` (`sessions_payload` →
`_last_active`), not `started_at` (GROUP order is the one deliberate
exception — `started_at`, for stability; the grouping section above): the transcript's mtime (the file grows on
every turn — the same activity signal interrupt-watch and escape-recheck
trust), else the audit `ended_at`, else the state DB's mtime (the audit-less
minimal parked rows carry no transcript path), else `started_at`. **Why not
`started_at` directly** (the original design): an unlabeled "1h ago" on a
session card universally reads as *last activity*, so a live session an hour
into its work looked stale while actively streaming — and a week-old session
touched yesterday got folded into the archive. **Why not the audit
`hook_events MAX(ts)`:** a per-row query against the big audit DB per tick
vs one `stat` on a path the row already carries — and the audit can be
disabled. `last_active` stays IN the SSE diff key (unlike `paused`): it
moves only when the transcript actually grows, which is genuine news and
arrives alongside stats changes anyway. Known wrinkle, accepted: a web
rename appends a naming record, so it bumps recency — it *is* user activity.
Agent cards keep `ago(started_at)` deliberately — a *running* agent's age is
the meaningful number, and it becomes a `dur()` once it ends.

## Hidden directories (declutter the list)

The `✕` on a group header hides that directory from the list — for when a
crowded main page has projects you're not looking at. It is **non-destructive**:
nothing is closed or removed, the sessions keep running, their tab colours and
red/green toasts still fire (the notify watcher is independent of what the list
renders); the group just disappears from view. It **re-appears on its own the
moment a NEW session starts there** — no manual "unhide" — whether that session
is launched from the dashboard's new-session prompt or from a terminal.

A directory can only be hidden while it has **no active (live) session** — you
can't declutter away a project you're actively working in. The `✕` is **disabled**
(dimmed, with a "can't hide — N active session(s) here" tooltip) whenever the
group has a live card, and the server **`POST /api/dirs/hide` 409s** on the same
condition (`dir_live_sessions` — the authoritative guard, so a stale page that
still shows an enabled ✕ can't slip a hide through). A group with only *parked* /
*archived* sessions hides normally.

The mechanism is a single stored timestamp per directory. `POST /api/dirs/hide
{cwd}` stamps `time.time()` into `{group_key: hidden_at}` (`prefs.hide_dir`,
under the `hidden-dirs` key of the durable global prefs store — the same
`dashboard/prefs.py` kv DB that holds the new-session prefs, so a hide survives a
reboot and is shared across every browser pointing at this dashboard). `GET
/api/dirs/hidden` serves the map, which the page seeds `S.hidden` from on load
(the SSE `sessions` snapshot carries the session ROWS, not this pref — and only
the browser that clicks `✕` mutates it, so no SSE push is needed). The re-appear
rule is **client-side** (`app.js` `dirHidden`): a group stays hidden only while
NONE of its sessions is **live** and NONE has `started_at > hidden_at`. Because
every wire row already carries both `live` and `started_at` (audit `time.time()`
epoch, same clock as the hide stamp), the filter needs no server round-trip — a
fresh launch (or a resume, which re-stamps `started_at` and flips the row live)
whose row rides the next snapshot stops matching the hide predicate, and the
group returns. The `live` clause mirrors the hide guard from the *other* side: a
directory can't be hidden while it has a live session, and a hidden directory
that *gains* one (a parked session resumed) re-shows at once rather than waiting
on the `started_at` comparison. Re-hiding a re-appeared group just overwrites the
stamp with a newer `time.time()`, which is what re-hides it.

**Why a timestamp and not a boolean.** The stamp is what lets a hidden group
re-appear **automatically** — no explicit "unhide" step, no server-side event to
clear a flag. A plain boolean would need someone to reset it when a new session
started there; the timestamp instead is a passive client-side predicate
(`started_at > hidden_at`) re-evaluated on every render against rows the page
already has. Comparing against `started_at` specifically (not `last_active`) is
deliberate: only a genuinely *new* or *resumed* session (which re-stamps
`started_at`) re-shows the group — a parked session merely lingering, or its
transcript mtime ticking, does not. (Live sessions never reach this comparison —
they can't be hidden in the first place, and the `dirHidden` `live` short-circuit
keeps them visible.)

The **key is the list's group key** (`group_dir || cwd`) — the page posts the
group header's own `g.cwd`, which already holds that key, so the server stores it
verbatim (validated as a string under a length cap; it is only ever a kv key and
a client-side compare, never a filesystem path). The **`✕` hides ANY group,
including the projectless "no project" aggregate** (sessions with no cwd / git
root): its group key is the empty string, which `hideDir` and the server accept
as a first-class key — only a *missing / non-string* `cwd` is a bad request. The
one thing that group lacks is the `+` new-session button (there is no directory
to launch into). The **new-session picker is deliberately NOT filtered** by
`S.hidden` (its candidate list is the raw `S.sessions` cwds): a hidden directory
must stay reachable to launch into — and doing so is exactly what un-hides it.

Like every control-plane write the POST sits behind `_post_guard` (so
`CLAUDE_DASH_READONLY=1` disables it) and audits each hide as a `hide-dir`
`state_files` row (empty session log/path — it is dashboard-global, not
per-session, exactly like the `ns-prefs` write).

## The list renders once, then patches

Two layers used to make the sessions list rebuild its entire DOM every
second. The server pushed a fresh `sessions` snapshot on every 1s tick
because consecutive snapshots always *differed*: the scorebar accrues
`stats.paused` roughly once per second for every session sitting at a prompt
(its awaiting-pause accumulator), so an otherwise idle dashboard still
churned 84KB/s per client. And on every push the client's `renderList()`
wiped `$view` and rebuilt every group header, fold, and card — losing hover
state and burning layout for rows that hadn't changed.

Both halves are fixed independently:

- **Server — the paused-blind diff, per row (`_row_key`).** Each wire row's
  change-detection key is the row minus `stats.paused`. Only the DIFF is
  blind: a pushed row still carries the exact value. This is
  behavior-preserving for the card's ⏱ chip because that shows elapsed
  MINUS paused — constant while paused accrues — so the frozen card a
  suppressed push leaves behind already displays the right number. An idle
  dashboard now receives zero `sessions` events.
- **Server — wire deltas, not full resends.** Even with the paused-blind
  diff, an ACTIVE dashboard legitimately changes every tick, and the full
  snapshot re-sent each time measured 2.2MB/min per viewer — uncompressed,
  because SSE frames can't ride `_send`'s gzip, so a remote/tunnel list page
  paid all of it. The stream now sends the full `sessions` snapshot only on
  connect and when the sid set OR ORDER changes (a new/parked session — a
  delta can't express insertion), and a `sessions-delta`
  `{rows: [changed wire rows]}` otherwise, which the client merges in place
  by sid (`S.sessions[i] = row`) — safe precisely because membership/order
  moves always arrive as full snapshots. During activity that's a few
  hundred bytes per tick instead of ~77KB. Wire rows are also stripped of
  `transcript_path` and `log` (`_wire_row`, both here and on
  `/api/sessions`) — server-side paths the client never reads, ~20% of the
  snapshot. An open page running PRE-delta JS ignores `sessions-delta` and
  freezes until refresh — the `hello` BOOT_ID toast on reconnect covers the
  redeploy, the same accepted staleness as every earlier protocol change.
- **Client — shape-keyed patching.** `renderList()` computes `listShape()` —
  group order, which cards are VISIBLE (active + open folds), fold
  counts/open state — and while the shape matches the last full render (and
  that DOM is still mounted: a session view wipes `$view`, so a stale card
  map must never be patched blind), it only patches: `patchCards()` rebuilds
  the innards of exactly the cards whose row JSON changed, in place. The
  card element itself survives, so scroll, `:hover`, and the rest of the
  layout stay put. A live↔parked flip, a new session, or a fold toggle
  changes the shape and takes the full-rebuild path, which is also where the
  `S.cards`/`S.rowPrev` maps are rebuilt.
- **The clock still moves.** Relative "ago" labels and the 3d archived
  boundary depend on wall time, not data — with idle pushes suppressed
  nothing would ever recompute them, so a boot-registered timer forces one
  full render per `LIST_REFRESH_MS` (60s).

**Why not per-row delta events:** the snapshot is already small (≤50 rows),
the SSE only fires during genuine activity now, and a delta protocol would
need its own resync story across reconnects — the full snapshot IS the
resync. **Why not virtual-DOM diffing:** the row JSON comparison already
skips unchanged cards; the only DOM work left is proportional to what
actually changed.

## The conversation in the web stream

The terminal mirror deliberately omits the main agent's messages — the main
pane already shows them. The web has no main pane, so the dashboard
interleaves the main-thread conversation (prompts / assistant messages /
teammate mail) into the session stream — web-side only; no producer or
terminal-renderer change.

**AskUserQuestion answers surface too** (added 2026-07-19, from a "my answer
didn't appear in this session" report). An answer is recorded as a *tool_result*
(not plain user text), so `transcript.conversation` dropped it into `blocks` and
it never rendered — the card cleared and the choice vanished from the feed.
`conversation` now emits a distinct `answer` record for it, identified by the
line's `toolUseResult` sidecar being a dict with an `answers` key (so a Bash/Read
tool_result stays out). `opshtml.msg_html` renders `answer` as a `you ▸ answered`
bubble WITHOUT the rewind affordance (it is not a re-runnable prompt). This is the
DASHBOARD's conversation view only — the terminal mirror never showed main-thread
messages anyway.

**The answer bubble is a STRUCTURED card** (added 2026-07-22, "it just appears as
one line — make each question and answer its own section"). Claude Code's recap
string ("Your questions have been answered: …") crammed every Q="A" onto one line.
The `toolUseResult` sidecar, though, carries `answers` as a `{question_text:
answer_string}` map (the chosen label, `", "`-joined labels for multiSelect, or
the typed free text) alongside the `questions` list (for each question's `header`
AND its option labels). `transcript._answer_pairs` pairs them into
`[{q, header, values:[…]}]` (attached to the record as `qa`) — a multiSelect
answer is split back into its separate values by `_split_answer`, which is
LABEL-AWARE: Claude Code joins the selected option labels (in option order) and
THEN the ONE typed custom value, so it peels KNOWN option labels off the front
(longest-first, so a label that itself contains `", "` like "Salt, pepper" stays
whole) and treats whatever REMAINS as a single custom value — never split further,
because a comma inside the typed custom text ("test, test2") is not a value
boundary. `opshtml.answer_html` renders one section per question — its
optional header chip + question text — with EACH picked value as its own
HIGHLIGHTED chip (`.ansv` in the `--done` hue, wrapped in `.ansvs`), mirroring the
`question` bubble's per-question layout. It degrades to the flat recap markdown when
the sidecar isn't that map (an older shape / no pairs) — so the old rendering is the
fallback, never lost. Escape-first like every `md_html` leaf. Pure read-model: no
new hook, stream, or state.

**The QUESTION surfaces too** (added 2026-07-20, "I want questions also to be in
the transcript"). The answer alone was half the record — the transcript showed
what you picked but not what was asked. The question Claude asks is an assistant
*tool_use* block (`name == "AskUserQuestion"`, `input.questions`), which
`conversation` previously used only as an anchor. It now also emits a distinct
`question` record — `transcript._format_questions` flattens the questions into
readable markdown (each question's text + a bulleted list of its offered
options; the answer bubble already shows which was chosen), and
`opshtml.msg_html` renders it as a `claude ▸ asks you` bubble in the red
"asking-you" hue, again without the rewind ↶. Every OTHER tool_use stays the
terminal mirror's job (rendered as ops); only AskUserQuestion becomes a
conversation record. It appears for DECLINED questions too (the tool_use is
written regardless of the answer), so the transcript honestly records what was
asked even when nothing was picked. The interactive ask CARD is unchanged and
still handles answering — the bubble is the permanent record, the card the
ephemeral answerer. No new hook or state: a pure read-model addition over the
already-audited transcript, exactly like the `answer` record above.

**Interleaving by timestamp, anchors as the fallback.** The ops table carries
a `ts REAL` column (`core/state.py`, one wall-clock stamp per `ops_append`
batch — additive migration, so older parked `*.keep` DBs keep working and their
pre-migration rows read back `_ts` None), and `ops_after`/`ops_at` inject that
value into each op dict under the reserved `_ts` key (the mirror renderer reads
ops via `.get` and ignores it). `transcript.conversation(path, pos)` likewise
stamps each record with `ts` — the transcript line's ISO `timestamp` as an
epoch float, None when absent. When BOTH sides have a timestamp,
`merged_backlog()` interleaves chronologically: each message lands after the
last op that precedes it in time. This is why ops needed a real time column —
the earlier anchor-only scheme could not order a message *between* two ops of
the same tool block.

`anchor` (the last tool_use id seen before a record; ops carry the matching
`g`/`v`) survives as the FALLBACK for pre-migration history — an op or record
without a timestamp is placed after its anchor's LAST op. Pre-first-tool
messages (anchor None, no ts) lead the stream; messages whose anchor never
painted an op keep their relative order at the tail. This works for ALL
history, parked sessions included. Live updates need neither key: the
per-session SSE tails the transcript by byte cursor (`mpos`, resumed across
reconnects like the ops `after` cursor) and appends `msgs` events in arrival
order — interleave is a backfill affordance, not a live-ordering guarantee.
`/api/session/<sid>/ops` stays PURE ops (the mirror-parity endpoint); the merge
exists only in the SSE backlog.

**The `tab` event re-resolves the window mid-stream.** `sse_session` resolves
the session's `kitty_window_id` at connect, but a RESUME moves the session to
a NEW kitty window while streams are open (the SessionStart upsert refreshes
the sessions row) — so the loop re-reads the row on the slow cadence before
polling `tab_states()`. Without this, a stream opened before the resume
polled the dead window's lingering tab state forever: the page showed the
old window's green while the real tab sat magenta (shipped).

## Lazy backlog (a big session paints its newest slice instantly)

A long-running session's merged backlog is multi-MB of rendered HTML — sending
it all in the first SSE `ops` event stalls the paint. So the initial event
carries only the NEWEST `TAIL_BLOCKS` (80) stream **blocks**, and older history
loads on demand.

**The initial slice arrives over GET, not SSE.** Even the trimmed slice is
100–400KB of HTML, and SSE frames are NEVER compressed (`_send`'s gzip is
non-SSE only — compressing a held-open stream would buffer it), so a
remote/tunnel page paid the full raw transfer before "waiting for activity…"
cleared. `GET /api/session/<sid>/backlog` returns the identical
`merged_backlog` payload (`{last, mpos, oldest, items}`) through `_send`,
which gzips it 8–9× (391KB → 44KB measured). The page fetches it first, then
connects the SSE WITH the returned cursors (`?after=<last>&mpos=<mpos>`), so
the SSE fresh-connect backlog branch is skipped and the stream carries only
increments — the exact no-gap resume contract a reconnect already uses. The
zero-cursor SSE backlog stays as the fallback (the client falls back to it
when the fetch fails, and direct SSE consumers still get a complete stream).

**One merge core, two windows.** `_merge_order()` builds the full oldest→newest
interleave once — as `(slot_id, kind, obj)` triples, deliberately UNRENDERED so
the block cut discards most ops before the costly `op_html` render runs — and
both `merged_backlog()` (the newest `TAIL_BLOCKS`) and `history()` (the previous
`N` blocks older than a cursor) slice it the same way (`_cut_blocks` → `_snap` →
`_render_window`). Factoring the merge, not forking it, is what makes the slices
provably reconcile: the concatenation of the initial backlog and every `/history`
page equals the unlimited merge, with no gap and no overlap (a test asserts
exactly this).

**Why slot ids, not op ids alone.** A "block" (a distinct copy-group `g`, or a
standalone item) is the unit the *count* limits, but a block can span several op
rows and a conversation msg has NO op id — so a raw op-id cursor could split a
block's rendering across the boundary or double-count an interleaved msg. Each
item instead carries a `slot_id`: the row id of the op it belongs to (an op's
own id; a conv record's is the id of the op it follows), `0` for the
pre-first-tool HEAD group, `last+1` for the never-painted TAIL group. Windows are
always whole slots (`_snap` pulls the cut back to a slot boundary), and the
`oldest` cursor names a slot boundary — so `history(before=oldest)` takes exactly
the slots below it. `oldest` is `0` when the whole history already fit (nothing
to lazy-load). Concurrent streams (a bg job emitting mid-foreground-block) can
make one group's op rows non-contiguous, so a group CAN straddle the cut — its
newer ops in the initial window, its older ops in a history page; the client
folds the older ops into the already-live block card (see below), never a
duplicate card.

**Conversation is parsed in full, sliced by the window.** Each backlog/history
call re-parses the whole transcript (cheap relative to op HTML — O(turns) text
records versus O(thousands) ops, each op carrying a rendered, possibly large
output block) and slices the conversation implicitly by the merged window; there
is no separate transcript byte cursor for history. The `mpos` the backlog returns
is still the whole-transcript end, so the live SSE tail resumes correctly.

**Client (`app.js`).** The feed is newest-top, so older history loads DOWNWARD:
a `.loadmore` button pinned at the bottom of the stream (a child of the stream,
so the live top-prepends never disturb it) shows while `S.ses.oldest > 0` and
clicking fetches `/history` and appends the page via `appendOlder()` — the
mirror image of the live `appendItems()` top-prepend. `appendOlder` inserts at
the bottom; blocks born in a history page start FOLDED and are NOT tracked in
the live `S.ses.blocks` map or the `KEEP_OPEN` window (they are history, not the
live tail); a straddling group already in the live map has its older ops folded
into that card's body at the end (older ops trail — acceptable). Filters apply to
lazily loaded items (`appendOlder` runs the shared `applyFilterTo`). The button
hides once `/history` reports `oldest == 0`. Past a 3000-child cap, each live
arrival trims the feed's oldest DOM nodes off the bottom, skipping over the
pinned `.loadmore` button (it must stay the last child) and evicting a trimmed
block card from the live `S.ses.blocks` map so a straggler op for that group
can't render into a detached node. That cap counts only DIRECT children of
`.stream` — a block card is one child no matter how many ops nest inside its
body — so a long-lived copy-group (a bg stream, a `monitor`, `tail -f`, a
subagent) that keeps emitting ops into ONE block would grow the DOM without
bound, invisibly to the child cap, and its live-tail card never reaches the
bottom to be evicted. So `fillBlock` also caps each block **body** at
`MAX_BLOCK_BODY` (800), trimming its oldest (top) op nodes as new ones append.

## Mirror card styling

The mirror stream is styled to read like the **activity-timeline cards** (which
the user liked and asked the mirror to adopt — the standalone `activity` tab was
retired, *Ordering: newest-first* above). Each fold block (`.blk`) is a roomy
card: a solid kind-coloured pill (the label op's `.chip`, already background-
coloured per kind by `opshtml`) plus a one-line `.bsum` summary, expanding onto
a darker inset body — the same shape as an `.ent`. There is **no disclosure
triangle**: the whole header is the click target, matching an activity entry.
Loose top-level rows (file-op one-liners, standalone chip lines) get the same
card treatment, scoped to DIRECT `.stream` children so ops nested inside a block
body keep their compact inline form.

This is **CSS only** (`style.css` `.blk`/`.bhead`/`.bsum`/`.bbody` + the
`.stream > .opl/.ol/.og/.ogut` rule). The fold/expand machinery — `createBlock`,
the `data-open` toggle, the `KEEP_OPEN` window, click-to-view, and ⧉ copy — is
UNCHANGED; only the appearance moved. Deliberately NOT ported from the activity
tab: its information architecture (a short *category* pill like `BASH`/`READ`
with the detail in the summary). The mirror keeps its own richer pill (glyph +
command/name) and what it shows — the request was the *look*, not the data.

## Live agent timelines

An agent's drill-down (`/api/session/<sid>/agent/<aid>`) is fetched once for its
rich header (model / usage / tools) and its entries; a RUNNING agent's page then
grows live over the `/events/agent/<sid>/<aid>` SSE, so you watch a subagent work
without reloading. The client opens the SSE only when the agent looks live (its
`agents` row has no `ended_at` — a parked transcript won't grow) and hands it the
`pos` byte cursor the REST response carried (additive field, from
`plugins.activity()`), so the stream resumes exactly where the fetch stopped —
no gap, no overlap (the same race-free hand-off the per-session stream's
`after`/`mpos` cursors use).

**Ordering: newest-first.** The drill-down renders the transcript's chronological
entries **reversed** (`renderTimelineInto`'s `newestFirst`), so a subagent's most
recent message reads at the TOP — matching the main agent's mirror stream, which
prepends (`appendItems`, `st.prepend`). The timeline head stays pinned above. The
live SSE then *prepends* each new increment (in chronological order, so its newest
lands topmost and the whole increment sits above older entries) — the mirror of
the oldest-first append path. The same component still supports an oldest-first
render (`renderTimelineInto` without `newestFirst`): the server's `/activity`
main-thread endpoint (`plugins.activity(sid)`) is unchanged and test-covered,
but the dashboard **no longer surfaces a `activity` tab** for it — it was a
main-thread timeline that only duplicated the mirror tab's stream, so it was
retired (2026-07-21).

Server-side the SSE polls `plugins.activity_since(sid, aid, pos)` at `TICK_S` —
the incremental companion to `activity()`, sharing timeline()'s per-record entry
builder (`transcript._fold_record`, the single owner of the record→entry
mapping). It returns `(entries, resolutions, new_pos)` and the SSE pushes two
event kinds: `entries` (the new increment's entries, server-enriched by the same
`_enrich_entries` the REST endpoints run — markdown/rich-tool HTML) added to the
list in the render's order (prepended newest-first, per the ordering note above),
and `resolve`
(`[(tool_use_id, output, failed), …]`). A `resolve` exists because a tool_use in
one increment can have its tool_result land in a LATER one — the entry was
already serialized and sent, so it can't be patched in place; the client finds
it by a `data-tool-id` attribute and fills in the result region (a `.tout` block
kept separate for exactly this), or ignores it when no such entry is on the page
(a genuine orphan whose tool_use it never saw — increments deliberately don't
emit orphan-result entries, since a byte window can't distinguish the two). Usage
is omitted from increments (the header's rollup is a whole-file figure; the
message-id dedup cursor can't survive a per-call window). **codex has no
incremental provider** (its rollout renderer lacks the parse split), so a codex
run's drill-down stays fetch-once — the `activity_since` fan-out finds no provider
and the SSE idles as a heartbeat keep-alive.

### Monitor events in the drill-down

A `Monitor` tool launch shows in the timeline as an ordinary `tool` entry (name
`Monitor`, its command/description in the input). Its **events** — which Claude
Code writes to the transcript as `queue-operation` `<task-notification>` records
(docs/streaming.md, *Monitor events in the transcript*) — become their own
`{"t": "monitor"}` entries (`◉ monitor event` / `◉ monitor completed` pill, the
event line in the body, cyan `.k-monitor` chip), interleaved in transcript order
right after the launch. So the drill-down carries a monitor's *whole* story —
launch, every event, and the stream-ended marker — including for a parked session
viewed long after (the transcript is durable). These are **not** re-emitted into
the mirror stream (`conversation()` drops them): the mirror already streams a
monitor's events live as ops via `claude-stream.py`, so surfacing them from the
transcript too would double them.

### Breadcrumbs (back up the hierarchy)

A subagent drill-down (`showAgent`) prepends an **agent-hierarchy breadcrumb**
above the timeline — **◆ ‹main agent› › ◇ ‹subagent›** (`agentCrumbs`) — showing
just the two agent nodes, because the hierarchy is one level deep (a session's
flat agent list; an agent launching a sub-subagent is not modeled anywhere). The
**main agent** node is a link to the session's mirror (`#/s/<sid>`) labelled by
the session title — clicking it is how you go back to the main agent; the current
**subagent** is the highlighted end pill. Rendered as a boxed bar (`.crumbs`), it
sits in `ses.body` alongside a child wrapper that `renderTimelineInto` clears —
the breadcrumb is outside that wrapper so the post-fetch rebuild doesn't wipe it.
A drill-down also lights the **agents** tab (`showAgent` toggles `.on` onto the
`…/agents` tab link): the `agent:<id>` pseudo-tab has no tab-bar entry of its
own, so without this every tab went dark and there was no "you are here" cue —
the breadcrumb and the lit agents tab now both carry it. (It deliberately does
NOT re-surface the session/list rungs — those live on the brand link and the tab
bar; the breadcrumb is purely the main→sub agent relationship.)

## Monitors tab

A session-view tab **`monitors`** (between `agents` and `errors`) lists the
session's Monitor tool runs as cards — the same card/grid/drill-down shape as the
agents tab — so every monitor's *state* is visible at a glance and clicking one
opens its full detail. It answers "what is this session watching, and what have
those watches seen?".

**Data path — `plugins.monitors(sid)`** (a registry fan-out like `activity`;
claude_code only — the Monitor tool is a Claude Code concept, so codex declines).
It merges two authorities per taskId:
- the **MAIN transcript** (`transcript.session_monitors` → `monitors()`) owns the
  *content*: the Monitor tool_use's command / description / `persistent` /
  `timeout_ms`, and its **events** (the `queue-operation` `<task-notification>`
  records — docs/streaming.md, *Monitor events in the transcript*). A Monitor
  tool_use carries no taskId in its input, so the launch is tied to its events
  through the **"Monitor started (task X)"** tool_result (the one place the taskId
  appears); a WebSocket monitor (`ws.url`, no command) records `source: "ws"`.
- the audit **`streams`** rows (`sessionapi.monitor_streams`, kind `monitor`, the
  same keystone `agents()` reads) own the *state*: `started_at` / `ended_at` /
  `end_reason`, and `live` (the newest row's `ended_at` being None). A streams row
  with no matching transcript launch (a truncated head) still surfaces — state
  only, blank command — so a running monitor is never hidden.

**Card state** (`monitorStatus`) mirrors the agent cards' `data-st` tint:
`running` (exec/blue) while live, else `ended` (done/green), with `no output` /
`not found` variants read off `end_reason`. Each card shows the description (or
command), a `persistent`/`≤timeout` chip, the event count, and duration/age.

**Live-ness.** The tab **badge count** rides a cheap `monitors` SSE event — the
distinct-monitor `streams` COUNT (`sessionapi.monitor_count`, no transcript
parse), pushed on change, so a new `Monitor` launch bumps it like the `errors`
badge. The card **list** is fetched lazily on tab-open (`/api/session/<sid>/
monitors` — one transcript parse) and, while any monitor is `live`, re-fetched on
a light 4s client poll (`scheduleMonitorPoll`); the poll stops when none is live
or you leave the tab. (No dedicated per-event SSE increment — a monitor's live
events already stream into the *mirror* tab as ops; the monitors tab is the
state-and-history view.)

**Drill-down** (`#/s/<sid>/m/<task>` → `showMonitor`, guarded from chrome
re-renders by `ses.monitorFocus` exactly as `agentFocus` guards the agent
drill-down) shows a detail card — status, the command (or `ws` url) as a `<pre>`,
a key/value meta grid (task, lifetime, events, started/ended/duration, end
reason) — then the **full event list** (newest-first; the stream-ended
`completed` notification styled apart). Events are capped at `MON_EVENT_CAP`
(2000, most-recent) with an exact `event_count` and a truncation note. A
breadcrumb (**◉ monitors › this monitor**) leads back to the list.

**Monitor vs background-job notifications.** Both a monitor's events AND a
background job's completion ride the *same* `queue-operation` `<task-notification>`
mechanism (a bg job's is `summary: 'Background command … completed'`,
`status: completed`, no `<event>`). `transcript.parse_line` keeps only the
MONITOR ones (a `<event>` tag, or a `Monitor …` summary) — otherwise a bg
completion would show as a phantom monitor here and mislabel the activity
timeline. Background jobs get their own tab instead (below).

## Jobs tab

A session-view tab **`jobs`** (between `monitors` and `errors`) lists the
session's **background Bash jobs** — `run_in_background` launches and Ctrl+B
conversions — as cards, the same shape as the monitors/agents tabs, drilling into
each job's command + full output.

**Data path — `sessionapi.jobs(sid)`** (pure core, parallel to `agents()`; no
transcript — a bg job's output isn't in the transcript). It merges:
- the audit **`streams`** rows (kind `bg`, `task_id` = backgroundTaskId — the same
  keystone `agents()`/monitors read) for the STATE: `started_at` / `ended_at` /
  `end_reason` / `lines`, and `live` (newest row's `ended_at` is None);
- the mirror **ops** for the COMMAND: a bg job's block is copy-grouped by its
  taskId, so `core.copy.group_commands` (one mode=ro ops scan) pulls each job's
  `code` op. A Ctrl+B-converted job's command op lives in its foreground group, so
  its `command` may be blank — the card falls back to the taskId.

The full **output is not carried in the list** (a build log can be huge). The
drill-down (`#/s/<sid>/j/<task>` → `showJob`, `ses.jobFocus`-guarded like the
others) shows the command + a meta grid (task, lines, started/ended/duration, end
reason), then fetches the **output on demand** from those same ops via the
existing `⧉out` copy endpoint (`GET /api/session/<sid>/copy/<task>/out` →
`core.copy.collect`) into a scrollable box. `jobStatus` maps state to the agent
cards' `data-st` tint: `running` while live, else `finished` (a bg job's normal
`writer-gone`/vanished completion), with `timed out` off `end_reason`.

**Live-ness** matches the monitors tab: the badge count rides a cheap `jobs` SSE
(`sessionapi.job_count`, distinct bg-stream count), the list is fetched lazily on
tab-open and re-fetched on a 4s poll while any job is `live`. (A job's live output
already streams into the *mirror* tab as ops; the jobs tab is the
state-and-history view.)

## Memory tab

A session-view tab **`memory`** (between `jobs` and `errors`) lists the
**memory-wiki notes** the session touched — the Obsidian-style knowledge vault at
`~/wiki/01` (markdown notes with YAML frontmatter, cross-linked with bare
`[[wikilinks]]`). A Read/Write/Edit whose path falls under that root is a MEMORY op
— recall (Read), persist (Write), or revise (Update/Edit). `plugins/claude_code/
memory.py` is the single owner of that vocabulary (the root, the project scope, the
`is_memory` test, the project gate, the mirror 🧠 `MARK`, the `memory` kv, and the
read-side vault helpers).

**Scoped to one project.** The wiki (`~/wiki/01`) is shared across all of
`code/01`, but the feature is deliberately enabled ONLY for sessions inside
`~/code/01/aggregator-adapters` (`memory.project()`, `BAQYLAU_MEMORY_PROJECT`
overrides — a test seam). The producers gate on `is_memory(path) and
in_scope(cwd)`, so a wiki note touched from another project is a plain file op; and
the server serves `memory_scope` (`in_scope` over the session's cwd) so the client
**hides the Memory tab entirely** off-scope (a deep-link to `…/memory` there falls
back to the mirror). A worktree under the project (`…/.claude/worktrees/<x>`) is in
scope.

**Mirror side.** When `file_fmt.py` (main agent) or `substream_render.py` (a
subagent) renders a file op under the root, it appends 🧠 (`memory.MARK`) to the
one-liner and tags the op `mem` (`ops.line`/`ops.gut`), which `opshtml` surfaces as
`data-mem` so the page sorts it into its own **`memory`** stream-kind filter
(*Stream kind filters* below), distinct from generic `files`.

**Tab data path.** Both producers also `memory.record()` the touched note into a
per-session **`memory` kv** (state DB, survives park) — `{files: [{path, name,
verb, agent, count, ts}]}` keyed by path, verb ESCALATED by rank (Write > Update >
Read) on a repeat touch, stamping the escalating op's agent (None = main). Unlike
the main-agent-only *mirror*, this is **team-wide**: a subagent (e.g. a note-writer)
records under `self.agent`, so the tab shows who touched each note. The read model
is `sessionapi.memory(sid)` (`kv_at`, live-or-parked), newest-touch first; the badge
rides a cheap `memory` SSE (`sessionapi.memory_count`). The list renders one card
per note: a verb chip (read=blue · update=gold · write=green, the `FILE_RGB`
semantics) + the note name + the subagent name (if any) + a `×N` repeat count.

**Note viewer + link following.** Clicking a card opens the note via `GET
/api/session/<sid>/note?path=<abs>` (a followed link uses `?stem=<stem>`). The
server resolves the stem through `memory.resolve()` (a TTL-cached vault index of
`{stem: path}`, Obsidian bare-name resolution), reads it path-traversal-guarded to
the root (`memory.read_note`), and renders `{name, frontmatter, html, backlinks,
missing}`. The body is markdown → **safe HTML** via `dashboard/notehtml.py`, which
reuses `opshtml.md_html` (the escape-first, dependency-free subset the message
bubbles use) and adds `[[wikilink]]` linkification: links are protected as
control-byte sentinels BEFORE `md_html` and restored as `data-note` anchors AFTER
(so a stem's `_`/`*` can't be eaten by emphasis and nothing raw reaches the page);
a stem that doesn't resolve gets a `dead` class (the wiki keeps dangling links on
purpose). Clicking a `[[link]]` fetches the target and pushes a breadcrumb (🧠
memory › note › followed note …) so you can walk the vault beyond the touched set
and back out. A **Backlinks** section lists the notes whose text links to this one
(`memory.backlinks`, same index), each itself clickable. Each wikilink/backlink
anchor gets a DIRECT `onclick` (not a container-level delegated listener): the
anchors have no `href`, and mobile Safari won't dispatch a bubbled click from a tap
on such an element to an ancestor listener — a delegated handler silently did
nothing on the phone while the desktop worked (the grid cards use a direct onclick
for the same reason).

## Stream kind filters

The session view's mirror tab carries a filter bar directly above the stream:
toggle chips (`all · commands · files · memory · agents · messages`) and an `N of M
shown` count. Clicking a chip narrows the stream to one kind. Filtering never
removes DOM (SSE keeps appending); non-matching items get a `.fhide`
(`display:none`) class, applied in `appendItems` to newly arrived items too via
the shared `matchesFilter()` — so a live filter holds as the stream grows.
Filter state lives on `S.ses.filter` (`{kind}`) and is cleared when switching
sessions (a fresh `S.ses`).

**There is deliberately no free-text search box.** The bar once carried a
debounced substring input (over each item's `textContent`, folded bodies
included) with a clear button; it was removed 2026-07-21 as unused — the kind
chips are the whole filter surface now, and the `data-kind` machinery below is
what they act on.

Each top-level stream child is stamped with a `data-kind`
(`commands`/`files`/`agents`/`messages`) ONCE at creation in `appendItems`
rather than re-sniffed per filter pass — selector stability beats matching the
exact chip text, which drifts. Blocks default to `commands` and upgrade to
`agents` on an agent signal (an outer-gutter `.og` wrapper == a subagent's
nested job, or a block-opening chip that starts with a who-prefix rather than a
main-session command glyph `▶▷◉■` — subagent/teammate/codex chips lead with
their label/`codex`). Ungrouped items classify by item type: `msg` items are
`messages`, memory-wiki file ops (they carry `data-mem` — the 🧠 marker, checked
first) are `memory`, other file-op one-liners (they carry a `data-v` click-to-view
id) are `files`, the rest `commands`. On a CURRENT session the `agents` chip mostly
matches nothing: agent/codex stream ops are producer-source-stamped and never
reach the page (the main-agent-only rule, *The web presenter* above). The
chip and its heuristic survive deliberately for pre-stamp history — parked
DBs written before the stamp existed still carry agent blocks.

## Notifications (the toaster)

One daemon thread diffs the ENTIRE tab table (`sessionapi.tab_states()` — the
whole-table reader added for exactly this; per-window probes would be N
queries for one snapshot) once a second, and maps windows to sessions via the
audit `sessions` rows' `kitty_window_id` (newest session wins the window — a
kitty window outlives sessions). A transition INTO `awaiting-command` (red —
Claude is asking you) or `awaiting-response` (green — done, your turn) pushes
a `notify` event to every `/events` client; the app shows an in-page toast
always and an OS `Notification` when the page is hidden. The win→session map
depends on `audit.session_start`'s upsert REFRESHING `kitty_window_id` (and
clearing `ended_at`): a resume fires SessionStart again under the same sid
from a NEW kitty window, and before the upsert refreshed the id the map kept
pointing at the dead window — a resumed session's toasts silently vanished
(no error anywhere; the notifier just found no row for the new window and
skipped). The payload carries
the session TITLE (`session_title` over the row's transcript, resolved at
push time — the transcript just grew, so a winmap-refresh-time title would be
stale) and the app shows it as the toast/notification body line, so
"kitty is done" says *which* session is done; the generic
"Claude is asking a question" / "finished — your turn" line survives only as
the no-title fallback. The first scan is a baseline, never news. Windowless sessions (headless/daemon) produce no
toasts, same as they have no tab colour — that's the tab system's own
scoping, not a dashboard limitation.

### Telegram alerts (deferred, opt-out)

The in-page toast + OS `Notification` only reach you if a browser tab is open;
when you're away from the desk, nothing does. So the SAME red/green transitions
also drive a **deferred off-device alert** over the reused global `notify`
skill (`~/.claude/skills/notify/scripts/notify.py` → a Telegram bot), gated on
**you not reacting in time**:

- On the transition the notifier **arms** `Notifier.pending[win]` (the same
  `_payload` the toast carries, plus a monotonic `armed_at` and the armed
  `state`). The immediate toast still fires — the arm is purely additive.
- Each subsequent scan **cancels** any armed entry whose tab has **left** its
  armed state — you answered (→ busy), the turn resumed, or the session closed
  (its window vanished from the tab table) — OR whose session has **ended**
  (audit `ended_at` set, `_session_ended`): you closed/quit it on the dashboard,
  so you were satisfied and moved on and the alert (its deep link would open a
  dead session) is moot. The `ended_at` check is the robust one the win-vanish
  test can miss — a stale tab row can linger past a close, and a reused kitty
  window id can even re-match the armed state under a DIFFERENT session. It is
  ALSO cancelled while you're **composing** a reply — a non-empty unsent web
  composer draft (`_composing` over `_composer_draft`): typing a draft means
  you're already on it, so an alert would just nag. That is the "did I react?"
  test: reacting is the tab moving off red/green, the session being gone, or an
  unsent draft in hand — decided deliberately over "did the page get viewed"
  (which would need client heartbeat plumbing).
- It is ALSO cancelled while you're **answering the question AT THE TERMINAL**.
  A red `awaiting-command` is a modal AskUserQuestion dialog; you typing a
  free-text answer or toggling a selection there moves neither the tab off red
  nor the transcript, so none of the checks above catch it — its ONLY trace is
  the on-screen dialog changing. So for an `asking` arm the notifier reads the
  window's dialog region (`askdialog.region` over the frontend's `get_text`,
  which isolates the dialog from a live-ticking status line below it), baselines
  it on first sighting (the untouched dialog), and **drops the arm the moment it
  differs** — you're on it. The region is `""` for a non-ask red tab (a
  permission / plan prompt has no `☐`/`☒` header chip) and when no terminal
  channel resolves, so both keep the plain grace-window behaviour. The drop is
  audited as a `notify-suppress` `state_files` row (`reason: dialog-activity`).
  Limitation by design: pure thinking with ZERO keystrokes for the whole grace
  window is indistinguishable from walking away and still fires — bump
  `CLAUDE_DASH_NOTIFY_DELAY_S` for a longer think.
- An entry that **survives** past the grace window is **sent once** (popped),
  then never re-fires for that transition. It fires **regardless** of whether a
  browser is connected — reaching you when away is the whole point.

The send is a **detached** `subprocess.Popen` of the notify script
(`start_new_session=True`, DEVNULL stdio, no `wait`) so a slow Telegram
round-trip can't stall the 1 s watcher; it's best-effort and audited as a
`telegram-notify` `state_files` row (an `A.error` on a launch failure). The
message is `🔴 <project> needs you` / `🟢 <project> is done`, the session title,
and a `<public-url>/?s=<sid>` deep link — pointed at the PUBLIC proxied origin
(`CLAUDE_DASH_PUBLIC_URL`, default `https://baqylau.zhambyl.top`), never the
`127.0.0.1` bind: the alert lands on a phone, where localhost is useless. The
sid rides a QUERY PARAM, not the app's own `#/s/<sid>` hash route, because
Telegram's auto-linker drops a URL fragment — a `#`-link would open the
dashboard root on the phone, not the session. The page translates `?s=<sid>`
back into the hash route on load (`deepLinkFromQuery` in app.js).

**Per-session opt-out.** The header's **🔔 alerts / 🔕 muted** button
(`renderSessionChrome`, beside ✎ rename / ⇆ migrate) toggles
`POST /api/session/<sid>/notify` `{"muted": bool}`, which flips the session's
entry in the durable global prefs store (`dashboard/prefs.notify_muted` /
`set_notify_muted`, one `notify-muted` kv map keyed by sid — an un-mute deletes
the key so the map stays the small muted set). Like rename it is deliberately
NOT live-gated: the opt-out is a dashboard pref, not session state, so it works
live AND parked, and `session_payload` carries `notify_muted` so the button
paints the right label on load. The mute is checked at SEND time (not arm time),
so muting during the grace window still suppresses the alert.

**Env knobs** (read once at server start — a restart picks up changes):
`CLAUDE_DASH_NOTIFY_DELAY_S` (grace seconds before firing, default `60`; bad /
negative → default), `CLAUDE_DASH_NOTIFY_TELEGRAM` (`0` disables arming +
sending entirely, the in-page toast is unaffected; default on), and
`CLAUDE_DASH_NOTIFY_CMD` (the notify script path — `~` expanded, overridable for
a different transport or the hermetic test's recorder), and
`CLAUDE_DASH_PUBLIC_URL` (the deep-link base — the proxied origin the alert
opens, default `https://baqylau.zhambyl.top`; trailing slash tolerated).

**The session strip is the persistent complement to the toasts.** Toasts are
transient (a 7s slide-in on the transition); the strip is the standing view of
every live chat, doubling as the session switcher while you're inside one. A
slim hairline bar pinned under the header on every view (`#attn` in
index.html — a fixed container outside `#view`, so it survives the router's
re-renders) lists EVERY live session as a jump pill, needs-you states first:
`awaiting-command` as a red pulsing pill (`--ask`, the badge's own dot
animation), `awaiting-response` as a quieter green pill (`--done`), then the
rest with a colored dot only and no ring — busy (`--busy` magenta,
thinking/working), running (`--exec` blue, executing/awaiting-bg), and idle
(grey, quietest — including tabless headless/daemon sessions, whose `tab` is
`""`). The tab-state→pill mapping is `ATTN_CLASS` in app.js, mirroring the
kitty tab palette. Within a state group pills sort by label then sid, NOT
recency — the bar re-renders on every snapshot tick, and pills that shuffle
under the cursor are a misclick trap (a session still *moves* when its state
group changes; surfacing on becoming-red is the point). It is `hidden`
entirely only when no session is live, and when it shows, `body.attn-on`
drops the session view's sticky agents rail (`.rail`) below it so the two
never overlap. It is fed by the same global `sessions` SSE
snapshots the app already holds (`renderAttention()` reruns on every snapshot)
plus the open session's per-session `tab` SSE event, which patches that row in
place so the bar reacts before the next global snapshot lands. The count of
asking sessions (only asking — busy/idle chats are ambient, not news) also
prefixes the browser tab title (`(2) baqylau`)
and swaps the favicon to a red-dotted variant, so a backgrounded tab still
shows the ask count. The currently-open session's own pill is de-emphasized
(it's the one you're already looking at) but still shown, for consistency —
and, now that every chat is listed, so you always see where you are among
them.

## The husk rows (hidden agents)

`agents()` returns some rows with EVERY field empty (no kind/desc/slot/
transcript/start): bookkeeping left by the subagent finaliser's
`never started (hidden agent)` path — a `SubagentStop` for one of Claude
Code's hidden auxiliary agents, which fires no `SubagentStart` and streams no
transcript (the same population the OTEL pipeline exists to price). Zero
user-facing signal, so the server's `visible_agents()` filters them out of
the dashboard's payloads — presentation policy; the API itself keeps
reporting them (they're real state, and the audit `hook_events` decision
string is the provenance). A row with at least one real field always shows;
one that's merely thin (desc but no transcript yet) renders dim and stays
clickable — the layout-derivation fallback in `plugins.activity` sometimes
finds a transcript the audit never saw.

## The "running now" ribbon

The session header carries a compact ribbon under the stats row — one chip per
thing EXECUTING under the session right now: the foreground command tailer
(`⚙ fg`), background jobs (`⏳ bg`), monitors (`👁 monitor`), and streaming
subagents/teammates (`◇ agent`), each tinted by kind. It is fed by the state
DB's `live` slot table (`core/slots.py`), the same ground truth the tab
tracker's blue-while-busy signal reads — NOT the audit `streams` table (which
records lifetimes, not liveness). `sessionapi.running(sid)` resolves
`state_db_for(sid)` and returns only rows whose owning pid is still alive
(`state.live_at`'s `pid_alive` verdict — EPERM = alive; the reader never steals
a stale slot the way `slots.claim` does), grouped by kind. It rides
`session_payload` as `running` and is pushed as a `running` SSE event on change
(the same only-on-change, slow-tick cadence as `agents`/`costs`). A parked
session's rows are all dead, so its ribbon is empty (hidden).

## Subagent scoreboard swap (drill in → the scoreboard becomes the agent's)

Clicking a subagent (an agent card, or the `#/s/<sid>/a/<aid>` route) doesn't
just open the drill-down timeline — it **swaps the top scoreboard to that agent's
own numbers**. `showAgent` sets `ses.agentFocus = {aid, data}`
and repaints the header; `updateStatsRow` branches on `agentFocus` and calls
`renderAgentScoreboard` instead of the session totals — the prominent header
**name** (`ses.projEl`) becomes the subagent's (`◇ ‹desc›` / `👥` for a teammate),
and the stats row shows status, `model·effort`, event count, `⏱` duration, the
`Σ` token rollup, and `≈` cost, with the ctx row showing the agent's own ctx bar
and a leading **← session** link that restores the session view (it points at
`#/s/<sid>`, the mirror = the main agent). The session title returns when a full
`renderSessionChrome` rebuilds the header on the way back (the name write is
skipped mid-inline-rename). The running ribbon hides while focused (it's
session-scoped).

The header **state indicator follows the focused agent too**: the badge pill
(its text and colored dot) and the whole `.shead` state wash switch from the
session's tab state to THIS agent's status. `renderAgentScoreboard` calls
`setBadgeAgent`, which stamps `data-st` (from `agentStatus`: running blue · done
green · cancelled/crashed red · unknown amber) and clears `data-tab` on both the
badge and the `.shead`, so the pill reads e.g. "done" over a finished subagent
even while the main agent is still busy (the CSS is `.badge[data-st]`/
`.shead[data-st]`, mirroring the agent cards). Without this the session pill said
"busy" over a done subagent. The live `tab` SSE handler skips `setBadge` while
`agentFocus` is set (the same focus guard as `updateRunning`/`updateStatsRow`) so
a session tab event can't repaint the header back; a running→done flip while
focused re-renders through `updateAgents` (an `agents` SSE, which doesn't move
`statsSig`). `setBadge` clears `data-st` on the way back, and a full
`renderSessionChrome` rebuilds the header outright.

The header **action buttons** are pruned to what applies to a subagent
(`applyAgentActionVis`): the session-only actions — rename, migrate, cancel,
rewind, close, resume, and the compact/model/effort quick commands (all marked
`.actses`) — hide while focused, since none of them act on an individual
subagent (Claude Code has no per-subagent rename/rewind/compact/…). The lone
exception is **■ stop** (`.actstop`): interrupting the session is the one way to
stop a *running* subagent, so it stays visible while the focused subagent is
running and hides once it's done (re-evaluated on the `agents` SSE via
`updateAgents`). An action row left with nothing visible collapses so it leaves
no gap. The full `renderSessionChrome` rebuild on the way back restores every
button.

The fast-available fields (status/model/effort/events/ctx/duration) come straight
off the enriched `ses.agents` row, so the swap is instant on click; the drill-down
fetch (`/api/session/<sid>/agent/<aid>`) then feeds its `usage` **and** the
server-priced `cost` back up through `renderTimelineInto`'s `onData` callback,
and the scoreboard repaints with tokens + cost. Per-agent **cost** is stamped
server-side by `_stamp_agent_cost` (`accounting.cost_usd` over the agent's usage +
last model) — the ONLY per-agent cost figure there is, since OTEL `costs()` is
aggregate by `query_source` (main/subagent/auxiliary), never attributable to a
single `agent_id`. `agentFocus` is cleared by any full `renderSessionChrome` (a
tab switch or a return to the list), and the SSE `stats`/`costs`/`ctx` events that
keep flowing are absorbed by the `updateStatsRow` branch — they repaint the agent
view, never clobber it back to the session.

## The live ⚠ error badge

The stats-row ⚠ chip and the errors-tab count are the web sibling of the
scorebar's errwatch chip: count-only on the fast path (`sessionapi.error_count`
is a chain-aware `COUNT(*)`, not `len(errors())` hauling every traceback),
pushed as an `errors` `{count}` SSE event on the same only-on-change slow
cadence as `agents`/`costs`/`running`, with the full rows staying behind
`/api/session/<sid>/errors`; `app.js` patches the chip and the tab count in
place and re-fetches the errors list only when that tab is open and the count
grew.

## Codex runs in the agents list

A session's codex runs ride the same agents list and drill-down, with no
dashboard-side special-casing: `sessionapi.agents()` merges the audit
`streams` rows of `kind='codex'` in the same row shape (kind `codex`, `desc`
= the run label, `agent_id` = `sessionapi.codex_aid()` — synthesized from the
stream's src_path basename, since codex tailers record no hook agent_id), and
`/api/session/<sid>/agent/<aid>` reaches the codex `plugins.activity()`
provider (`plugins/codex/rollout.timeline` — the same timeline dict shape as
the claude one, see [sessionapi.md](sessionapi.md)). A companion job's `.log`
run shows a card but has no parseable rollout — its drill-down renders the
"no recorded activity" empty state, same as a transcript-less husk.

## Design language

Hermes-harness-inspired (Nous Research's Hermes Agent dashboard): the whole
theme derives from a 3-color palette via CSS `color-mix()` — near-black
canvas, one midground accent tinting text/borders/hovers alike — plus a warm
radial glow vignette and a film-grain noise overlay; borders are 1px INSET
accent-tinted hairlines (box-shadow), never drop shadows. Retuning the theme
is editing `--bg`/`--mid`/`--warm-glow` in `style.css`. Status and semantic
hues are NOT part of the derivation — they stay the terminal system's own
(`core/tabs.py` COLORS, `core/ops.py` semantic table) so the web and the kitty
mirror read as one system.

**State tint.** The tab state doesn't stop at the badge dot: the whole surface
washes with the state hue — the session cards on the main list, the session
header (the web scoreboard: title line, stats row, ctx bar), and the agent
cards — as a soft 135° gradient (≈13% → 3% → transparent, layered over the
normal panel background) plus a state-tinted inset hairline replacing the
neutral `--card` one. One custom property drives it: `--state`, defaulting to
`--idle` grey and remapped by `[data-tab=…]` (busy magenta · executing blue ·
asking red · your-turn green — same buckets as the badge dot); everything else
derives via `color-mix()`, so the wash stays subtle on the near-black canvas.
The attribute is stamped by `sessionCard()`/`renderSessionChrome()` and kept
live by `setBadge()` (which re-stamps the enclosing `.shead` on every `tab`
SSE event — but is skipped while a subagent is focused, see below); the list
cards re-stamp on each global-snapshot re-render. Agent
cards key off agent STATUS instead (`data-st` from `agentStatus()`: running
blue, done green, cancelled/crashed red, unknown amber) since a subagent has
no tab of its own — and when you **drill into** one, the session header itself
switches to that `data-st` (`setBadgeAgent` swaps `data-tab`→`data-st` on the
badge and `.shead`, `.badge[data-st]`/`.shead[data-st]` rules), so the pill and
wash track the focused agent's status rather than the session tab (*Subagent
scoreboard swap*). The tint made the "live" chip redundant — it's gone from
both the session cards and the header; only the inactive states still label
themselves (`parked`/`gone`).

**The header scrolls with the page.** The session header (the web scoreboard)
is deliberately NOT sticky — it was once pinned under the top bar, but a
tall header (title + two action rows + stats + ctx bar) hogged viewport over
the conversation. Only the global chrome stays pinned: the top bar, the
session strip, and the agents rail (sticky beside the stream, yielding to the
strip via `body.attn-on .rail`). Nothing becomes unreachable when the
header scrolls away — the control gestures are document-level (Esc =
interrupt, etc.) and the session strip + toasts still surface state — so
don't re-pin it as a "fix"; if the mouse path to ■ stop ever matters, the
answer is a collapsing slim bar, not restoring the full sticky header.

## Mobile / iPad

The layout is width-clean at every iPad viewport (probed headless-WebKit at
13"/11"/mini portrait + landscape: zero `scrollWidth` overflow on the list and
session views) — what actually broke iPad was **zoom, not layout**: iPadOS
Safari auto-zooms the page ~1.3× whenever focus lands on a text control whose
font-size is under 16px, the zoom never resets on blur, and it survives
rotation. The dashboard's inputs were 12–12.5px and `app.js` auto-focused the
composer on every session open — so opening any session zoomed the page
(horizontal panning in portrait, "mysteriously zoomed in" after rotating).
Three rules keep the bug class out:

- **No focused text control under 16px on touch.** The
  `@media (pointer: coarse)` block at the bottom of `style.css` bumps every
  focusable box (`.cinput`, `.finput`, `.nsinput`, `.askother`, `.renamein`)
  to 16px; a new text control must join that list. Desktop keeps the dense
  12.5px. Belt-and-braces, the viewport meta carries `maximum-scale=1`, which
  suppresses only the *automatic* focus zoom — Safari has ignored
  `maximum-scale` for user pinch gestures since iOS 10, so accessibility zoom
  still works.
- **No unasked-for `.focus()` on touch.** Every non-user-initiated focus site
  (view-open composer focus, new-session form focus, post-send refocus) is
  gated on `!IS_IPAD` — besides the zoom, each one pops the on-screen keyboard
  over the content. User-initiated ones (tapping ✎ rename, "chat about this")
  stay.
- **No hover-only affordances.** Touch has no hover: `@media (hover: none)`
  keeps the hover-revealed controls (the ⧉ copy links, the prompt bubbles'
  rewind ↶) permanently visible. A new `opacity: 0`-until-hover reveal needs a
  `hover: none` override in the same commit.

The rest of the touch section is ergonomics, not bug-fix: `touch-action:
manipulation` on `html` (kills double-tap smart-zoom on fold headers/tabs;
pan and pinch still work), tap targets grown toward the 44px HIG guideline
under `(pointer: coarse)` (36–40px effective — padding grows the hit area,
not the type), `viewport-fit=cover` + `env(safe-area-inset-*)` gutters on
`#top`/`#accounts`/`#attn`/`#view`/`#toasts` (the shared `--gx` gutter var,
12px under 900px; the top inset is its own `--sat` var — `env(safe-area-inset-top)` —
added to `#top`'s top padding AND to every sticky element pinned below the
header (`#attn` at 47px, `.rail` at 59/93px) so the whole stack clears the
notch instead of the brand/buttons hiding under the status bar), `interactive-widget=resizes-content` + `40dvh` grow caps so the
keyboard resizes the layout instead of hiding the composer, and — below the
1000px `.split` breakpoint — the agents rail flips from a sticky sidebar to a
horizontally swipable card strip *above* the stream (`order: -1`; its DOM
position would otherwise bury the agent cards below a long stream).

**Don't rebuild DOM that didn't change** (added 2026-07-19, from a "text
selection vanishes after ~1s on iPad" report). iOS Safari drops an in-progress
selection when the layout reflows, and `updateStatsRow` tore the scoreboard
down (`sr.textContent = ""`) and rebuilt it on every `stats`/`costs`/`ctx` SSE
tick — several times a second during an active turn. It now gates on a content
`statsSig` (all the shown numbers, EXCLUDING the live `⏱` elapsed, which is
`Date.now()`-derived) and skips the teardown when nothing the row shows changed;
a fresh (empty) row resets `_statsSig` so the first paint always runs. This
kills the redundant rebuilds (a clock-only or no-op tick); genuine number
changes still rebuild, so a selection during heavy token streaming can still be
dropped — the deeper fix would be per-chip in-place text updates.

## Testing

`tests/test_l0_dashboard.py`: opshtml contract tests (escaping, SGR/OSC8,
copy-link specs, lex/num gut bodies, and the rich tool renders — Bash
highlight, Edit diff with escaped content, Write cap, Read one-liner, deflist,
unknown-tool fallback), the server on an ephemeral in-process
port (never through `serve()` — no singleton lock in tests) against data
seeded via the real product APIs, and the notification watcher's transition
logic. The lazy-backlog tests assert the tail limit + `oldest` cursor, that
`/history` chains to exhaustion with the slices concatenating to the unlimited
merge (no gap, no overlap), and that a straddling group is never duplicated.
Import safety for both modules rides `test_import_safety.py`.
