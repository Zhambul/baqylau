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
| `/api/accounts` | `[{slug, label, alias, usage}, …]` — the launchable subscription accounts (`plugins.accounts`) plus each one's freshest captured 5h/7d usage (aggregated across sessions); backs the new-session picker and the top usage strip |
| `/api/commands?cwd=<dir>` | the "/" menus: `[{name, desc, src}, …]` — CLI built-ins + the directory's discovered `.claude` commands/skills (`plugins.slash_commands`); cwd-keyed, not sid-keyed — the new-session form completes for a directory with no session yet (non-directory → built-ins + user-level) |
| `/api/session/<sid>/view/<gid>` | rendered click-to-view stash (HTML) |
| `/api/session/<sid>/copy/<gid>/<what>` | copy text (`core/copy.collect`) |
| `POST /api/session/<sid>/message` | **control plane:** `{"text"}` → type it (+ Enter) into the session's kitty window (`Frontend.send_text`); replies `{ok, queued, tab}` — `queued: true` when the send landed mid-turn in Claude Code's own message queue (`QUEUE_TABS`); 409 headless, 400 empty, 503 no terminal |
| `POST /api/session/<sid>/stop` | **control plane:** close the session's kitty tab (`Frontend.close_tab` — a graceful stop: Claude Code exits on the HUP and SessionEnd runs the normal lifecycle); 409 headless, 503 no terminal |
| `POST /api/sessions/new` | **control plane:** `{"cwd", "account"?, "resume"?, "continue"?, "model"?, "effort"?, "prompt"?}` → launch `<account-alias> [--resume sid \| --continue] [--model m] [--effort e] [prompt]` in a new tab at `cwd` (`Frontend.launch_tab`); `account` is a switcher slug → its vetted alias command word (default `claude`); 400 bad cwd/model/effort/resume/account, 503 no terminal |
| `/events` | global SSE: `sessions` snapshots on change + `notify` toasts |
| `/events/session/<sid>?after=N&mpos=M` | per-session SSE: `ops`/`msgs`/`stats`/`agents`/`costs`/`tab`/`errors`, each on change; a fresh connection's first `ops` event is the merged backlog, tail-limited, carrying `oldest` (see below) |
| `/events/agent/<sid>/<aid>?pos=N` | one agent's LIVE timeline SSE: `entries` (new increment entries) + `resolve` (cross-increment tool results), from byte cursor `N` (see below) |

SSE is plain polling server-side (`TICK_S` per session, `GLOBAL_TICK_S`
global) pushed over a held response — no websockets dependency, and
`EventSource` gives the client reconnect for free (the app reconnects with
`?after=<last seen op id>` so nothing repeats).

## Control plane (web writes)

The dashboard was born read-only; these POST endpoints deliberately break
that charter so you can drive a session from the browser: **message a running
session**, **interrupt its turn** (an Escape key press), **close one** (its
whole tab), and **launch a new one** (fresh, `--continue`, or `--resume`).
None writes session state — they reach
the TERMINAL through the `Frontend` interface (`send_text` / `send_key` /
`launch_tab`, over
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
composer is disabled with a hint for it. Empty text is `400`. The text rides
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

**The "/" menu** (the composer AND the new-session form's first-prompt box —
one shared `slashMenu` helper in app.js). A leading `/` with no whitespace yet
opens a Claude-Code-style completion menu over `GET /api/commands?cwd=…` —
the composer keys it to the session's cwd (fetched once per view), the form
to whatever directory is currently typed (cached per dir). ↑/↓ move, Tab
completes, Enter completes a *partial* token but sends/launches when the
token already IS the selection (a fully-typed `/compact` goes through on one
Enter — both boxes pass `enterSends`), Esc closes. The menu drops BELOW its host box, never upward over
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
newline, and the textarea auto-grows with its content (`autoGrow`), capped
at `GROW_CAP` = 40% of the viewport (mirrored as `max-height: 40vh` in CSS)
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

`POST /api/sessions/new` `{"cwd", "model"?, "effort"?, "prompt"?}` validates
`cwd` is an existing directory (`os.path.isdir`, else `400`), `model` against
`_MODEL_OK` (one clean argv word — an alias like `opus` or a full id like
`claude-fable-5`; the form offers the aliases, the API takes any id) and
`effort` against `EFFORTS` (the CLI's `low`…`max` levels), then
`Frontend.launch_tab(cwd, launch_argv(["--model", m?, "--effort", e?,
prompt?]))` opens a new tab — the flags are just more positional `"$@"` words
ahead of the prompt, so the injection story is unchanged; the session then appears through its
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
is `False`, `_frontend()` returns `None`, and every control-plane endpoint
returns a clean `503`, never a 500 traceback.

**Liveness = an OPEN tab, not a lingering state DB.** A session's `live` flag
is *not* just "its `/tmp` state DB exists" — that only means the session was
never PARKED, and a tab closed WITHOUT a SessionEnd (crash / `kill -9`, or a
leaked test DB) leaves the state DB intact, so the session would masquerade as
running with a `kitty_window_id` that kitty has since REUSED for an unrelated
tab. Both payloads therefore reconcile against `_live_windows()` — one
`kitten @ ls` (memoized `_LIVE_TTL`) mapping each pane's `claude_session=<sid>`
user-var → its window id, the authoritative "which sessions have an open tab".
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
two-step confirm (first click arms for 4 s, second fires); a parked session
shows a **resume** button there instead, which opens the new-session form
preset to `--resume <sid>`.

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
unless any real signal (tab-state movement, transcript growth over the
press-time size) appears within its 2s grace.

`POST /api/session/<sid>/rewind` presses **Escape twice** — Claude Code's
double-Esc gesture, identical to its `/rewind` command: on empty input it
opens the rewind/checkpoint menu (restore code and/or conversation,
summarize from/up to a point; checkpoints are automatic, one per user
prompt — code.claude.com/docs/en/checkpointing.md). The menu opens **in the
terminal** — the web mirrors the key presses, navigating it happens in the
kitty tab (the toast says so). Two SEPARATE `send_key` calls with a
`REWIND_GAP_S` (500 ms) beat: the TUI's double-press detection wants two
discrete key events at HUMAN speed — one send-key call batches its keys
into a single press,press,release,release burst, and a 150 ms gap shipped
and the TUI sometimes missed the second press; ~500 ms is the hand-pressed
cadence observed opening the panel live, and Claude Code's second-Esc
acceptance is state-based and generous (no documented timing window), so
slower is safe. Same guard chain, window discipline and `escape-recheck`
backstop as `/interrupt` (mid-turn the first Esc interrupts — the TUI's
own semantics); audited as `web-rewind` (`{win, ok, tab}`, `ok` = both
presses accepted). The page wires it as the **↶ rewind** button. The
session view's **Esc key** deliberately does NO double-press detection of
its own: Claude Code's second-Esc window has no fixed timing, so any
client-side window either under- or over-shoots it — a 350 ms batching
hold shipped and mismatched in BOTH directions (web said rewind while
kitty saw nothing; web said double interrupt while kitty rewound). Every
Esc press streams to the terminal immediately as its own `/interrupt`
POST, making the TUI the ONLY double-press detector — kitty's outcome is
right by construction — and the page's labelling is presentation only: a
second press within `ESC_SEQ_MS` (1.5 s) gets the "Esc ×2 — rewind"
toast, a first press on a busy tab gets "interrupted", on an idle one
"Esc sent". A double press does spawn two escape-rechecks when magenta —
harmless (both verify silence; the second usually bails "state moved
on").

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
freshest snapshot across that account's sessions), polled slowly and hidden until
some account has usage. The `web-launch` audit row records the chosen `account`.

## Grouping and titles

The sessions view groups by DIRECTORY (cwd — the audit `sessions` row),
groups ordered by their newest session; the directory name lives on the group
header, so the card itself is titled by the SESSION's name. That name comes
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
