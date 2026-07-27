# The web dashboard (`dashboard/` + `bin/claude-dashboard.py`)

A localhost web UI over the whole session estate: every session (live and
parked) with its mirror stream, scoreboard stats, agents, costs and errors —
plus the two things a terminal pane can't give you: **agent scope** — the whole
view re-pointed at any one subagent or teammate — and **toast/OS notifications
across all sessions** when a session starts asking you something or finishes its
turn.

It is a CONSUMER, not a producer — read-only **except the control plane** (the
write endpoints below): everything it *shows* comes through
`core/sessionapi.py` (the one read-side door — [sessionapi.md](sessionapi.md))
and the `plugins` registry fan-outs. It writes no session state directly; its only state
writes are its own singleton pid-lock and audit rows. The control plane does
not write session state either — it drives the TERMINAL (types into a window /
opens a tab) through the `Frontend` interface, and Claude Code's own hooks then
produce the resulting state. See *Control plane (web writes)* below.

```
bin/claude-dashboard.py     thin CLI shim — delegates to dashboard/cli.py
dashboard/cli.py            the CLI lifecycle: serve | start | stop | status |
                            open (holder/url/start/stop/status/open_browser +
                            the command dispatch) — importable/testable
dashboard/server.py         PUBLIC FACADE — re-exports the surface bin/ + tests
                            reach through `dashboard.server`; behaviour lives in:
dashboard/config.py         constants + env knobs (the one owner of the tunables)
dashboard/read/             the read model: meta (per-session title/git/ctx/goal)
                            · cache · lists (sessions/resumable/accounts/stats)
                            · session (one session's detail + ask/plan/composer
                            cards) · mirror (op-stream -> HTML backlog/history)
dashboard/notify/           presence signals (presence.py), the /events fan-out
                            bus (broker.py) + the tab-diff Notifier
                            / toast / off-device-alert fan-out (notifier.py)
dashboard/control/          launch.py — the terminal-facing control machinery
                            (Frontend resolver, live-window map, launch argv,
                            macOS focus/appearance watches)
dashboard/http/             the HTTP layer: base (send/SSE/guard/static + query
                            parsers) · get (GET read plane) · post/ (POST control
                            plane, a PACKAGE — below) · sse (the SSE streams) ·
                            handler (Handler = base+mixins, + serve())
                            BOTH planes route through registries (below)
dashboard/http/post/        the control plane, one module per concern, composed
                            into _PostMixin by its __init__ (which is the router
                            and nothing else): typing (message/command/stop/
                            rewind — the ones that reach a live TUI) · interrupt
                            (THE stop gesture + its screen-delta probes) ·
                            dialogs (the ask + plan cards) · state (drafts,
                            prefs, mutes, view mode, hidden dirs, push — no
                            terminal touched) · telemetry (the browser's
                            beacons + presence) · files (attachments, clipboard
                            paths, dictation grants) · session (new/migrate/
                            rename)
dashboard/opshtml/          paint ops -> HTML (the web presenter), split by concern:
                            ansi · ops · markdown · tools
dashboard/static/           the single-page app (vanilla JS/CSS, no build step) —
                            app.NN-*.js parts loaded in order (classic scripts,
                            one shared global scope; app.12-shell.js is the app
                            FRAME — the header buttons + the page-wide keyboard;
                            app.13-init.js runs last)
```

The dashboard was decomposed from two monoliths (a ~4800-line `server.py` and a
~900-line `opshtml.py`) into the packages above; `server.py` is now a thin
facade. Dependency direction (nothing imports the dashboard back): `config` <-
everything; `read` / `control` / `notify` import core+plugins+frontends+config;
`http` imports all of the above; `server` re-exports `http`. Patchable knobs and
cross-module helpers the handlers call are read MODULE-QUALIFIED (`config.X`,
`launch.X`, `presence.X`) so a test patches the one owning module.

The facade's contract is exact: a name lives in `server.py` only while something
actually reaches it through `dashboard.server`. A third of the original list was
reached by nobody — internals that had simply moved, still reading as supported
API. New code inside the package imports its owner directly; the facade exists
for the historical `DS.X` handles alone, and a re-export nothing consults gets
deleted rather than kept for symmetry. **A config KNOB is never re-exported
flat** (`DS.config` is the only handle): every reader of a live knob reads
`config.X`, so a flat alias is not just dead but a patch TRAP —
`monkeypatch.setattr(DS, "NOTIFY_DELAY_S", 0)` would bind a name nobody consults
and pass while changing nothing. Pinned by
`test_facade_re_exports_no_config_knob_flat`.

### Routing: both planes are registries

`http/get.py` and `http/post/` dispatch the same way (the styleguide's tables
over if/elif ladders):

| table | key | handler signature |
| --- | --- | --- |
| `_FIXED_GET` | the full path tuple — `("push", "config")` | `(self, url)` |
| `_SESSION_GET` | a one-segment session verb — `"ops"` | `(self, sid, url)` |
| `_FIXED_POST` | the full path tuple | `(self)` |
| `_SESSION_POST` | a one-segment session verb | `(self, sid)` |

Adding an endpoint is a table line plus a named method whose docstring is its
design note. The GET signatures are uniform so the dispatch stays a `getattr` —
most fixed handlers ignore `url`, and passing it always is cheaper than a
per-endpoint argument decision.

**The POST tables hold the handler FUNCTIONS, not method-name strings.** That is
what let the control plane split: a table that can only name methods of `self`
forces every handler into one class, and the POST plane had grown to 45 methods
across twelve unrelated subjects in a single 2000-line file. Registering
`_TypingMixin.post_message` instead of `"post_message"` lets the table span the
`post/` modules, and makes an unresolvable handler an ImportError at start-up
rather than a 500 on the one request that hits that row. (The mixins compose
rather than becoming free functions on purpose: handlers routinely need a helper
belonging to another concern — `post_message` calls files.py's
`_attachment_paths`, everything calls base.py's `_post_guard` /
`_reject_input` / `_audit_target` — and composition keeps each of those an
ordinary `self.` call instead of a threaded-through handler argument.) Pinned by
`test_post_registries_hold_functions_from_every_mixin`, which also checks every
row is reachable on the composed `Handler` and has the table's arity. What deliberately stays *explicit* (matched by
shape in `route`/`route_events`/`route_session`, not by table) is everything
whose trailing segment is a **name** rather than a verb — `/api/session/<sid>/`
`agent/<aid>`, `view/<gid>`, `copy/<gid>/<what>` — plus the `/events/*` streams,
where each form has its own arity *and* its own cursor query params.

The read plane was a 165-line if/elif ladder until 2026-07-25, in which the
three lines of actual routing were invisible between the arms' design comments
and the two "nothing here" 404s sat 100 lines apart. Two guards keep the tables
honest: `test_get_routing_registry_resolves` (every entry names a real handler
with the table's arity, every fixed endpoint answers 200, a miss is 404 not 500)
and `test_page_session_verbs_are_all_routed` — the cross-tier direction that
actually breaks, since a verb the page fetches but nothing routes is a silent
404 (an empty tab, a control gesture that never lands) with no handler to leave
an audit row.

`./bin/claude-dashboard.py` (default verb `open`) starts the server if needed
and opens `http://127.0.0.1:8377` (`CLAUDE_DASH_PORT` overrides).

## No emoji: the glyph vocabulary

**The UI carries NO emoji** — every marker is a monochrome text glyph, styled by
CSS like the rest of the page. This is a deliberate rule, not taste-of-the-day:
a colour-emoji font paints its own colours (ignoring the theme, and unreadable
against the dark/light washes both themes use), brings its own line-box metrics
(the attach button's paperclip is an inline SVG for exactly this reason — the
emoji made the button a different height than the mic beside it, see *Web
attachments*), and renders differently per platform, so the terminal-flavoured
look of the page fell apart on a phone. The vocabulary in use:

| glyph | meaning | glyph | meaning |
|---|---|---|---|
| `◆` | main agent | `◇` | subagent |
| `◈` | teammate / account chip | `◉` | monitors · alerts on |
| `○` | alerts muted | `◷` | background job |
| `❖` | memory-wiki note | `◎` | active goal (`✓` once met) |
| `✦` | model | `✧` | effort |
| `⊜` | compact | `⊘` | cancel |
| `■` | stop | `⇆` | migrate |
| `✎` | rename | `↶` | rewind |
| `▦` | stats | `▤` | attached file |
| `⧗` | queued message | `⧉` | copy |
| `⚠` | errors | `⋔` / `⎇` | worktree / branch |

**Picking a non-emoji glyph is only half of it — presentation is the other
half.** A codepoint can be a plain text symbol and *still* come out as a colour
emoji: everything in the table above is fine, but `⚠ ▶ ⚙ ✉ ⏱ ▪ ↩ ☀` carry the
Unicode **Emoji** property with a *text* default presentation, which means the
browser only paints them monochrome IF one of the page's fonts has the glyph —
otherwise it falls back to the system colour-emoji font. That is exactly what
happened to the header's keep-awake `☀` (monochrome in kitty, a colour sun in
the browser on Apple platforms). Two mechanisms, both cheap:

- **U+FE0E (VARIATION SELECTOR-15)** — the standard "render this as text"
  request — is appended to every emoji-capable codepoint on its way to the page:
  `opshtml.text_presentation()` at the module's escape leaf (`_esc`, so ALL op
  text, chip glyphs, and markdown go through it) and its twin `tp()` in app.js,
  applied inside `el()`/`tnode()` so every text node the app builds is covered.
  It lives in the PRESENTER, not the producers: `⚠ audit: <script>: …` is
  single-owned audited vocabulary asserted verbatim by the tests and quoted by
  docs/audit.md, and the terminal has no problem to fix — same reason this
  module html-escapes here rather than upstream. Both passes are idempotent.
- **An inline SVG** where the glyph is decoration rather than vocabulary and
  must be exact: the keep-awake sun (`#wakebtn .sunmark`) and the attach
  paperclip (`CLIP_SVG`). `currentColor` + a CSS size means they follow the
  theme and line up with neighbouring buttons — which the emoji never did.

The one place emoji survive is the OFF-device alert (the Telegram message and
the Web Push notification titles, `🔴 needs you` / `🟢 is done`, server.py) —
those render in someone else's UI (a chat client, an OS notification centre)
where a colour dot is the only styling available, and none of the reasons above
apply. Anything painted BY this app stays emoji-free.

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
  The same rule reaches the two in-memory PRESENCE maps
  (`dashboard/notify/presence.py`), which are not memos but have the identical
  key-set shape — one entry per session / per browser ever seen. `_VIEWING`
  (sid → beat deadline) is bounded EXACTLY rather than by an LRU, because an
  entry past its deadline is dead by definition: `mark_viewing` sweeps the
  expired ones on every beat, so what remains is one key per session actually
  being watched and nothing live is ever dropped. (`web_viewing` GC's only the
  ONE key it is asked about, and the notifier asks only about ARMED sessions —
  so before the sweep, every session you ever opened without an alert sat there
  for the life of the process.) `_DEVICE_SEEN` cannot be swept — no beat ever
  goes stale, since the MRU push target is deliberately "the last device you
  used, however long ago" — so it is a `BoundedLRU(DEVICE_SEEN_CAP)` instead:
  eviction drops the least-recently-BEATEN device, which by construction is not
  the MRU target the map exists to pick, and an evicted device that beats again
  is just re-added.
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
stamped ops (and `read/mirror._cut_blocks` skips them when sizing the backlog
window, so "newest N blocks" means N *visible* blocks — both through the ONE
`opshtml.in_scope` predicate, so a window and its contents can never disagree). What survives of an
agent is the lead's own record of it — the `subagent_fmt` launch header and
finish chip — PLUS the two endpoints of the subagent's own contribution: its
`⇢ prompt` and `⇠ result` blocks. Those are the one exception to the
main-agent-only rule: the substream stamps them `web=1` (a keep-on-the-web
override, `core/ops.py`'s "web" field) so `op_items` keeps them despite the
`src` stamp, while everything in between (its messages, commands, file ops)
stays in that agent's OWN scope. The full detail is the same ops stream read
with the filter inverted — see *Agent scope* below — not a second read model.
Why surface just those two: a subagent reads on the dashboard as "here's what I asked it, here's
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
the bubble.

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
label, cyan, no rewind ↶ since it isn't a re-runnable prompt). No new audit wiring: a recap is derived from the already-audited session
transcript (its path is in the audit `sessions` row), like every other
conversation record.

**Rich tool rendering** (`opshtml.tool_html` / `tool_output_html`). The
presenter renders the well-known built-in tools structurally, reusing the single owners of
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
same additive post-processing markdown uses (`server.mdify`): tool entries gain
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
| `/api/session/<sid>[?agent=<aid>]` | overview: `session()` + error count + `ctx` + `git` + `view_mode` (the mirror density, *View modes* below); agent rows carry their own `ctx`. With `?agent=` it also carries `agent_usage` — that agent's token rollup + priced cost for the scoped scoreboard (*Agent scope*) |
| `/api/session/<sid>/ops?after=N[&agent=<aid>]` | `{last, html: […]}` server-rendered ops; `agent` scopes them to that agent (*Agent scope*) |
| `/api/session/<sid>/history?before=<opid>&blocks=N[&agent=<aid>]` | the previous `N` stream blocks OLDER than op id `before` (lazy backlog): `{oldest, items}`, `oldest` the next cursor (0 = exhausted); `agent` scopes them |
| `/api/session/<sid>/backlog[?agent=<aid>]` | the initial newest-`TAIL_BLOCKS` slice (`merged_backlog`): `{last, mpos, oldest, items}` — the gzip-able GET twin of the SSE fresh-connect backlog; the page fetches this first, then connects the session SSE with the cursors (*Lazy backlog* below) |
| `/api/session/<sid>/errors` | swallowed-exception rows |
| `/api/accounts` | `[{slug, label, alias, usage}, …]` — the launchable subscription accounts (`plugins.accounts`) plus each one's freshest captured usage: every status-line rate-limit window (the 5h/7d pair, aggregated across sessions, served EFFECTIVE — a rolled-over window reads 0 with no reset) PLUS per-model weekly windows fetched from the OAuth `/usage` endpoint and merged in (`plugins.model_windows`, *Per-model usage bars*); each row also carries `sched_score` (weekly-quota perishability) and `sched_ok` (5h safety gate) for the new-session default-account picker, plus the `five_hour_eff` figure `sched_ok` itself gates on (*Default account*), plus `limit_hit` (active rate-limit stamp else null) and `logged_out`/`logged_out_msg` (the account's login was revoked — *Logged-out accounts*); backs the new-session picker and the top usage strip; never blocks on the OAuth fetch — past its TTL the model-window cache serves the previous value while one background thread refreshes (*Per-model usage bars*), and changes reach open pages as the global stream's `accounts` event |
| `/api/stats` | the **Stats / Insights** page (`stats_payload` over `sessionapi.activity_stats`): `{total_sessions, daily:[[day,n]], punch:[[dow,hour,n]], windows:{7d,30d,all}, projects:[…]}` — cross-session aggregates for the contribution heatmap, day×hour punch card, per-window Pulse summary, and per-project cards; server-computed + memo-cached (`STATS_TTL_S`), read-only (no audit rows) (*Stats / Insights* below) |
| `/api/commands?cwd=<dir>` | the "/" menus: `[{name, desc, src}, …]` — CLI built-ins + the directory's discovered `.claude` commands/skills (`plugins.slash_commands`); cwd-keyed, not sid-keyed — the new-session form completes for a directory with no session yet (non-directory → built-ins + user-level) |
| `/api/resumable?cwd=<dir>&limit=25&q=<text>` | the new-session **resume picker**'s rows (`resumable_payload`): the directory's sessions (canon-cwd-scoped, newest-first, `limit` clamped to `RESUMABLE_MAX`), each `{sid, title, last_active, live, model, effort, account{slug,label}}` — enough to reuse a session's model/effort on resume (*Resume picker* below); `q` filters by title+sid across the directory's WHOLE history (discovery scans up to `RESUMABLE_SCAN`, not just the newest — the client can't); blank/unknown cwd → `[]` |
| `/api/session/<sid>/view/<gid>` | rendered click-to-view stash (HTML); leaves a `web-view` `state_files` row (`gid`/`ok`) — the web twin of the terminal ⧉view toggle's audit |
| `/api/session/<sid>/copy/<gid>/<what>` | copy text (`core/copy.collect`); leaves a `web-copy` `state_files` row (`gid`/`what`/`chars`) — the web twin of the terminal `copy` row (the dashboard calls `collect()` directly, bypassing `claude-copy.py`'s audit) |
| `/api/dictate` | `{available}` — Deepgram key-file probe; the page renders mic buttons iff true (*Web dictation* below) |
| `POST /api/dictate/token` | **control plane:** `{"sample_rate"}` → `{token, expires_in, ws_url}` — a ~30s Deepgram grant JWT + the fully-assembled live-listen URL; the browser connects to Deepgram DIRECTLY (*Web dictation* below); 400 bogus rate, 501 no key, 502 grant failed |
| `/api/limits` | `{upload_max, rename_max, view_ttl_s}` — the server-side numbers the PAGE has to agree with (*Served limits* below): `config.UPLOAD_MAX` (the attach path's client-side size refusal), `config.RENAME_MAX` (the rename input's `maxLength`), and `presence.VIEW_TTL_S` (the presence heartbeat's cadence is derived from it). Read-only, no audit rows |
| `/api/dirs/hidden` | `{group_key: hidden_at_epoch}` — the directories the `✕` hid from the list (the durable prefs store, `prefs.hidden_dirs()`); the page seeds `S.hidden` from this on load (*Hidden directories* below) |
| `POST /api/dirs/hide` | **control plane:** `{"cwd"}` (the group key `group_dir\|\|cwd`) → stamp `time.time()` into the hidden-dirs prefs and return `{ok, hidden}` (the full map); the group vanishes until a session started after now shows up in it (*Hidden directories* below); 400 non-string key; **409 when the directory has an active (live) session** |
| `/api/notify-config` | `{enabled}` — the GLOBAL alerts master switch (`prefs.notify_enabled()`, default ON); the list page's `#notifytoggle` seeds its ◉/○ label from this on load (*Global alerts toggle* below) |
| `POST /api/notify` | **control plane** (a FIXED route, distinct from `/api/session/<sid>/notify`): `{"enabled": bool}` → write the durable global `notify-enabled` pref, audit a `notify-global` `state_files` row, and push a `notify-config` SSE event so every other open page repaints; the ONE master switch over all toasts/OS notifs + Telegram/web-push, overriding per-session mutes when OFF (*Global alerts toggle* below); 400 non-bool |
| `POST /api/session/<sid>/message` | **control plane:** `{"text", "attachments"?, "clear_draft"?}` → type it (+ Enter) into the session's kitty window (`Frontend.paste_text`); `attachments` are `@`-mention paths prepended to the text (*Web attachments* below); replies `{ok, queued, tab}` — `queued: true` when the send landed mid-turn in Claude Code's own message queue (a `QUEUE_TABS` tab colour VERIFIED against a live screen, `_turn_live` — a terminal-side cancel freezes the colour mid-turn); 409 headless, 400 empty, 503 no terminal |
| `POST /api/session/<sid>/command` | **control plane:** `{"cmd", "arg"?}` → the scoreboard's quick-command row (*Web quick commands* below): a FIXED vocabulary of the TUI's own slash commands — `compact` (argless), `model` (arg: `MODEL_ARG_OK`), `effort` (arg: `EFFORTS`) — pasted like a composer send; model/effort auto-answer the TUI's switch-confirm menu (`dashboard/confirmdialog.py`, non-queued only); replies `{ok, queued, tab, confirm?}`; 400 off-vocabulary, 409 headless or a dialog open (red tab), 503 no terminal |
| `POST /api/session/<sid>/stop` | **control plane:** close the session's kitty tab (`Frontend.close_tab` — a graceful stop: Claude Code exits on the HUP and SessionEnd runs the normal lifecycle); 409 headless, 503 no terminal |
| `POST /api/upload` | **control plane:** `{"sid"?, "name", "mime", "data"(base64)}` → stage the bytes under `paths.UPLOADS_DIR/<sid\|staging>/` and return `{path(abs), name, mime, is_image}`; the composer injects `path` as an `@`-mention (*Web attachments* below). JSON+base64 (no multipart), cap raised to `UPLOAD_MAX`; 400 bad base64, 413 oversize |
| `POST /api/clipboard/files` | **local-machine read** (no terminal write, nothing staged): `{"names": [basename, …], "sid"?}` → `{paths: [abs, …]}`, the FULL paths of those files on the host's pasteboard (`dashboard/clipboard.py`). The one way a pasted file becomes a usable path instead of an upload — the browser only ever sees a basename (*Web attachments* → *Pasting a copied FILE*). Returns paths ONLY when the basenames match exactly (`clipboard.match` — a remote device's clipboard is not the host's); a miss is a 200 with `[]`, audited either way as a `web-clipboard` row; 400 missing/non-string `names` |
| `POST /api/clientlog` | **frontend audit** (audit-only, no terminal write): `{"client", "device", "conn"{online,view,es,conn}, "events":[{t,sid,ev,…}]}` → one `web-client` `state_files` row per event, scoped to each event's own `sid` (*Frontend audit (clientlog)* below); every row carries this browser's `device` id (device-attributable — the frontend side of notification *Device routing*); the browser reporting the transport + connection + JS-error timeline the server can't see; ≤`CLIENTLOG_MAX` events, scalars only; 400 non-list events |
| `POST /api/presence` | **device presence** (no terminal write, no per-beat audit): `{"device", "sid"?}` → stamp `_DEVICE_SEEN[device]` (so the on-device push routes to the most-recently-used device — *Web push* → *Device routing*) and, when `sid` present, refresh the `_VIEWING` deadline (the "you're watching this session" suppress). Sent on a heartbeat DERIVED from the served `view_ttl_s` (TTL/2.5, *Served limits* above) while the page is visible+focused, from ANY view; the client's single presence beat, superseding the old per-session `viewing` beat (that endpoint still exists) |
| `POST /api/sessions/new` | **control plane:** `{"cwd", "account"?, "resume"?, "continue"?, "model"?, "effort"?, "prompt"?, "attachments"?}` → launch `<account-alias> [--resume sid \| --continue] [--model m] [--effort e] [prompt]` in a new tab at `cwd` (`Frontend.launch_tab`); `account` is a switcher slug → its vetted alias command word (default `claude`); responds `{ok, win}` — `win` the new tab's window id when the terminal reported one (the page's exact jump-match key, "" otherwise) — and starts the `launch_wake` SSE hurry-up watch; 400 bad cwd/model/effort/resume/account, 503 no terminal |
| `POST /api/session/<sid>/rename` | **control plane:** `{"name"}` → append the `agent-name` naming record to the session's transcript (`plugins.set_session_title` — the `/rename` channel, docs/session-naming-findings.md) and, when a live window exists, `Frontend.set_tab_title` (*Web rename* below); works for live AND parked sessions; replies `{ok, title, tab_retitled}`; 400 empty name, 409 no transcript / unsupported (a codex rollout), 502 append failed |
| `POST /api/session/<sid>/…` | **control plane**, each with its own section below: `interrupt` (Esc in the session's window), `rewind` (open the checkpoint menu; idle only), `rewind-to` (*Web rewind* — the full checkpoint restore), `answer` (*Web ask* — AskUserQuestion; a `chat`+`message` body routes a typed preview-question answer through "chat about this" then delivers the text) + `ask-draft` (persist the unsubmitted ask selections, no terminal write), `composer-draft` + `composer-queue` (persist the unsent message / pending ⧗ chips, no terminal write — *Web composer draft* / *Web composer queue*), `hint-audit` (audit-only beacon for the optimistic composer bubble's lifecycle — a `web-hint` state_files row, no terminal write, no session state — *Optimistic composer bubble*), `plan-options` + `plan-decision` (*Web plan mode* — ExitPlanMode), `notify` (`{"muted"}` → opt this session in/out of the deferred Telegram alert, a prefs write, no terminal — *Telegram alerts* below), `viewmode` (`{"mode": verbose|default|focus}` → this session's mirror DENSITY, a prefs write, no terminal and emphatically not Claude Code's own `viewMode` setting — *View modes* above; 400 outside the vocabulary), `tasks-hide` (`{"hidden": bool}` → dismiss/restore the pinned tasks CARD, a prefs write, no terminal and no task touched — *Web tasks* below; 400 non-bool, **409 unless every task is completed**), `viewing` (a presence heartbeat sent only while the page is visible+focused+on this session — refreshes the in-memory `_VIEWING` deadline so the deferred alert suppresses while you're watching; empty body, no terminal write, no session state, no per-beat audit — *Telegram alerts* below) |
| `/events` | global SSE: a `hello` (the server's `BOOT_ID` — the EventSource auto-reconnects across a server restart, and a changed boot id tells an OPEN page its loaded JS may be stale; the client toasts "dashboard updated — refresh", click to reload. Twice a redeploy shipped under an open page and its old handlers running against the new server read as a product bug), then a full `sessions` snapshot on connect + on membership/order change, `sessions-delta` `{rows}` for content-only changes (paused-blind per-row diff, wire-stripped rows — *The list renders once, then patches* below), an `accounts` event (the full `/api/accounts` payload) whenever the accounts strip's data changes (sched_score-blind diff — same section) + `notify` toasts |
| `/events/session/<sid>?after=N&mpos=M[&agent=<aid>]` | per-session SSE (`agent` scopes the MIRROR channel only — *Agent scope*): `ops`/`msgs`/`stats`/`agents`/`costs`/`ctx`/`git`/`title`/`running`/`fgrun`/`tab`/`errors`/`monitors`/`jobs`/`memory`/`ask`/`ask-draft`/`plan`/`tasks`/`composer-draft`/`composer-queue`, each on change; a fresh connection's first `ops` event is the merged backlog, tail-limited, carrying `oldest` (see below). Every field other than `ops` is a row of the stream's CHANNEL TABLE (`_SLOW_CHANS`/`_FAST_CHANS`, see *The stream's pushed fields are a channel table*), and the four tab-badge counts (`errors`/`monitors`/`jobs`/`memory`) keep their own table inside it — `_BADGE_COUNTS`, a cheap count wired to a `{"count": n}` event of the same name, its values `(sid, cwd)` callables so the count resolves at call time (a patched `sessionapi` moves the pushed number) and so `memory` can route through its scope-gating owner instead of a second reading of the rule; adding a badge is a table row |
| `GET /api/session/<sid>/monitors[?agent=<aid>]` | the Monitor tool runs (command/description/lifetime + events, merging transcript + audit streams state) for the monitors tab (*Monitors tab*) — the LEAD's own by default, one agent's with `?agent=` (*Agent scope*) |
| `GET /api/session/<sid>/jobs[?agent=<aid>]` | the background Bash jobs (command + lifecycle state, merging audit streams + ops + the launch hook) for the jobs tab (*Jobs tab*) — the LEAD's own by default, one agent's with `?agent=` (*Agent scope*); output via the `/copy/<group>/out` endpoint |
| `GET /api/session/<sid>/memory` | the memory-wiki notes the session touched (`{path, name, verb, agent, count, ts}`, from the `memory` kv) for the memory tab (*Memory tab*) |
| `GET /api/session/<sid>/note?path=<abs>` / `?stem=<stem>` | one memory-wiki note rendered for the viewer (`{name, frontmatter, html, backlinks, missing}`); path-traversal-guarded to `~/wiki/01` (*Memory tab*) |

SSE is plain polling server-side (`TICK_S` per session, `GLOBAL_TICK_S`
global) pushed over a held response — no websockets dependency, and
`EventSource` gives the client reconnect for free (the app reconnects with
`?after=<last seen op id>` so nothing repeats).

## Cache-busting (`?v=<BOOT_ID>`)

Un-versioned static responses are sent `Cache-Control: no-store`, but that only
asks a browser not to cache — it can't EVICT bytes already cached, and a remote
client (mobile Safari especially, or a CDN in front — the public origin is a
Cloudflare tunnel) can keep serving a stale `app.js`/`style.css` across a
dashboard restart. That is exactly the "does NOT hot-reload" hazard (CLAUDE.md):
a fix shipped, the origin served it, the phone kept the pre-fix bytes (the
*memory wikilinks don't follow on mobile* report was really this — the fix was
live at the origin the whole time). So `static()` rewrites the sub-resource URLs
in `index.html` to `/static/app.js?v=<BOOT_ID>` / `/static/style.css?v=<BOOT_ID>`
(`BOOT_ID` is bumped every server start). `index.html` is itself `no-store` AND
is the main document a reload always refetches, so each restart hands the browser
fresh `?v=` URLs that nothing (browser or CDN, which key by full URL incl. query)
has cached. The `?v=` is a cache key only — `do_GET` parses the path (query
stripped), so `static()` still serves the same file. A hard reload is no longer
required for a remote page to pick up new JS/CSS; a normal reload suffices.

**A fetch stamped with the CURRENT boot's `?v=` is served immutable**
(`config.CACHE_STATIC`, `public, max-age=31536000, immutable`) rather than
no-store — the stamp already IS the invalidation (it changes on restart, and the
bytes behind a static URL only change via a restart), so `no-store` on the
stamped URLs bought nothing and cost a full re-fetch of every asset on every
reload. That cost turned out to be a real failure, not just latency (the
2026-07-27 *"on refresh: js not loaded / css not loaded / 502"* report): a
reload re-pulled all 14 `app.NN-*.js` parts + `style.css` + the first API calls
as ONE parallel burst of ~16 cloudflared→origin connections, and the server's
accept backlog was the socketserver DEFAULT OF FIVE — the kernel resets every
connection past the queue, cloudflared logs *"Unable to reach the origin
service … connection reset by peer"* (`~/Library/Logs/dash-tunnel.log`) and
answers 502. A 502 on the document is the visible *Bad gateway*; a 502 on one
JS part is worse — the page half-loads and throws a `ReferenceError` per
missing global (`stopDictation` in the audited `js.error` rows = `app.07`
never arrived), the "js not loaded" symptom. Two fixes, both load-bearing:
`Server.request_queue_size = config.BACKLOG` (128 — a class attribute, since
`listen()` runs inside the constructor), and the immutable stamp above, which
shrinks a warm reload to `index.html` + API calls so a tunnel blip can no
longer brick the page (the cached parts load regardless). A STALE `?v=` (an
old boot's) still serves the current file but no-store — only the
currently-advertised URL is promised immutable.

The same stamp covers the **icons** — `apple-touch-icon.png`, the `icon-*.png`
set, and the `manifest.webmanifest` URL in `index.html`, plus the manifest's OWN
icon list when it is served (the installed-app glyph is read from there, not from
`index.html`). An icon is the worst case of the problem above: REGENERATING one
is new bytes at an UNCHANGED URL, and a browser's icon cache is far stickier than
its resource cache — Safari keeps a persistent favicon store
(`~/Library/Safari/Favicon Cache/favicons.db`, page-URL → cached bitmap) that a
hard reload does NOT evict. That is the *the tab logo doesn't match the page
logo* report: the PNG icon set had drifted from the canonical shanyrak (they were
authored separately, never regenerated when the logo changed) and, once fixed,
Safari still showed bitmaps it had cached weeks earlier, before the logo existed.

## Favicon fallback (`/favicon.ico`)

The declared tab icon is a **data-URI SVG** (`index.html`'s `<link rel="icon"
id="favicon">`), because it is rewritten live to carry the red asking-you badge
(`app.01-attention.js` `FAVICON`/`FAVICON_ASK`). Two clients can make no use of
it: **iOS Safari supports SVG favicons in no version at all**, and macOS Safari
only since 26. Their fallback is the path they probe on their own, `/favicon.ico`
— which used to 404, leaving them with no icon (or an indefinitely-cached old
one). So a real multi-size ICO (16/32/48/64/128/256, transparent so it reads on a
light or dark tab bar, same shanyrak geometry as the brandmark and the SVG) is
served at the ROOT path, its own route in `http/get.py` beside `/sw.js`.

It is deliberately given **no `<link rel="icon">` of its own**: a declared raster
icon out-ranks the data-URI SVG in browsers that handle both, which would cost
the dynamic ask badge. Root auto-discovery is precisely fallback-only semantics —
a client that can use the SVG never asks for the ICO.

All icon assets (the SVG glyph, the ICO, the PNG set, the README logo at
`docs/assets/logo.svg`) render the SAME shanyrak coordinates; `FAV_GLYPH` in
`app.01-attention.js` is the reference copy. Regenerating one means regenerating
all of them — the drift above is what happens otherwise.

## Served limits (`GET /api/limits`)

A handful of server-side numbers have to be known by the PAGE too, because the
browser acts on them before any request reaches a handler:

| Served | Owner | What the page does with it |
|---|---|---|
| `upload_max` | `config.UPLOAD_MAX` | refuses an over-size attachment client-side, with a named toast instead of a 413 |
| `rename_max` | `config.RENAME_MAX` | the rename input's `maxLength` |
| `view_ttl_s` | `presence.VIEW_TTL_S` | DERIVES the presence heartbeat cadence (below) |

Each of these used to be a literal in the JS carrying a `// mirrors the server's
X` comment — i.e. a second copy of a fact whose owner is `config.py` /
`presence.py`, which drifts the moment either side changes. The upload/rename
caps drift into a confusing UI (the page refuses a file the server would have
taken, or accepts one it won't); `view_ttl_s` is worse, because it is
env-overridable (`CLAUDE_DASH_VIEW_TTL_S`) and so drifts with **no code change
at all**: set it below the page's fixed 8 s beat and every watched session's
presence lapses between beats, which the deferred alert reads as "nobody is
looking" and fires the off-device Telegram/push alert while you sit staring at
the session (docs/dashboard.md *Telegram alerts*). So the server serves them and
the page stops guessing: `loadLimits()` (app.13-init.js) fetches once at boot
into the `LIMITS` object (app.00-core.js), and the consumers read `LIMITS.<k>` at
use time, not at load time. The literals still in `LIMITS` are only the
PRE-FETCH fallback — an attach or a rename in that one round-trip still behaves
— so they may lag `config.py` without breaking anything; the served numbers
always win. A failed fetch leaves them in place (degrade to the compiled-in
number, never to a dead button). Read-only, so it adds no audit rows — like
`/api/dictate` and `/api/stats`.

The heartbeat is a **derived** cadence, not a matching literal: `armBeat()` beats
every `view_ttl_s / 2.5`, floored at 2 s (a mis-set knob must not turn presence
into a request loop), and re-arms when the fetched limits land. TTL/2.5 leaves
room for one beat to be lost or late and still not lapse — which is the whole
point, since a lapse is indistinguishable from absence.

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
state at send time ∈ `QUEUE_TABS` = `thinking`/`working`/`executing`, **verified
against a live screen** — next paragraph) and `tab`, and both the raw tab state
and the verdict (`tab`/`live`/`queued`) ride the `web-send` audit row. The page shows a
queued send as a ⧗ chip under the composer (and the send button reads
"queue" while busy — a cosmetic client-side mirror of `QUEUE_TABS`; the
server's verdict is the chip authority). A chip is removed when its prompt
record actually arrives in the stream — `conv_items` items additively carry
`kind` and, for prompts, the raw `text`, and `drainQueue` matches on
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

**The tab colour alone cannot promise `queued` — the promise is verified**
(2026-07-25). `queued: true` is a *promise* the page acts on: it pins a ⧗ chip
and waits for a delivery. But Claude Code fires **no hook on cancel**, so a turn
cancelled AT THE TERMINAL (Esc-Esc) leaves the tab frozen on magenta with
nothing to repaint it — the colour says `thinking` while the TUI sits idle at
its prompt. A colour-only verdict then promised `queued` for a message the idle
TUI submitted *instantly*, and the chip had no delivery to wait for: session
`bdeca061` sent on a `tab: thinking` and `UserPromptSubmit` fired 0.105 s later.
So on a `QUEUE_TABS` tab `post_message` first runs `_turn_live` — the same
marker-free **screen-delta** liveness the interrupt's verify uses (two
ANSI-stripped `get_text` captures `QUEUE_VERIFY_GAP_S` apart: a running turn
always ticks its spinner / elapsed timer / token stream, a stopped one is
static; no marker *string* survives Claude Code's versions, see *Interrupt*).
Static ⇒ `queued: false`. Unreadable (`live: null`) ⇒ keep the colour's verdict,
so a probe failure can never lose a real queue. Two constraints are load-bearing:
the probe runs **before** the paste (our own paste changes the screen and would
itself read as motion), and it is paid **only** on a `QUEUE_TABS` send — where
the message is queueing anyway, so the gap costs the user nothing. Why not fix
the tab colour instead: there is no event to fix it with — a terminal-side
cancel is exactly the signal Claude Code doesn't emit (docs/tab-colors.md), and
an idle-timeout backstop is a rejected design. This endpoint, unlike the tab
painter, has a live terminal in hand and can just *look*.

**Interrupt flips the button out of "queue" immediately** (2026-07-20). Claude
Code fires NO hook on interrupt, so after an Esc the tab can sit stale-busy —
especially from `executing`, where not even the escape-recheck spawns (it only
covers magenta `thinking`/`working`). The composer then kept reading "queue"
even though the turn had ended and a plain send is what would happen. So
`interruptSession`, on a successful interrupt of a `BUSY_TABS` tab,
optimistically drives `composerMode`/`stopMode`/`quickMode` to the
your-turn state (`awaiting-response`) — the button reads "send" at once (and
■ stop greys out, since the turn just ended). This is only a
client-side hint: the escape-recheck's green (or the next prompt's tab event)
is what reconciles the real state, and if the turn actually kept going that
next `tab` event flips the button right back to "queue". Terminal-side Esc
still has no signal at all (the known no-hook gap), so its stale-busy label
only clears on the next real hook.

**A red `awaiting-command` tab refuses every Esc-sending gesture** (2026-07-20).
Red means a MODAL DIALOG is open — AskUserQuestion, ExitPlanMode, or a
permission prompt — and an Escape there DECLINES/dismisses the dialog, it does
not interrupt a turn. The since-retired cancel gesture once landed its
Esc-Esc on an open ask and killed the very answer the user was giving through
the web ask card (the tab read "User declined to answer questions", and the
web answer then hit "no question dialog on screen"). So `awaiting-command` is
DELIBERATELY excluded from `BUSY_TABS` on BOTH sides (one constant now, the
page's second copy having gone with the button), and `post_interrupt`,
`post_rewind` AND `post_rewind_to` all bail with a 409 *"a dialog is open —
answer it first"* (`_dialog_open_guard`, mirroring post_command's own red-tab
refusal). Client-side the ■ stop button disables on red, and the keyboard Esc gesture / ↶ rewind button swallow
themselves with a toast pointing at the card. The ask / plan / confirm cards are
the response path; the 409 is the authoritative backstop for a stale page that
still believes the tab is cancelable.

**Web attachments (images/screenshots + files).** The composer and the
new-session prompt accept attachments the way the Claude Code TUI does — paste a
screenshot (`onpaste` over `clipboardData.items` of kind `file`), drag-drop
files onto the composer/prompt box, or the attach (paperclip) picker. Claude Code has NO CLI
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
  `name`, `mime`); a write/decode failure adds an `A.error`. With a `sid` those
  rows file under that session (`_audit_target`); WITHOUT one — the new-session
  form's staging upload — they file under the GLOBAL stream, an empty log/path
  like `web-launch`/`ns-prefs`. Not `P.mirror_log("")`: with no sid that falls
  back to the **cwd slug of whatever directory the dashboard process was started
  in**, so staging uploads used to land in the audit timeline of an unrelated
  session running in the main checkout (the reject paths already used `""`; the
  success path didn't, and the two disagreed — fixed 2026-07-25). `serve()` best-effort
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
  ▤ + filename otherwise); an in-flight upload dims the chip and a send waits on
  it. Attachments are NOT persisted into the `composer-draft` kv, so a reload
  drops the pending chips (the staged files themselves survive on disk until the
  prune) — a deliberate scope limit; the draft machinery stays text-only.

*Pasting a copied FILE — its PATH, like kitty.* Copy a file in Finder or an
IDE, paste it into the composer, and the dashboard used to **upload** it: the
bytes were staged under `UPLOADS_DIR` and the message went out as
`@/…/baqylau-uploads/064783b1-glab.py`. The kitty TUI does the opposite, and its
behavior is what you want when the file came out of a project — it pastes the
**path**, so Claude Code reads the file in place, at its real location, still in
its repo. The composer now matches.

Deciding *which* gesture happened is the whole problem, and the page **cannot**
do it. A pasted screenshot and a pasted file both arrive as a `File` on
`clipboardData`, carrying a basename and bytes and nothing else. The one
distinguishing fact — is there a real file on disk behind this? — lives in
pasteboard flavors the browser never exposes:

```
public.utf8-plain-text   "glab.py"                   ← the BARE NAME
NSFilenamesPboardType    ["/Users/…/glab.py"]        ← the full path
public.file-url          "file:///Users/…/glab.py"   ← the full path
```

The web platform does not hand a filesystem path to script, by design, and no
clipboard API (`clipboardData.getData`, `navigator.clipboard.read`) surfaces
`public.file-url`. During a file paste Chrome reports `types: ["Files"]` and
`getData("text/plain")` is empty — and even when readable it is only the bare
name. So the browser reports WHICH files it was handed and the **server**,
which shares the pasteboard with kitty, answers whether they are real files and
where:

- **Paste**: any file paste is `preventDefault()`ed and `pasteFiles` POSTs the
  observed basenames to `/api/clipboard/files`. Resolved ⇒ the absolute paths
  are spliced at the caret (space-joined for a multi-file copy) by
  `insertAtCaret`, which fires `input` so autoGrow / the draft save / the ghost
  suggestion all see the edit. Not resolved ⇒ `tray.addFiles`, the upload path,
  exactly as before. A failed or refused probe also falls through to the
  upload, so the worst case is the old behavior. (The round-trip is why the
  paste is cancelled: the browser would otherwise write its own text flavor —
  the bare name — synchronously, and we would have to un-write it.)
- **Not resolved** covers the two cases where uploading is right: there is no
  file on disk behind the bytes (a screenshot, a copied image region — the
  pasteboard has no path to give), or the pasting device is not the host (a
  phone over the tunnel), where an upload is the only option that can work.
- `dashboard/clipboard.py` is the one owner of the read: `NSFilenamesPboardType`
  first (the only flavor carrying a multi-select, and already POSIX paths),
  else `public.file-url` decoded. pyobjc is imported lazily INSIDE the read —
  the module is on every request path and must not pay (or crash on) an AppKit
  load it may never need. Every failure — no pyobjc, no pasteboard, a non-macOS
  host — degrades to `[]`, i.e. to uploading. No caching: a pasteboard is live
  state, and a stale answer is a *wrong path*.
- **Correlation guard** (`clipboard.match`): paths come back only if their
  basenames are exactly what the caller reported — same names, same count,
  order-insensitive. The dashboard is reachable from a phone over the tunnel
  and a phone's clipboard is not this Mac's; without the check, a remote paste
  would be answered with whatever happens to sit on the host's pasteboard. We
  only ever *resolve* a file the caller already named, never volunteer one. A
  miss is a 200 with `paths: []`, not an error.
- **Drop and the paperclip picker keep uploading.** They are the explicit
  "attach this" gestures, and a drag carries its OWN pasteboard — asking
  `/api/clipboard/files` about a drop would answer about whatever was last
  *copied*. Dropping a file is therefore also the escape hatch when you want a
  copied file ATTACHED rather than referenced.
- Every paste drops an `attach.paste` clog beacon (`n`, `resolved`) — the
  client is the only witness to which branch ran (*Frontend audit
  (clientlog)*).
- A genuinely empty file is refused at `add()` with an "empty file" toast, so
  no path POSTs bytes the server is guaranteed to reject.

**The wrong turn, recorded because it is instructive.** The first report was a
failed upload of `__init__.py`, and the audit row read
`web-upload {"ok": false, "why": "empty file", "name": "'__init__.py'"}`. That
was diagnosed as a **file promise** — a clipboard entry with no bytes behind it
— and two fixes were built on it: skip zero-byte Files, then resolve their
paths server-side. The premise was false. `__init__.py` was an ordinary **empty
package marker**, 0 bytes on disk; Chrome had handed over the real file and the
server rejected it correctly. There is no promise in this story at all, and the
zero-byte gate never touched the actual bug — the next paste (`glab.py`, 1538
bytes) sailed straight into the upload path and was reported again. The lesson
is the repo's own rule about empirical claims: *the pasteboard was never dumped
until the third pass.* When it was, it answered both questions at once — the
text flavor is only the bare name (killing the "just let the default paste
through" fix) and `NSFilenamesPboardType` holds the path (giving the real one).
The server-side pasteboard read survived the correction because it was the
right mechanism attached to the wrong trigger; only the client-side gate
(`size === 0` → is this a real file?) had to change.

Rejected: **gating on zero bytes** (the promise theory above). It fires on
genuinely empty files and never fires on the case that matters — any file with
content, which is nearly all of them.

Rejected: **`navigator.clipboard.read()`** as a richer client-side read. It is
sanitized to a fixed type list (`text/plain`, `text/html`, `image/png`, web
custom formats); `public.file-url` is not among them and never will be — the
path boundary is the point, not an oversight.

Rejected: **searching the session's project dir for the basename** server-side.
It needs no clipboard access at all, but `__init__.py` (the file that started
this) matches dozens of directories in one Python repo — the answer would be a
guess dressed as a resolution, and picking wrong pastes a path to the wrong
file.

Rejected: **keeping images on the upload path even when they resolve** (so a
Finder-copied PNG still attaches). It is defensible — an image's bytes are
usually the point — but it makes the rule "paste gives you a path, except
sometimes", and screenshots (the dominant attach-by-paste case) carry no file
path anyway, so they already upload. One rule, plus drag-drop as the explicit
attach gesture.

**Slash commands are PASTED, never typed** (`launch.type_command`, 2026-07-25).
Raw keystrokes are not safe in Claude Code's input box: with **`editorMode: vim`**
it is MODAL, and anything that pressed Escape first — the interrupt presses up to
`INTERRUPT_TRIES` — leaves it in **NORMAL** mode, where the characters are vim
COMMANDS rather than text. Claude Code's own docs spell out the workaround: in
normal mode `/` opens reverse history search, and the empty-search hint reads
*"press `Esc` then `i` then `/` to open the command menu instead."*

Measured: a web rewind ~14 s after a web interrupt typed `/rewind` into a
NORMAL-mode box; the checkpoint menu never appeared (`web-rewind-to` `step:
"open"` — the FIRST such failure in the audit, against 4 clean successes) and the
tail of the keystrokes was submitted into the conversation as the message `nd`.
The identical `nd` artifact recorded earlier in the Esc-gesture comment was
blamed on an Escape racing the text through two server threads; vim mode explains
it without any race, and that older diagnosis now looks wrong.

A **bracketed paste** is mode-proof — Claude Code takes it as content, never as
keystrokes — and it was already how the quick commands (`/compact`, `/model`,
`/effort`) reached the TUI, which is exactly why *those* kept working where the
typed `/rewind` did not. So `launch.type_command` is the ONE slash-command
channel (`post_rewind`, `post_command`, `rewindmenu.drive`), and it folds in the
clipboard-image guard below, which every paste requires. The Enter rides outside
the paste, so it still submits. A grep-test pins it: nothing may reach the TUI as
a typed slash command.

Residual (unchanged, and NOT made worse): `drive`'s opening `ctrl+u`/`ctrl+k`
line-kill is still key events, which a NORMAL-mode box also reinterprets — so a
leftover draft can still get `/rewind` appended to it. That degrades to the same
honest `step: "open"` failure rather than stray input.

**Clipboard-image guard (the spurious-screenshot fix).** Separately from the
dashboard's own `@path` attachments (above), **Claude Code's TUI auto-attaches
whatever image is on the macOS clipboard to a message on ANY bracketed paste —
and when launched with an initial prompt argument, on that startup too.** Proven
live (2026-07-23): a web resume with the prompt `"say test"` and the audit's
`attachments:0` still arrived as `say test[Image #1]` with a PNG the user never
attached (a screenshot on their clipboard), cached to Claude Code's own
`image-cache/<sid>/N.png`, twice identically, with nobody at the terminal;
`claude -p` (print mode) never does it; a raw (non-bracketed) `send-text` doesn't
grab it but drops bytes, so it isn't a usable delivery. baqylau delivers every
web message via bracketed paste (`paste_text`) — so every send/launch is exposed.
It is undocumented Claude Code behaviour (v2.1.x, no opt-out flag). The fix, since
the paste itself does the grab and can't be dodged, is to **empty an IMAGE
clipboard right before each web send/launch** (`clear_clipboard_image` — macOS
`osascript`, gated on `_clip_has_image` so a TEXT clipboard is left untouched;
best-effort, no-op off macOS). Wired ahead of every bracketed paste
(`post_message`, `post_command`, the ask-chat send) and, when a launch carries a
first prompt, ahead of `launch_tab` (the argv-startup grab). Each of those rows
carries a `clip` bool (cleared? — `web-send`/`web-command`/`web-launch`). This is
the deliberate trade-off the user chose: a screenshot the user never meant to send
is worse than losing an image that happened to be on the clipboard at send-time
(a text clipboard is preserved; the dashboard's own `@path` attachments are
unaffected — they never use the clipboard). *Why not "bare launch + clear-then-
send"?* Tried and reverted: the clear-then-send still bracketed-pastes, which
re-triggers the grab (verified live), and it added timing-dependent delivery for
no benefit.

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
the stats row.

**Matching is CONTAINS, not starts-with** (`cmdMatches`): the typed token is
found anywhere in the command NAME, case-insensitively — `/commit` finds
`gh:commit`, `/debug` finds `audit-debug`. That is what the namespaced and
plugin-provided names need, since the part you don't remember is exactly their
prefix (`gh:`, `codex:`), and a prefix-only menu made those effectively
unfindable without first recalling the namespace. Prefix hits still rank
**first** (typing the head of a name means *that* name), and each group keeps
the server's own order — built-ins first, then nearest-first — so the ranking
never re-litigates the shadowing the server already settled, and no fuzzy
scoring is involved (a subsequence matcher would surface absurd hits for
2-character tokens and make the top row unpredictable, which matters here
because Enter completes it). Descriptions are deliberately NOT searched: a
word like "run" appears in dozens of them, and a menu you complete against
must stay predictable. Capped at `MENU_MAX` rows, as before.

The list is
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

**The picked command reads TINTED in the box** (`cmdHighlight`, both boxes —
it lives inside the shared `slashMenu`, which is what makes the composer and
the new-session first prompt carry it alike), echoing the TUI, which paints a
selected command/skill the same way. A `<textarea>` cannot style a *range* of
its own text, so the tint is a **mirror div laid over the box**
(`.cmhl`, `pointer-events: none`, its own text `color: transparent`) in which
only the leading `/name` token carries a translucent `--exec` background —
read as a wash over the textarea's own glyphs, like a selection highlight.
Rejected alternatives: a `contenteditable` box (loses the native textarea
behaviour the composer depends on — placeholder/ghost suggestion, autoGrow,
dictation splices, iOS keyboard handling) and drawing the mirror *behind* a
transparent textarea (the box's own background would have to move to a
wrapper, restructuring the composer's flex row and its drag-drop target).
The mirror's metrics — font, line-height, letter-spacing, padding, border
widths — are **copied off the live textarea** (`getComputedStyle`,
`HL_METRICS`), never re-declared in `style.css`: the box stays the single
owner of its typography (its `@media (pointer: coarse)` ≥16px anti-zoom
override among it), so a CSS twin can't drift a glyph out of alignment.
What is tinted is the leading token *while it names a known command* (a name
the menu has fetched, or the one just picked) — so editing it into something
else drops the tint by itself, and `/gh:fix some args` tints just the
`/gh:fix`. Painted one frame late (the caller's own `oninput` → `autoGrow`
resizes the box on the same event, and the mirror must match the settled
geometry), and re-placed through `autoGrow` — the one call every
*programmatic* value change already goes through (draft restore, another
device's SSE draft, a cleared send), none of which fire an `input` event.
Purely presentational, so like the ctx bars / goal card it adds **no audit
rows**: it restyles text the audited `send`/`command` path already records.

**The same tint carries into the TRANSCRIPT** — a prompt you sent as a slash
command keeps its `/name` tinted in its bubble, so the feed reads as a history
of commands rather than of text that happens to start with a slash. Scope, all
three deliberate: **your prompt bubbles only** (`kind == "prompt"` — Claude's
messages quote commands often enough that tinting them would be noise, and the
question/answer/recap bubbles aren't things you typed); the **leading** token
only (whitespace/EOL-terminated — `/gh:fix some args` tints just the `/gh:fix`);
and only when it **names a real command**. "Real" is `read.meta.cmd_names(cwd)`
— a TTL'd (`CMDS_TTL_S`) frozenset over the same `plugins.slash_commands(cwd)`
the "/" menu lists, so the menu and the tint can never disagree about what a
command is, and a `.md` added mid-session starts tinting without a restart. The
walk is per-cwd-per-TTL, resolved ONCE per render by `merged_backlog` /
`history` / the SSE tick (`session_cmds`), never per bubble.

Rendering is `opshtml.msg_html`'s `_lead_cmd` + `_tint_lead`. The wrap is
**structural, not a blind replace**: the escaped token must sit immediately
after the rendered body's first opening tag (`<p>/compact …`), else the body is
returned untouched — so an unexpected render (a list, a fence, an already
marked-up head) degrades to no tint instead of corrupted HTML. Rejected: doing
it in `md_html` (the markdown owner has no business knowing about commands) and
splitting the source into "first line + rest" before rendering (it re-flows a
multi-line prompt into two paragraphs).

The two bubbles the PAGE builds itself — the optimistic stand-in and the ⧗
queued chip — never pass through `msg_html`, so `promptMd` tints them
client-side via `leadCmd` (app.08-composer.js), the deliberate cross-language
twin of `_lead_cmd`. Its name list is the **server's**: `session_payload` ships
`commands` (names only — the projection of the same provider) rather than
having the page fetch its own, so both renderers agree by construction. That
matters most for a queued chip, which can sit in the feed for minutes before
its real bubble arrives. Same `--cmdtint` hue as the input overlay (one CSS
custom property owns it; `.cmdtok` adds the `--exec` text colour, which the
transparent-text overlay can't use), and, being presentational, it adds no
audit rows either.

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
text, which clears the stash; `composer_draft` treats a blank stash as None so
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

### Terminal draft sync (the kitty box → every device)

Asked for 2026-07-25: *"if I type into the kitty tab's message input without
sending and then open that session in the dashboard, I want the draft there —
so I can continue the prompt from my iPad."*

Claude Code fires no hook for typing, so the only source is the SCREEN — the
same `❯`-box read the ghost suggestion uses, but its opposite half:
`suggestion.parse` returns the FAINT text (a suggestion), `suggestion.typed`
the normal-weight text (what the user actually typed). `probe_box` returns both
from ONE capture, so the per-tick `kitten @ get-text` count doesn't change; the
SSE loop feeds the ghost to the placeholder and the typed half to this sync
(`launch.sync_terminal_draft`), on the same slow cadence and behind the same
gate (settled tab, no pending ask/plan).

It writes the **same `composer-draft` kv** the web composer uses, so it inherits
the whole existing mechanism for free: the seq-guarded CAS, the SSE
re-broadcast to every open page, and the restore-on-open path. Nothing new is
stored and no new event is needed. The record carries `origin: "terminal"` (so
no page mistakes it for its own echo) and a `composer-draft` audit row with
`action: "terminal"`, which is what makes a draft's PROVENANCE answerable.

**The asymmetry is the whole design.** A non-empty box means someone is typing
at the terminal, so it wins. An empty box does **not** mean "clear the draft":
a draft typed on a phone lives only in the kv, and the terminal box is empty
for it *always* — propagating emptiness would wipe that draft on the very next
tick. A clear rides through only when the STORED draft is one we synced
(`origin: "terminal"`), i.e. its text came from that now-empty box.

Keying the clear on the stored `origin` rather than on the previous probe's
memo is what makes it survive a reconnect. The memo is per-connection and
starts empty, so a page opening AFTER a terminal send saw empty box == empty
memo and left the stale draft forever ("the draft doesn't clear after I send
from kitty", 2026-07-25). The record remembers where it came from; a
freshly-connected page doesn't have to.

The other rules, each earned:

- **A still box says nothing.** Re-pushing an unchanged box every tick would
  overwrite an edit being made to that same draft on the web, making a synced
  draft impossible to touch anywhere else. Only a CHANGE pushes — except the
  first probe of a connection, which adopts whatever is in the box (that is
  what makes "type in kitty, then open the dashboard" work).
- **Unreadable ≠ empty.** `probe_box` returns `""` for a box it read and found
  empty, `None` when it couldn't read one (dead window, kitten failure). Only
  `""` is a signal; collapsing the two would clear the draft of every session
  whose window goes away.
- **Our own paste is not the user typing.** A web send puts the message in that
  box for a beat before its Enter; reading it back would echo the outgoing
  message into every device's composer. `post_message` stamps `note_send` and
  the sync ignores the box for `SEND_QUIET_S`.
- **A synced draft arms `clear_draft`.** The box HOLDS that text, so the sync
  also sets the `tui-draft` flag (*Interrupt*) — otherwise sending from the web
  the draft you typed in kitty pastes after it and delivers it twice.
- **Never on a red tab / pending ask/plan.** The `❯` region is then the
  DIALOG's input, not the message box.
- **Don't re-push what the kv already holds.** Every open device's FIRST probe
  adopts the box, so without this the same text was stored once per connection
  (three identical writes in four seconds, seen live).

Nothing is ever typed back INTO the terminal, so there is no echo loop; the
page's own guard keeps a synced draft from yanking text out from under active
typing — but that guard needed a caveat. It used to skip ANY update while the
textarea had focus, so a message sent from the kitty tab left its draft sitting
in the composer forever when the clear happened to arrive while the box was
focused (reported 2026-07-25). `applyComposerDraft` now applies an update to a
focused box too when the box still holds EXACTLY the draft it last showed —
untouched, merely clicked into. A box the user has actually edited is still
left alone.

Known limits: the sync runs only while a page has the session open (it rides
that session's SSE tick, on the `SLOW_EVERY` cadence — a few seconds, not
keystroke-live), and `suggestion.typed` whitespace-normalizes the box, so a
draft with its own newlines arrives as one line (the box pads its rows, so an
ordinary WRAPPED line keeps its spaces).

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

**The delivery match is a SUFFIX match, not an exact one** — one rule with one
owner, `chip_delivered` server-side and its deliberate twin `promptMatches` in
`app.00-core.js` (JS can't import it), used by *both* client reconcilers
(`drainQueue` for the ⧗ chips, `drainPending` for the greyed optimistic
bubbles). A suffix, because what the composer sent can arrive with anything
prepended, and **both** known prefixes are real:

- attachments prepend `@path` mentions + `\n` (server `_with_attachments`), so a
  queued message with a screenshot is delivered as `@path\n<text>`; and
- text **already in the TUI input box** is glued on with **no separator**. A
  terminal-side Escape can hand the previous message back into the `❯` box and
  the page cannot know (its `clear_draft` path only fires for a resend the page
  itself initiated), so the bracketed paste lands right after it.

The match was newline-ONLY until 2026-07-25, which missed the second case
entirely: in session `bdeca061` a cancelled `testing` sat in the box and the
delivered prompt arrived as `testing` + the sent text as ONE prompt, so neither
the client drain nor the server reconcile below could ever match it and the chip
pinned forever. Empty entry text never matches (it would reconcile every entry
away). Three hand-rolled copies of this match drifted apart once; a static test
(`test_app_js_drains_through_the_shared_prompt_match`) keeps them merged.

**Delivered entries are also reconciled server-side, or a persisted one stuck
forever.** `drainQueue` only reconciles NEW stream items, never the
already-loaded backlog. So if the client that persisted an entry closed or
reloaded *before* its message was delivered, every later page load re-seeded it
from the kv (`buildQueuePin`), found the delivered prompt already sitting in the
backlog, and had no fresh item to drain it against — a ⧗ queued bubble stuck
forever even though Claude Code received and answered the message (the "still
shows as queued after it was delivered" report). `composer_queue` now drops any
entry whose prompt already appears among the transcript's delivered prompts
(`_delivered_prompts` / `chip_delivered`, the shared suffix match above) before
it ever seeds the page. Read-only — the server can't rewrite the kv (`mode=ro`), so
the stale rows are pruned by the client's next `saveQueue` once this filtered
list seeds it.

**Sends into an open modal are refused.** A message pasted while an
AskUserQuestion / ExitPlanMode dialog is up goes INTO the dialog, not the
queue, and is lost (perturbing the dialog too). `post_message` now checks
`ask_pending`/`plan_pending` and returns a 409 `modal` with no paste,
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
terminal. On an **iPad** (`IS_IPAD`) the on-screen keyboard has no `→` / Tab, so
the composer also renders a **"use hint" button** (built only on iPad, hidden
until a ghost is live) that inserts the suggestion on tap.

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
unit-tested (`tests/test_l0_dash_probes.py`); `probe()` wraps it with the
get-text call and audit-before-swallow (`A.error` on any failure).

**Live-only, ephemeral, gated.** There is no kv and no persistence — a parked
session has no TUI, hence no suggestion. `sse_session` probes on the SLOW
cadence and emits a `suggestion` SSE event only on change, but only when a
suggestion could plausibly be there: the tab is **settled** (`SUGGEST_TABS` —
green *done* or grey *idle*; a busy/asking tab never shows one), there is no
pending ask/plan modal, and the web composer box is empty (`composer_draft`
None) — otherwise we don't screen-scrape at all (nothing to surface, and a
probe would fight a draft the user is editing elsewhere). The live window is
resolved through the memoized `claude_session=<sid>` map (`live_windows`),
never a reused start-time id.

**Frontend: placeholder + accept key.** `applySuggestion` stores the value on
`ses.meta.suggestion`; `syncSuggestion(ta)` borrows the placeholder slot while
the box is empty (`.cinput.hasghost::placeholder` — italic + a touch brighter,
so it reads as a suggestion vs. the static "message this…" hint), toggles the
iPad "use hint" button (`ses.hintBtn`, shown only while a ghost is live), and
restores the composer's own default placeholder (`ta.dataset.defph`) otherwise.
The keydown and the iPad button share one body, `acceptSuggestion(ses, ta, sid)`,
which fills the box **only on an empty box with a suggestion** — a normal
`saveComposerDraft` follows, so `→` is never stolen from caret movement or Tab
from the "/" menu (both non-empty), and the button is a no-op once you type. It
is a **mirror +
client-side accept**: accepting fills the WEB box only, nothing is written back
to the TUI — a subsequent send pastes over whatever the input holds, as always.

## Web composer history (↑/↓ recall)

**Claude Code's TUI recalls a previously-sent prompt when you press ↑ on the
input box** (successive ↑ walk further back, ↓ forward). The web composer now
does the same: `↑` on an empty box (or with the caret at the very start of a
multi-line draft) pulls back the most recent sent message, another `↑` goes
older, `↓` newer, and `↓` past the newest restores whatever you were typing.

**Source: the feed itself, not client bookkeeping.** The recall list is the
session's REAL delivered prompts — every `.msg.prompt` bubble already carries
its raw text in `data-txt` (`opshtml.msg_html`, the same lossless source the
rewind picker POSTs). `recallHistory` (`dashboard/static/app.08-composer.js`) reads them
live off `ses.stream` on each keypress, so the list survives reloads / device
switches / a return to the session with zero extra state, always reflects
exactly what was sent (from the composer OR the terminal), and includes a
just-sent message the moment its bubble lands. The window is naturally bounded
by what's loaded (older prompts join as you "load more"). Client-built pending
/ queued bubbles carry no `data-txt`, so they're excluded.

**The feed is newest-TOP** (`appendItems` inserts `afterbegin`), so document
order is newest→oldest: index 0 is the MOST RECENT prompt, `n-1` the oldest.
`↑` walks toward older (higher index), `↓` toward newer (lower). Getting this
inverted made the first `↑` jump to the *oldest* message instead of the most
recent — the direction is derived from the feed's insert order, not assumed.

**Navigation is edge-gated and ephemeral.** `ses.histIdx` is the cursor:
`null` = the live draft line (not navigating), `0..n-1` = a history entry.
Recall only *enters* from an edge — `↑` with the caret at the very start — so
arrows still move the caret inside a multi-line draft otherwise; once
navigating, either arrow keeps navigating regardless of caret. Entering stashes
the live draft in `ses.histBase` so `↓` below the newest brings it back. Typing
(`oninput`) or sending resets `histIdx` to `null` (leaves navigation). It runs
AFTER the "/" menu's own arrow handling (`sm.key(e)`) and after the
ghost-suggestion `→`/Tab accept, so it never steals keys from either. Like the
ghost suggestion, it's a WEB-side affordance — a recalled message is not
persisted as a draft (`saveComposerDraft`) until you actually edit or send it,
and nothing is written back to the TUI.

**Audit.** Every recall move drops a `composer.recall` clog beacon
(`{dir: up|down, idx, n}`) on the frontend-audit channel — one `web-client`
state_files row scoped to the session (*Frontend audit (clientlog)*), so the
feature is fully covered: you can see exactly how far back a session's composer
walked its history and when.

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

**A degraded prefs write is audited, not silent.** Everything durable the
dashboard remembers rides this one store — these launch defaults, the
per-directory drafts, the hidden directories, the per-session notify mutes, the
global alerts switch, the web-push subscriptions and VAPID keypair, the
`renamed-title` override — and every one of its five swallow sites used to
degrade with **no row anywhere**, breaking the audit-before-swallow invariant.
That mattered most for `mutate_map`, whose optimistic return (the *intended* map
even when the write was lost — deliberate: the page keeps the draft/toggle it
just made, and a 500 would only throw it away) means the caller answers `ok`
either way. So the handler's `web-*` row says `ok:True` while nothing persisted,
and "my alerts toggle didn't stick" / "my launch draft vanished" was
undebuggable from the DB. Each swallow now reports through `prefs._audit_fail` →
an `errors` row `dashboard prefs <get|set|mutate|connect>` carrying the kv key
and the DB path; **a gesture whose `web-*` row says `ok:True` next to a
`dashboard prefs mutate` row at the same instant is the "it didn't stick"
signature.** Writes are audited every time (each is one bounded user gesture);
READS are audited at most once per `(operation, key)` per process, because they
run on nearly every request and SSE tick and a `session_id=''` `errors` row
lights errwatch's `⚠ global:` chip in *every* session's scorebar — the same
audit-at-most-once reasoning as `core/errwatch.py`'s own recursion guard. In the
same spirit, `webpush._load_keypair`'s corrupt-record path now audits before it
regenerates: a new VAPID key silently orphans every existing subscription, so
that row is the only explanation for every subscribed browser going quiet at
once.

## New-session draft (`GET`/`POST /api/ns-draft`)

The form's **first prompt is a draft**, exactly like the composer's message box
(*Web composer draft*). It used to live only in the DOM: closing the form — the
`cancel` button, Esc, or a stray click on the backdrop — tore the textarea down
and a carefully-typed launch prompt was simply gone, and the second attempt came
up blank (reported 2026-07-25). Now every edit persists it and every open
restores it.

**Where it lives.** The same durable **global** prefs store as *New-session
prefs* above (`dashboard/prefs.py`, `NS_DRAFT_KEY = "new-session-draft"`), not a
per-session kv — the form has **no session yet**, which is the whole reason
`composer-draft` (keyed by sid) can't hold it. It is consequently cross-device
and reboot-proof: start typing a prompt on the phone, open the form on the
laptop, and it is there.

**One draft PER DIRECTORY** — `{cwd: {text, seq}}`. It shipped as a single
shared draft (the form is one transient box, and a per-cwd map looked like
avoidable state); that was wrong in practice and was fixed the same day: you
keep a half-written prompt for one project while starting something in another,
and the shared box bled the first project's prompt into the second's form. So
the box always shows the draft belonging to the directory currently in the form.
The map is **pruned to `NS_DRAFT_MAX` (24) entries by `seq`** — tombstones
included, since recency is what decides — because the key is a free-text field:
without a bound it would grow a row per directory ever typed into.

The cwd **key** is whatever the page sends: `nsDirKey` (trimmed, trailing
slashes dropped) is the form's own notion of "the same folder" and the ONE
implementation of it — the server stores the key verbatim rather than
re-normalizing, so two implementations can't disagree. `""` is a legitimate key
(the form opened with no directory yet).

**Lifecycle.**
- *Write* — `prompt.oninput` → `saveNsDraft(nsDraftDir, …)` (debounced
  `ASK_DRAFT_DEBOUNCE_MS`, the composer's constant). Dictation and the ⌃W/⌃A/⌃E
  readline keys dispatch `input` events, so their text is covered by the same one
  handler. `nsDraftDir` is which directory the box's text belongs to right now.
- *Flush* — `closeNewSession` saves immediately, debounce bypassed: that gesture
  IS the bug this fixes, and the textarea is about to stop existing. Its handle
  on the box is the module-level `nsPromptBox` (null while the form is closed).
  Skipped when the box already matches that directory's cached draft (a form
  opened and closed untouched writes nothing — and any pending debounced save
  carries the same text anyway, so it is left to fire).
- *Restore* — `openNewSession` seeds `prompt.value` synchronously from the
  `S.nsDrafts` cache for the directory it opens on (the prefill, or the last-used
  pref; the cache is primed at boot and kept current by every save), puts the
  caret at the END (you reopened to keep typing), and `autoGrow`s once mounted.
  It then reconciles with a fresh `GET /api/ns-draft` — the whole map, merged
  per directory with the newer `seq` winning, so our own just-typed entries
  survive — and repaints the box only while it still holds exactly what was
  seeded (never yank text from under an edit — the `applyComposerDraft`
  discipline).
- *Switch* — `settleDraftDir`, on the directory field's **blur**. Deliberately
  not on every keystroke: typing `/Users/me/proj` would walk a dozen half-paths
  and blank the box under each. The current text is parked under the directory
  it was typed for first (nothing is ever lost), then the new directory's own
  draft takes the box — and if that directory has NONE, the text follows you
  there instead (nothing to overwrite, and a re-targeted launch usually wants the
  prompt you just wrote). Beaconed as an `nsdraft.dir` clog row (*Frontend audit
  (clientlog)*) with `{from, to, carried, chars}`.
- *Clear* — a successful launch. `go()` already empties the box optimistically
  before the POST (so the prompt never LINGERS after you hit launch), and the
  close that follows flushes that empty box as the clear — under `nsDraftDir`,
  which is exactly where the consumed text was stored (even if you typed a new
  directory and hit Enter without ever blurring the field). A FAILED launch
  restores the text and never clears, so a retry keeps everything.

**Stale-write guard.** Every write carries a wall-clock `seq` stamped at
DISPATCH, and `prefs.set_ns_draft` drops a write older than **that directory's**
stored one (atomically, inside `mutate_map`'s one `BEGIN IMMEDIATE` — the
dashboard is a `ThreadingHTTPServer`, so the compare, the set and the prune must
not straddle a peer thread's write; per entry, so two directories' saves never
fight). This is the `post_composer_draft` guard, for the same reason: a debounced
save in flight when the launch clears must not resurrect the sent prompt by
landing later over the tunnel. A clear is an empty-text **tombstone**, never a
delete, so its `seq` survives to reject that straggler.

**Audit.** `ns-draft` `state_files` rows, global (empty log/path, like
`ns-prefs`): `action=write|clear|stale` with `cwd`, `chars` + `seq`. The TEXT is
never recorded — it is the user's unsent prose, and the directory plus the length
is what a "my draft vanished / came back / belongs to the wrong project" report
actually needs.

## Web quick commands (`POST /api/session/<sid>/command`)

The scoreboard's SECOND action row (its own line under
stop/cancel/rewind/close — live-with-window sessions only, like the buttons
above it): **⊜ compact**, **✦ model ▾**, **✧ effort ▾**. Each just types one
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
`MODEL_ARG_OK` (`MODEL_OK`'s one-clean-word alphabet plus the CLI's literal
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

The client row (`chromeQuickCmds`, the second `actrow` of the session chrome):
compact carries the close button's two-step arm via the shared `armConfirm`
("compact now?", 4 s) — a misclick would summarize the conversation out from
under you; model and effort open
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
effort label (`✧ high ▾`) shows the SAVED effort level — session meta
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
`MODEL_OK` (one clean argv word — an alias like `opus` or a full id like
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
**passive steal watch** (`steal_watch`, a daemon thread; skipped off-mac,
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
is `False`, `frontend()` returns `None`, and every control-plane endpoint
returns a clean `503`, never a 500 traceback.

**Liveness = an OPEN tab, not a lingering state DB.** A session's `live` flag
is *not* just "its `/tmp` state DB exists" — that only means the session was
never PARKED, and a tab closed WITHOUT a SessionEnd (crash / `kill -9`, or a
leaked test DB) leaves the state DB intact, so the session would masquerade as
running with a `kitty_window_id` that kitty has since REUSED for an unrelated
tab. Both payloads therefore reconcile against `live_windows()` — one
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
can't verify. The four-condition check has a SINGLE owner,
`launch.demote_if_dead`, called by all three read payloads (the list, the
session detail, and the resume picker) so they can't drift; the session-detail
call passes a separate `target` dict because its liveness comes from
`API.session` while the window id + `started_at` come from the audit
`session_row`. This is also why the control-plane writes below resolve the
**live** window rather than the stored id.

**An empty `ls` is can't-tell, not "no live tabs" (why cards flashed "gone").**
`kitten_ls` swallows EVERY failure — a timeout, an rc≠0, a transient socket
hiccup — into an empty list and never raises (`frontends/kitty.py`), so a failed
scan is indistinguishable from a genuinely empty desktop and the `except`
guarding `live_windows` can never catch it. Trusting an empty result as
authoritative meant one socket hiccup returned `{}` (not `None`), which — since
`{} is not None` — passed the demotion guard and flipped EVERY running session
to not-live; a session that is live-but-not-parked renders **gone** on its card
(`row.parked ? "parked" : "gone"`), so all cards momentarily flashed "gone"
while the sessions were working, self-healing on the next `_LIVE_TTL` tick once
kitty answered. Because a running dashboard implies kitty HAS windows, an empty
tree is virtually always a failed `ls` — so `live_windows` now maps an
empty/failed `ls()` to `None` (can't-tell, keep the state-DB signal), reserving
`{}` for a real non-empty tree that carries no `claude_session` tags. (The same
transient failure IS audited on the tab-status side as a `kitten @ failed rc=N`
transition — the read-side dashboard demotion leaves no audit row of its own,
so that transition is the correlating tell.)

**Startup grace (why a brand-new session must NOT be demoted).** The demotion
above has a race at the START of a session: the audit `sessions` row (carrying
`kitty_window_id`) is written a beat BEFORE the pane is tagged
`claude_session=<sid>` (`split.cmd_open` runs `A.session_start`, then
`tag_window`), and `live_windows` is memoized up to `_LIVE_TTL` (5s) on top —
so for a few seconds a fresh launch has a window id but isn't in the tagged-window
map, and the naive demotion flips it to not-live: the card flashes **parked** and
the session-detail header (whose `meta` is fetched ONCE at open — the launch jump
navigates straight into it) *froze* on that reading, leaving the parked chip stuck
on and every live-gated action (stop/cancel/rewind/close/quick-commands) missing —
so the just-launched session couldn't even be closed. Two fixes, both needed:
`within_live_grace` EXEMPTS a session from the missing-window demotion for
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
two-step confirm (first click arms for 4 s, second fires — `armConfirm`, the
ONE implementation of that gesture; the header's ✕ and ⊜ compact each
hand-rolled it until 2026-07-25, 60 lines apart in one function); on success it
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

**One button, because the terminal decides (2026-07-25).** There used to be a
second button — **⊘ cancel** — sending TWO Escapes for Claude Code's "cancel the
turn and hand the message back for editing", on the theory that the second press
was what made the difference. It isn't. A plain single Escape from **this** button
produced exactly the same outcome, and the transcript proves it: the discarded
prompt and its replacement end up sharing one `parentUuid` (see *Discarded
prompts*) either way. What decides the outcome is **when** you press:

- press before the turn has produced anything and Claude Code **discards the
  prompt** and restores it to the input box;
- press once it has done work and the **work is kept**.

The press count never entered into it, so the two buttons were one gesture
wearing two labels, and the survivor is the verified one. `post_rewind` lost its
whole mid-turn branch with it (see *Rewind*), and the page's Esc gesture stopped
paying the `ESC_DOUBLE_MS` hold on a busy tab — that delay existed only to tell
"interrupt" from "cancel", so a mid-turn Escape now fires immediately and a
habitual double-tap is swallowed into one stop.

**`restored`: the take-back, read off the screen.** When the interrupt discards
the prompt, the terminal's input box ends up holding it — and the web composer
used to stay empty, so the text was simply lost on that side ("the message went
back into kitty's input but the dashboard's box stayed empty"). The endpoint now
reads the box (`_restored_input` → `suggestion.typed`, the same input-box reader
the Telegram "still at the keyboard" check uses) and returns the message as
`restored`; `applyTakeBack` prefills the composer with it and drops the
discarded bubble from the feed. **The screen says WHETHER, the transcript says
WHAT**: a box that now holds the message we just sent is a take-back, but the
exact text (newlines intact) comes from the transcript record, because the
capture flattens a wrapped box. Anything ELSE in the box is the user's own
terminal draft — left alone, never echoed. The match is a `RESTORE_MATCH_CHARS`
prefix of `suggestion.cmp_key` (whitespace REMOVED, since a wrapped box joins
its lines without a separator); a miss just yields `""` and the page doesn't
prefill. A take-back adds a `phase: "restore"` `web-interrupt` row carrying
`uid`/`flagged`.

**Two things have to PERSIST, or the take-back half-undoes itself on reload**
(reported 2026-07-25: *"the message disappeared from the input and reappeared
in the transcript"*).

1. **The composer text.** `prefillComposer` sets `textarea.value` in code,
   which fires no `input` event — so the composer's own debounced draft save
   never ran and a reload dropped the restored message. It now writes the same
   `composer-draft` stash a typed character would (*Web composer draft*).
2. **The dropped bubble.** `_dead_uuids` recognizes a discard by two prompts
   sharing a `parentUuid` — but the sibling only exists once the REPLACEMENT
   message is sent. In the window between, a taken-back prompt is orphaned and
   yet indistinguishable on disk from a live one, so the next full read painted
   it again. So the interrupt STASHES what it saw: `transcript.mark_taken_back`
   appends the record's `uid` to the session's `takeback` kv (capped, deduped;
   prompt records now carry `uid` for exactly this), and `conversation_for`
   feeds it back as `suspects`.

   Both stashes write through **`kv_set_at`**, never `kv_set` — and both check
   the returned bool. The dashboard is a `ThreadingHTTPServer`, so every
   request runs on its own thread, while `kv_set`'s cached connection belongs
   to whichever thread opened it: from any other thread sqlite raises inside
   `kv_set`'s own swallow, so it writes NOTHING and returns False. The first
   cut used it and ignored the bool, so the stash landed only when a request
   happened to hit the connection-owning thread and the bubble came back at
   random — with `flagged: true` rows claiming otherwise. A failed stash is now
   an `A.error` (`dashboard web-interrupt (take-back stash)`) and a `noted`
   field on the row.

   The flag is **advisory and self-correcting**: a suspect counts as dead only
   while NOTHING descends from it. The observer read a screen and can be wrong
   — you might have retyped the same message into the box yourself — but the
   transcript can't be: if the turn really ran, its records name that prompt as
   their parent and the bubble stays. Which is why the flag can be a cheap kv
   hint rather than a decision.
3. **That the TUI's box is still holding it.** The take-back leaves the message
   in the `❯` box, so the NEXT send must REPLACE it rather than paste after it
   (`clear_draft`). The page remembered that in a per-view variable
   (`clearDraftNext`) — which the same reload wiped while the TUI's draft
   survived, so the next send glued them together and delivered
   `testingtesting2` (reported 2026-07-25). `launch.set_tui_draft` now records
   it server-side (the `tui-draft` kv), `post_message` ORs it into
   `clear_draft` and consumes it on success, and `post_rewind_to` sets the same
   flag for a restore. Server state outlives the page and covers a send from a
   different device; a stale flag is benign, since the clear it triggers is a
   Ctrl+U/Ctrl+K on an already-empty line. The page keeps its variable as an
   immediate same-page hint, but nothing depends on it any more.

**Root cause of "STOP does nothing" (2026-07-24).** A single Escape does **not** reliably stop a busy turn here, for two compounding reasons: (1) `send-key` reports no per-window delivery and synthesized keys are only **~2/3 reliable** (the same measurement that made the idle rewind path type `/rewind` instead of pressing keys — see *Rewind*); and (2) the user runs Claude Code with **`editorMode: vim`**, so the input box is modal — while a turn runs it is in INSERT mode (`-- INSERT --`), and during the **thinking** phase the first Escape only leaves INSERT mode (INSERT→NORMAL); it never reaches the interrupt handler, so the turn runs to completion. Measured directly: every real single-Esc interrupt on a `thinking` tab missed and ran to its natural `Stop` (`a16a181f`, `3d70feca`), while a mid-STREAM Esc landed; a controlled throwaway diff showed the lone Esc deleting `-- INSERT --` and changing nothing else. This is also why the retired cancel gesture's **two** Escapes measured "3/3 reliable" — the first exits INSERT, the second interrupts; the verified re-press below reaches the same place without a second button.

**Robust verified re-press.** So on a BUSY tab (`thinking`/`working`/`executing`) the endpoint presses Escape, then RE-PRESSES *while the turn is still LIVE*, up to `INTERRUPT_TRIES` times. Liveness is **not** a marker string — spinner glyphs animate, gerunds vary, and the thinking level changes how long each phase lasts, so no fixed literal (`esc to interrupt`, `tok/s`, …) is robust. Instead it is **whether the screen is still CHANGING**: two `Frontend.get_text` captures `INTERRUPT_RETRY_S` apart (well above the TUI's own ~150 ms double-Esc window, so re-presses never read as a double-Esc) — a running turn always ticks its spinner / elapsed-timer / stream within that window at *every* thinking level, a stopped one is static. It stops the instant the screen goes static (dead), so an already-idle box never gets a stray Esc. The `web-interrupt` row carries `attempts`, `stopped` (True = verified static/dead · False = still animating after every re-press, the Esc never landed · None = idle press / unreadable) and `probes` — the per-capture phase snapshots. When `stopped` is **False** the endpoint returns `502` and spawns **no** `escape-recheck` (flipping the tab green would mask a live turn, exactly how the failure hid), so the page toasts a real failure. Every capture is also folded into an **`interrupt-probe`** `state_files` row (`insert`/`toks`/`spin` flags + a tail per capture point) — the durable ground truth for diagnosing a recurrence across thinking levels.

**A QUEUED message outranks the screen — the transcript stops the re-press
loop** (2026-07-27). The stop gesture must mean in the browser exactly what Esc
means in the terminal, and with a message queued that is *not* "the session goes
idle": Claude Code delivers the queued prompt **the instant the turn ends**, so
the interrupt hands the session straight over to your message and a NEW turn
starts thinking (the same fact `interrupt-watch` already reasons about —
docs/tab-colors.md). Which is precisely what screen-delta cannot see: the screen
never goes static, the loop reads "still live", and it presses again — killing
the message it just delivered. Because that fresh turn has produced nothing yet,
Claude Code **discards its prompt and hands it back to the TUI's input box**
(*Interrupt*, above), where the web cannot see it: the ⧗ pin never drains, the
composer never prefills (`_restored_input` matches against the last *transcript*
prompt, and a queued message never became one), and the message is simply gone
from the web side. Measured end to end in session `3266f418` (2026-07-27):
`enqueue` at 20:31:05 (a `queued: true` web send), **four** Escapes at 20:33:21,
a `queue-operation`/`dequeue` with no delivered prompt behind it, and the user
re-sending the same 94 chars by hand 26 s later — which then arrived **doubled**,
the leftover first line of the box glued onto the resend (the `clear_draft`
Ctrl+U/Ctrl+K kills one line, and the taken-back message had two).

So the loop takes a second, authoritative stop condition, checked before every
re-press: **`transcript.queue_drained(path, since)`** over the growth past the
press-time baseline `_press_baseline` already takes. Two record shapes count,
both unambiguous — `{"type": "queue-operation", "operation": "dequeue"}` and a
`queued_command` attachment — and nothing else: a generic "a new user record
appeared" tell was rejected because a running turn appends user-shaped records
constantly (tool_results, teammate mail, Stop-hook feedback) and one of those
would stop the loop over a turn that never died. Any drain is the same
conclusion for the caller — **the queue only drains at a turn BOUNDARY**, so the
turn the Esc was aimed at is over (even a `task-notification` delivered at a
natural end says so). The tell rides the `web-interrupt` row as **`drained`**
(`"dequeue"` / `"queued_command"` / `""`) so the audit says WHY the loop stopped
— screen-static or boundary — and the response carries **`queued: true`**, on
which the page skips its optimistic "your turn" flip (the composer stays in
queue mode, since the session is busy again) and toasts *"your queued message is
running now"*. The record shapes belong to `plugins/claude_code/transcript.py`,
the single owner of the transcript grammar; the dashboard only asks the
question. The `escape-recheck` still spawns: the delivered turn is mid-flight
and deserves the same cancel recovery, and its own queued-prompt guard already
keeps watching instead of flipping green (docs/tab-colors.md).

When the (verified or unverifiable) Escape lands on a MAGENTA tab
(`thinking`/`working`) the endpoint also spawns the **`escape-recheck`** tab
dispatch (detached `claude-tab-status.py escape-recheck <log> <transcript>
<press-size>`, env carrying the window id): an Esc that kills a turn mid-think
leaves no signal anywhere (the interrupt-watch KNOWN GAP — docs/tab-colors.md),
so the tab would sit magenta and the dashboard would keep showing busy; a web
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
other immediate no-confirm header action — ■ stop would otherwise double-send
Escape mid-flight; it re-enables on settle (re-deriving from the tab, so an idle
turn keeps it disabled). This is button-closure state, not `S` like the card ✕
(above): nothing tears the detail view's action row down mid-action, and the
Esc-KEY gesture path has its own debounce (`escHold` when idle, the `escFired`
window when busy), so the lock lives on the buttons rather than the shared
`interruptSession`/`migrateSession` functions (which just return their POST
promise for it).

`POST /api/session/<sid>/rewind` opens Claude Code's rewind/checkpoint menu.

- **MID-TURN** (a `BUSY_TABS` colour): **409**. This endpoint used to FORK
  here — a mid-turn double-Esc meant "cancel the turn and restore the message",
  and that fork WAS the ⊘ cancel button. It is gone (2026-07-25): `post_interrupt`
  produces the same take-back with ONE Escape, decided by when you press rather
  than how many times (see *Interrupt*), so the two gestures were one gesture and
  the verified one survives. Mid-turn the menu is simply unavailable — a typed
  `/rewind` would queue as a message. A red `awaiting-command` tab refuses even
  earlier (`_dialog_open_guard` — see the interrupt section), since typing
  `/rewind` would land in the open dialog.
- **IDLE**: double-Esc opens the rewind/checkpoint menu (restore code
  and/or conversation, summarize; checkpoints are automatic, one per user
  prompt — code.claude.com/docs/en/checkpointing.md). Mirrored by **the
  `/rewind` command** (documented identical) — NOT synthesized key events:
  measured on a live idle session, two `send-key` Escapes opened the menu
  only ~2/3 of the time at the BEST gap (0.15 s), ~1/3 at 0.5 s, never
  from one batched call, focus irrelevant, while `/rewind` opened it
  **every time**. No Escape ⇒ no recheck. The command is **pasted, never
  typed** — see *Slash commands are pasted* below.

Every attempt rides a `web-rewind` audit row (`{win, ok, tab, clip}`; a busy
refusal carries `refused: "busy"`). The take-back's `restored` moved to
`post_interrupt`, where the screen — not a guess at the last prompt — decides
whether there was one.
Same guard chain and window discipline as the other writes. The page now
calls this endpoint only for the MID-TURN meaning (the cancel); its idle
rewind is the full web rewind below — the endpoint's idle branch (type
`/rewind`, navigate in kitty) survives for API callers and tests.

**What the page does on a take-back — the full loop, no jumping to the
terminal.** `applyTakeBack` drops the discarded prompt bubble from the feed
(kitty un-renders it too, and it is genuinely out of the conversation: the
record stays in the transcript FILE but orphaned, which `_dead_uuids` prunes on
the next full read — see *Discarded prompts* — so this removal only says sooner
what the server would say anyway) and puts `restored` into the composer for
editing. Resending the edit goes through
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

Known limit (Claude-Code-imposed): a take-back that ORIGINATES in the kitty
tab (you press Esc there) reaches the web only PARTLY, because Claude Code
fires no hook for it — nothing observes it, so there is no `takeback` flag and
no composer prefill. The ghost bubble still goes, just later: the discard
becomes visible in the transcript's parent chain as soon as the next prompt
lands, and `_dead_uuids` prunes it from then on (*Discarded prompts*). The web mirrors a take-back it
TRIGGERED; a terminal-side one it can only clean up after.

The **`escape-recheck`** the interrupt spawns watches the transcript for a
new `"type":"user"` RECORD, not raw byte growth: the gesture appends pure
METADATA
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
- **Input hygiene:** control bytes are stripped (`NAME_CTRL`) — the name
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
  list renders (`menu_open`: the `Rewind` header + `to continue`
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

  **Marker drift (2026-07-25).** That footer is COMPOSED at runtime: Claude
  Code renders `[<chord label>, " to ", <action>]`, and the chord label has
  three formats (`Enter` / `enter` / `⏎`). The detector matched the whole
  phrase in title case, measured on v2.1.214; by v2.1.220 it no longer matched
  and EVERY web rewind failed with `step: "open"` — "checkpoint menu never
  appeared" — while the menu was in fact open on screen. Only the action word
  is the product's own literal, so `MENU_FOOT` is now `to continue`, matched
  case-insensitively; `CONFIRM_HEADER` stays a whole-phrase match because it IS
  a JSX literal. This is the repo's own rule paying out (CLAUDE.md,
  *Experimenting with live sessions*): screen markers are version-fragile, and
  a composed one is fragile twice over.

  The failing step now also carries the SCREEN it gave up on
  (`StepError.screen` → a clipped `screen` field on the `web-rewind-to` row and
  its `errors` row). Without it, `step: "open"` cannot distinguish "the menu
  never opened" from "our marker stopped matching a menu that did" — which is
  why this took three rounds to find instead of one look.

The endpoint refuses a BUSY tab outright (409 — mid-turn the gesture
means cancel, and a typed `/rewind` would just queue as a message; stop
or cancel first). Success returns `restored` (the target text) for the
conversation-restoring modes: Claude Code puts the rewound prompt back
into the TUI input, so the page runs the same tail as a take-back
(`prefillComposer`) — composer prefilled, next send `clear_draft` — and
`applyRewind` un-renders everything from the target bubble on, matching
what the terminal now shows (optimistic like a take-back: the transcript
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
take-back above), idle picking mode (no POST until you pick a message),
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
with `suggest()` — the same menu language over the snapshot's distinct PROJECT
directories — NOT a `<datalist>`: Safari renders that list in the system style
too, and pops it open on focus, which made the prefilled field look
already-clicked. The suggestion list is built from `groupKey(row)`, NOT the raw
`row.cwd`: that is the same grouping notion the list page's project headers use
(the server's `group_dir` — the session's frozen original cwd resolved to its
linked-worktree OWNER, with `cwd` as the legacy fallback), so a session running
in a throwaway `.claude/worktrees/<name>/` checkout (`EnterWorktree`, or any
`git worktree add`) is offered as its MAIN checkout instead of the worktree
path — nobody starts a new session in a worktree that an agent is about to
delete, and the noise crowded out the real projects. `groupKey` in
app.00-core.js is the ONE client-side implementation, shared with
`groupSessions`, so the picker and the list can't name the folder differently.
SCRATCH directories are dropped on top of that (`nsSuggestDirs` / the
`NS_SCRATCH` regex): a `/tmp` ANYWHERE in the path — `/tmp/…`, a realpath'd
macOS `$TMPDIR` (`/var/folders/…/T/tmpXXXXXX`), the hermetic suite's per-test
dirs — is a throwaway that no longer exists by the time it could be clicked.
Accepted false positive: a genuine project under a `/tmp`-prefixed component
(`~/code/tmpl`) is menu-invisible; the field is freeform text, so typing or
pasting the path still launches there — the menu is a shortcut, never the only
way in. This filter is deliberately PICKER-ONLY, not applied to the list: a
session running in a tmp dir must still show its card (that's how you find and
close it), it just isn't a place to START one.
Only a pointer CLICK on the field (or typing / ArrowDown) opens the menu —
never focus alone, which also fires on the form's own auto-focus — with the
value blank or an exact known directory it lists EVERYTHING (the picker look, current
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
<sid>` is emitted; `body.resume` still validates against `SID_OK` (one clean
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

**Space previews the recent mirror transcript — in a POPUP WINDOW.** With a row
highlighted, `Space` opens a roomy overlay (`.nspvback`/`.nspvpanel`, up to
960px × 88vh) STACKED ABOVE the new-session form (z-index above `.nsback`) — the
inline panel it replaced was too cramped to read. It fetches the session's recent
mirror tail from **`GET /api/session/<sid>/backlog`** (the newest `TAIL_BLOCKS`
slice — the mirror tab's own on-load call) and renders it with `renderPreview` —
the same server `{g,t,html}` items and block grouping the mirror tab uses, into a
throwaway container (never `S.ses`), blocks FOLDED so it's a compact scannable
peek (command/file/agent blocks collapse to a one-line summary; conversation
messages show inline; click a header to expand). Use `backlog`, NOT
`/history?before=N` — `/history` returns blocks *older than* a cursor, so
`before=0` returns nothing (the "no mirror history" bug the first cut shipped).
The popup closes on `✕`, click-outside, `Space` again, or `Esc`, returning focus
to the row; because it lives on `document.body` (not `$modal`) and the form has a
document-level `Esc`→close, the popup owns a **capturing** keydown handler that
`stopPropagation()`s so `Esc` dismisses the popup, not the form, and
`closeNewSession` tears down a still-open popup (`resumePreviewCleanup`). For the
picker to be keyboard-drivable, selecting a row updates its highlight IN PLACE (a
full repaint would recreate the row element and drop keyboard focus, so `Space`
would land nowhere — the "space did nothing after I clicked" bug); on open the
**search box** is focused so a query can be typed with no extra click — EXCEPT a
`↻ resume` deep-link (which preselects a specific row and focuses IT, ready to
Enter) and an iPad (where focusing an input pops the on-screen keyboard, so it
falls back to the selected row — `focusSearch`). The `resume.preview` audit row carries the rendered item count `n`, so
an empty-but-successful preview is distinguishable from a rendered one in the DB
alone (the blind spot that made the first diagnosis need an endpoint repro).

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

*The `wake` fast path (server).* On a successful launch `launch_wake` (a
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
`repr()`'d — the shared `Handler._reject_input` helper. Pass it a `sid` and the
reject files under THAT session's timeline (not just the global stream) —
without which every empty-message / empty-name / bad-payload reject was a silent
4xx, the exact class the `web-reject` guard fix closed one layer down. That `sid`
resolves through `_audit_target`, the one owner, **so a handler's reject row and
its success row land in the same place**: the 11 session-scoped sites used to
hand over a re-derived `log=P.mirror_log(sid)` instead, and since `_audit_target`
prefers the audit row's own `log` and `session_row` walks the adopt FORK CHAIN,
the two disagreed for a sid whose `sessions` row wasn't written yet (a
`--resume`/backgrounding fork before `adopt.py` catches up) — the success row
joined the predecessor's timeline while the reject landed under a sid with no
row at all, which is itself a canned anomaly signature. They also passed no
`path`, so a reject carried no state-DB attribution while its sibling did. The
explicit `log`/`path` arguments remain for the two callers that can't use `sid`:
`post_upload`, whose sid is OPTIONAL (it resolves the target once, global `""`
when absent), and `post_client_log`, which resolves one target per BATCHED event. Deliberately NOT an `errors` row: these are expected
client-input 4xx, not swallowed exceptions (their traceback would be a bare
`NoneType: None`), and `errwatch` surfaces every `session_id=''` `errors` row as
a `⚠ global:` chip in EVERY session's scorebar — so a stray "ba" typed into the
new-session form must not light a warning light that never clears. Genuine
server-side failures (no terminal, launch/grant returned false) stay `A.error` —
those ARE bugs worth the light. (The stash-race 409s — `no pending question` /
`ask expired` on the ask/plan cards — deliberately stay row-less: they fire
legitimately when the dialog was resolved AT THE TERMINAL, and the
`ask-pending`/`plan-pending` stash lifecycle already records that.)

Those stash-race refusals have ONE owner per dialog kind: `_ask_stash` for the
two ask endpoints (`answer` drives the dialog, `ask-draft` only stashes
selections) and `_plan_guard` for the two plan ones. The `tool_use_id` match is
the load-bearing half — it is what stops a decision meant for a REPLACED
question from being typed into the dialog that took its place — and the ask side
used to hand-roll all three refusals at both call sites, which is how they came
to answer a stale card with two different bodies (a bare `ask expired` vs the
fuller "a newer question replaced it (refresh)"). `count=False` is the one
per-caller knob: `answer`'s `chat: true` declines the questions rather than
answering them, so it carries no `answers` list to length-check.

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
  - **Notification delivery**: `notify.recv` (`kind`, `shown`, `vis`, `focus`) —
    whether THIS device received the immediate toast SSE and whether it showed it
    (only the focused+visible device does). The frontend bracket around the
    backend `notify-route`/`notify-arm` device-routing rows: a "I didn't see the
    toast on this device" is explained by a `shown:false` recv (you weren't
    looking here). Every clientlog batch also carries this browser's `device` id,
    so `notify.recv` — like all `web-client` rows — is device-attributable.
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
  - **Composer history recall** (*Web composer history* above): `composer.recall`
    (`dir` up|down, `idx` = the recalled entry index or `"draft"`, `n` = the
    history size) — a purely client-side ↑/↓ affordance the server never sees,
    so the browser is its only witness (a "↑ gave the wrong message" report is
    answerable from the `dir`/`idx`/`n` trail).
  - **File pastes** (*Web attachments* → *Pasting a copied FILE* below):
    `attach.paste` — `n` = how many Files the paste carried, `resolved` = how
    many the host's pasteboard read answered with. `resolved > 0` = the paths
    were spliced into the box (the kitty-parity branch); `resolved: 0` = the
    files were uploaded as attachments instead (a screenshot, or a device whose
    clipboard isn't the host's). The client is the only witness to WHICH branch
    ran — the `web-clipboard` row records what the server was asked and what it
    answered, but not what the page did with it.
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

Both halves of that in-flight state now have ONE owner, `closeBegin` /
`closeSettle` in `app.00-core.js` (`test_close_in_flight_state_has_one_owner`):
`S.closing` (the greyed card + disabled ✕) and `S.closePend` (the `optPending`
handle) have to move together, and the handle must settle EXACTLY once — a
leaked one beacons a bogus `web-hint stale` 20s later for a close that did
resolve, i.e. it manufactures the very bug signal this machinery exists to
report. Three sites in two files (the card ✕, the header ✕, `reconcileCloses`)
hand-rolled the pairing; every other site only READS the maps.

**The launch tag-race (why a just-launched session's controls were dead).** A
dashboard launch jumps straight to the new sid, but its kitty pane isn't tagged
`claude_session=<sid>` for a moment, so `/api/session` momentarily reports
`live:true` with a BLANK `kitty_window_id` (`session_payload` resolves the
window through `live_windows`, empty until the pane is tagged). The client gates
the composer AND the `✕ close` button on `meta.live && meta.kitty_window_id`, and
that partial meta fails BOTH the send gate (`live && window`) and the resume
gate (`!live`) — so the box locked and the close button never rendered until a
manual reload (the reported bug). `showSession` re-fetches meta directly
(bounded, `LAUNCH_RESOLVE_TRIES` × `LAUNCH_RESOLVE_MS`) until the window
resolves — authoritative and self-healing, no reload needed.

**Both endpoints serve the SAME window id (the action-row flicker fix,
2026-07-24).** The sessions LIST used to carry the RAW start-time audit
`kitty_window_id` while `/api/session` served the live-RESOLVED one — two
different id-spaces for the same field. The client's `updateHeadFromList`
compares the list snapshot's window against the open session's `meta` window
(gated on `row.live`) and rebuilds the header on a change; across the tag-race
those two disagreed (`""` resolved vs a raw id), so it read a spurious "window
moved" and rebuilt the header EVERY list tick, fighting `loadMeta` and flickering
the action-buttons row on/off 2–3× until the pane tagged. Fixed at the source:
`sessions_payload` now reconciles a LIVE row's `kitty_window_id` to the same
`live_windows` resolution `session_payload` uses (blank until tagged, then the
same id) — so the two endpoints agree, the compare is apples-to-apples, and the
row appears exactly once when the window resolves. The demotion check still runs
on the RAW id first (it needs "this row ever claimed a window"); a not-live row
keeps its raw id (never compared). It also gates the list card's own `✕ close`
correctly — it now shows only once the window is really tagged, not prematurely.

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
the ask payload in `ask_wire` — kept OUT of `ask_pending`, which is the
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
draft). `ask_draft` only returns the draft while it still matches the
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
retired cancel gesture once fired its Esc-Esc into an open ask (the tab was
red `awaiting-command`, which the cancel path wrongly treated as a
cancelable mid-turn state), so by the time the user's answer POSTed the
dialog was already declined and `drive` bailed at the very first check with
`AskError("open", "no question dialog on screen")` — the "I answered but it
failed, and the tab said *User declined*" report (2026-07-20). The fix lives
on the gesture side (`_dialog_open_guard`, the interrupt section): no web
interrupt or rewind sends a key while a red dialog is open.

**The open-check polls (2026-07-22).** `drive`'s first check — is the dialog
on screen at all — used to read `get_text` ONCE with no retry, unlike every
later step (which polls via `_wait` up to `STEP_TIMEOUT_S`). So a capture
taken a beat too early — the dialog still rendering right after a `--resume`
into a fresh kitty window, or a transient blank/partial `get_text` — bailed
immediately with `step: open` on an ask that was genuinely up and never
answered (session `0247ebb2`, 2026-07-21). It now polls like the rest. And
every `AskError` carries the SCREEN it saw (`e.screen`); `post_answer` folds
it (via `clip_screen`) into the `dashboard answer (<step>)` audit `errors`
row's `screen` field, because the bail otherwise records only its outcome — a
step:open can't be told apart after the fact (dialog too tall for the visible
screen · a `FOOT`/`REVIEW` footer-string drift after a Claude Code upgrade · a
blank capture) without the pixels. `clip_screen` keeps BOTH ends of a long
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
- the card detects a preview question (`qHasPreview` — that question's own
  options carrying a `preview`) and, on a TYPED answer to THAT question, routes
  it through "Chat about this" AND carries the typed text as `message` in the
  `/answer` body. `post_answer` presses chat, waits for the dialog to close,
  then delivers the text as a normal message (`fe.paste_text`, a `web-send` row
  `via: ask-chat`) — so the custom answer reaches the session. Selecting an
  *option* still drives normally;

  **The escalation is PER QUESTION, and it never discards picked answers**
  (fixed 2026-07-26; pinned by `test_ask_submit_never_discards_picked_answers`
  over `tests/jsdom/asksubmit.js`). Both halves were wrong and together they
  ate a user's answers four times running:

    * the test was the ask-WIDE `askHasPreview(ask)` — true if ANY option
      ANYWHERE in the ask had a preview — so a preview on question 1 hijacked
      typed text on question 4, a question with an ordinary layout that has a
      perfectly good free-text row;
    * and a chat escalation sends **no `answers` array at all**, so every
      option picked on every other question went with it. One typed word beat
      four answered questions, the tool received "no answer provided", and
      nothing in the UI said so.

  The tell in the audit is a `web-answer` with `chat:true` sitting next to a
  tiny `web-send` `via: ask-chat`, where a genuine submission would have
  carried `answers`. Escalating is unavoidable when the dialog cannot accept
  the text; losing the rest is not, so the `message` now states the WHOLE
  submission (`header: value, value` per answered question). This is exactly
  the class of bug a grep cannot see — it is about which branch a compound
  condition takes and what that branch omits from the body — hence the
  harness that executes `submitAsk` and asserts on the POST body;
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
(`heal_stash` → `state.kv_del_at`, the explicit-path fresh-connection
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
the session has no tasks. The task list itself is read-only — unlike
ask/plan there is no modal to drive, and nothing on the page ever
completes, re-opens or deletes a task (those are the TUI's; the `tasks`
kv is only a snapshot of Claude Code's own records).

**Dismissing a finished card (the header ✕).** A list whose work is
done still sits pinned above everything else, so the header carries a
`✕` that hides the card. Three properties, each deliberate:

- **Only when every task is completed.** The button is `disabled`
  otherwise, and `POST /api/session/<sid>/tasks-hide` **409s** on an
  unfinished list — the same disabled-button/authoritative-409 pair as
  the list page's group-hide ✕, because a stale page can still POST. The
  predicate has ONE implementation (`read/session.py tasks_done`), so
  the button and the endpoint cannot disagree.
- **Purely visual, and cross-device.** It writes nothing but the
  `tasks-hidden` key of the durable global prefs store (`dashboard/
  prefs.py`) — no task, no session state, no terminal. Global like
  `view-mode`/`notify-muted` rather than per-browser `localStorage`
  precisely so it FOLLOWS you: dismissing on the phone must un-pin the
  desktop page already open on that session. That is also why the SSE
  channel's value is the card's whole state (`tasks_card` → `{tasks,
  hidden}`) instead of the bare list — the dismissal moves no task, so a
  list-only diff would never fire and the other device would keep the
  card until the next `TaskUpdate`. It survives park with the list.
- **No un-hide button — the ids are the expiry.** What gets stored is
  that finished list's ID SET, and the card is hidden only while every
  task is still completed AND every current id is in that set. So the
  next `TaskCreate` (or a completed task re-opened) makes it a different
  list and the card comes straight back. A bare `true` flag would have
  needed an un-hide gesture, and a card you dismissed once would then
  swallow the next thing Claude Code plans — the failure mode worth
  designing out. `hidden: false` exists on the endpoint anyway, but only
  as the page's own undo when the optimistic paint's POST fails.

The gesture is a two-step confirm (`armConfirm`, `✕` → `hide?` → fires),
the same "ask once" helper as ✕ close and ⊜ compact — but its armed
state is AMBER, not their red: nothing here is destructive and the
button must not claim otherwise. Audited as a `web-taskshide`
`state_files` row (`{sid, hidden, ids}`), pruned to `TASKS_HIDE_MAX`
dismissals by recency.

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

`session_payload` carries the list as `data["tasks"]` (and the
dismissal as `data["tasks_hidden"]`) — deliberately **NOT live-gated**
(unlike `ask`/`plan`): the kv survives park, so a parked session still
shows its final task list. The per-session SSE diff-emits a `tasks`
event carrying both fields (`tasks_card`) on the slow cadence (tasks
change per-hook, not per-keystroke; nobody is blocked waiting on this
card).

## Web goal (the pinned goal card)

Claude Code's `/goal <condition>` built-in (2.1.139+) puts the session
into an **autonomous mode**: Claude works across turns toward a stated
completion condition until an internal checker confirms it, at which
point the goal auto-clears. The dashboard mirrors the active goal as a
**goal card pinned at the very top of the mirror tab — above the tasks
card** (`buildGoalCard`/`renderGoal` in app.js, `.goalcard`): a ◎ mark,
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
worklet converts Float32→Int16 and **resamples to Deepgram's own 16 kHz model
rate** (`DICT_RATE`, declared in `sample_rate=` — see *Dictation lag* below for
why it is no longer the native rate), batched to `DICT_CHUNK`-sample chunks
(1024 = 64ms @16k) so the socket sees a sane message rate — bare 128-sample
render quanta would be ~375 tiny ws messages/s, and Deepgram wants 20–250ms.
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

### Instant-on mic

The second half of the same report (2026-07-27): *"it takes a lot of time for
the microphone to be in a ready state — I want to dictate as soon as I press
the button."*

**The wait was a CHAIN, and most of it didn't need to be.** Activation used to
run: mic permission ∥ token mint → **wss handshake** → **worklet compile** →
graph → `.rec`. Only the first pair was concurrent. So the mic went live only
after a tunnel round trip to our server, the server's *own* HTTPS call to
Deepgram's `/v1/auth/grant`, a wss handshake from the device, *and* an
`addModule()` compile had all completed **in series** — and nothing about
compiling a worklet or asking for the microphone depends on any of that. All
three legs now start together inside the click's gesture chain, and **capture
begins when the mic and the worklet are ready** — typically the permission
grant alone. The token and the socket finish on their own time.

**The preroll is what makes that safe.** Audio captured before the socket is
ready is *held* (`DICT_PREROLL_MAX_S`, a safety valve rather than a budget —
the JWT itself is only ~30s) and flushed in one burst the instant the socket
opens. Deepgram consumes a *stream* and does not care that its first seconds
arrived faster than real time. So the press-to-connect gap stops costing you
the words you said during it: **speak the moment the button turns red.**

**The button tells the truth about both halves.** `.rec` (red, pulsing) now
means *your voice is being kept*; the `.pre` modifier keeps the `.wait` blue
ring while the connection is still coming up — composed from the two states
that already existed rather than inventing a third colour for "half ready".

**Stop is honest about the pre-open window too.** Pressing stop before the
socket opens does **not** throw the sentence away: if anything was said, the
connection is allowed to finish, the preroll flushes, and *then* `CloseStream`
goes out, so a short dictation over a slow link (press · six words · stop —
the case that used to lose everything) still lands. If nothing was said there
is nothing to wait for and no connection is opened at all. A
`DICT_STOP_GRACE_MS` failsafe covers a handshake that never lands.

Consequences that had to be handled, each of which is a test in
`tests/jsdom/dictstart.js`: `live` is now published at *arm* time (the button
must toggle to stop, and `stopDictation()` must be able to reach this mic,
during the whole pre-socket window); a `starting` latch covers the async gap
before `live` exists, since two quick presses would otherwise run two
pipelines and orphan the first's mic; the re-anchor `input` listener is
registered at arm rather than at press, so a denied mic leaves nothing
attached to the textarea; and `finish()` now owns closing a socket that may
still be *connecting*.

One deliberate trade: since `.rec` can precede the first transcript, you can
now speak and hit send before any text exists. That degrades gracefully rather
than sending a truncated message — `send()` already bails on an empty box, so
nothing goes out and the words land in the composer a moment later. The
principle is unchanged: **the visible, validated text is what sends.**

Measured by `arm_ms` (press → capturing, the wait you still feel) and
`open_ms` (press → socket, the wait you used to feel) on the `dictate.start`
and `dictate.stop` clientlog events, with `preroll_s` recording the speech
that would have been lost between them.

### Dictation lag

The reported symptom (2026-07-27): *"I say a lot of words and it takes some
time for them to appear in the input"* — from an **iPad over the tunnel**.

**The uplink was ours to fix.** The worklet used to declare and send the
AudioContext's **native** rate untouched — the design note said "no resampling
code", which was true and was the bug. 48000 Hz × 2 bytes mono is **768 kbps of
sustained upload**, and because continuous PCM is deliberate (silence is data,
which is what keeps Deepgram's no-audio timeout from firing) it is 768 kbps for
as long as the mic is open, pauses included. An iPad on a phone-grade uplink
cannot hold that, so `ws.send()` queued faster than the socket drained and
**the delay grew with every sentence** — nothing anywhere read
`ws.bufferedAmount`, so the backlog was invisible and unbounded. That growth is
the tell: a slow *API* is a roughly constant delay; a saturated *uplink*
compounds. Deepgram's models are 16 kHz, so every sample above that was pure
upstream cost buying zero accuracy. `DICT_RATE` = 16000 is **3× less** wire.

**Why the resampling is ours and not the browser's.** `new
AudioContext({sampleRate: 16000})` needs no DSP at all and was rejected: the
first-class client is iPad Safari (docs/remote.md), and a non-native-rate
context feeding `createMediaStreamSource` is exactly where Safari's own
resampler has a history of misbehaving. **A silent mic is a far worse bug than
a laggy one**, so the conversion happens in the worklet and behaves identically
on every browser. It is a 4th-order Butterworth low-pass (two cascaded biquads
at the Butterworth Q pair, cutoff 0.425 × the output rate) into a **fractional**
linear-interpolating read head — fractional because 44100 devices are real and
44100/16000 is 2.756, so a decimate-by-N would be wrong there and only there.
The filter is not optional: without it everything above 8 kHz **folds back into
the speech band**, landing sibilant energy on top of vowels, which is worse for
ASR than the HF being dropped. Hardware already at or below 16k (8k/16k
devices) is a byte-exact **passthrough** — filtering a 1:1 stream only degrades
it. The rate is decided **once**, on the main thread, before the mint: it is
baked into the listen URL's `sample_rate=` *and* handed to the worklet as a
`processorOption`, because a re-derived rate could disagree and mislabel the
stream. `tests/jsdom/dictpcm.js` executes the real worklet over synthetic
tones (exact sample counts at 48k and 44.1k, unity passband gain, a 12 kHz tone
attenuated to ~0.05, byte-exact passthrough) — every one of those failures
produces plausible-looking PCM that no grep would catch.

**And the delay is now attributed, not guessed.** "Dictation is slow" was
unanswerable from the audit DB by construction: the server mints a token and
**never sees the stream** (that is the whole architecture), so nothing on this
machine knew whether the words were stuck in our socket or still inside
Deepgram. The browser now samples both onto the frontend audit channel
(*Frontend audit (clientlog)*), every `DICT_LAG_MS`, in seconds-of-audio so
they add up to the delay you actually see:

- **`queue_s`** — `ws.bufferedAmount / (rate × 2)`: audio sitting in **our**
  send buffer, i.e. uplink we can't keep up with. This is the one that grows,
  and the one we can fix.
- **`svc_s`** — audio the network has taken that Deepgram hasn't accounted for
  yet, measured against **Deepgram's own audio clock** (its `Results` carry the
  segment's `start` + `duration`). This one is theirs, and should be roughly
  constant.

Events: `dictate.start` `{rate, native, arm_ms, open_ms, preroll_s}` (the
native rate is what proves the resampler is engaged on that device; the two
`_ms` figures are the *Instant-on mic* measurement — `arm_ms` is press →
capturing, `open_ms` press → socket, and their gap is what the preroll
covers), `dictate.lag`
`{queue_s, svc_s, sent_s, buffered}`, a one-shot `dictate.backlog` +
**toast** when `queue_s` passes `DICT_BACKLOG_WARN_S` — worth knowing *while*
you speak, not after you send — and `dictate.stop`
`{rate, spoke_s, max_queue_s, max_svc_s, arm_ms, open_ms}`, the maxima rather
than whatever the last sample happened to catch, so one session is comparable
to the next (`open_ms` 0 there = the socket never came up at all).
Collapsing the two into a single "lag" number would put the next report right
back at guessing. Read them with:

```sh
python3 bin/claude-audit.py sql "SELECT ts, content FROM state_files
  WHERE action='web-client' AND content LIKE '%dictate.%' ORDER BY ts DESC LIMIT 40"
```

**Audit.** Every mint attempt is a `web-dictate` `state_files` row (no sid —
the new-session form dictates too), `{ok, rate, cwd, keyterms}` on success
(`cwd` + the term count answer "why didn't my project word bias" — an empty
`cwd` there means the sent directory failed the isdir guard),
`{ok: false, why: bad-rate|no-key|grant}` on failure, grant failures also an
`A.error("dashboard dictate (grant failed)")`. "Mic button missing or dead"
triages as: `/api/dictate` says available? → `web-dictate` rows → dictate
errors (the audit-debug skill's bug shape). "Dictation is SLOW" is a different
question with different evidence — the `dictate.lag` clientlog rows above, not
the mint rows; a `web-dictate` `rate` back at 48000 would mean the resampler
never engaged on that device.

## Stats / Insights (`GET /api/stats`)

The header's **▦ stats** button routes to `#/stats` — a GitHub-Insights-inspired
cross-session, over-time view (the list page only shows *current* sessions).
The unit is a **session** (one audit `sessions` row = one "commit"). Four panels:

- **Pulse** — a period toggle (`7d` / `30d` / `all time`, client-side over
  precomputed windows) driving KPI tiles (sessions · active · ended · tokens ·
  cost · errors) plus a top-projects ranked bar list. **active** is GENUINE
  liveness, NOT `ended_at IS NULL`: since Claude Code fires no hook on
  cancel/kill/crash and a reboot wipes `/tmp`, a session that died without a
  clean SessionEnd keeps `ended_at=NULL` in the audit corpus forever, so
  counting those as active over-reported it wildly (13 "active" against 4 truly
  running). `stats_payload` reuses the list page's OWN window-corrected
  liveness (`sessions_payload`, exactly as `dir_live_sessions` does — a live
  session is always recent, so `SESSIONS_LIMIT` discovery always covers it), so
  the Stats "active" and the list page can't disagree. Consequently **active +
  ended no longer partitions sessions** — a stranded `ended_at=NULL` row that
  isn't actually live is neither (the three tiles are rendered independently).
- **Contributions** — the green calendar heatmap: weeks as columns, 7 day-rows,
  one cell per day, 5 self-normalized intensity buckets (0 + quartiles of the
  nonzero days, so the scale adapts to your own volume). Month + Mon/Wed/Fri
  labels, a *less→more* legend, per-cell tooltip.
- **When you work** — the day×hour punch card: a 7×24 grid of bubbles whose
  RADIUS ∝ sessions started in that slot (size encoding, GitHub's punch card).
- **Projects** — one card per project (grouped exactly like the list — `start_cwd`
  canonicalised + resolved to its worktree owner via `group_dir`), each with a
  90-day sparkline and token/cost/error counters.

**Data + optimization.** Everything is computed SERVER-side (single-owner rule;
the JS only renders SVG/DOM — no chart library) by `stats_payload()` over
`core.sessionapi.activity_stats()`, which runs a handful of indexed `GROUP BY`s
against the audit `sessions`/`otel`/`errors` tables (the DURABLE cross-session
record — per-session state DBs get parked). Daily counts and the punch card are
SQLite `date(…,'localtime')` / `strftime('%w'/'%H',…)` buckets; per-session
tokens/cost come from two grouped `otel` passes folded in Python (not one query
per session). The whole payload is memo-cached for `STATS_TTL_S` (wall-clock,
distinct from the per-state-DB `_db_sig` memos) so re-opening the page is free.
Heatmap bucketing is deliberately left client-side so the scale self-normalizes
without a round-trip. Like `accounts_payload`, ctx saturation, and the goal probe,
it is a **read-only aggregate — it adds no audit rows** (nothing to record: it
neither writes state nor drives the terminal). No `sid_chain` resolution: these
are whole-corpus SUM/COUNTs where each row/datapoint is already counted once (a
forked sid's tokens land under whichever `sessions` row `adopt.py` wrote).

The stats view shares `#view` with the list, so `renderList()` bails on the
`#/stats` route (`onStats()`) — otherwise a live `sessions` SSE tick would repaint
the list over the stats page.

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
- **Refresh ownership** (one rotating credential, cooperative writers).
  Anthropic ROTATES the refresh token on every refresh and revokes the whole
  token family when a superseded refresh token is replayed (reuse detection).
  The original design — "never overwrite Claude Code's copy; persist rotations
  only in baqylau's own `baqylau-model-usage: <service>` mirror entry" —
  therefore STOLE the family whenever it refreshed a login Claude Code still
  uses: Claude Code kept the superseded refresh token, replayed it on its next
  refresh, and the server revoked the family, surfacing as
  "401 OAuth access token has been revoked" /login loops several times a day
  (diagnosed 2026-07-27). The fix cooperates the way a SECOND Claude Code
  process would: refresh only when the token is (near-)expired
  (`grant_type=refresh_token` at `platform.claude.com/v1/oauth/token`), then
  WRITE THE ROTATION BACK to Claude Code's own entry, merged over the prior
  blob so plan metadata (`subscriptionType`/`rateLimitTier`) survives — its
  keychain watcher picks up the new tokens instead of replaying stale ones
  (both sides use `/usr/bin/security`, so no ACL prompt). Legacy mirror
  entries are still READ (fresher-copy wins) so a family living only there is
  adopted back into Claude Code's entry on its next refresh, but they are
  never written again. **Coverage is limited to accounts with a scoped
  keychain login** — a switcher-only account gets a bar only after one
  interactive `claude auth login`.
- **Tokenless-departure discipline.** This is the sole API call in a
  tokenless-by-design tool, so it is gated (`CLAUDE_MODEL_USAGE=0` disables),
  macOS-only, TTL-cached (60s — the keychain/network work runs at most once a
  minute however often the page polls) **stale-while-revalidate**, and
  **fail-silent + audited-once**. Stale-while-revalidate + a parallel fan-out,
  because the synchronous TTL expiry was a reported reload stall (2026-07-27,
  "the accounts strip takes a few seconds to appear"): the page's poll
  interval equals the TTL, so nearly every reload landed on an expired cache
  and its `/api/accounts` blocked on the whole fan-out — measured ~3–4s, the
  bulk of it five keychain login services fetched SEQUENTIALLY at ~0.5s of
  HTTPS each. Now a call past the TTL returns the PREVIOUS value immediately
  while ONE background thread (single-flight, in-process daemon — not a
  detached process, so no stream rows) recomputes; the per-service GETs run
  on a small pool (`FETCH_WORKERS`, `account_usage` resolved once up front so
  the shared `db_cached` memo stays off the pool threads); only the first
  call of a process computes synchronously (~1s parallel). Serving stale is
  correct by construction — the value is a ≤TTL-stale snapshot by design, and
  a completed refresh (even a failed one, which stores `{}` like the old
  design) restarts the TTL clock, so staleness stays bounded at one TTL plus
  one refresh. The fresh value reaches open pages via the global stream's
  `accounts` event (*The list renders once, then patches*), whose per-tick
  read also keeps this cache perpetually warm while any page is open. A
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

**The 5h bar is pegged to 100% under an account-wide limit.** The frozen
snapshot doesn't just understate by a few percent — after a rate-limit
**migration**, the blocked account's state DB is re-stamped to the NEW account
(adopt.py), so the OLD account's freshest snapshot is whatever a stale/older
session last captured, which can be far below the cap (measured 2026-07-24: a
migrated c2 sitting at its 5h session limit showed a **25%** 5h bar, 98 min old,
*under* a "limit hit" chip — and its 5h/7d coincidentally equal at that frozen
moment, the "why are 5h and 7d the same" report). So when an ACCOUNT-WIDE
`limit_hit` is active (no `model` scope — the whole account is blocked),
`accounts_payload` pegs the served `five_hour` to **100%** and aligns its reset
to the stamp's `resets_at` (the session limit resets on the 5h window;
`relimit` sources the stamp's `resets_at` from `five_hour_reset`). This is the
inverse of the model-scoped mid-week-reset override above and, like it,
DASHBOARD-PRESENTATION ONLY — the tokenless snapshot and the relimit target
picker (`five_hour_eff`) stay honest. A MODEL-scoped stamp does NOT peg the 5h
bar (only that model is capped; Opus/Sonnet still run on the account). The 7d
bar is left on the snapshot (only the 5h/session limit is known-maxed).

### Logged-out accounts

An account whose OAuth login has been **revoked or expired** (you logged out, or
the token was invalidated server-side) is flagged on its pill with a filled red
`⚠ logged out` badge — louder than the outline `limit hit` chip because a launch
there dies immediately, so it is the account's headline state. The badge's
tooltip is the CLI's own message (`"Please run /login · … OAuth access token has
been revoked."`).

Same as the limit-hit chip, the truth comes from an **event, not a probe**: a
main-session turn under a logged-out account dies on a `StopFailure` carrying
`error="authentication_failed"`, which `relimit` stamps as a per-account
`logged-out` kv (docs/relimit.md *Logged-out accounts* — including why probing
the account's token is both unreliable and dangerous). `accounts_payload` serves
it as `logged_out` (bool, via `sessionapi.logged_out_active`) + `logged_out_msg`.
Because a logged-out account may have no fresh usage snapshot, `renderAccounts`
shows a pill when it has usage **or** a logged-out flag (it otherwise hides
usage-less accounts).

**Clears on re-login, no hook.** Logging back in is itself a session; its
status-line `usage` snapshot lands more than `sessionapi.LOGGED_OUT_GRACE_S`
(60s) after the stamp, and `logged_out_active` drops the flag then. The grace
margin is load-bearing, not slop: the DYING session re-renders its own status
line ~0.3s after the failed turn, and a bare ts comparison let that snapshot
clear the badge before it was ever painted (docs/relimit.md *Why the grace
margin*).

**Not auto-selected.** The new-session picker never auto-selects a logged-out
account (`autoAcct` filters them out, falling back to the full list only if ALL
are logged out) and marks each with `· ⚠ logged out` in the dropdown. The
rate-limit **migration** picker skips them too (docs/relimit.md), so an
auto-migrate off a rate-limited account never lands on a dead login.

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
running model's default (`model.model_default_effort` — `high` for opus-5/4.8,
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
owner: `group_dir(start_cwd)` in server.py walks the `.git` files (via
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
  `group_dir`) and is stripped from the wire by `wire_row`.

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

## The session chrome, in named phases

`renderSessionChrome(tab)` builds the whole session view, and it does six
unrelated jobs. Since 2026-07-25 each is its own function and the entry is just
the order they go in — the styleguide's *long entry `main()`s are named phases*
rule, applied to the page:

| phase | builds |
| --- | --- |
| `chromeIdentity` | `l1` — title · state badge · parked chip · directory · sid · git chip · account chip |
| `chromeActions` | `actrow` #1 — ✎ rename / ⇆ migrate / ◉ alerts (live AND parked), then ■ stop / ↶ rewind / ✕ close (live+windowed) or ↻ resume (parked) |
| `chromeQuickCmds` | `actrow` #2 — ⊜ compact + the model/effort pickers (live-only; returns an EMPTY row the caller drops) |
| `chromeLiveRows` | the three rows that start empty and are filled by the patchers: `statsrow` · `ctxrow` · `runrow` |
| `chromeTabs` | the tab strip + its badges (fetched list length, else the payload's cheap eager count) |
| `chromeBody` | the open tab's body — the mirror composite, or a grid + the fetch that fills it |

Each phase returns its element and parks on `ses` whatever the SSE patchers
reach for later (`ses.projEl`, `ses.badge`, `ses.gitChip`, `ses.stopMode`,
`ses.quickMode`, `ses.monTab`, …). As one 350-line function this was a poor place
to look for any single one of the six: the ✕ close button sat 130 lines below the
identity chips it shares nothing with, and "does the effort picker exist when
parked?" meant scrolling for the live gate instead of reading one signature.

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

- **Server — the paused-blind diff, per row (`row_key`).** Each wire row's
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
  `transcript_path` and `log` (`wire_row`, both here and on
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
- **The accounts strip rides the same loop (`accounts` event).** Each global
  tick also computes `accounts_payload()` and pushes the full payload when it
  changed — under a **sched_score-blind** key (`lists.accounts_key`, the
  `row_key` precedent applied to the strip: the score is
  remaining%/hours-to-reset, which moves with the clock every tick, while
  every other field is step-valued — integer percentages, reset epochs,
  booleans). Connect only takes the baseline and pushes nothing: the page's
  boot fetch paints the first strip, and a connect push would also change the
  stream's event order under every existing consumer. The client
  (`app.02-router.js`) re-renders the strip and refreshes `S.accts` so an
  open new-session picker's cache tracks too; the old 60s `refreshAccounts`
  poll survives as the SSE-down fallback. Side effect the reload latency
  depends on: this per-tick read keeps `plugins.model_windows`' TTL cache
  perpetually warm while any page is open (*Per-model usage bars*).

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
history, parked sessions included. **Live updates share the same ts merge.**
The per-session SSE loop polls BOTH cursors each tick — new ops (`after`) and
new transcript records (`mpos`, resumed across reconnects like the ops cursor)
— then interleaves the delta through `merge_live()` (mirror.py) and emits it as
ONE `ops` event carrying both cursors. `merge_live` is `_merge_order`'s
increment-side twin: a two-pointer merge over the two already-ts-ordered inputs,
a record placed before the next op only when its ts is STRICTLY less (so an op
with equal ts sorts first, matching `place`'s `ots <= ts`). This closed the
"messages come after commands" inversion: the loop used to emit ops and a
separate `msgs` event in arrival order (ops first), which the newest-top feed
prepended so a turn's preceding text landed ABOVE its command — visible only
live, since a reload re-ran the backlog ts-merge and read right. `op_items` is
stateless per-op (the same per-op render the backlog window uses), so
interleaving single ops with conv items is identical to a batch render. The old
`msgs` event is gone; `/api/session/<sid>/ops` stays PURE ops (the mirror-parity
endpoint).

**The `tab` event re-resolves the window mid-stream.** `sse_session` resolves
the session's `kitty_window_id` at connect, but a RESUME moves the session to
a NEW kitty window while streams are open (the SessionStart upsert refreshes
the sessions row) — so the loop re-reads the row on the slow cadence before
polling `tab_states()`. Without this, a stream opened before the resume
polled the dead window's lingering tab state forever: the page showed the
old window's green while the real tab sat magenta (shipped).

### The stream's pushed fields are a CHANNEL TABLE

Everything `sse_session` sends *other than* `ops` is a row of `_SLOW_CHANS` /
`_FAST_CHANS` (`dashboard/http/sse.py`): the `prev`-map key holding its
last-sent value, the SSE event name, a producer over the per-tick context
(`_Tick`), and the wire WRAPPER — a field name when the value rides inside a
one-key dict (`{"tab": tab}`), `None` when it goes on the wire verbatim.
`_push_chans` walks a table in order, sending only what changed;
**the per-connection `prev` map is DERIVED from the tables** (`_prev_map`)
rather than written beside them.

Four fields stay INLINE and own their `prev` slot through `_INLINE_KEYS`, each
for a reason a table row can't express: `ask`'s wire payload is a transcript
read built only when the raw stash changed, `ask_draft` is meaningful only
while an ask is open, `suggestion` is gated on tab + dialogs + draft and
carries the terminal-draft sync's side effect, and `term_box` is never *sent*
at all — it is only the sync's previous-value slot.

*Why not twenty stanzas.* It was twenty, and the cost was not cosmetic. The
loop body carried twenty locals in one flat 200-line scope, and the tab-badge
stanza inside it read `for key, count in _BADGE_COUNTS.items(): n = count(…)`
— rebinding **two** names the loop depended on:

* `key`, the session's mirror-log key resolved once before the loop, is what
  every rendered op's ⧉ copy / click-to-view link is stamped with
  (`data-cc="<key>/<g>/<what>"`). From the second tick on, every
  **live-streamed** block was stamped `memory` (the badge table's last row), so
  its links resolved to a session that does not exist — an empty copy, a 404
  view, and a `dashboard copy (state DB gone)` errors row lighting the ⚠ chip
  in every session's scorebar. A reload masked it: the backlog path passes the
  key *before* the loop.
* `n`, the tick counter behind `n % SLOW_EVERY`, became the memory-note count,
  so the SLOW cadence turned into a function of the data — with 4 notes the
  "slow" block (a `git status`, the transcript probes, the ghost-suggestion
  `kitten @ get-text` screen scrape) ran on *every* 0.6s tick. Invisible for
  most sessions only because `memory` is project-scoped and returns 0 there.

Shipped 2026-07-25 in the commit that folded the four badge stanzas into
`_BADGE_COUNTS` — a correct refactor that the surrounding scope made unsafe.
As table rows there is no loop variable left to collide, and the `prev` literal
that had already lost `view_mode` is derived. Pinned by
`test_live_ops_carry_the_session_key_not_a_badge_name` (which fails on the old
code with `data-cc="memory/…"`) and
`test_session_stream_channels_are_a_derived_table`.

### Discarded prompts (the transcript is a TREE, not a list)

Reported 2026-07-25: *"I wrote `testing`, instantly hit Esc-Esc in the kitty tab,
and the message got removed from the transcript and moved into the input — but in
the web dashboard `testing` is still there."*

The premise the reader was built on — that the transcript is an append-only
LIST of what happened — is wrong. Every record names its `parentUuid`, and the
live conversation is the branch the newest records hang off. **Claude Code never
rewrites the file**; it discards a turn by RE-PARENTING around it, leaving the
dead records in place, orphaned. The reporter's own transcript (v2.1.220):

```
225 system                 uuid=0a03011c…
227 user   promptId=7eb…   uuid=86e9492a…  parent=0a03011c…   "testing"
229 user   promptId=6a0…   uuid=4c12f2ad…  parent=0a03011c…   "testingthat's what I did…"
230 assistant              uuid=b9f2cb39…  parent=4c12f2ad…
```

227 and 229 **share a parent**: the conversation forked there and only the later
branch survived. (229 opening with the discarded text is the other half of the
gesture — Esc-Esc hands the prompt back to the TUI input, and the user typed on
after it.) `conversation()` walked lines in file order and emitted every prompt
record, so the dashboard replayed a message the terminal had already taken back.

`transcript._dead_uuids` prunes it. **The tell is two user PROMPTS sharing one
`parentUuid`** — all but the last are dead, and each dead prompt takes its whole
subtree with it (a tree walk, not a line drop): an Esc-Esc discard has no
descendants, but a REWIND supersedes the restored-to prompt and its entire turn.

The rule is deliberately narrow, and that narrowness is the design. **The tree
forks legitimately all the time**: an `attachment` hangs off the record it
annotates, and PARALLEL tool calls each parent their `tool_result` to the
assistant message that issued them — measured across the corpus, ~30 such forks
in a 250-record session. A general "last sibling wins" / leaf-to-root walk would
prune live content. Only prompt-vs-prompt siblings count, which is why
`_prompt_bearing` distinguishes a `results` record carrying typed TEXT (a prompt
with pasted or attached content) from the same kind carrying only tool_results.
Across 30 recent transcripts the prune drops exactly the two known discards and
nothing else.

Cost stays where it was: `_line_meta` returns `(ts, uuid, parent)` in the ONE
json parse that `_line_ts` already did per renderable line, prompt forks are
detected from those ids alone, and the full-tree walk runs only when a discard
is actually found (the common case pays nothing).

**Live, the page prunes itself.** The server sees only the window it was handed,
so an incremental (`pos > 0`) call catches a discard only when both forks land in
one poll — and a live feed has already PAINTED the dead bubble anyway. So every
prompt record carries `par` (its `parentUuid`), `msg_html` stamps it as
`data-par`, and `dropSuperseded` (app.05-session.js, called from `appendItems`
beside `drainQueue`/`drainPending`) removes any older bubble sharing the arriving
prompt's `data-par`. Newest-top feed ⇒ the survivor is the first match in DOM
order; only server-rendered bubbles carry `data-par`, so the optimistic
`.pending` and ⧗ `.queued` stand-ins are untouched. The next full read (reload,
`/history` page, navigating back into the session) is authoritative either way.

This subsumes `applyCancelEdit`'s optimistic bubble removal, which only ever
covered a cancel issued FROM the web and did not survive a reload — a cancel
pressed in the terminal left a ghost, and so did every rewind.

No audit rows: like ctx saturation, the goal card and the stats page, this is a
read-side view change over data the transcript already holds (the transcript
path is recorded in `sessions`, so any pruning decision is reconstructible from
the file plus the rule above).

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
the live `S.ses.blocks` map (they are history, not the live tail); a straddling group already in the live map has its older ops folded
into that card's body at the end (older ops trail — acceptable). Filters apply to
lazily loaded items (`appendOlder` runs the shared `applyFilterTo`). The button
hides once `/history` reports `oldest == 0`.

**A page is laid out REVERSED — the whole feed is one descending sequence.** The
server sends a page oldest→newest and the feed is newest-top, so `appendOlder`
collects the page's top-level nodes (`tops`, in server order) and inserts them
LAST FIRST: each item takes the position a live top-prepend would have given it, a
block still holds the position of its FIRST op with its body reading top-down, and
successive pages (older still) stack below. Inserting a page in arrival order
instead made the loaded stretch read bottom-up while the live tail above it read
top-down — the "order of messages is backwards" report. It stayed latent for as
long as only an explicit click reached that path; the collapsing modes' auto-fill
(*The three view modes* below) pulls a page on every session open, which turned a
rarely-seen inversion into the normal way the feed looked. It also fed focus mode
a mis-ordered feed underneath: its "newest message per turn" cut walks the DOM
top-down and trusts that direction. Asserted end-to-end by the JS harness —
`/history` stamps an age marker on every served item and the test reads the final
DOM order back out (`test_a_loaded_history_page_lands_newest_first`). Past a 3000-child cap, each live
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

### The quiet register: a command is a LINE, not a card

The card look survived for content — an opened body, a message bubble — but not for
the *header* of a command. A foreground command, a background job and a monitor now
read as ONE dim line, the same register as an agent note and a collapsed run's
summary:

```
⏺  make test                                                    finished · 91.4s
⏺  git push                                                 failed (exit 1) · 2.1s
⏺  pytest -q tests/                                                       · 1m04s
⏺  background  npm run dev                              background finished · 3m 2s
⏺  monitor · watch the suite · persistent  pytest -q --ff   monitor ended · 12m 4s
```

Asked for in those words: *"let's style foreground/background/monitors to the same
style that we have established / I don't like those boxy blocks / also get rid of the
colors / I still want the dot and the time info"*. The card comes BACK the moment the
line is opened (`[data-quiet][data-open="1"]`), for the same reason a note's does: the
body it reveals is a whole command's output and needs the panel to read as one thing.
The **terminal pane keeps its coloured pills** — a pane reader scans by colour.

Two things go, and both had to:

- **The colour.** It was the pill's whole visual weight, and on this surface it
  competed with the conversation for the eye while saying only what the words already
  said.
- **The glyph.** `▶ ▷ ◉ ■` are only unambiguous *in* colour — `◉` is a monitor in a
  slot palette and a mail read notice in the semantic green (`actclass._MAIL_RGB`), `▶`
  is a command or a subagent launch. Un-coloured they are four indistinguishable
  shapes, so the WORDS carry the kind now: `background`, `monitor · <desc>`. The one
  word dropped is `foreground` — it is the default kind and the line shows the command
  itself.

What stays is what was asked for: the **dot**, tinted by outcome exactly like an agent
note's (grey running · green finished · red failed/interrupted — `data-out`, stamped by
`fillBlock` from the ops themselves, no agents payload needed here), and the **time**,
the producer's own `finished · 0.6s` / `failed (exit 1) · 2.1s` / `monitor ended · no
output`, verbatim.

**Where it is computed.** `actclass.cmd_note(op)` → `(text, role)`, colour-gated
exactly like the classifier (`_CMD_RGB` ∪ the bg/monitor slot palettes — a subagent's
`■ <type> ended` footer wears SUB_PALETTE and is NOT command family). The role is
which slot the words go in, and it exists because a lone op cannot know where in a
header it lands:

| role | op | slot | why |
|------|----|------|-----|
| `CQ_OPEN` | `▶ foreground`, `▷ background`, `◉ monitor · …` | `.bchips` | carries the line's DOT |
| `CQ_SUB` | a ws monitor's `⇄ ws · <url>` | `.bchips` | a second header row, and must NOT mint a second dot |
| `CQ_CLOSE` | `■ finished · 0.6s` | `.btail` | after the command, where a duration reads as that command's |

`op_items` hands the pieces over SEPARATELY (`html` = the words, which may be empty;
`links` = the ⧉ pair; `quiet` = the role) because the page owns the header's layout —
this is the only way the duration can sit *after* the command, and it also gets the
hover-only ⧉ links out of the reading line (in the flow they reserved a ~90px hole
between the dot and the command while invisible). A GROUP-LESS quiet label (the
`▷ backgrounded (ctrl+b)` notice) is not split: it has no header to be a slot of, and
wears the flag alone. The live elapsed chip (*Live command elapsed*) joins the register too —
no outline, no accent, no glyph — and ticks in the same column the final duration lands
in.

### What gets an inner scroll box, and what must not

Read content is never boxed; skimmed content is. A **message bubble** (`.msg .md`,
`.msg .md`) and a **subagent's `⇢ prompt` / `⇠ result`
block body** (`.blk[data-act="agent"] > .bbody`) have NO `max-height`: they grow
to their content and you scroll the feed. A **generic block body** keeps
`.bbody`'s 480px scroller, because that is a command's output — the thing the
`CAP_*` line ceilings exist for (docs/subagents.md).

This asymmetry is one decision made in two layers, and getting it right in only
one is a real bug we shipped: after the server stopped eliding an agent's
message/result by line count, the text still arrived into a 360/440/480px
`overflow: auto` box, so long messages *still* had to be scrolled — a
`max-height` on read content is the same elision wearing a scrollbar. Worse than
plain truncation, in fact: a nested scroller is easy to miss, needs a hover to
use, and eats the page's own scroll when the pointer crosses it.
`test_conversation_text_is_not_in_a_nested_scroll_box` pins both halves.

**Every block arrives FOLDED.** `createBlock` sets `data-open="0"` and nothing on
the page ever opens one — only a click does, and that is sticky (`userSet` /
`data-userset`), which is why there is no re-fold pass: nothing opens, so nothing
needs closing. The `KEEP_OPEN` window (the newest five blocks expanded) is gone
with `enforceWindow`, and so is the agent NOTE's special case, which was this
same rule applied to one block kind. The reasoning generalised: a block that
expands itself decides how much of your screen it deserves, and the ones taking
the most were the least interesting — a `ToolSearch`'s request/response pair, a
`TaskGet` payload, the output of a command that finished a minute ago. The feed
is a list of WHAT HAPPENED; depth is always one click ("everything by default
should be not expanded … not only those I mentioned"). Single-line items
(messages, file-op one-liners) are unaffected — they have no body to fold.

The card look is otherwise **CSS only** (`style.css`
`.blk`/`.bhead`/`.bsum`/`.bbody` + the `.stream > .opl/.ol/.og/.ogut` rule). The
rest of the fold machinery — the `data-open` toggle, click-to-view, ⧉ copy — is
UNCHANGED; only the appearance moved. Deliberately NOT ported from the activity
tab: its information architecture (a short *category* pill like `BASH`/`READ`
with the detail in the summary). The mirror keeps its own richer pill (glyph +
command/name) and what it shows — the request was the *look*, not the data.

## Agent scope

Clicking a subagent or teammate does not open a separate view — it **re-points
the session view at that agent**. `#/s/<sid>/a/<aid>/<tab>` is a real route, so a
scoped page is linkable, survives a reload, and a monitor/job detail nests inside
it (`…/a/<aid>/m/<task>`). The tab bar, the components and the stream engine are
the session's own; what changes is a `?agent=<id>` on every read.

**What re-scopes, and what deliberately doesn't.** The **mirror**, **monitors**
and **jobs** follow the agent — *including their tab BADGES*, which is the same
split declared once in the read model's `BADGES` table (`scoped`) and reached
through the one `badge_count`, so the overview payload and the SSE badge channel
cannot answer differently for the same tab. That was the whole of a real bug: the
badge counters took `(sid, cwd)` and never the agent, so every scope showed the
LEAD's numbers — an agent with 19 background jobs read `jobs 1`, one with 8
monitors had no badge at all, and its background work looked like it simply
wasn't there. The lists behind those tabs had been scoped from the start; only
the number that tells you to click was not. **Memory** and **errors** do not
follow the agent — a memory note is the team's and an error belongs to a script,
neither has an agent dimension — so in scope they keep showing the session's,
with a quiet `session-wide` note on the tab bar rather than an ambiguous
silence. The **agents** tab also stays
unscoped: a list of the session's agents is what you navigate *between* them
with. The scoped mirror additionally drops the session's pinned cards (goal,
tasks, plan, ask) and the **composer** — those are the lead's, and a composer
that types to the lead while you are looking at an agent is a lie about what it
does.

**Outside scope the session view is the LEAD's own work.** `?agent=` absent
means the main agent: main-agent-only ops (as it always was), and now jobs and
monitors the lead launched itself, badge counts included. An agent's belong to
that agent.

**The mirror is the same pipeline with the filter inverted.** An agent's blocks
are already in the ops stream, stamped `src: sub:<id>` / `team:<id>` — the
terminal mirror paints them, the web drops them (*Main-agent-only* above).
`opshtml.in_scope` is the ONE producer-source predicate: with no scope it keeps
the unstamped (plus `web`-stamped) ops, with a scope it keeps only that agent's.
`read/mirror.agent_scope` resolves the scope to a SET of exact `src` strings
rather than passing the bare id, because the stamps are not uniform — a codex run
is stamped `codex:<label>` while its agent id is the rollout basename
(`sessionapi.codex_aid`), so its label is looked up off the run's row.

**The conversation is the same call, keyed by identity.** `plugins.conversation(
sid, pos, agent_id)` reads the LEAD's main thread for `""` and that agent's own
transcript for an id, and the merge is otherwise untouched — so an agent's prose
becomes message bubbles exactly as the lead's does, and everything built on that
(the view modes, focus mode's prompt/final-reply rule, the ⧉ links) works in
scope without knowing scope exists. The agent's prose OPS are dropped in exchange
(`actclass.prose_block`: the `⇢ prompt`, `✎ message`, `⇠ result` and `✉ from|to
<peer>` headers, each with its body, by copy group) — the substream paints them
because the terminal pane has no other channel for an agent's text, but here they
would be the one thing rendered twice.

**Mail is conversation, in both directions** (*Team mail* below for the mail
plumbing itself). An INCOMING message is already a transcript `teammsg` record;
an OUTGOING one is the `SendMessage` tool_use, which `conversation()` surfaces as
a `sendmsg` record when the read asks for it (`transcript.mail_send` owns the
(recipient, body) shape — the substream's chip reads the same function). Both
render as the ordinary message bubble, with the direction as the only difference:
`✉ from team-lead` / `✉ to main`, worded from `core/streamfmt`'s `MAIL_FROM` /
`MAIL_TO` so the producer's chip and the web's label cannot drift. That is what
replaced a coloured pill over a black inset holding twelve lines of a report:
the op carries a pane-sized excerpt (`CAP_SENDMSG`), the transcript carries the
whole message.

Only an AGENT read asks for `sendmsg`. The LEAD's outgoing mail is already a
first-class mirror row that its transcript cannot match — `mail_fmt.py` fires on
every send *including every teammate's* (the ops are unstamped, so the session
view is the team's whole mail census), while the lead's transcript sees only the
lead's own sends. Adding the bubble there would double exactly those and leave
the teammates' rows unmatched, so `conversation_for` passes `sends=bool(
agent_id)` — the mirror image of the take-back stash, which is the lead's alone.

**One normalisation, not a second render path.** `actclass.as_lead` runs right
after the scope filter and is THE only place agent scope differs from the session
view. The terminal needs every per-agent block to say which agent it is — a
`<who>` name, the `opus-5·high  ctx 5% · 50k/1M` tags, the agent's palette
colour, the outer gutter bar — because one pane is shared by all of them; a scope
is one identity and says it once in the header. Worse, those differences made
every downstream stage fail to recognise the block: `cmd_note` is colour-gated,
the activity classifier reads the leading glyph, the view modes count what those
two answer. So an agent's `▶ foreground` fell through to the legacy coloured pill
while the lead's became a quiet `⏺` line. Normalising here — drop the tags,
recolour a command header to the semantic colour the lead's wears, drop the outer
bar, and turn a file one-liner's `gut` op into the `line` op the lead's file ops
are (same click-to-view and memory tags, and a gut op names no activity class, so
an agent's reads and edits were unfilterable) — leaves one vocabulary for
everything below.

That includes the block kind the lead has NO equivalent of: a GENERIC tool call
(`· ToolSearch`, `· WebFetch` — the lead's hooks paint only Bash, file ops,
monitors, skills and mail, so nothing in the session view opens with `·`). It is
recoloured like the rest and joins the quiet register through the same
`cmd_note`, reading `⏺ ToolSearch` with its request AND its result behind one
click. Two things had to change for that: `·` is a header marker in `cmd_note`
(only reachable in scope, since an agent's tool block is `src`-stamped and
dropped in the session view), and it gets its own activity class `ACT_TOOL`
(`used 3 tools`) — falling through to the agent fallback made a ToolSearch fold
into `ran 1 teammate`, which names the wrong thing entirely in a view where every
row is that one agent's. The RESULT joining the block is a producer fix, not a
presenter one: `substream_render._use_other` now mints the copy group
unconditionally and parks it in `pend`, because a generic tool — unlike a Bash
block, whose `tool_use_id` IS the group — has no id-keyed op for its result to
key on, and ungrouped it landed in the feed as a loose row beside its own block
("I should see the result of the ToolSearch"). History predates that group, so an
old session's result still shows as the loose row directly under the header.

The name and the tags are not string surgery: producers carry them as the op's
own `who`/`tags` FIELDS (`core/ops.py`), which the terminal composes at paint
time (`streamfmt.compose`) and the web simply never renders. Ops written before
those fields have them baked into the text and no restart can re-stamp a parked
session, so the composition is undone structurally for history — off the block
MARKER for a header (`actclass.lead_head`), off the stream COLOUR for a body line
(`streamfmt.strip_who`, compose's byte-exact inverse). That is not cosmetic: with
the name leading the text, every gate keyed on what a block OPENS with missed,
and history's prose blocks stayed in the stream beside their own transcript
bubbles — doubled prompts, messages and results.

**One SSE, not two.** Agent scope has no stream of its own — `?agent=` on
`/events/session/<sid>` scopes the mirror channel and the two scoped badges
(monitors, jobs — the connection's agent rides on `_Tick`, fixed for its
lifetime, since a scope change is a new connection; without it a live tick
pushed the lead's counts straight over the ones the initial payload got right,
a badge that reads correctly for one second and then lies), so a scoped page
still gets the tab colour, scoreboard, cards and dialogs on the same
connection. Note
the cursor still advances over out-of-scope ops (a tick carrying only the lead's
work renders to nothing and sends no event), or the stream would re-read them
forever.

**The scoreboard swap** (below) keeps working: the scoped agent's token rollup +
priced cost ride on the session payload as `agent_usage` whenever a `?agent=` is
in play (`read/session.agent_usage` → `plugins.agent_usage` →
`transcript.agent_usage`, folded through the shared `accounting.usage_fold`).
It is per-request rather than a field on every agents row because it folds a
whole transcript — paying that for all of a 28-agent session's rows on every
overview would be absurd, and only the scoped one is ever shown. A codex run
declines the fan-out: its tokens are folded from its rollout and priced at its
footer, so there is nothing for the web to re-price.

**Known gaps in history.** Ops written before the `src` stamp carry no producer
source, so an OLD parked session's agent scope shows an empty mirror — accepted
deliberately, since the drill-down timeline was the only view of that history and
it is gone. And a pre-`tags`-field body line keeps its `opus-5·high  ctx 5%`
chips: unlike the name, they sit at the END of the text with no marker or colour
boundary to key on, and the only precise handle would be the model/ctx wording
itself — exactly the fragile string-matching the fields exist to remove. New ops
carry the field and drop them.

### What this replaced, and why

Until 2026-07-27 an agent opened a **drill-down timeline**: `/api/session/<sid>/
agent/<aid>` + `/events/agent/<sid>/<aid>`, backed by `plugins.activity()` /
`activity_since()` over `transcript.timeline()` (and `codex/rollout.timeline()`),
rendered by its own `renderTimelineInto`/`timelineEntry` stack. It was a SECOND
presenter of the same transcript records the mirror already paints — a parallel
entry vocabulary, its own SSE, its own enrichment pass, its own ordering rule —
and everything around it stayed stubbornly session-level, so an agent's monitors
and jobs were invisible while its messages had a whole view of their own.

All of it is deleted. What the timeline could show that the scoped mirror cannot
is nothing the mirror lacked — it is the same records, painted the way the rest
of the product paints them. The one genuine loss is pre-stamp history, above.

### Breadcrumbs (back up the hierarchy)

Agent scope prepends an **agent-hierarchy breadcrumb** above the tab's body —
**◆ ‹main agent› › ◇ ‹subagent›** (`agentCrumbs`) — showing just the two agent
nodes, because the hierarchy is one level deep (a session's flat agent list; an
agent launching a sub-subagent is not modeled anywhere). The **main agent** node
is a link to the session's own mirror (`#/s/<sid>`) labelled by the session title
— clicking it is how you leave scope; the current **agent** is the highlighted
end pill. Rendered as a boxed bar (`.crumbs`), it sits at the top of `ses.body`,
above whatever the open tab renders.

**It is a property of the body RESET, not of one painter.** Clearing `ses.body`
goes through the single `resetBody()`, which re-lays the scope crumb; every
painter that replaces the body wholesale calls it. It was appended once by
`renderSessionChrome` instead, and each of those painters — a monitor/job
drill-down, the memory grid, an open note — wiped it and put back only its own
crumb, so opening one of an agent's background jobs left you inside that agent
with nothing on screen saying so and no way up but the browser's Back. Pinned by
`test_agent_scope_survives_what_the_page_repaints`, which also holds the count of
body clears at one.

**The header NAME is the agent's, and stays that way.** `renderAgentScoreboard`
paints `◇ <agent>` into the same element the session title uses, and the `title`
SSE — which fires on the slow tick for a rename or a fresh auto-title — is gated
on `!agentFocus`, exactly like the state badge beside it. Ungated it reverted the
name to the session's a second after you entered scope, so the scoreboard read as
belonging to the lead.

### Monitor events

A `Monitor` tool launch and its **events** — which Claude Code writes to the
transcript as `queue-operation` `<task-notification>` records (docs/streaming.md,
*Monitor events in the transcript*) — reach the web through the **monitors tab**
(below), whose read model merges the transcript's per-monitor event list with the
audit `streams` lifecycle row. They are deliberately **not** re-emitted into the
mirror stream (`conversation()` drops them): the mirror already streams a
monitor's events live as ops via `claude-stream.py`, so surfacing them from the
transcript too would double them.

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
a light client poll (`scheduleSectionPoll`, `SECONDARY_POLL_MS`); the poll stops when none is live
or you leave the tab.

Monitors, jobs and memory are ONE engine, not three: the `SECTIONS` descriptor
in `app.11-chrome.js` names what differs (endpoint, the `S.ses` field the list
caches into, the grid/poll/tab-anchor fields, the route letter, the glyph and
label, the empty-list wording, the card and detail renderers, the item's display
name) and `loadSection` / `renderSectionGrid` / `scheduleSectionPoll` /
`clearSectionPoll` / `showSection` / `repaintSectionDetail` / `sectionCrumbs` /
`setSectionCount` / `updateSectionCount` are generic over it. It was written
twice — fourteen near-identical function pairs 200 lines apart, `sortedMonitors`
and `sortedJobs` byte-identical apart from a parameter name — and the
`SECONDARY_POLL_MS` constant had already been unified with a comment noting the
rest had not. Memory is a member for its fetch and badge only: it repaints
through its own `paintMemory` (a note grid OR an open note viewer) and has no
per-item drill-down, which its `repaint` hook and missing `detail`/`grid` fields
are what say. Executed, not grepped, by `tests/jsdom/sections.js`
(docs/testing.md). (No dedicated per-event SSE increment — a monitor's live
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
existing `⧉out` copy endpoint (`GET /api/session/<sid>/copy/<gid>/out` →
`core.copy.collect`) into a scrollable box. `<gid>` is the row's served
**`group`**, not its taskId: those are the same thing for the lead's own jobs
(the tailer paints under the taskId) but NOT for an agent's, whose block the
substream opened under the **tool_use_id** before any taskId existed. Asking by
task returned an empty body for every subagent job — visible output in the mirror
tab, "(no output)" in the drill-down of the same job — which is what `group` is
carried for. `task` remains the fallback for a row that predates the field. `jobStatus` maps state to the agent
cards' `data-st` tint: `running` while live, else `finished` (a bg job's normal
`writer-gone`/vanished completion), with `timed out` off `end_reason`.

**Live-ness** matches the monitors tab: the badge count rides a cheap `jobs` SSE
(`sessionapi.job_count`, distinct bg-stream count), the list is fetched lazily on
tab-open and re-fetched on the shared `SECONDARY_POLL_MS` poll while any job is `live`. (A job's live output
already streams into the *mirror* tab as ops; the jobs tab is the
state-and-history view.)

## Memory tab

A session-view tab **`memory`** (between `jobs` and `errors`) lists the
**memory-wiki notes** the session touched — the Obsidian-style knowledge vault at
`~/wiki/01` (markdown notes with YAML frontmatter, cross-linked with bare
`[[wikilinks]]`). A Read/Write/Edit whose path falls under that root is a MEMORY op
— recall (Read), persist (Write), or revise (Update/Edit). `plugins/claude_code/
memory.py` is the single owner of that vocabulary (the root, the project scope, the
`is_memory` test, the project gate, the mirror ❖ `MARK`, the `memory` kv, and the
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
subagent) renders a file op under the root, it appends ❖ (`memory.MARK`) to the
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
rides a cheap `memory` SSE — one row of the badge table below, and the ONE place
the project-scope gate is applied (`read/session.memory_count`, shared with the
overview payload: off-scope both report 0, since off-scope there is no tab to
badge. The stream used to push the real count there — benign on screen, but a
per-tick kv read for nobody and two readings of one rule). The list renders one card
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
purpose). Clicking a `[[link]]` fetches the target and pushes a breadcrumb (❖
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
(`commands`/`files`/`agents`/`messages`) ONCE at creation (`stampItem`)
rather than re-sniffed per filter pass — selector stability beats matching the
exact chip text, which drifts. The kind now comes from the SERVED activity class
(`act`, *View modes* below — `ACT_KIND` maps it), which replaced the page's own
`CMD_GLYPH = /^\s*[▶▷◉■]/` regex over the rendered chip text: same answer, but
the glyph table has one owner and it is server-side. A block still upgrades to
`agents` on an outer-gutter `.og` wrapper (a subagent's nested job), and the
upgrade stays monotonic. Ungrouped items classify by item type: `msg` items are
`messages`, memory-wiki file ops (they carry `data-mem` — the ❖ marker, checked
first) are `memory`, other file-op one-liners (they carry a `data-v` click-to-view
id) are `files`, the rest `commands`. On a CURRENT session the `agents` chip mostly
matches nothing: agent/codex stream ops are producer-source-stamped and never
reach the page (the main-agent-only rule, *The web presenter* above). The chip
survives deliberately for pre-stamp history — parked DBs written before the
stamp existed still carry agent blocks, and since the activity class is derived
from the op rather than stamped into it, those classify exactly like live ones.

## View modes (verbose · default · focus)

Claude Code's own transcript densities, over the web mirror. The filter bar
carries a 3-way segmented control left of the kind chips (`.vmodes`).

**The choice lives on the SERVER, per session** — `POST
/api/session/<sid>/viewmode` writes the `view-mode` map in `dashboard/prefs.py`
(durable, at `~/.claude`, outside any checkout), and it rides the session payload
as `view_mode`. So it survives reloads, park and resume, and is set ONCE for a
session rather than per browser: open that session on the phone and it is already
in the mode you left it in. A page ALREADY open follows a switch made elsewhere
too, over a `view-mode` SSE event on the slow cadence (same shape as the global
alerts toggle's `notify-config`); the client ignores its own echo, since
re-applying would clear the runs the user just expanded. Deliberately NOT
`localStorage`, which is per-browser and would need re-selecting on every device
— the same reasoning that moved the new-session prefs off it.

- **verbose** — every block, exactly as the dashboard rendered before this
  feature. Nothing is ever hidden.
- **default** — runs of adjacent read/command activity (plus task rows and team
  mail) collapse into ONE clickable summary line, while **agent activity stays
  standing** as its own lines (see below). File **mutations stay expanded**, so an Update/Write
  always breaks the run and is always on screen; so do conversation messages and
  the ⚠ audit one-liner. **This is the default mode** (`prefs.VIEW_DEFAULT`), the
  same one Claude Code's `viewMode` defaults to — the dashboard reads like the
  TUI it mirrors. It shipped defaulting to `verbose` while the collapse was new
  and unproven, and that caution outlived its reason: nothing at `default` is
  unreachable (every run is one click from expanded, and mutations/messages/the ⚠
  line never fold at all). Note `VIEW_MODES` is in CONTROL order — densest to
  sparsest — so the default is deliberately NOT its first entry; the page carries
  its own `VIEW_DEFAULT` and the grep test pins both halves against
  `prefs.VIEW_DEFAULT`.
- **focus** — your prompts and each turn's FINAL reply at full weight, its
  mid-turn prose **dimmed** (`.vdim`, 50% — full weight on hover), and ONE summary
  line accounting for everything else the turn did. Every intermediate step folds
  into it; nothing is dropped from its counters.

  Mid-turn prose is greyed rather than dropped, which it was at first. Hiding it
  read as content VANISHING: only the NEWEST message in a turn is its "final"
  one, so each new reply flipped its predecessor from shown to hidden — the
  message you were reading disappeared the instant the turn ended. Claude Code
  greys it for the same reason.

  **The greying is a paint change and nothing more — the collapse semantics do
  not move with it.** A dimmed item still CONTINUES a run exactly as a hidden one
  did (`inRun`), so the activity either side of it still merges into one summary
  line, and a dimmed item that falls inside a collapsed run's span is simply
  never given `.vhide` (`hideIt`). Letting visibility drive the run cut instead —
  the obvious reading of "it's on screen, so it should end the run" — silently
  re-cut every focus-mode stream into more summary lines, which is a different
  feature from greying one bubble.

### Skills (`⏺ Skill(slack)`)

A **Skill invocation** is one note line — `⏺ Skill(slack)` in Claude Code's own
wording, with the ARGS it was called with behind the click. Asked for exactly so:
*"I want skills in default mode to appear like this `⏺ Skill(slack)`, and in focus mode
in the summary to appear, and in both places it is expandable"*. So `skill` is in
`VIEW_FOLD.focus` and NOT in `default`: the line stands in default, folds into `Used 2
skills` in focus, and is clickable in both (the fold reveals the line, the line reveals
the args).

Nothing rendered skills at all before this: the tool fires **both** tool hooks (294
`PreToolUse` + 294 `PostToolUse` rows with `tool_name=Skill` sit in the audit) but had
no formatter, so a `/logs` or a model-invoked skill left no trace in the mirror.

The row needs no new page machinery — it is a NOTE block like an agent's or a message's,
because the producer stamps the wording (`plugins/claude_code/skill_fmt.py`, on
PostToolUse *and* PostToolUseFailure per the invariant). Its three page-side facts are
its act's table rows: the `commands` filter chip, the `used N skills` fragment, and the
fold above. Its terminal chip is `✦ skill · <name>` in the semantic table's own
`VIOLET` — a glyph no other producer writes, which is what lets the classifier read the
class back off it (`actclass.ACT_SKILL`) without a colour tie-break; the colour gate is
still there so a stream-palette `✦` could never be claimed as the session's own.

**What is NOT behind the click, and why:** the skill's BODY. `tool_response` is
`{success, commandName, allowedTools}` — Claude Code injects the loaded `SKILL.md` into
the conversation as a user-shaped turn (transcript `isMeta`, the mirror's
`data-injected`), not as a tool result, so the args are the only content the row has.
A skill invoked with no args gets a line with nothing behind it, and the note block's
own empty-body guard makes it unclickable rather than opening an empty panel.

**A MONITOR folds in default too** (`VIEW_FOLD.default`), asked for in those words:
*"also monitors should be in the under summary in default mode"*. A monitor is a
watcher you set up once and then read only if it fires, so its card standing open in
the feed is noise — `Watched 2 monitors` is the whole of what default needs to say
about it, and the line is one click from its events. A **background job deliberately
does NOT fold there**: it is work still running, whose output is why you opened the
session. (Focus folds both, like everything else.)

**`agent` is the one act the two modes disagree about.** Task rows (`task`) and
team mail (`mail`) fold in both; **agent activity folds only in focus**. Claude
Code's own default density prints agent work as its own lines — `6 background
agents launched`, `Agent "Fix common/ui terminal bugs" finished · 21m 16s` — because
on a lead session that IS the turn: who you dispatched and who reported back is the
shape of the work, not a detail of it. So default leaves the mirror's launch/resume
headers (`▶︎ rev-ui-util · Review common/ui`, `↻ … · teammate · …`) and each agent's
`⇢ prompt` / `⇠ result` cards standing, and folds only the rest. Focus — one line
for the whole turn — folds them in with everything else, and still COUNTS them:
`Edited 1 file +12 -3, ran 2 agents, ran 1 shell command, tracked 1 task, passed 2
messages`. The rows are gone there; the accounting is not.

**A subagent reads as two quiet NOTE lines, not two coloured cards.** On this
surface an agent's two web-surfaced blocks render as
`⏺ Agent "Fix git/config commands + glab" launched` and
`⏺ Agent "…" finished · 21m 31s` — the register of a collapsed run's summary line,
no stream colour, no `⧉` links, and **no model/ctx tags**: those numbers
(`fable-5·high  ctx 22% · 225k/1M`) already live on the agent's own card, and in the
feed they shouted an agent's bookkeeping at the weight of the conversation. Clicking
the line opens the block's body, which is the thing worth having — the agent's brief
on the launch line, its result on the finish line. The block arrives CLOSED (unlike
a live command block) for the same reason, and never re-closes one you opened.

**Every one of these lines leads with the SAME DOT** — `.vdot`'s 7px CSS circle, in
`.vsum`, in a note (`.anmark`) and in a quiet command header alike. The note's marker
started out as the `⏺` GLYPH rendered at 9px, and a glyph's ink is whatever the font
makes of it: it drew visibly smaller than the circle beside it (*"why are agent dots
smaller than other dots, all dots should be the same size"*), and no font-size can be
relied on to match a CSS box across fonts and platforms. So the span keeps the
character — it is still Claude Code's marker, and still what a copy of the line yields
— and paints as a box (`font-size: 0`, `background: currentColor`, which is what lets
the `data-out` outcome rules keep colouring it by setting `color`). The rows are
`align-items: center` rather than baseline for the same reason: a box has no text
baseline, so a baseline row put the dot at a different height in each line kind.

**It sits on the summary line's grid, to the pixel.** The two are the same kind of
line — one activity notice — so the note's `⏺` stands in `.vsum`'s 7px DOT column and
its words start where the summary's words start (13px padding, 7px marker, 8px gap),
with the same font, margins and hover. That is why the marker and the sentence are
separate spans: one `⏺ text` string lands at neither column, because the glyph's own
advance width is not `7px + 8px`, and the pair then reads as ragged (the reported "not
visually aligned"). Both rules are asserted against each other, property by property,
so changing one without the other fails.

The wording is the PRODUCER's, carried as the op's `note` (core/ops.py): the
terminal keeps its dense colour-coded chip, the browser gets the sentence. Not a
reformat in the presenter — parsing a chip back apart to reword it is the sniffing
`actclass` exists to have ended. The **duration** comes from the same place
`emit_footer` reads it (the agent's own slot row, via the injected `agent_dur`
hook), so the note and the footer cannot disagree. One builder, `agent_note`, words
both lines.

**An AGENT and a TEAMMATE are worded apart**, in Claude Code's own two registers: a
Task-spawned subagent is `Agent "<type>"` (quoted), an agent-TEAM member is
`Teammate @<name>` — verbatim what its TUI prints (`⏺ Teammate @fix-smoke-dedup
finished`, 2.1.220). One word for both read as a bug (*"I want a clear distinction Agent
from Teammate in those summaries and message transcripts"*), and they ARE different
things: a named, long-lived peer you can mail, versus a one-shot delegate. The wording
lives in `core/streamfmt.agent_note` (`AGENT_WORD`/`TEAM_WORD`) because BOTH sides need
it — the producer stamps the note, and the presenter recovers it for pre-`note` ops — and
a dashboard module may not reach into a plugin for a string. Which register an op gets is
read off the `src` stamp it already wears (`team:` vs `sub:`), never its name or its
colour; `src` is OLDER than `note`, so history is worded right too, and an op older than
both reads as an Agent (the neutral guess). A teammate also COUNTS as its own kind
(`actclass.ACT_TEAM`, `ran 2 teammates` beside `ran 4 agents`), so a collapsed run says
which it ran rather than merging the two.

**The note's DOT carries the outcome** — the same three states a collapsed run's `.vdot`
shows: grey while the agent runs, green once it finished, red when it did not (*"why is
it grey and not green/red based on the outcome?"*). It cannot come off the op: a LAUNCH
note is written before there is an outcome, and a finish note knows only about its own
op. So the row is joined to the agents payload by `data-agent` and stamped `data-out`
(`tintAgentNotes`), re-run on every `agents` SSE event — which is what turns a launch
note green the moment its agent ends, an event no op is written for. The mapping from an
agent row to running/ok/bad is `agentStatus`, the same function the rail's cards read, so
a note and its card can never disagree; a failing op inside the block (`data-bad`)
reddens that row on its own, exactly as it does for the summary dot. A row with no agent
(team mail) gets no `data-out` and stays dim.

**A brief carries no injected reminders, and an empty one gets no click.** Claude
Code injects `<system-reminder>` blocks into the text it hands an agent, so a launch
note opened onto the roster of every addressable teammate instead of the task.
Producers strip it (`transcript.strip_reminders`), and `op_html` strips it again for
ops already on disk — scoped to `web`-stamped gut bodies, which are exactly a
subagent's brief and result, so the strip can never roam over command output or file
content that merely quotes the tag. A body that strips to NOTHING drops out entirely:
a TEAMMATE's spawn record is only reminders (its real instructions arrive as mail, so
there is no brief to show), and the page also refuses a click that would reveal an
empty panel.

**…and a launch with no brief is not a ROW either.** Dropping the body left the
header standing, which is a worse artefact than the one it fixed, because a launch
opens the agent's transcript with **two** user records: the brief, then a record that
is nothing but the addressable-teammates roster reminder (measured 2026-07-27,
v2.1.220, on a 20-agent team — every launch, both records parsing as `prompt`). Each
painted its own `⇢ prompt` block, so the feed carried two identical `Agent "Explore"
launched` notes and only ONE of them opened onto anything: the reported *"why one is
expandable where I can see the initial prompt and the other is not"*. Fixed at the
PRODUCER — `substream_render.render_prompt` strips first and returns without emitting
when nothing is left, so the block exists in neither surface (the terminal pane showed
the same empty pair; a web-only drop would have left it there). Ops **already on
disk** get the same drop read-side: `op_items` skips a `⇢ prompt` / `⇠ result` chip
(`actclass.agent_brief` — the marker, so it holds for pre-`note` history too) whose
body op is in the batch and renders empty (`_empty_body`). Deliberately only *in the
batch*: a header whose body was cut off the end of a window is unknown, not empty, and
must survive — the drop may never be a guess about an op it cannot see.

Nothing else renders it any more: the drill-down timeline that used to fold the
same roster record into a `prompt` entry is gone with the rest of that read model
(*Agent scope*). The scoped mirror reads the agent's conversation from that same
transcript now, so it would be the one surface that could bring the record back —
it doesn't, because `transcript.conversation` strips reminders before yielding a
record and a reminder-only one yields nothing (verified against a 28-agent
session: every agent's first bubble is its brief).

Pre-`note` ops get the wording recovered read-side from the `⇢ prompt` / `⇠ result`
MARKER (`core/streamfmt.MARK_*`, named there because two surfaces read it):
`<who>` is the text before it, the tags after it are dropped, and there is no
duration because the chip never carried one. A parked session's ops cannot be
re-stamped, and would otherwise show the terminal's chip forever.

The main session's own `▶ <type> · <desc>` launch header is **dropped** on the web
(`actclass.agent_header`): the substream's launch note says the same thing and holds
the brief, so keeping both put two launch lines in the feed per launch. The
trade-off: an agent whose substream never emits a prompt op has no launch line here
(its card in the rail and its finish note still show it).

Still not matched to Claude Code exactly: our launch is one line per agent rather
than a count plus an `@name` list.

**Rejected: a second axis that dropped them from the counters.** Focus briefly had
a `VIEW_HIDE` table — no row, no fragment, no counter — on the reasoning that a
lead session's summary reading `Ran 22 agents` is "work you did not do". That was
wrong on the summary's own terms, and the user said so plainly: *everything should
be in the focus summary; that's the whole point of the summary.* A one-line
account that silently omits the largest part of a turn is a lie about the turn,
not a précis of it — and the sparser the mode, the more that one line has to
carry. The rows it was meant to suppress were being kept on screen by the CSS
cascade bug below, not by the counting, so the whole axis was solving a problem it
had misdiagnosed. It is deleted rather than left empty (a grep test asserts
`VIEW_HIDE` stays gone): the only thing any mode still DROPS is an injected prompt,
which is not conversation at all and is keyed on `data-injected`, never on an act.

**Hiding must outrank layout (`display: none !important`).** The class was right
and the row still showed: `.vhide`/`.fhide` are ONE-CLASS selectors and so is every
stream row's own rule — and some of those set `display` (`.ol`, a loose chip row, is
`display: flex`). Equal specificity means the CASCADE fell to source order, and
`.ol` is declared below the hide classes, so a loose chip row was **never hidden**
however correctly the page marked it: a subagent launch header
(`▶︎ explore2 · Symbol reference sweep`) and a `●` mail row sat in focus mode
wearing `.vhide`, while `.blk` cards — which set no `display` of their own —
vanished properly. That asymmetry is what made the bug look like a classification
problem: the agent BLOCKS obeyed focus and the loose HEADERS did not.

`!important` is deliberate here and nowhere else in the file. Raising specificity
(`.stream > .vhide`) or moving the hide classes below every row rule would both
work today and break silently on the next row kind that needs a `display`;
`!important` is the only form that says "no layout rule outranks this". It is
asserted as a property of the stylesheet, not a string match:
`test_hiding_a_row_beats_its_own_layout_rule`. Note the JS harness could never
catch this — it executes the engine but applies no CSS — which is why three
successive JS fixes each verified clean while the stream on screen was unchanged.

**A hidden item is hidden wherever it lands — including inside an EXPANDED run.**
The hidden rows are not outside the runs; they sit *within* a run's span (that is
what `inRun` covers), and expanding a summary revealed the whole span. So one click
on any summary line brought back every agent launch, result and mail row the mode
had just dropped — 7 visible rows became 165 on the reported session — and
`viewOpen` remembers the expansion, so it stayed back. The pass therefore tracks
every hidden item as ONE list and hides them all unconditionally; an expanded run
reveals only the members its summary COUNTED. The group rail is drawn over the
span's VISIBLE members for the same reason: `.vrun-last` on a `display:none` node
leaves the group looking unterminated. Both halves are executed by the JS harness
(`teamExpanded`) — this is the kind of bug a grep cannot see and a non-clicking
test never reaches: the two earlier fixes for the same report were each verified by
replaying real sessions through the real engine, and both looked right, because the
replay never clicked.

Getting there needed the classifier to actually KNOW those rows, which it did not:

- **Team mail had no class at all.** Its arrival chip is `● <from> → <to>` in
  yellow and its read notice `◉ read · …` in green — and `◉` is *also* a monitor
  block's glyph, so every read notice was classified `monitor` and counted as one
  (that "watched 7 monitors" was mail). `●` fell through to the agent fallback.
  Both are now `mail`, keyed on the glyphs + colours IMPORTED from their producer.
  Which required moving that vocabulary out of `bin/claude-scorebar.py`: entry
  scripts are un-importable by design, so as long as the painter owned the glyph
  the classifier could only *guess* at it. `plugins/claude_code/msgs.py` now owns
  the shape AND builds the ops (`event_ops`), the plugin's `census()` returns them
  as ops rather than raw events, and the tool-agnostic scorebar just emits what it
  is handed — it no longer knows what team mail looks like.
- **A mail BODY could not inherit anything.** "A body op inherits its block's
  class" is the classifier's rule, but mail was a `●` label followed by the message
  text as a `gut` op with **no `g`** — no block to inherit from, so it landed as a
  top-level row, unclassifiable, therefore never collapsible, therefore on screen
  in every mode however strict. That is exactly how a teammate's report-delivery
  summary sat in the middle of focus mode *with its own header hidden*, which reads
  as "the subagent results are still there".

  Fixed in two places, because the first one alone did not hold. `op_items`
  resolves a group-less body op against the item it FOLLOWS (the only block it
  has) — but that only works **within one call**, and both render paths called it
  ONE OP AT A TIME (`_render_window` per entry, `merge_live` per op in the
  two-pointer merge). So the inheritance never fired in production while its unit
  test — a batch — passed. Both paths now batch consecutive ops into one call
  (a conversation record flushes the run: a message is no op's block, and
  inheriting `msg` would make a mail body conversation text).

  The real fix is at the SOURCE: an arrival's chip and its summary body now share
  a copy-group (`msgs.event_ops` takes the log for `new_group`), so the body is
  *inside* the block like every other block's body and needs no adjacency at all —
  no batch boundary, no window cut, no interleaved message can strand it. The
  inheritance stays for PARKED history written before that, where the ops are
  already on disk ungrouped and only the read side can still help them.

  Two more things a group-less body op inherits, both for that history's sake. Its
  **placement**: the feed is newest-on-top, so the page reverses the item list — which
  put the body ABOVE the row it belongs to, wedged between that arrival and the `·
  read` notice of the message before it. The body was on screen the whole time,
  attributed to the wrong line (*"I don't see the change"* — pasted with the body
  sitting under a read notice). `op_items` now inserts it at its header's index, which
  reverses into "header, then its bodies, in order", the way a real block's card reads.
  And its **subject** (`mid`): pre-`mid` history has no message id anywhere in the op,
  so the subject is reconstructed from the `<from> → <to>` pair off the chip
  (`actclass.mail_pair`, the one parser of that chip shape) plus the ROW ID of the
  arrival that opened it — `● X → Y` opens a message and the `◉ read · X → Y` after it
  belongs to that same one. So an arrival, its body and its read notice count as ONE
  message instead of three, and a teammate that reports twice still counts twice. Two
  weaker keys were tried and both merged messages that were not the same: the pair
  alone collapsed a teammate's two reports into one (the reviewed session has exactly
  that shape), and a per-pair COUNTER collided across the batches of a single render —
  `#1` from two batches is one key. The row id is stable across every batch and fetch,
  which is why `op_items` takes the `ids` the history paths already carry for their
  window cuts. Mail is chronological, so a read always trails its arrival; one whose
  arrival fell outside the render opens its own subject rather than merging into
  whichever arrival comes later. Within one render it always finds it: a conversation
  record between an arrival and its read flushes the run and splits them across
  batches — the reviewed session's shape — so the caller owns a `carry` dict holding
  what one batch learned and hands it to every batch of the pass.
- **Task rows** (`✚ task #7 · …` / `✓ …`) get `task`, on the same imported-glyph
  basis (`task_fmt.GLYPHS`).

**The agent counter counts AGENTS, not agent-ish rows.** One subagent contributes a
launch note and a finish note (plus a resume header, plus a second result if it
reports twice), so counting rows announced `running 77 agents` for a session with 21
of them. Every agent item now carries `agent` — the producer-source id parsed from
`src` (`sub:<id>`/`team:<id>`), which IS the agents payload's `agent_id` — and the
run's counter is the size of that id set. A row with no id counts once rather than
dropping out: unattributable, never uncounted.

**And the mail counter counts MESSAGES**, on the same rule and through the same
mechanism, because mail has the same shape: an arrival, its body and its read notice
are three rows about one message, so `passed 4 messages` appeared for two that had
been sent. The subject id is the msg_id, carried as the op's `mid` (core/ops.py — the
producer stamps it on all three ops; the terminal ignores it) and stamped onto the row
as `data-mid`. `VIEW_SUBJECT` is the table of counters that count subjects rather than
rows — `{agent: data-agent, mail: data-mid}` — so the two cases are one code path.

### Team mail: the message comes from the SEND, the poller only reports on it

Mail on the web is TWO kinds of row, and confusing them is what made this feature take
five attempts to get right:

- **The message.** `⏺ Message fix-smoke-dedup → team-lead: Money-cycle dedup complete`
  — written at SEND time by the `SendMessage` hook (`plugins/claude_code/mail_fmt.py`),
  its body the message text, so the click opens what was said. Shown in every mode.
- **The plumbing.** `⏺ Mail fix-smoke-dedup → team-lead · delivered` / `· read` /
  `· idle` — the inbox poller (`msgs.event_ops`) reporting on a message. One line, no
  body, and **verbose only**: asked for in exactly those words — *"I don't want to see
  the lifecycle messages, mail arrivals in the default or focus mode, only the real
  messages on sent time … but in verbose mode I want to see all of them with a label"*.
  `data-plumb` (from `actclass.mail_plumbing`) is what the mode pass keys on, dropped
  like an injected prompt: not the thing it looks like, and counted into nothing.

**Why the poller cannot be the message.** `msgs.py` tracks mail by scanning the team
inboxes once a second, so it only ever sees a message still sitting unread at a tick.
Measured on one reviewed lead session: **33 messages sent, 12 arrivals recorded, 10 of
those 12 lifecycle frames** — so 2 of 33 real messages left a row at all, and the rest
were consumed between ticks and vanished. Nor does the poller have the text at the right
moment: it reads the inbox record, which is gone once the recipient drains it. Four
rounds of fixes to those rows (wording, placement, counting) each landed and each left
the same complaint standing — *"why do you keep fixing but it is not fixed?"* — because
the row the reader was clicking never had a message in it. The SendMessage hook fires on
every send, at send time, with `tool_input.message` in hand. That is the fix; the rest
was polish on the wrong surface.

The hook is the ONE formatter that deliberately ignores the main-session-only invariant
(the same exception `cmd_pre.py` takes for a subagent's teed command): a teammate's send
carries an `agent_id`, and teammate mail is most of team mail. The op is emitted
unstamped, so it lands in the main mirror where the lead reads it, and `msg_id` ties it
to the poller's rows for the same message — which is how three rows count as one
message. A REFUSED send paints nothing (the audit row is its trace), and the structured
`{message: {type: …}}` form is a protocol frame, not prose, so it is left to the poller.

Capped at `msgs.CAP_TEXT` (60 lines, `CAP_BODY`'s ceiling for a command's output — a
review report is the same order of thing) because the terminal paints it inline;
deliberately its own ceiling and not `substream_render`'s `CAP_TEAMMSG`/`CAP_SENDMSG`,
which cap the same content inside an AGENT's stream.

**A lifecycle frame is named by its type.** Claude Code delivers teammate lifecycle
events through the same inboxes as an ordinary mailbox record whose `text` is a JSON
frame — `{"type":"idle_notification","from":"rev-ui-util","idleReason":"available"}`.
These have no SendMessage anywhere, so the poller's row is their only surface, and it
words them from `msgs.FRAME_PHRASE` (`· idle`, `· task assigned`, `· terminated`,
`· idle (failed)`) whose type vocabulary is Claude Code's own (2.1.220 refuses exactly
that list from a plain-text SendMessage: *"message text must not be a teammate
lifecycle/task frame"*). An unknown type still gets a line naming its `type`, which is at
least true. Painting the JSON would be worse than painting nothing: a reader wants the
event, not the wire format.

**The wording is a LABEL, and that is deliberate.** `Message` marks the row that carries
words; `Mail` marks the mail system talking about one. A line can then never promise
content it does not have — which is precisely what `⏺ Message team-lead → rev-ui-util`
over an empty click did. Both are worded by their owner (`msgs.note_message` /
`note_mail`) like every other note, and recovered read-side for chips already on disk
(`actclass.legacy_mail_note`, colour-gated exactly like the classifier so a monitor's
`◉` is never reworded).

**History keeps its `Message` rows — and they are ONE BLOCK too.** Before the send-time
row existed, an arrival WITH a body was the only trace a real message left, so
`mail_plumbing` spares those (the body lookahead in `op_items`) — demoting them would
leave every pre-2026-07-27 session showing no mail at all outside verbose. Those ops are
two TOP-LEVEL rows on disk, though — a `●` chip and the body as a bare gutter, neither
carrying a copy group, because the send-time row that groups them came later — and the
page folds a block by its `g`, so the message sat OPEN under its own header instead of
behind it (*"the actual message should be expandable from `Message team-lead →
rev-ui-util`, following the pattern of other stuff"*). So the read side hands the pair a
SYNTHETIC group, `mail:<row id>` — a shape no producer can mint (a real copy group is
`b<n>` or a tool_use_id) and one that stashes nothing, since these ops carry no ⧉ links
to resolve. Three guards make it safe: only a chip that CAN hold a message opens one (a
`◉ read` notice never has a body and would otherwise swallow the next producer's bare
gutter — `_mail_holder`), only the op immediately after it may claim it (plus any further
consecutive body ops, which are the same message's continuation), and a batch CUT between
the two leaves them ungrouped as before — the pair is written in one transaction and read
in one id range, so the live path cannot split them. Those bodies are the SUMMARY, not the message; the text
of a historical message is not in the mirror at all and can only come from the audit's
own `SendMessage` payloads (a join measured exact on the reviewed session: 2 of 2 rows
matched by sender + summary, one yielding a 6,364-character report) or from the sending
teammate's drill-down.

Known duplication: for mail a TEAMMATE sent, the terminal shows the body twice — once in
the teammate's own `✉ to <who>` substream block, once on the session's message row.
Accepted, because the web mirror drops the substream copy (it is `src`-stamped) and mail
the LEAD sent has no substream block at all, so the message row is the only place either
surface can show it. In that teammate's OWN scope the same send is a message bubble read
back from its transcript, uncapped (*Agent scope*) — a third rendering of one message,
and deliberately so: they answer three different questions (what the pane shows, what the
team passed around, what this agent said).

Because they are classes now, both collapsing modes fold them and need words for
them:
`tracked N tasks` and `passed N messages`. Neither is Claude Code vocabulary (it
has no agent-team surface to word), so they follow the table's shape — an active
participle and a plain past tense — and are marked as ours in `VIEW_FRAGMENTS`.
`passed N messages` now counts only the rows that HOLD a message (the plumbing is
dropped before the counters see it), so the summary can no longer promise mail that
turns out not to be there.

Both non-verbose modes additionally drop **injected prompts** — turns written in
the USER's shape that the human never typed (`transcript._injected`: three
structural marks and one anchored text shape). The first is `isMeta`:
a **Stop hook's blocking feedback**, a **loaded skill's whole SKILL.md body**
(injected as a text block right after the `Skill` tool_result — the noisiest of
them), and the resume nudge `Continue from where you left off.`. They used to
render as `YOU` bubbles, so a hook's feedback — or an entire skill — read as
something you had said. `<`-wrapped envelopes (`<command-name>`,
`<local-command-caveat>`, `<system-reminder>`) were already dropped by
`conversation()`; these are bare prose, so nothing but the flag distinguishes
them, which is why `transcript.parse_line` now CARRIES the flag (as `meta`)
instead of discarding it — on both record shapes, since a skill body arrives as
list content while a hook's feedback is a plain string. `session_title` had
skipped `isMeta` rows all along for the same reason; the fact is now shared
rather than re-read per consumer.

The second mark is `interruptedMessageId`, which flags Claude Code's synthetic
`[Request interrupted by user]` / `[… for tool use]` annotation. That record is
NOT isMeta, so it needed its own mark; the field carries the id of the message the
cancel cut off. Matching the annotation's TEXT instead would re-run the
false-positive class `tabstatus.is_interrupt_line` documents at length — a Read of
a doc that mentions the marker, a grep hit, or a conversation about it is
textually identical to the real thing, and that once flipped tab colours mid-turn.
An id-bearing field cannot be quoted.

The third mark is `isCompactSummary` — the **compaction summary**. After a
`/compact` (or an auto-compaction) Claude Code writes a `compact_boundary` system
record and then, as the new context, a `user` record holding the whole
`This session is being continued from a previous conversation…` recap. It is a
user turn only in shape: the human did not type it, and it is *enormous* — the
six in one measured session ran 11k–17k characters each, so every compaction
dropped a wall of prose into the feed under a `YOU` label. Like the annotation it
is NOT isMeta, hence its own mark, and like the other two it is matched
structurally: the summary's opening sentence is ordinary English that any
conversation about compaction reproduces verbatim, while the boolean field cannot
be quoted. Verbose still shows it (it is in the transcript, and it IS what the
model now sees); default and focus drop it. The `compact_boundary` record itself
never reached this stream at all, so nothing marks the boundary in the mirror,
which is deliberate: the collapse is about what you read, and the boundary's one fact
(context was compacted) is already on the ctx-saturation bar.

The fourth mark is the only one read out of TEXT: the **teammate-mail envelope**.
Claude Code delivers another session's message as a user turn of its own making —
the framing sentence `Another Claude session sent a message:`, the peer's
`<teammate-message teammate_id=… color=…>` block(s), then its own trailing "this
came from another Claude session … that's permission laundering" instruction. None
of it was typed by the human, and in a teammate-heavy session it arrives every few
minutes, so it read as a stream of `YOU` bubbles full of `idle_notification` JSON
in every mode (the report). Unlike the other three there is **nothing structural
to read**: measured on the corpus the record is `type: "user"`, `isMeta` absent,
`userType: "external"`, `isSidechain: false` — byte-for-byte the shape of a typed
prompt. So the mark is the envelope's shape, **anchored at the start of the
content** (`transcript._TEAM_ENVELOPE`), which is what makes reading text safe
here: a message that merely QUOTES an envelope — a paste asking "why is this in my
transcript?", this file — has something in front of it and stays yours. A wording
change in the framing sentence degrades to the old behaviour, not to a crash. The
BARE `<teammate-message>…` form (no envelope) is untouched: `classify_user_text`
already turns that into a `teammsg` record with its own ✉ sender bubble, because
there the sender is known.

**Verbose relabels them: ⚙ SYSTEM, not YOU.** Verbose deliberately keeps injected
turns — it shows the transcript as it is, and they ARE in it — but a bubble
labelled `YOU` over a hook's feedback, a loaded skill or another session's mail is
a lie about who said it, whatever the mode. `msg_html`'s `meta` therefore renders
them as a system bubble: the `⚙ system` label, a second class `sys` beside the
kind (the kind stays `prompt` — the page's focus logic keys on it), and the
deliberately neutral slate of `core/ops.py`'s SLATE (`--sys`) instead of prompt
gold, because none of this is conversation. It also drops the affordances that
only make sense for something you wrote: no `data-txt` and no `↶`, so a system
turn is not a rewind target and not a `↑`-history entry in the composer — and the
two selectors that reach for "your newest prompt" (the interrupt take-back, the
rewind picker's click target) exclude `.sys`, since an injected turn can easily be
newer than the prompt they mean.

An injected prompt also does **not close the turn** for focus mode's
final-reply rule — the reply after a hook firing still belongs to the prompt you
typed, and treating it as a boundary surfaced a second "final" reply per firing.

**This is a rendering choice, not a setting.** Claude Code has its own
`viewMode` (settings.json; Ctrl+O toggles verbose/default, `/focus` toggles
focus) and this feature deliberately does NOT touch it: nothing is written into
any settings.json, the kitty mirror keeps painting everything at every mode, and
switching modes here changes only what this browser paints.

### The summary vocabulary is Claude Code's, verbatim

Read out of the `2.1.220` binary's bundled JS (`strings` + the
`collapsed_read_search` renderer) rather than guessed, because the whole point is
that a collapsed line reads exactly like the TUI's. The rules:

- one fragment per counter, `"<verb> <n> <unit>"`, joined with `", "`;
- the FIRST fragment is capitalized, the rest are not;
- while the run is still going, the verb is a present participle and the line
  ends in `…`; once it is done, past tense and no ellipsis;
- fragments emit in a FIXED order (edits first, memory last) — not in the order
  the tools ran.

| counter | running | done | unit |
|---|---|---|---|
| edits (incl. Write) | `editing` | `edited` | `N file(s)` + `+A -R` |
| reads | `reading` | `read` | `N file(s)` |
| agents | `running` | `ran` | `N agent(s)` |
| shell commands | `running` | `ran` | `N shell command(s)` |
| background jobs | `running` | `ran` | `N background job(s)` |
| monitors | `watching` | `watched` | `N monitor(s)` |
| memory reads | `recalling` | `recalled` | `N memor(y/ies)` |
| memory writes | `writing` | `wrote` | `N memor(y/ies)` |

So: `Read 1 file, ran 1 shell command` · `Ran 2 shell commands` ·
`Reading 4 files, running 2 shell commands…` ·
`Edited 2 files +52 -3, read 1 file, ran 1 shell command`.

Claude Code's own table also carries `searching for N patterns`, `listing N
directories`, `calling <mcp server> N times`, `REPL'd N times` and
`Thought for <t>` — dropped here because **the mirror carries no such ops**:
only Bash, the file tools, and agent/monitor/codex streams reach the ops table
(a Grep never appears in the web stream at all, in any mode). The last two rows
are ours, not Claude Code's — it has no background-job or monitor concept — and
keep its verb pattern. A Write counting as an *edit* is Claude Code's own
behaviour (one `editFileCount` over its whole edit-tool set), so the summary says
"edited" even for a created file; expanded, the mirror still shows `Write(name)`.

### Where the collapse is computed, and why on the client

The activity class each item is grouped on — `act`, plus `bad` and a mutation's
`add`/`rem` — is computed SERVER-side, once, in `dashboard/opshtml/actclass.py`
and carried on every stream item by `op_items`. The classifier keys on structure
first (the op's `t`, and the semantic colours imported from `core/ops.py`: a chip
in the shared `RED` IS a failure; a `▶` in a slot PALETTE colour is a subagent
launch, not a shell command — the glyph alone is ambiguous), then the file-op
verb taken from its owner `tools.FILE_LABEL`, then the block-opening glyph
(`▶ ▷ ◉ ↻ ■` — producer vocabulary, and this table is its one reader; the glyph
is deliberately preferred over the WORD beside it, which has been reworded).
An unclassifiable op gets no `act`, which means "not collapsible": a
classification gap always fails toward SHOWING content.

**Why not stamp `act` in the producers** (the route `src`/`web` took)? Because
unlike a producer's identity, the activity class is fully recoverable from the op
the producer already wrote — so stamping would put the same knowledge in eight
formatters AND still need the render-time classifier for every PARKED session
(which can't be re-stamped), i.e. two implementations that drift.

**Why the grouping is client-side** (`applyViewMode` in `app.05-session.js`): a
run is a maximal set of ADJACENT items, and the server never sees the stream as a
whole — ops arrive as SSE increments and history as separate `/history` pages, so
a run routinely straddles two responses. The client, which owns the assembled
feed, is the only place that can cut runs correctly; it also means switching
modes is instant and re-fetches nothing. The price is that the phrase table above
lives in JS as a deliberate twin of no Python owner, pinned by grep tests
(`test_act_vocabulary_matches_the_page_phrase_table`) plus one executed test:
`tests/jsdom/viewmode.js` runs the real engine under `node` over a DOM shim,
because a grep cannot tell a correct run cut from an off-by-one.

Mechanics worth knowing:

- **The run key is its OLDEST member's** (`data-vk`, a monotonic per-session
  stamp). Runs grow at the newest end, so that key is stable as a run absorbs new
  items — which is what makes a user's expansion survive the next SSE tick.
- **Expanding leaves the summary in place** (caret `▾`): it is the only way back
  to collapsed, and it keeps naming what the revealed blocks are. It also becomes
  the group's HEADER: the blocks it revealed are marked `.vrun` (`.vrun-last` on
  the oldest) and share a left rail whose vertical gaps are closed, so the group
  reads as one connected stack and you can see where the expansion ENDS — without
  it, revealed blocks land in the feed looking like any other activity.

  **ONE rail, at one x, on one text column — nothing shifts when you click.** The
  members were INDENTED 13px at first, which drew a SECOND vertical line beside the
  header's own and a row lower: two parallel rails, neither spanning the group
  (*"the left side gutter looks broken … there's a shift to the right … and the
  gutter of those actions also shift to the right"*). Now every row in the group —
  header and members — sits at the same left edge, so the two borders ARE one
  unbroken line, and each gives up 2px of its own left padding to the rail's 2px
  border so the whole group lands on the feed's usual 13px text column. Left at
  their own 13px the members sat 2px right of the header: too small to read as an
  indent, exactly large enough to read as a mistake. Members also drop their own
  CARD (`box-shadow`/fill) when they are single-line rows — the tile edges cut the
  rail into segments and made the group read as loose plates under a bar — while a
  `.blk` keeps its card for the body it reveals if you open one inside the group.
  The rail plus the header's panel fill are what say "these belong together"; an
  indent is not needed to say it.
  The marks are classes, not a wrapper element: SSE inserts by position, the block
  map holds live references, and the eviction sweep walks top-level children, so
  re-parenting into a container would break all three. Every mark is cleared at
  the top of each pass (`clearViewMarks`) — a leftover would rail a run that is no
  longer open.

  The rail's geometry is `!important`, the second and only other place this
  stylesheet allows it (the first is the hide classes, and it is the SAME hazard):
  a row rule sets `margin` too, and being declared later only wins on EQUAL
  specificity. `.blk`'s `margin: 7px 0` is one class and loses to `.stream > .vrun`,
  but an AGENT NOTE's box is `.stream > .blk[data-note]` — three — and four with
  `[data-open="1"]`, so its `margin` SHORTHAND reset the rail's `margin-left` to 0
  and reopened the gaps: expanding a run of agent notes put every note 13px left of
  every other member with the rail in disjoint segments (reported 2026-07-27 as *"the
  alignment of elements is off when I expand the summaries"*). Nothing was wrong in
  the JS — `.vrun` was on the right nodes — which is why the JS harness could never
  see it and the check is a CSS property test instead
  (`test_the_run_rail_outranks_a_rows_own_margin`): no `.stream >` row rule may
  out-cascade the rail, however specific. The view-mode classes are an outer layer
  over row styling, and that is what `!important` states here.

  The revealed blocks also arrive **folded** (`data-open="0"`), each showing just
  its header line. Expanding a summary asks *which actions were these*, not *dump
  every command's output* — a run of five commands opening at full body is the
  wall the collapse exists to remove, and any one block is a further click away. A
  block you opened YOURSELF is exempt: the head toggle stamps `data-userset` on
  the node, which this pass reads, so it cannot re-fold your manual toggle on the
  next tick. (The flag is mirrored onto the DOM because `b.userSet` is
  unreachable here — a history block has no entry in `S.ses.blocks` at all.)
- **Hidden AND dimmed items are transparent to the run cut** — the activity
  either side of an injected prompt, or of focus mode's greyed prose, merges into
  one summary rather than leaving two lines with a gap. Only a fully shown item
  ends a run. Dimming deliberately does NOT change this (see *focus* above).
- **The dot**: grey and pulsing while the run is going, green when done, red when
  any member failed (`bad`). A run counts as running when it contains the live
  foreground command (`fg_running`) or sits at the top of the feed while the tab
  is busy — the same two facts the ⏱ chip uses.
- **The elapsed** ` · 12s` ticks locally once a second, anchored on the running
  command's real `start_ts` when there is one, and only appears after 2s (Claude
  Code's own threshold, and it stops a fast run flashing `· 0s`).
- **A repeat pass is a no-op**: the plan is reduced to a signature (runs, their
  members, open/running/bad — never the elapsed clock) and the DOM is only
  rebuilt when it changes. Without that, every 0.6s SSE tick tore down and
  re-created every summary, which reflows the feed under a reader who has
  scrolled back and drops a text selection. Same trick, same reason, as
  `statsSig`.
- **"load older" is measured in VISIBLE items, not blocks.** The server counts
  BLOCKS (`_cut_blocks`) and cannot do better: what a page leaves on screen
  depends on the current mode AND on runs that merge across the page boundary,
  neither of which the server knows. So in a collapsing mode a 40-block page
  could arrive and change nothing — the run at the boundary simply absorbed it
  and its counter went up. That was the "I click load older 40 more blocks and 40
  more blocks don't appear" report. `loadOlder(want)` therefore LOOPS: fetch,
  measure the rise in `visibleCount()` (unhidden items + summary lines), and if
  it is short, fetch again — sizing the next page at the observed yield
  (`olderPageSize`, capped at `OLDER_PAGE_MAX` 400) rather than creeping 40 at a
  time, because every `/history` call re-merges the session's whole history
  server-side. Measured on real sessions a 40-block page yields ~20-30
  focus-visible items, so it converges in about two requests. `OLDER_TRIES` (6)
  bounds it, for the pathological case the loop cannot win: a long
  commands-only stretch where every block merges into the same run, so the
  visible count physically cannot rise. Verbose is unaffected — one page is 40
  visible items, so it never issues a second request. The button also stops
  saying "blocks" outside verbose, where the noun would be wrong.
- **Auto-fill**: collapsing 80 blocks can leave two lines on screen, so a mode
  switch tops the feed up to `VIEW_FILL_MIN` (15) visible items, at most
  `VIEW_FILL_TRIES` (3) times per switch — through the SAME `loadOlder`, aimed at
  a smaller target. Two independent pagers would fight over `loadingOlder` and
  double-fetch the boundary. **A switch is not a page** — these are two different
  promises, and conflating them is the natural reading of the UI ("is it really 40
  when I switch?"): the button aims at 40 visible items, a switch only refills the
  window. The floor is 15 rather than the 6 it shipped with, measured by driving
  the real engine over the live `/history` on a long session: at 6 a switch into
  focus spent one request and left ~11 visible (a third of a screen, which reads as
  "the mode ate my session"); 15 spends two for ~25; 20 buys nothing 15 didn't. In
  default one page already clears 15, so only focus pays the extra request.
  Switching INTO verbose fills nothing — it hides nothing, so the window is
  already as full as the loaded data allows (`test_switching_into_a_collapsing_
  mode_fills_the_window` pins both halves).
- The two axes are independent classes: `.fhide` is the kind filter's, `.vhide`
  the view mode's. One shared class would let either pass un-hide the other's
  items.

Known gap: a STANDALONE codex host session's blocks classify as `agent` (there
codex IS the main agent, and its chips carry no main-session glyph), so its
default-mode summary reads "ran N agents" instead of naming the codex run. It
needs a codex-specific act to fix properly; until then `verbose` shows such a
session unfolded.

## Notifications (the toaster)

One daemon thread diffs the ENTIRE tab table (`sessionapi.tab_states()` — the
whole-table reader added for exactly this; per-window probes would be N
queries for one snapshot) once a second, and maps windows to sessions via the
audit `sessions` rows' `kitty_window_id` (newest session wins the window — a
kitty window outlives sessions). A transition INTO `awaiting-command` (red —
Claude is asking you) or `awaiting-response` (green — done, your turn) pushes
a `notify` event to every `/events` client; the app shows an in-page toast on
the FOCUSED device only (the handler self-gates on
`visibilityState`/`hasFocus`). There is deliberately no `new Notification()`
here any more — the old immediate `osNotify` on a hidden tab buzzed every
backgrounded device at once, the exact duplicate *Device routing* fixes, and
iOS never supported that constructor in an installed app anyway; the on-device
system notification is Web Push, at the deferred fire point (*Web push*). The
win→session map
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
  (audit `ended_at` set, `session_ended`): you closed/quit it on the dashboard,
  so you were satisfied and moved on and the alert (its deep link would open a
  dead session) is moot. The `ended_at` check is the robust one the win-vanish
  test can miss — a stale tab row can linger past a close, and a reused kitty
  window id can even re-match the armed state under a DIFFERENT session. It is
  ALSO cancelled while you're **composing** a reply — a non-empty unsent web
  composer draft (`composing` over `composer_draft`): typing a draft means
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
- It is ALSO cancelled while you're **continuing the conversation AT THE
  TERMINAL**. A green `awaiting-response` is your turn; you typing a reply into
  the session's `❯` input box moves neither the tab off green nor the transcript
  until you submit (submitting flips it to busy, which the state-change cancel
  already catches), so none of the checks above see it. Its trace is REAL
  (non-faint) content in the input box: a settled tab pre-fills only a FAINT
  ghost suggestion, so the notifier reads the window's input line
  (`suggestion.typed` over the ANSI `get_text`, the same faint-SGR technique the
  *Web ghost suggestion* uses — a ghost is dim, your typed/queued text is normal
  weight) and **drops the arm the moment any real text is there** — you're on
  it. It stays armed for an empty box, a ghost-only box, or when no terminal
  channel resolves, so those keep the plain grace-window behaviour. The drop is
  audited as a `notify-suppress` `state_files` row (`reason: terminal-input`).
  Limitation by design: pure thinking with ZERO keystrokes for the whole grace
  window is indistinguishable from walking away and still fires — bump
  `CLAUDE_DASH_NOTIFY_DELAY_S` for a longer think.
- Finally, the alert is suppressed if you are plainly **looking at the session**
  through either surface, dropped with a `notify-suppress` row. The WHEN differs
  by kind — a deliberate asymmetry:
  - For a **`done`** arm the rule is **"if I've SEEN the final message, don't
    tell me"**: a done tab's result is on screen the moment it goes green, so
    the check runs **every scan while armed** (not just at send time). A single
    glance ANY time during the grace cancels the alert — even one that has since
    ended and you've moved on — because you don't need a ping about a result you
    already read.
  - For an **`asking`** arm the check runs only at **send time** ("are you
    looking RIGHT NOW"). Seeing a question is not answering it, so a glance that
    then walked away WITHOUT answering must still fire the reminder; only being
    on it at the moment we'd ping (or answering at the terminal, above) suppresses.
  Both use the same two "looking at it" channels:
  - **The kitty TAB is frontmost** (`Frontend.tab_focused` → `reason:
    tab-focused`). Keyed on the terminal's `is_focused`, deliberately NOT
    `is_active`: a session the dashboard just SPAWNED opens a new tab that is
    `is_active` inside kitty, but while kitty is a BACKGROUND app (you're on
    your phone / in a browser) that tab is **not** `is_focused` — verified
    empirically (a plain web-launch does not raise kitty; `is_focused` flips
    true only when kitty holds OS keyboard focus AND the tab is active, i.e.
    it's genuinely in front of you). So a synthetic tab can never falsely
    suppress an away alert. False (fires as before) when no terminal channel
    resolves — a dashboard started outside kitty has no `Notifier.fe`. The tab
    read is one shared `ls()` per scan (passed to every armed entry's check),
    not one `kitten @ ls` per session.
  - **A browser is VIEWING the session** (`reason: web-viewing`). The page POSTs
    `POST /api/session/<sid>/viewing` on a heartbeat, but ONLY while it is
    visible + focused + inside that session's view (`document`
    `visibilityState`/`hasFocus`), so the beat's mere arrival is the signal; the
    server holds it in an in-memory `_VIEWING` deadline for `CLAUDE_DASH_VIEW_TTL_S`
    (default 20s). Because a beat fires immediately on focus/reveal and the
    deadline outlives the ~1 s scan, even a brief look at a done session lands a
    beat that the next scan sees — so the "I saw it" rule catches short glances.
    This is the "did the page get viewed" heartbeat the arm/cancel design above
    deliberately avoided — added specifically so seeing the final message on the
    dashboard suppresses the off-device ping. It is ephemeral live-only presence:
    NO per-beat audit row (like the SSE connection), only the `notify-suppress`
    outcome it drives is recorded.
- An entry that **survives** past the grace window is delivered in TWO stages —
  **device-first, Telegram-if-ignored** (*Device routing*, below):
  - **Stage 1 (on-device):** a Web Push to the ONE device you most recently used
    (`mru_push_targets`), NOT every subscription — so a session going done/asking
    buzzes the device you're working on, never your iPad and Mac at once. The
    entry stays armed with an `escalate_at` = now + `CLAUDE_DASH_ESCALATE_S`
    (default 300 s / 5 min).
  - **Stage 2 (Telegram nudge):** if it survives to `escalate_at` — you STILL did
    nothing with the session (any reaction / look already dropped it in the
    cancel loop, so surviving means genuine inaction) — Telegram fires, in case
    you're away from that device.
  - If there's **no device to push to** (nobody subscribed), Telegram is the
    IMMEDIATE stage-1 fallback (nothing to escalate from). `NOTIFY_TELEGRAM_ALWAYS`
    fires BOTH at stage 1 (no escalation wait). Each stage still honours the
    per-session mute and the send-time "you're looking at it now" suppress, and
    fires **regardless** of whether a browser is connected — reaching you when
    away is the whole point.

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

**Per-session opt-out.** The header's **◉ alerts / ○ muted** button
(`chromeActions`, beside ✎ rename / ⇆ migrate) toggles
`POST /api/session/<sid>/notify` `{"muted": bool}`, which flips the session's
entry in the durable global prefs store (`dashboard/prefs.notify_muted` /
`set_notify_muted`, one `notify-muted` kv map keyed by sid — an un-mute deletes
the key so the map stays the small muted set). Like rename it is deliberately
NOT live-gated: the opt-out is a dashboard pref, not session state, so it works
live AND parked, and `session_payload` carries `notify_muted` so the button
paints the right label on load. The mute is checked at SEND time (not arm time),
so muting during the grace window still suppresses the alert.

### Global alerts toggle (the master switch)

The per-session opt-out silences ONE chat. The **◉ alerts / ○ alerts off**
button on the list page's header — right after **+ session** (`#notifytoggle`,
styled with the shared `.ghost` header class) — is the ONE master switch over
EVERY dashboard notification: both the immediate cross-session toasts / OS
notifications AND the deferred Telegram / web-push alerts. Default **ON**.

It is gated at the single transition site in `Notifier.scan()`: when
`prefs.notify_enabled()` is false the scan skips both the immediate
`self.push("notify", …)` and the deferred arm, and writes a
`notify-suppress` `state_files` row with `reason="global-off"` (so "no alerts at
all" is answerable from the audit DB). Because it short-circuits before the arm,
it OVERRIDES the per-session `notify_muted` check downstream — global OFF means
everything is suppressed; global ON leaves the per-session mutes in force.

The state is durable and machine-global: one bare bool under the `notify-enabled`
key in `dashboard/prefs.py` (`notify_enabled` / `set_notify_enabled`, default ON
so an absent key reads `True`). Being in the global prefs store
(`core.paths.DASH_PREFS_DB`, `~/.claude`) — not per-browser, not per-checkout —
it is **cross-device / cross-session and covers every git worktree**: the
notifier reads the flag live, so one flip governs all sessions at once.

- `POST /api/notify` `{"enabled": bool}` (a FIXED, non-session route — distinct
  from `/api/session/<sid>/notify`) writes the pref, audits a global
  `notify-global` `state_files` row, and pushes a `notify-config` SSE event.
- `GET /api/notify-config` → `{"enabled": bool}` seeds the button on page load.
- The `notify-config` SSE event repaints the button on every OTHER open page
  (`paintNotify` in app.js), so the toggle's visual state stays in sync across
  devices. The functional suppression is already instant cross-device (the
  notifier gates firing server-side); the SSE event only keeps the button honest.

**Env knobs** (read once at server start — a restart picks up changes):
`CLAUDE_DASH_NOTIFY_DELAY_S` (grace seconds before firing, default `60`; bad /
negative → default), `CLAUDE_DASH_NOTIFY_TELEGRAM` (`0` disables arming +
sending entirely, the in-page toast is unaffected; default on), and
`CLAUDE_DASH_NOTIFY_CMD` (the notify script path — `~` expanded, overridable for
a different transport or the hermetic test's recorder),
`CLAUDE_DASH_PUBLIC_URL` (the deep-link base — the proxied origin the alert
opens, default `https://baqylau.zhambyl.top`; trailing slash tolerated), and
`CLAUDE_DASH_VIEW_TTL_S` (how long a browser viewing-heartbeat keeps a session
marked "you're watching it", default `20`; must stay above the page's ~8s beat
cadence so a continuously-viewed session's presence never lapses), and
`CLAUDE_DASH_ESCALATE_S` (seconds after the on-device push before the Telegram
nudge fires if you still did nothing with the session, default `300` = 5 min;
bad / negative → default).

### Web push (on-device, esp. the installed iPad app)

The **in-page toast** only fires while a page is OPEN and (as of *Device
routing*) FOCUSED — useless for the main mobile case: an installed iPad
home-screen app (*Mobile / iPad*, *Add to Home Screen*) that's CLOSED when a
session needs you. iOS delivers a system notification to a closed/backgrounded
web app **only** via **Web Push** — a service worker the SERVER wakes — and does
NOT support the `new Notification()` constructor there at all (the old immediate
`osNotify` on hidden tabs was REMOVED: it buzzed every backgrounded device, the
exact cross-device-duplicate problem *Device routing* fixes). So Web Push is the
on-device channel, delivered at the **same deferred fire point** as the Telegram
alert: the same red `asking` / green `done` transitions, the same grace window +
arm-cancel + all the suppress logic, the same per-session ○ mute (checked at send
time). Either channel arms the pending alert (`NOTIFY_TELEGRAM or NOTIFY_WEBPUSH`).

**Device routing (device-first, Telegram-if-ignored).** The deferred alert is
NOT fanned out to every subscription (which put the SAME alert on your iPad AND
your Mac at once). Instead:
- **Stage 1** sends the Web Push to the ONE device you most recently used
  (`mru_push_targets`): every subscription is tagged at subscribe time with a
  stable `device` id (app.js `DEVICE_ID`, persisted in `localStorage`) + a
  `label`; the ~8s `/api/presence` beat stamps `_DEVICE_SEEN[device]` while that
  device's dashboard is visible + focused (from ANY view); the push goes to the
  subscriptions of the device with the newest beat. (Legacy untagged
  subscriptions can't be routed, so they degrade to send-all.)
- **Stage 2** escalates to **Telegram** `CLAUDE_DASH_ESCALATE_S` later (default
  5 min) **only if you still did nothing with the session** — the away nudge.
- If **nothing is subscribed**, Telegram is the immediate stage-1 fallback.
  `CLAUDE_DASH_NOTIFY_TELEGRAM_ALWAYS=1` forces BOTH channels at stage 1 (no
  escalation wait) — e.g. you always want the Telegram copy too.

This, plus the immediate toast now firing **only on the focused device** (the
`notify` SSE handler self-gates on `visibilityState`/`hasFocus`), is what routes
a notification to the device you're working on and leaves the others quiet. A
subtle edge: if the stage-1 push send later fails, Telegram isn't a backup for
THAT alert — but a `gone` subscription self-prunes, so the next one re-routes.

**Audit coverage (every routing decision is reconstructible).** The whole
deferred lifecycle leaves `state_files` rows so a "wrong / missing / duplicate
notification" is answerable from the DB after the fact — the routing is NOT a
black box:
- `notify-arm` (`phase:arm`, `delay_s`) on the transition — the lifecycle
  anchor; a silent disappearance instead = you reacted (the tab moved, see the
  paired `tab_transitions` row).
- `notify-suppress` (`reason:` dialog-activity / terminal-input / tab-focused /
  web-viewing / muted / global-off) when a look/reaction/opt-out dropped it.
  `Notifier._drop(win, reason=None)` is the ONE disarm site, and that is what
  keeps the anchor's promise auditable: a drop with a `reason` files the row, and
  the *only* no-row drops are the deliberate ones — you reacted (tab moved off
  red/green, session ended, a web draft in progress), which `tab_transitions` /
  `sessions` / `composer-draft` already explain, and the two SEND paths, whose
  `telegram-notify` / `web-push` rows are their own record. A per-session
  **mute** used to drop unaudited and so read as "you reacted"; it now files
  `reason='muted'`.
- `notify-route` — the DEVICE-SELECTION decision at stage 1: `{target,
  target_label, candidates:[{device, label, age_s}], n_subs, legacy}`. This is
  the "why did the iPad and not the Mac get it" evidence: the chosen device AND
  every candidate's presence age at decision time.
- `web-push` `action:send` now carries the target `device` (per delivery).
- `notify-arm` (`phase:escalate`, `in_s`) when the on-device push arms the
  Telegram escalation; `telegram-notify` carries `reason:` **escalation** (the
  5-min nudge) / **no-device** (immediate fallback) / **always** — so a Telegram
  alert is never an unexplained duplicate.
- On the FRONTEND (`web-client` rows, *Frontend audit* below): every batch
  carries this browser's `device` id (so any row is device-attributable), a
  once-per-load `boot` maps `device`→`dlabel` (human platform), and a
  `notify.recv` event records whether THIS device received the toast and whether
  it showed it (`shown`/`vis`/`focus`) — a gated recv explains a toast you never
  saw (you weren't looking at this device). Together the backend `notify-route`
  and the frontend `notify.recv` bracket a notification end to end.

The pieces:

- **VAPID identity + payload crypto** — `dashboard/webpush.py`, built on the
  stdlib + `cryptography` (already present; `pywebpush` is NOT — hence the
  hand-rolled RFC 8291 aes128gcm encryption + RFC 8292 ES256 VAPID JWT). If
  `cryptography` is missing the whole feature degrades OFF (`enabled()` False) —
  never a crash. The **VAPID keypair is generated once and persisted** in the
  durable prefs store (`vapid-keypair` kv); rotating it would silently orphan
  every existing subscription, so it must stay stable. `send()` never raises — it
  returns a `Result` the caller acts on (`ok` / `gone` (404/410 → prune) / soft
  failure).
- **The service worker** — `dashboard/static/sw.js`, served at the **root**
  `/sw.js` (its own server route, NOT `/static/`) so its scope is the whole
  origin. It caches nothing and intercepts no fetch (the dashboard is a live SSE
  app, not an offline one); it only turns a `push` into `showNotification` and a
  `notificationclick` into focus-or-open of the `?s=<sid>` deep link (the same
  query-param link the Telegram alert uses).
- **Subscription lifecycle** — the header's existing **enable-notifications**
  button grant (or a silent re-subscribe on load when permission is already
  granted, `initPush` in app.js) registers the SW, calls
  `pushManager.subscribe({userVisibleOnly, applicationServerKey})` with the
  server's VAPID public key (from `GET /api/push/config`), and POSTs the
  subscription — **plus this device's `DEVICE_ID` + `label`** — to
  `POST /api/push/subscribe`. Stored (upserted by endpoint) in the durable global
  prefs store (`push-subs` kv — per-DEVICE, not per-session), the `device`/`label`
  saved ALONGSIDE the wire fields (`webpush.send` ignores the extras) so
  `mru_push_targets` can route by device. `POST /api/push/unsubscribe` (and a
  server-side prune on a `gone` send) drops
  it.
- **The send** — `channels.send_webpush` builds the `{title, body, sid, kind,
  url, badge}` payload and fans it out to the routed subscriptions on a
  **detached daemon thread** (`_webpush_fanout`) so the crypto + network
  round-trips never stall the 1 s watcher; each outcome is a `web-push`
  `state_files` row (`action: send` with `status`/`ok`/`gone`), a `gone`
  subscription is pruned. Subscribe / unsubscribe are their own `web-push` rows
  (`action: subscribe`/`unsubscribe`), and a retraction reuses the same fan-out
  with `action: resolve` (*Alert retraction*).

**Env knob**: `CLAUDE_DASH_NOTIFY_WEBPUSH` (`0` disables arming + sending on the
push channel; default on — but still a no-op without the crypto backend),
`CLAUDE_DASH_VAPID_SUB` (the VAPID `sub` contact claim, default a `mailto:`), and
`CLAUDE_DASH_NOTIFY_TELEGRAM_ALWAYS` (`1` sends BOTH push AND Telegram every time
instead of push-supersedes-Telegram — see the dedup note above).

**iOS caveat**: this works only from the **installed** home-screen app (iOS
16.4+ exposes `Notification`/`PushManager` only in a standalone web app), reached
over the PUBLIC origin, and the permission prompt must come from a user gesture
(the button) — a plain Safari tab shows no button and never subscribes.

### Alert retraction (clearing an alert you've dealt with)

An alert is a claim about the present — *this session needs you*. Once you deal
with the session the claim is false, but the Telegram message and the iPad
banner stay exactly where they were: a chat full of things that no longer need
you, and a lock-screen badge that lies. So the watcher does not stop at the
send. Every DELIVERY moves into `Notifier.sent` with the **handle** its channel
returned, and once the session stops needing you the alert is **taken back** —
the Telegram message is `deleteMessage`d, and a resolve push closes the banner.

**"Reacted" and "resolved" are different questions, and conflating them is the
bug this design exists to avoid.** Both are answered by one predicate
(`Notifier._reaction`, which names every way an alert can stop mattering), but
two declared tables decide who acts on each name:

- `SILENT_REASONS` — which cancels owe no `notify-suppress` row (unchanged).
- `RETRACT_REASONS` — which reasons also retract an alert **already
  delivered**: `tab-moved`, `session-ended`, `composing`.

Cancelling a PENDING alert is the broad question, *"do you still need to be
told?"*, and a mere glance answers it (`tab-focused` / `web-viewing` — you saw
the final message, so don't ping). Retracting a DELIVERED one is the narrow
question, *"is what you were told still true?"*, and a glance must NOT answer
it: look at a red asking-tab on your phone, get distracted, walk away — the tab
is still red, nothing has been answered, and deleting the alert there would
destroy your only reminder. So glances are deliberately excluded. The two
screen-scraped signals (`dialog-activity` / `terminal-input`) are excluded for a
duller reason: pass 4 runs with `screen=False` because a delivered alert is
tracked for HOURS, and a `kitten @ get-text` per record per second is a
subprocess bill the 60 s cancel window never had to pay. Answering at the
terminal moves the tab off red within seconds anyway, so `tab-moved` catches it
for free.

**Where it lives.** Retraction is why `dashboard/notify/channels.py` exists.
Before it, a send was a leaf call and the watcher could own both the WHEN and
the HOW; a retraction is a *second* wire operation that has to reach the exact
thing the first one produced, so "what was delivered, and what does it take
back" became a fact needing an owner. `notifier.py` now decides only when
(`self.pending` → `self.sent` → gone) and never touches a socket, a subprocess
or a payload shape; `channels.py` owns both directions of both channels behind
`send_telegram` / `send_webpush` / `retract`, the last dispatching through a
`_RETRACT` registry rather than an if/elif on channel.

**Telegram needed a real transport** (`dashboard/telegram.py`, the sibling of
`webpush.py`). The reused `notify` skill is spawned detached with DEVNULL
stdio and never waited on, so the `sendMessage` reply — carrying the
`message_id` that `deleteMessage` needs — was discarded before anything could
read it. The dashboard now calls the Bot API itself: credentials in
`~/.config/telegram/{bot-token,chat-id}` (the Deepgram-key precedent from
`dictate.py`; `CLAUDE_DASH_TELEGRAM_DIR` relocates the pair, which is also how
the suite stays hermetic — an autouse fixture points it at an empty dir so a
test can never send, or DELETE, a real message). **Unconfigured it degrades to
the legacy script**: the alert still reaches you, it just returns no handle and
so is never retractable. That is the deliberate trade — losing the alert would
be far worse than losing the retraction, and the `telegram-notify` row's
`retractable` flag records which kind it was.

**Nothing touches the wire on the watcher thread**, in either direction — the
rule the send already followed and the retraction had to be held to
(`telegram.delete` is a synchronous HTTPS call with a 10 s timeout; the scan
loop ticks at 1 s). So the send creates its handle synchronously and *fills* it
from its thread, and the delete likewise settles over two ticks: the first spawns
it and answers `PENDING`, a later one reads what the thread left. `PENDING` is
therefore load-bearing twice over — it also covers a retraction that genuinely
beats the message id home (you answered within the second). Either way the record
stays and is asked again next tick rather than being dropped with the message
stranded, and the `notify-retract` row still reports what happened on the wire
instead of an optimistic guess made before the call returned.

**On-device: the resolve push, and its honest risk.** `channels._retract_webpush`
pushes `{type:"resolve", sid, tag, badge}` to the subscriptions the alert
actually went to (NOT whatever device is most-recently-used by then — the banner
is on the former), and `sw.js` closes everything under the tag and **shows
nothing**. That is a knowing bend of the `userVisibleOnly` contract an iOS
subscription is made under: WebKit may answer a push that raises no notification
with a generic "updated in the background" placeholder, and can revoke the
subscription if it becomes a habit. Three things keep it survivable:

- **A 1:1 budget.** Exactly one resolve per delivered alert — the notifier
  forgets the record either way — so silent pushes are bounded against visible
  ones rather than being a background chatter channel. WebKit's budget model
  tolerates that; a chatty app is what it punishes.
- **A kill switch.** `CLAUDE_DASH_RESOLVE_PUSH=0` disables just this push. The
  Telegram delete is unaffected.
- **A client-side fallback.** `app.01-attention.js` `sweepStale()` closes every
  banner whose session no longer needs you, once per foreground visit, driven
  from `renderAttention` so it only ever runs with a REAL sessions snapshot in
  hand (sweeping off a boot-empty `S.sessions` would read "nothing needs you"
  and close banners that are still true). So a resolve that is refused, dropped,
  or switched off degrades to "cleared a bit later", never to a wrong badge.
  It also covers the case the server structurally cannot: the handles live in
  the running process's memory, so a dashboard **restart** strands every
  delivery it was tracking — the same bargain `pending` already makes.

**Bounds.** `CLAUDE_DASH_RETRACT_S` (default 24 h) is how long a delivery stays
retractable; it sits under `telegram.DELETE_WINDOW_S` (48 h, the Bot API's own
ceiling on deleting your own message) with margin. `config.SENT_CAP` (200) is
the backstop for the pathological case. Past either, the delivery is forgotten
*unretracted* and audited as such — an alert left behind is exactly the thing
worth being able to find later.

**Audit.** One action, `notify-retract`, with a single writer (the notifier —
`channels.retract` deliberately does not file it, so the lifecycle has one row
shape, and the expiries never reach a channel at all). Fields: `sid`, `kind`,
`channel`, `reason` (the RETRACT_REASONS name, or `ttl`/`capped` for an
expiry), `outcome` (`ok` · `gone` — already out of the chat, which is the same
thing · `failed` · `expired`), `ok`, `age_s`. The channels still audit their own
WIRE detail, which the notifier could not describe: the resolve push's
per-device delivery is a `web-push` row with `action: resolve`. The canned
anomaly **"off-device alert left behind (notify-retract not ok)"** is the
query — note it matches the sid *inside the JSON*, since notify rows are global
(`session_id=''`).

### Installed-app polish (badge · icon · wake lock · back)

Extras that only matter once the dashboard is a home-screen app (all
feature-detected — a plain browser tab silently gets none; `IS_STANDALONE` in
app.js, `matchMedia("(display-mode: standalone)")` ∪ `navigator.standalone`,
gates the chrome-assuming ones):

- **App-icon badge = sessions needing you.** The Badging API
  (`navigator.setAppBadge`) puts a count on the home-screen icon = live sessions
  in a needs-you state (red `awaiting-command` + green `awaiting-response`),
  cleared at 0. `updateBadge` rides the SAME `sessions` snapshot the attention
  strip does (called from `renderAttention`), so while the app is OPEN the badge
  tracks live. While the app is CLOSED the push service worker sets it from a
  `badge` field the server stamps into every push (`Notifier._needs_you_count`
  over the tab DB — the same red/green `NOTIFY_STATES` vocabulary). The two can
  briefly disagree; opening the app re-syncs from the live snapshot.
- **Real home-screen icon + manifest.** `dashboard/static/manifest.webmanifest`
  (linked from index.html, served off `/static/`) gives the app its name,
  `display: standalone`, theme color, and PNG icons (`icon-{192,512}.png` +
  a `maskable` variant); iOS uses the `apple-touch-icon.png` link (a real PNG
  beats the screenshot iOS auto-generates without one). The icons are the gold
  shanyrak on the `#0a0e15` canvas, rasterized from the brand SVG. **Re-add to
  Home Screen to pick up a changed icon** — iOS caches the install-time glyph.
  The manifest's `shortcuts` (long-press the icon → New session / Needs you) are
  honored on Android/desktop and IGNORED by iOS; `?new=1`/`?attn=1` land on the
  list and (for `new`) pop the new-session form (`deepLinkFromQuery`).
- **Screen Wake Lock** — the ☀ header button (`initWakeBtn`, shown only where
  `navigator.wakeLock` exists) holds a screen wake lock so the iPad stays awake
  while you watch a run; it glows gold while held. The lock auto-releases when
  the tab hides, so it's re-acquired on the next `visibilitychange` to visible
  while the toggle is on. Pure client state — no persistence, no audit.
- **In-app back** — a standalone app has no browser back button, so the ‹
  header button (`initBackBtn`, standalone-only, shown by `showBack` inside a
  session view) drives `history.back()` over the hash-router's own history
  entries (falling back to `#/`).

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
clickable — the layout-derivation fallback in `transcript.agent_path` sometimes
finds a transcript the audit never saw.

## The "running now" ribbon

The session header carries a compact ribbon under the stats row — one chip per
thing EXECUTING under the session right now: the foreground command tailer
(`⚙ fg`), background jobs (`◷ bg`), monitors (`◉ monitor`), and streaming
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

## Live command elapsed (the ticking `· 1m04s` chip)

A foreground command's mirror block used to show its duration only in hindsight
— the `■ finished · 3.2s` chip the block gets when it ends. While it ran there
was no clock at all, so a long `make test` / build / deploy read exactly like a
wedged one. The block now carries a **live elapsed chip** (`· 1m04s`, gently pulsing) in the
quiet header's tail slot, ticking once a second, and retired the moment the real finish
chip lands. It wears **no stopwatch glyph**: `⏱` (U+23F1) is emoji-capable, this was the
one page path that assigned text without `tp()` applying the U+FE0E pin, and it shipped
a colour emoji (*"No emoji"*, reported 2026-07-27, the moment the chip started painting
at all). A pin is only a REQUEST — a font lacking the text glyph ignores it, which is
why the ☀ wake button became an SVG — and the glyph was never needed here: the line
already reads `· 91.4s` when the command ends, so the ticking form is the same words,
and "still counting" is carried by the grey dot plus the pulse.
`test_no_page_glyph_can_turn_colour` now pins the whole class: no emoji-capable
codepoint may be written to the page without `tp()`.

**Where the start comes from: the `fg-live` hand-off, not a new store.**
`claude-cmd-pre.py` already writes a take-once hand-off record when it spawns
the live tailer, keyed to the tool call by `tid` — and `tid` **is** the mirror
block's copy-group id (the `g` stamped on the `▶ foreground` header ops, the
same id ⧉ copy collects by). Adding a `ts` (the command's start) to that record
makes it, verbatim, the statement the dashboard needs: *block `<tid>` has been
running since `<ts>`*. `sessionapi.fg_running(sid)` reads it through the new
read-only `state.hand_peek_at` twin and returns `{g, start_ts}` or `None`. It
**peeks, never takes** — consuming it here would strand PostToolUse's finish
chip — and it drops a record whose owning tailer pid is dead, the same staleness
verdict `cmd_pre` itself reaches before clearing an abandoned record (a manually
cancelled command fires no hook at all, so nothing consumes its record).

Because the record is take-once, *its presence is the liveness signal*: it
appears when the command starts and is gone the instant PostToolUse (or the
tailer's own reclaim) consumes it. No new state, no new sentinel, no lifetime to
leak.

**Split of labour: the server sends the start, the browser counts.**
`fg_running` rides `session_payload` as `fg_running` (so a page opened
mid-command starts ticking from the real start instead of waiting for the next
command) and is pushed as an `fgrun` SSE event on change. The seconds are
counted client-side on a 1s `setInterval` (`app.05-session.js` —
`setFgRun`/`tickFgElapsed`), which is why the event carries a timestamp and not
a number.

Deliberate choices, each rejecting something that was tried or considered:

- **Fast SSE cadence, unlike the `running` ribbon it resembles.** The elapsed
  advances locally, so what the event is really for is the START and the END —
  and on the slow (~3s) cadence a finished command would keep counting for
  seconds *next to* its authoritative `■ finished · 3.2s` chip. One hand-off
  peek per 0.6s tick (a single indexed SELECT plus a pid probe), pushed only on
  change.
- **The finish chip also retires the ticker, client-side** — but only a chip that
  CLOSES the block may. `fillBlock` drops the live chip when the block's `■ …` closer
  arrives (the served role, `quiet === "close"` — *The quiet register*). Both signals
  ride the same 0.6s tick in no fixed order, so whichever lands first wins;
  `S.ses.fgEnded` remembers the retired block id so a late `fgrun` can't resurrect a
  ticker on a command already reported done.

  **The role test is the fix for the chip never appearing at all** (reported
  2026-07-27: *"for running foreground commands, I still want to see the live time"*).
  The rule used to be "any FURTHER label op on the ticking block is the finish chip",
  which cannot tell an opener from a closer — and the opener routinely arrives with the
  ticker already armed: `cmd_pre` writes the `fg-live` record and the `▶ foreground` op
  in ONE hook run, the `fgrun` event rides a faster cadence than the ops, and
  `tickFgElapsed` bails when the block does not exist yet ("next tick"). So the opener
  landed on a matching `fgRun.g` and retired a chip that had never painted —
  permanently, because `fgEnded` then refused every later `fgrun` for that block. Same
  block, same `g`, and nothing in the DOM to show it had happened, which is why it read
  as "the live time is gone" rather than as a race. Pinned from both arrival orders by
  the jsdom scene (`fgLiveArmedFirst`/`fgLiveOpsFirst`), which drives the real
  `setFgRun`/`fillBlock`/`tickFgElapsed`.
- **Not derived from the ops' own `ts` column.** Every op row is timestamped, so
  the client *could* compute a block's start from its first op — but that gives
  no liveness: a block holding one chip is indistinguishable from one whose
  finish chip never came (a crashed tailer, a parked session, pre-`ts` history),
  and it would tick forever, in history, for sessions that ended weeks ago.
- **Not the `live` slot row's `start_ts`,** even though it is already on the wire
  via `running`. A slot is keyed by palette index and carries no tool_use_id —
  nothing ties it to a block. It can only power the session-level ribbon above,
  which is exactly what it does.
- **Not a per-second server push.** The start time is static; sending a
  recomputed number every second to every open page would be an event stream for
  a cosmetic counter.
- **Foreground only.** Background jobs, monitors and subagents each have their
  own card with a `running for` line (Jobs/Monitors tabs, the agent cards), and
  their blocks stay open for the life of the stream anyway. The fg block is the
  one the eye is on while you wait.
- **Web only.** The terminal mirror paints on op arrival and has no clock of its
  own; a per-second repaint there would mean emitting ops to animate a number
  (and every op is replayed on every SIGWINCH reflow). The scorebar's ⏱ already
  covers "how long has this session been going" in the pane.

Styling is `style.css` `.chip.blive` — deliberately NOT a filled `.chip` like
its `▶`/`■` siblings: those are the session's own painted labels replayed from
the ops stream, this one is the dashboard talking. Outlined, `--exec` blue,
`tabular-nums` so the ticking seconds don't twitch the chip wider. **Inside a quiet
command header it joins that register** (`.blk[data-quiet] .chip.blive`): no outline, no
accent, no glyph, just the dim pulsing figure — and it sits in the `.btail` slot, the
same column the final `finished · 91.4s` lands in, so the number does not jump across
the line when the command ends. It also drops `.chip`'s `display: inline-block` +
`overflow: hidden` for a plain text run, which is a BASELINE fix and not tidiness: per
CSS an inline-block whose `overflow` is not `visible` takes its bottom margin edge as
its baseline, so the box lined up while the digits inside rode ~2px high (*"this live
timer is a little bit misaligned"* — measured in a browser: an 18px box beside the 16px
text run it shares the line with). Inline, it has the same baseline as the `.cqt`
duration that replaces it. A chip armed before the header knew its register is
re-homed to that slot by the op that sets the flag. Read-only
throughout, so it adds no audit rows (like the ctx bars and the goal card); the
one producer change — `ts` in the record — is covered by the `state:fg-live`
`state_files` row `cmd_pre` already writes with the record as its content.

## Subagent scoreboard swap (in scope → the scoreboard becomes the agent's)

Entering agent scope (an agent card, or the `#/s/<sid>/a/<aid>` route) **swaps
the top scoreboard to that agent's own numbers**. `renderSessionChrome` derives
`ses.agentFocus = {aid}` from the scope (rather than clearing it, so a tab switch
inside scope stays in scope) and repaints the header; `updateStatsRow` branches on `agentFocus` and calls
`renderAgentScoreboard` instead of the session totals — the prominent header
**name** (`ses.projEl`) becomes the agent's (`◇ ‹desc›` / `◈` for a teammate),
and the stats row shows status, `model·effort`, event count, `⏱` duration, the
`Σ` token rollup, and `≈` cost, with the ctx row showing the agent's own ctx bar
and a leading **← session** link that leaves scope (it points at `#/s/<sid>`,
the mirror = the main agent). The session title returns when a full
`renderSessionChrome` rebuilds the header on the way back (the name write is
skipped mid-inline-rename). The running ribbon hides while focused (it's
session-scoped).

Because it is the SAME scoreboard in two modes, the two renderers share their
repeated pieces rather than re-encoding them: `chipAdder` (the chip markup),
`sigmaChip` (the `Σ` breakdown — the web twin of `core.ops.token_parts`, which is
the single owner of that display on the terminal side, so a per-site copy is a
bug there too) and `paintCtxRow` (the ctx row is REPLACED on every repaint, and
hidden when there's no occupancy figure). The one thing that legitimately differs
is where the four counters come from: the session reads the stats row's
`tk_in/tk_out/tk_read/tk_create`, an agent its `agent_usage`
`in/out/cache/create` — so each caller maps its own fields into `sigmaChip`.

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
off the enriched `ses.agents` row, so the swap is instant on click; the scoped
session payload's `agent_usage` (fetched with the rest of the meta — no request of
its own) then fills in the `Σ` tokens and `≈` cost. Per-agent **cost** is priced
server-side by `read/session.agent_usage` (`accounting.cost_usd` over the agent's
usage + last model) — the ONLY per-agent cost figure there is, since OTEL
`costs()` is aggregate by `query_source` (main/subagent/auxiliary), never
attributable to a single `agent_id`. `agentFocus` follows the scope, so leaving it
(the ← session link, the breadcrumb, the list) restores the session scoreboard,
and the SSE `stats`/`costs`/`ctx` events that keep flowing are absorbed by the
`updateStatsRow` branch — they repaint the agent view, never clobber it back to
the session.

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

A session's codex runs ride the same agents list and the same scope, with no
dashboard-side special-casing: `sessionapi.agents()` merges the audit
`streams` rows of `kind='codex'` in the same row shape (kind `codex`, `desc`
= the run label, `agent_id` = `sessionapi.codex_aid()` — synthesized from the
stream's src_path basename, since codex tailers record no hook agent_id), and
its scoped mirror is the ops that run already painted. That last part needs one
resolution step the others don't: a codex run is `src`-stamped `codex:<label>`
while its agent id is the rollout basename, so `read/mirror.agent_scope` looks
the label up off the run's row (*Agent scope*). A codex run exposes no
`agent_usage` provider — its tokens are folded from the rollout and priced at
its footer (`CODEX_PRICES`), so the scoped scoreboard simply shows no Σ/≈cost
rather than a second, differently-derived figure.

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

**Fullscreen on iPad — the ⛶ button vs. Add to Home Screen.** The header ⛶
button (`fsbtn`, app.js *fullscreen toggle*) drives the browser Fullscreen API
(`webkitRequestFullscreen` on iPadOS, the only spelling it ships; hidden on
iPhone Safari, which has none). That path is real fullscreen but carries a
gesture the page **cannot suppress**: a swipe down from the top edge exits it.
Apple guarantees the user can always escape API-fullscreen, so there is no
web API to block the exit — `preventDefault` on touch events never sees it
(Safari handles the gesture in its own UI layer, above the document). The only
swipe-*proof* fullscreen on iPad is **standalone / "Add to Home Screen" mode**:
the `apple-mobile-web-app-capable` meta tag (+ the Android/Chrome
`mobile-web-app-capable` twin, `apple-mobile-web-app-status-bar-style:
black-translucent` to run content under the status bar with the existing
safe-area insets, and `apple-mobile-web-app-title`) in `index.html`'s head make
the home-screen icon open the dashboard chrome-less and persistently fullscreen
with no swipe-to-exit — only the OS home-swipe, which is unavoidable and fine.
Reached over the public origin (`CLAUDE_DASH_PUBLIC_URL`) the user does it once:
Share → Add to Home Screen, then launch from the icon. No server route or
manifest is involved; the meta tags are the whole mechanism.

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

The L0 dashboard suite is ONE FILE PER SUBJECT — it was a single 8468-line,
355-test module, the largest file in the repo and the same monolith the
dashboard package itself was decomposed out of:

| file | subject |
| --- | --- |
| `tests/test_l0_dash_opshtml.py` | the ops→HTML presenter: escaping, SGR/OSC8, copy-link specs, lex/num gut bodies, and the rich tool renders (Bash highlight, Edit diff with escaped content, Write cap, Read one-liner, deflist, unknown-tool fallback) |
| `tests/test_l0_dash_server.py` | the HTTP server on an ephemeral in-process port (never through `serve()` — no singleton lock in tests) against data seeded via the real product APIs: endpoints, payloads, routing, caching |
| `tests/test_l0_dash_conversation.py` | session titles + the merged ops/conversation stream, incl. the lazy-backlog tests (the tail limit + `oldest` cursor, `/history` chaining to exhaustion with the slices concatenating to the unlimited merge — no gap, no overlap — and a straddling group never duplicated) |
| `tests/test_l0_dash_notify.py` | the notification watcher's transition logic, presence, and the deferred alerts |
| `tests/test_l0_dash_control.py` | the control plane: send / close / rename / launch / uploads |
| `tests/test_l0_dash_dialogs.py` | the screen-driven dialogs — rewind, ask, plan — and the terminal→web draft sync |
| `tests/test_l0_dash_probes.py` | dictation, the ghost-suggestion probe, and the single-owner grep tests |
| `tests/test_l0_dash_viewmode.py` | the view modes (verbose / default / focus) |

`tests/dashkit.py` holds what more than one of them needs (the HTTP helpers, the
audit-row readers, the fake frontend) — imported BY NAME, so a reader can see
where `_post` or `_FakeFE` came from. The one piece that cannot live there is
the `dash` server fixture: pytest resolves a fixture by NAME out of a test's
signature, so it sits in `tests/conftest.py` with the other hermetic-environment
fixtures. Import safety for the dashboard modules rides
`test_import_safety.py`.

The view modes are the one feature whose core logic is not reachable from
Python — the run cut lives in the page — so besides the usual classifier /
endpoint / vocabulary-parity tests there is `tests/jsdom/viewmode.js`: a
DOM-shim harness that `test_view_mode_engine_collapses_runs_and_words_them`
executes under `node` and asserts the resulting summary lines on. It SKIPS when
`node` is absent and is the only JS-executing test in the suite — deliberately
not a build requirement (see docs/testing.md).
