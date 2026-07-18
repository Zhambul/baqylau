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
  GET surface is pure read (the ⧉ copy endpoint *returns* text; the browser owns
  its clipboard). The only writes are the control-plane POSTs (*Control plane
  (web writes)* below), which type into / launch a terminal and are guarded
  against the browser cross-origin vector.
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
  a cached value newer than its sig (re-read next tick), never staler. The
  other historical poll-path sink was `sessionapi.sid_chain()`'s adopt-map
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
finish chip — and the full detail lives in the per-agent drill-down
(`plugins.activity()`), which reads transcripts, not ops. Why filter at
render, not at write: the terminal mirror must keep painting everything
(same ops table, two presenters), and the stamp doubles as provenance in the
audit's op rows. Pre-stamp history (parked DBs) has no `src` and renders as
before — the client's heuristic `agents` filter chip still covers those.

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
click-to-view. Any other URL becomes a plain `target=_blank` anchor.

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
| `/api/accounts` | `[{slug, label, alias, usage}, …]` — the launchable subscription accounts (`plugins.accounts`) plus each one's freshest captured 5h/7d usage (aggregated across sessions); backs the new-session picker and the top usage strip |
| `/api/commands?cwd=<dir>` | the "/" menus: `[{name, desc, src}, …]` — CLI built-ins + the directory's discovered `.claude` commands/skills (`plugins.slash_commands`); cwd-keyed, not sid-keyed — the new-session form completes for a directory with no session yet (non-directory → built-ins + user-level) |
| `/api/session/<sid>/view/<gid>` | rendered click-to-view stash (HTML) |
| `/api/session/<sid>/copy/<gid>/<what>` | copy text (`core/copy.collect`) |
| `/api/dictate` | `{available}` — Deepgram key-file probe; the page renders mic buttons iff true (*Web dictation* below) |
| `POST /api/dictate/token` | **control plane:** `{"sample_rate"}` → `{token, expires_in, ws_url}` — a ~30s Deepgram grant JWT + the fully-assembled live-listen URL; the browser connects to Deepgram DIRECTLY (*Web dictation* below); 400 bogus rate, 501 no key, 502 grant failed |
| `POST /api/session/<sid>/message` | **control plane:** `{"text"}` → type it (+ Enter) into the session's kitty window (`Frontend.send_text`); replies `{ok, queued, tab}` — `queued: true` when the send landed mid-turn in Claude Code's own message queue (`QUEUE_TABS`); 409 headless, 400 empty, 503 no terminal |
| `POST /api/session/<sid>/command` | **control plane:** `{"cmd", "arg"?}` → the scoreboard's quick-command row (*Web quick commands* below): a FIXED vocabulary of the TUI's own slash commands — `compact` (argless), `model` (arg: `_MODEL_ARG_OK`), `effort` (arg: `EFFORTS`) — pasted like a composer send; model/effort auto-answer the TUI's switch-confirm menu (`dashboard/confirmdialog.py`, non-queued only); replies `{ok, queued, tab, confirm?}`; 400 off-vocabulary, 409 headless or a dialog open (red tab), 503 no terminal |
| `POST /api/session/<sid>/stop` | **control plane:** close the session's kitty tab (`Frontend.close_tab` — a graceful stop: Claude Code exits on the HUP and SessionEnd runs the normal lifecycle); 409 headless, 503 no terminal |
| `POST /api/sessions/new` | **control plane:** `{"cwd", "account"?, "resume"?, "continue"?, "model"?, "effort"?, "prompt"?}` → launch `<account-alias> [--resume sid \| --continue] [--model m] [--effort e] [prompt]` in a new tab at `cwd` (`Frontend.launch_tab`); `account` is a switcher slug → its vetted alias command word (default `claude`); 400 bad cwd/model/effort/resume/account, 503 no terminal |
| `POST /api/session/<sid>/rename` | **control plane:** `{"name"}` → append the `agent-name` naming record to the session's transcript (`plugins.set_session_title` — the `/rename` channel, docs/session-naming-findings.md) and, when a live window exists, `Frontend.set_tab_title` (*Web rename* below); works for live AND parked sessions; replies `{ok, title, tab_retitled}`; 400 empty name, 409 no transcript / unsupported (a codex rollout), 502 append failed |
| `POST /api/session/<sid>/…` | **control plane**, each with its own section below: `interrupt` (Esc in the session's window), `rewind` (mid-turn cancel-edit, the double-Esc), `rewind-to` (*Web rewind* — the full checkpoint restore), `answer` (*Web ask* — AskUserQuestion), `plan-options` + `plan-decision` (*Web plan mode* — ExitPlanMode) |
| `/events` | global SSE: a `hello` (the server's `BOOT_ID` — the EventSource auto-reconnects across a server restart, and a changed boot id tells an OPEN page its loaded JS may be stale; the client toasts "dashboard updated — refresh", click to reload. Twice a redeploy shipped under an open page and its old handlers running against the new server read as a product bug), then a full `sessions` snapshot on connect + on membership/order change, `sessions-delta` `{rows}` for content-only changes (paused-blind per-row diff, wire-stripped rows — *The list renders once, then patches* below) + `notify` toasts |
| `/events/session/<sid>?after=N&mpos=M` | per-session SSE: `ops`/`msgs`/`stats`/`agents`/`costs`/`ctx`/`git`/`title`/`running`/`tab`/`errors`/`ask`/`plan`/`tasks`, each on change; a fresh connection's first `ops` event is the merged backlog, tail-limited, carrying `oldest` (see below) |
| `/events/agent/<sid>/<aid>?pos=N` | one agent's LIVE timeline SSE: `entries` (new increment entries) + `resolve` (cross-increment tool results), from byte cursor `N` (see below) |

SSE is plain polling server-side (`TICK_S` per session, `GLOBAL_TICK_S`
global) pushed over a held response — no websockets dependency, and
`EventSource` gives the client reconnect for free (the app reconnects with
`?after=<last seen op id>` so nothing repeats).

## Control plane (web writes)

The dashboard was born read-only; these POST endpoints deliberately break
that charter so you can drive a session from the browser: **message a running
session**, **interrupt its turn** (an Escape key press), **close one** (its
whole tab), **launch a new one** (fresh, `--continue`, or `--resume`), and
**rename one**. All but one reach
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
- **A custom header required** (`X-Claude-Dash: 1`) — a header a `<form>` or a
  simple `fetch` cannot set, independently forcing the preflight; absent is
  `403`.
- **We answer `OPTIONS` with a bare `501`** (no `do_OPTIONS`, so no
  `Access-Control-Allow-*` headers ever) — the forced preflight therefore fails
  and the browser never sends the real POST. Same-origin requests never
  preflight, so the dashboard's own page is unaffected.
- **Origin allow-list** as defense in depth — any `Origin` header present and
  not `http://127.0.0.1:<port>` / `http://localhost:<port>` is `403`.
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
TUI's queue; the web cannot unqueue it). `awaiting-command` (red) is
deliberately NOT in `QUEUE_TABS`: a dialog is up and typed text goes to the
DIALOG, not the input box — a send then is neither immediate nor queued, and
claiming "queued" would be a lie.

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
stays in the box — nothing is lost on a failed wake. The heavyweight action
stays deliberate by wording alone: from the iPad, "resume & send" opens a
real kitty tab on the laptop — the label is the consent. Reused, not new:
the launch is the form's own audited `web-launch` path (the row carries
`resume` + `account` + `ok`), so there is no new server surface and no new
audit row kind. Headless-live sessions (live, no window) stay disabled —
they aren't asleep, resume is the wrong medicine — and their mic button is
now honestly `disabled` (dim, inert) instead of a live-looking button that
swallowed clicks (`ta.disabled` guard in `dictation.start`).

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

**⌃⇧←/→ cycle through live sessions** — kitty's next/previous-tab keys,
mirrored: the cycle is the LIVE sessions ordered oldest-first (creation
order, the same order kitty's tab bar holds them in), wrapping at the ends;
from the list view or a parked session (nowhere in the cycle) → enters at
the oldest live session and ← at the newest. Works with focus anywhere,
including a text box — macOS claims ⌃←/→ for Spaces but nothing claims
⌃⇧←/→, so the only thing shadowed is a selection gesture that already
lives on ⌥⇧/⌘⇧.

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
own `SessionStart` (no synthetic row).

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
for, keeping the user's cursor in the claude window; background cost: the
pane holds inner focus until the user clicks back, strictly better than
stealing app focus), and `kitten_launch_tab` never passes it. `kitten @
focus-window` cannot substitute as a restore — it, too, raises the OS window
of a background kitty (`set_active_window(switch_os_window_if_needed=True)`).

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
row; `409` when no other account qualifies. Full mechanics + the
manual/auto differences: docs/relimit.md *Manual migrate*.

`POST /api/session/<sid>/rewind` mirrors Claude Code's double-Esc, whose
MEANING depends on session state — and the endpoint splits on the tab
state at gesture time:

- **MID-TURN** (a `BUSY_TABS` colour): double-Esc CANCELS the running work
  and restores the last message into the input for editing (removing it
  from the conversation). Mirrored with **two Escape key events**
  `DOUBLE_ESC_GAP_S` (150 ms) apart — measured **3/3 reliable** mid-turn
  on a live session (2026-07-18), unlike the idle menu — plus the same
  magenta `escape-recheck` (that experiment showed the tab stays stuck
  `thinking` after the cancel). Editing then happens in the kitty tab.
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
- **Display decay is accepted:** once the rename record scrolls more than
  `TITLE_TAIL_B` (64KB) behind EOF, the bounded tail scan falls back to the
  newest `ai-title` (the parser's documented one accepted gap) while the
  `--resume` picker (a full read) keeps the custom name. Renaming again
  re-appends at EOF and wins again.

Every post-validation attempt is a **`web-rename`** `state_files` row
(`{win, chars, ok, tab, tab_retitled, reason?}`); an append failure is also
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

**Resume / continue.** The new-session form's "start from" picker maps to the
CLI's own conversation-pickup flags: `continue` → `claude --continue` (the
directory's most recent conversation), `resume: <sid>` → `claude --resume
<sid>` — the resume options are the chosen directory's known sessions from the
current snapshot (title + age), rebuilt as the directory field changes. The
server validates `resume` against `_SID_OK` (one clean argv word, the same
alphabet as the sid routes) and rejects `resume`+`continue` together (400,
like the CLI); both ride as positional `"$@"` words ahead of
`--model`/`--effort`/prompt, so the injection story is unchanged. A resumed
conversation **forks to a new sid** (CLAUDE.md: resume forks) — but NOT at
launch: SessionStart fires under the OLD sid (restoring its parked DB, so
that sid flips parked→live), and the fork happens at the first event after.
The adopt machinery handles the state hand-off as always; the jump watch must
target the OLD sid (see below — "new sid in the cwd" alone shipped broken
once).

**Jump to the new session.** The launch response carries no session id — none
exists yet (the session appears through its own `SessionStart`; the server
deliberately returns no synthetic row, and inventing one would desync the
list). So the *client* watches: on a successful launch `app.js` stashes the
known sids, the currently-LIVE sids, and the launched cwd (`armJump`), and
every following global `sessions` snapshot is checked (`checkJump`). What
counts as a hit depends on the start mode — **fresh**: a never-seen live sid
in that cwd; **resume**: *that* sid coming back to life (matched by sid, not
cwd — you can resume into a different directory); **continue**: any
already-known cwd-row flipping parked→live (which conversation `--continue`
picks is the CLI's own history's business). The `liveAtArm` set is what makes
the last two work — a plain "new sid" check misses them, because resume and
continue re-animate an EXISTING sid at SessionStart and only fork to a new
one at the first event after (this shipped broken once). The watch is
cancelled when the user opens any session themselves (`route()` clears it on
user navigation — `checkJump` disarms *before* setting the hash so its own
jump doesn't read as one) and by a 120 s timeout, so a launch that never
produces a session (claude failed to start) can't yank the browser somewhere
minutes later.

**Audit.** Every attempt lands a `state_files` row: `web-send`
(`{win, chars, ok, tab}` — `tab` is the state at send time, so "my message
vanished" is answerable as "it queued mid-turn"; keyed to the session's
state-DB path) and `web-launch`
(`{cwd, model, effort, resume, cont, ok}`, no session yet so log/path are
empty), `web-stop` (`{win, ok}`) and `web-interrupt` (`{win, ok, tab}` —
the tab state at press time says what the Escape landed on). Failure paths
(no window, no terminal, send/launch/close/key returned false) also write an
`A.error` per the audit-before-swallow rule, so a "my message never arrived"
report is answerable from the DB.

## Web ask (`POST /api/session/<sid>/answer`) — AskUserQuestion from the browser

When Claude asks a question (the AskUserQuestion tool), the session view
grows an **ask card** above the composer mirroring the TUI dialog: one
block per question (the header chip + question text + a dim
"pick one"/"pick any" hint), option buttons whose leading mark makes the
select mode legible at a glance (a radio circle for single-select, a
checkbox square that fills with a ✓ for multiSelect), a free-text "type
your own" input per question (the dialog's "Type something" row), a
submit row, and **chat about this** (the dialog's own
decline-and-discuss). Submission is ALWAYS the explicit submit button
(or Enter in a free-text row) — a lone single-select question does NOT
submit on the option click itself. That one-keystroke feel is right for
the TUI's digit press but wrong for the web: a misclick would fire the
answer with no chance to reconsider, so the card favors
review-before-send (selections stay editable until submitted).

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

**Answering** drives the TUI's own dialog — `dashboard/askdialog.py`,
the rewindmenu philosophy (screen-verified key events, never a blind
press) but deliberately NOT unified with it: different anatomy, and
OPPOSITE bail semantics — rewindmenu bails by pressing Escape, while
here **Escape declines the whole question set**, so a failed step leaves
the dialog exactly as it was (AskError → 409 with `step`; a retry
re-normalizes). The measured key model it encodes:

- single-select: a digit answers and auto-advances; the sole question of
  a one-question ask submits the tool outright (no review pane);
- multiSelect: a digit TOGGLES its checkbox — so the driver DIFFS the
  desired selection against the checkboxes the screen actually shows
  (boxes the user pre-toggled in the terminal are reconciled, not
  re-pressed); `right` from a non-edit row moves to the next tab;
- free text: arrow onto the "Type something" row (navigated by its ROW
  NUMBER, `len(options)+1` — the label mutates to whatever was last
  typed), type (the text replaces the label inline), then Enter:
  single-select selects+advances, multiSelect toggles the custom row
  checked (measured: the typed CR alone commits but does NOT check);
  then leave the edit row with `up` before any tab navigation (on the
  edit row left/right move the TEXT cursor);
- `left` at the first question is a no-op, so `left`×len(questions)
  deterministically normalizes to question 1 from any state (including
  the review pane, including a half-answered dialog);
- each question is verified CURRENT by finding its text in the dialog
  region — ALL whitespace stripped from both sides before the substring
  match, because long question text wraps across screen lines and a
  wrap can land mid-word (a hyphenated path); a real 555-char question
  never matched the original exact line-set lookup (the live `question
  1 never became current` bail, 2026-07-18). The review pane is
  excluded explicitly (`current_question` → None on "Review your
  answers") since its answer recap repeats every question's text;
- the review pane ("Review your answers") follows the last question;
  digit `1` = "Submit answers". PostToolUse then fires with `answers`
  {question → label, ", "-joined labels (custom text joins as a label),
  or the free text} — verified live for every shape: single label,
  free-text-only, two-question mixed with custom multi text
  (`{"Pick a planet": "Venus", "Pick metals": "Iron, Zinc, titanium"}`),
  and chat-about-this.

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
  server-side (model, formatting, one `keyterm=` per line of
  `~/.config/deepgram/keyterms` — `#`-comments dropped, capped at
  `KEYTERMS_MAX`). The client contributes ONLY its AudioContext sample
  rate. The long-lived key never leaves the server process — not in a
  response, an audit row, or an error detail.
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
the new-session form dictates too), `{ok, rate, keyterms}` on success,
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

The picker's **default selection load-balances across subscriptions**: the form
preselects the account with the most 5-hour headroom (lowest effective
`five_hour` used %; ties keep registry order). "Effective" because a snapshot
whose `five_hour_reset` has passed — or, when the reset time is unknown, one
older than the 5h window itself — means the window rolled over and counts as 0
used; an account with no snapshot at all has had no recent traffic and also
counts as 0. That arithmetic is SERVER-computed and served as each account's
`five_hour_eff` (`core/sessionapi.effective_five_hour` — the single owner,
because the rate-limit migration's target picker needs the SAME number,
docs/relimit.md; app.js `fiveHourUsed` just reads the field). The suggestion is
recomputed when the fresh `/api/accounts` fetch supersedes the cached list, but
a manual pick (the dropdown's `onpick` hook) always wins and is never
overridden. (Historically this lived client-side in app.js; the migration
feature forced a Python owner, and two encodings of "effective" is exactly what
the single-owner rule exists to prevent.)

**Which account a chat runs under** is stamped into the session's state DB at
SessionStart (`split.cmd_open` → `state.kv_set("account", account.current())`,
read from the env contract — no token touched) and shown in the session header
(`◈ c2 · claude-01`) and the terminal scoreboard's id row.

**Usage limits (5h / 7d).** Claude Code exposes per-session rate-limit data to
exactly ONE place — the **status-line command's stdin** JSON
(`rate_limits.{five_hour,seven_day}.{used_percentage,resets_at}`), after each API
response. It is NOT in any hook payload, the transcript, or OTEL (all checked),
and the API endpoint that would return it needs a `user:profile` scope the
`setup-token`-minted account tokens lack (403). So the number is captured by
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
picker), polled slowly and hidden until some account has usage. The `web-launch`
audit row records the chosen `account`.

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
passed, or younger than one 5h window when the reset is unknown) and rendered
as a red `limit hit` chip + its reset countdown next to the usage bars.

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
`<root>/.git/worktrees/<name>`; `null` for a main checkout): that is the list
page's grouping key (*Grouping and titles* below) and the toast `project`
name, so a worktree session files under its project. HEAD at the resolved
gitdir gives the
branch (`ref: refs/heads/<b>`, or a 7-char sha when detached). The ancestor
walk + gitdir indirection is cached per cwd forever; HEAD itself is re-read on
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

The sessions view groups by PROJECT directory — `git.root || cwd` (cwd from
the audit `sessions` row, root from `git_info`, the git-chips section above):
a linked-worktree session files under the main checkout that owns it, not its
worktree dir, so N agents fanned out over `.claude/worktrees/*` of one repo
stay ONE group (the per-card `⋔` chip is what tells them apart, and the group
header's "+" launches new sessions at the main checkout). A parked session
whose worktree was since REMOVED degrades to its own cwd-keyed group
(`git_info` returns null once the `.git` file is gone — the branch chip drops
the same way). Groups are ordered by their newest session's `started_at`
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
can't render into a detached node.

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

Server-side the SSE polls `plugins.activity_since(sid, aid, pos)` at `TICK_S` —
the incremental companion to `activity()`, sharing timeline()'s per-record entry
builder (`transcript._fold_record`, the single owner of the record→entry
mapping). It returns `(entries, resolutions, new_pos)` and the SSE pushes two
event kinds: `entries` (the new increment's entries, server-enriched by the same
`_enrich_entries` the REST endpoints run — markdown/rich-tool HTML) appended at
the bottom (the timeline reads chronological top-down), and `resolve`
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

## Stream search + kind filters

The session view's mirror tab carries a filter bar directly above the stream: a
mono text input plus toggle chips (`all · commands · files · agents ·
messages`) and an `N of M shown` count. Text filtering is a debounced
(~150ms) case-insensitive substring over each top-level item's `textContent`
(folded block bodies included — `textContent` reads hidden children, so a match
in a collapsed command output still counts without force-opening it). Filtering
never removes DOM (SSE keeps appending); non-matching items get a `.fhide`
(`display:none`) class, applied in `appendItems` to newly arrived items too via
the shared `matchesFilter()` — so a live filter holds as the stream grows.
Filter state lives on `S.ses.filter` and is cleared when switching sessions
(a fresh `S.ses`).

Each top-level stream child is stamped with a `data-kind`
(`commands`/`files`/`agents`/`messages`) ONCE at creation in `appendItems`
rather than re-sniffed per filter pass — selector stability beats matching the
exact chip text, which drifts. Blocks default to `commands` and upgrade to
`agents` on an agent signal (an outer-gutter `.og` wrapper == a subagent's
nested job, or a block-opening chip that starts with a who-prefix rather than a
main-session command glyph `▶▷◉■` — subagent/teammate/codex chips lead with
their label/`codex`). Ungrouped items classify by item type: `msg` items are
`messages`, file-op one-liners (they carry a `data-v` click-to-view id) are
`files`, the rest `commands`. On a CURRENT session the `agents` chip mostly
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
prefixes the browser tab title (`(2) claude · dashboard`)
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
SSE event); the list cards re-stamp on each global-snapshot re-render. Agent
cards key off agent STATUS instead (`data-st` from `agentStatus()`: running
blue, done green, cancelled/crashed red, unknown amber) since a subagent has
no tab of its own. The tint made the "live" chip redundant — it's gone from
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
`#top`/`#attn`/`#view`/`#toasts` (the shared `--gx` gutter var, 12px under
900px), `interactive-widget=resizes-content` + `40dvh` grow caps so the
keyboard resizes the layout instead of hiding the composer, and — below the
1000px `.split` breakpoint — the agents rail flips from a sticky sidebar to a
horizontally swipable card strip *above* the stream (`order: -1`; its DOM
position would otherwise bury the agent cards below a long stream).

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
