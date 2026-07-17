# The web dashboard (`dashboard/` + `bin/claude-dashboard.py`)

A localhost web UI over the whole session estate: every session (live and
parked) with its mirror stream, scoreboard stats, agents, costs and errors —
plus the two things a terminal pane can't give you: **drill-down into any
agent's full activity timeline** and **toast/OS notifications across all
sessions** when a session starts asking you something or finishes its turn.

It is a CONSUMER, not a producer — read-only **except the control plane** (the
two write endpoints below): everything it *shows* comes through
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
  its clipboard). The only writes are the two control-plane POSTs (*Control plane
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
blockquotes, `http(s)` links, rules, paragraphs). Two rules dictate the shape.
The **no-build/no-deps rule** rules out a markdown library, so it is hand-rolled
(~150 lines). The **escape rule** (the `neutralize()` analog) rules out any
"escape later" design: block *structure* is detected on the raw lines (the
sigils `#-*>`` ``[]()` are ASCII and emit nothing themselves), but every
fragment that reaches the page is `html.escape`d at its leaf — `_md_inline`
escapes before layering emphasis, and a fenced block is highlighted through the
single lexer owner (`render.lexer` via `coderender.render_code`) to ANSI and
then `ansi_html` (which escapes), falling back to plain escaped text when
pygments/the lexer is absent. So `<script>` survives as escaped text in every
context, and a `javascript:` link renders as literal text (only `http(s)` URLs
become anchors). Malformed markdown never raises — the outer guard returns
escaped plain text. The timeline endpoints (`/activity`, `/agent`) add an
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
| `/api/sessions` | discovery list + per-row stats + tab state |
| `/api/session/<sid>` | overview: `session()` + error count |
| `/api/session/<sid>/ops?after=N` | `{last, html: […]}` server-rendered ops |
| `/api/session/<sid>/history?before=<opid>&blocks=N` | the previous `N` stream blocks OLDER than op id `before` (lazy backlog): `{oldest, items}`, `oldest` the next cursor (0 = exhausted) |
| `/api/session/<sid>/activity` | main-thread timeline (`plugins.activity(sid)`) |
| `/api/session/<sid>/agent/<aid>` | one agent's timeline (carries a `pos` byte cursor for the live SSE) |
| `/api/session/<sid>/errors` | swallowed-exception rows |
| `/api/session/<sid>/view/<gid>` | rendered click-to-view stash (HTML) |
| `/api/session/<sid>/copy/<gid>/<what>` | copy text (`core/copy.collect`) |
| `POST /api/session/<sid>/message` | **control plane:** `{"text"}` → type it (+ Enter) into the session's kitty window (`Frontend.send_text`); 409 headless, 400 empty, 503 no terminal |
| `POST /api/sessions/new` | **control plane:** `{"cwd", "prompt"?}` → launch `claude [prompt]` in a new tab at `cwd` (`Frontend.launch_tab`); 400 bad cwd, 503 no terminal |
| `/events` | global SSE: `sessions` snapshots on change + `notify` toasts |
| `/events/session/<sid>?after=N&mpos=M` | per-session SSE: `ops`/`msgs`/`stats`/`agents`/`costs`/`tab`/`errors`, each on change; a fresh connection's first `ops` event is the merged backlog, tail-limited, carrying `oldest` (see below) |
| `/events/agent/<sid>/<aid>?pos=N` | one agent's LIVE timeline SSE: `entries` (new increment entries) + `resolve` (cross-increment tool results), from byte cursor `N` (see below) |

SSE is plain polling server-side (`TICK_S` per session, `GLOBAL_TICK_S`
global) pushed over a held response — no websockets dependency, and
`EventSource` gives the client reconnect for free (the app reconnects with
`?after=<last seen op id>` so nothing repeats).

## Control plane (web writes)

The dashboard was born read-only; these two POST endpoints deliberately break
that charter so you can drive a session from the browser: **message a running
session** and **launch a new one**. Neither writes session state — they reach
the TERMINAL through the `Frontend` interface (`send_text` / `launch_tab`, over
the same silenced `kitten @` machinery the tab painter uses), and Claude Code's
own hooks then produce whatever state results. The dashboard stays a consumer of
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
- **Body cap** (`POST_MAX`, 64 KiB) and a JSON-object check; a guard rejection
  closes the connection (an unread body would desync HTTP keep-alive).

`POST /api/session/<sid>/message` `{"text"}` resolves the session's
`kitty_window_id` (`sessionapi.session_row`) and, when it has one,
`Frontend.send_text(win, text)` types the text plus a carriage return.
**Windowed sessions only:** a headless / `claude daemon run` session has no
window (same scoping as tab colours and toasts), so it returns `409` — the
composer is disabled with a hint for it. Empty text is `400`. The text rides
kitten's `--stdin` verbatim (no shell, no escape interpretation).

`POST /api/sessions/new` `{"cwd", "prompt"?}` validates `cwd` is an existing
directory (`os.path.isdir`, else `400`) and `Frontend.launch_tab(cwd,
launch_argv([prompt?]))` opens a new tab; the session then appears through its
own `SessionStart` (no synthetic row). **The argv is NOT a bare `["claude"]`**
— kitty execs launch argv with kitty's OWN environment, and a GUI-launched
kitty has no user PATH (`~/.local/bin` absent → command-not-found → the tab
flashes and closes while `kitten @ launch` still exits 0; this shipped once)
and no shell aliases (`claude` here IS an alias). `launch_argv` therefore runs
`$SHELL -lic 'claude "$@"' claude <prompt?>` — the user's interactive login
shell, i.e. exactly what typing `claude` in a fresh tab does (profile PATH, rc
aliases). Injection safety is preserved: the command string is FIXED and the
prompt rides as a positional `"$@"` arg, never interpolated. Non-POSIX `$SHELL`
(fish) falls back to `/bin/zsh` (`LAUNCH_SHELLS`). The server may have no resolvable kitty
socket at all (started outside kitty) — `frontends.get(resolve=True).usable()`
is `False`, `_frontend()` returns `None`, and both endpoints return a clean
`503`, never a 500 traceback.

**Audit.** Every attempt lands a `state_files` row: `web-send`
(`{win, chars, ok}`, keyed to the session's state-DB path) and `web-launch`
(`{cwd, ok}`, no session yet so log/path are empty). Failure paths (no window,
no terminal, send/launch returned false) also write an `A.error` per the
audit-before-swallow rule, so a "my message never arrived" report is answerable
from the DB.

## Grouping and titles

The sessions view groups by DIRECTORY (cwd — the audit `sessions` row),
groups ordered by their newest session; the directory name lives on the group
header, so the card itself is titled by the SESSION's name. That name comes
from `plugins.session_title(transcript_path)` — a path-keyed fan-out (the
list view already holds every row's path; 50 sid-keyed `session_row()`
resolutions per poll would be waste). The claude_code provider
(`transcript.session_title`) returns the last `summary` record in the head
window (Claude Code prepends them on resume) or, when none exists — this
setup stores no summaries; `conversation_summaries` in `__store.db` is empty —
the first line of the first REAL user prompt, which is effectively what the
`claude --resume` picker shows (`history.jsonl` `display`). `isMeta` rows and
`<command-*>`/`<local-command-*>` wrappers are plumbing, never titles. The
server caches titles by `(path, size)` — a title can only change when the
transcript grows. Agent cards follow the same rule: the Task description
(`desc` from the state DB's agents table) IS the agent's name; the raw
`agent_id` drops to the subtitle.

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

## Lazy backlog (a big session paints its newest slice instantly)

A long-running session's merged backlog is multi-MB of rendered HTML — sending
it all in the first SSE `ops` event stalls the paint. So the initial event
carries only the NEWEST `TAIL_BLOCKS` (80) stream **blocks**, and older history
loads on demand.

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
hides once `/history` reports `oldest == 0`.

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
`files`, the rest `commands`.

## Notifications (the toaster)

One daemon thread diffs the ENTIRE tab table (`sessionapi.tab_states()` — the
whole-table reader added for exactly this; per-window probes would be N
queries for one snapshot) once a second, and maps windows to sessions via the
audit `sessions` rows' `kitty_window_id` (newest session wins the window — a
kitty window outlives sessions). A transition INTO `awaiting-command` (red —
Claude is asking you) or `awaiting-response` (green — done, your turn) pushes
a `notify` event to every `/events` client; the app shows an in-page toast
always and an OS `Notification` when the page is hidden. The first scan is a
baseline, never news. Windowless sessions (headless/daemon) produce no
toasts, same as they have no tab colour — that's the tab system's own
scoping, not a dashboard limitation.

**The attention bar is the persistent complement to the toasts.** Toasts are
transient (a 7s slide-in on the transition); the bar is the standing view of
what needs you *right now*. A slim hairline bar pinned under the header on
every view (`#attn` in index.html — a fixed container outside `#view`, so it
survives the router's re-renders) lists every LIVE session whose tab state is
`awaiting-command` as a red pulsing pill (`--ask`, the badge's own dot
animation) and every `awaiting-response` session as a quieter green pill
(`--done`) after them; it is `hidden` entirely when nothing needs attention,
and when it shows, `body.attn-on` drops the session view's sticky `.shead`
below it so the two never overlap. It is fed by the same global `sessions` SSE
snapshots the app already holds (`renderAttention()` reruns on every snapshot)
plus the open session's per-session `tab` SSE event, which patches that row in
place so the bar reacts before the next global snapshot lands. The count of
asking sessions also prefixes the browser tab title (`(2) claude · dashboard`)
and swaps the favicon to a red-dotted variant, so a backgrounded tab still
shows the ask count. The currently-open session's own pill is de-emphasized
(it's the one you're already looking at) but still shown, for consistency.

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
