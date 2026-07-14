# Copy links, click-to-view, and paint-time neutralization

The mirror pane's click affordances (see [mirror-pane.md](mirror-pane.md) for the
pane itself), and the neutralization rule every paint path must obey.

**⧉ copy links — click to copy any activity block.** Nearly every block in the
mirror carries a dim, browser-style copy affordance on its header chip. A
**command** block (foreground / background, in the main session and inside a
subagent's or codex run's stream) shows two — ` ⧉cmd ⧉out`: `⧉cmd` puts the
block's exact command on the clipboard, `⧉out` its output (ANSI styling
stripped). Every other body-bearing block — an assistant **message**, a
**result**, a **prompt**, teammate **mail**, codex **reasoning** / **review** /
**search**, a subagent tool **request** — shows a single ` ⧉copy` that grabs the
whole block's text. Mechanism: producers stamp a **copy-group id** (`"g"`) on the
block's ops — the Bash `tool_use_id` / `backgroundTaskId` for a command (live-fg /
bg tailers inherit it via `CLAUDE_STREAM_GROUP` so their `gut`/finish ops join the
same group), or a fresh session-unique `O.new_group()` id (a `counters`-table
sequence, `core/state.py:next_group`) for a block with no natural tool id — and a
`"lk"` **link spec** on the header (`[[what, glyph], …]`, `what ∈ cmd/out/all`;
absent → the command block's default `cmd`/`out` pair; body blocks pass
`O.COPY_ALL` = `[["all", "⧉copy"]]`). The renderer wraps each affordance in an
**OSC 8 hyperlink** with the custom scheme
`claude-copy:///<key>/<gid>/<what>`. kitty resolves a plain left-click through
`~/.config/kitty/open-actions.conf` (see [wiring.md](wiring.md)), whose `protocol claude-copy`
rule launches **`claude-copy.py`** (impl `core/copy.py`) with the URL. The
handler re-reads the group's ops from the state DB **read-only** (`mode=ro` — a
click after SessionEnd must never recreate a DB whose file-existence is the
session-alive signal), takes `code` ops for `cmd` — the text **as displayed**,
i.e. the pretty-printed reflow, WYSIWYG (owner's call; it started as the
byte-exact original but pasting something other than what you see read as a
bug — and the reflowed form is equivalent, runnable bash either way) —
ANSI-stripped `gut` ops for `out`, and BOTH (interleaved, in insertion order) for
`all`, pipes the text to the clipboard
(`pbcopy`, else `wl-copy`/`xclip`/`xsel`; `CLAUDE_COPY_CMD` overrides — the test
seam), and appends a one-line `⧉ copied …` feedback op so the click visibly
landed. Why this design and not the alternatives:
- **Copy from the ops table, not the `.out` tee files** — the tee files are
  transient (deleted when the tailer finishes), while the ops table is the
  session's history, parked/restored across resume: scrolled-back and
  resumed-session blocks stay copyable.
- **OSC 8 + `open-actions.conf`, not mouse reporting** — a renderer that grabs
  mouse mode would steal normal text selection in the pane and need row→op
  geometry bookkeeping that reflow invalidates. Hyperlinks keep the pane a dumb
  stream; kitty underlines the link on hover and handles the click.
- **The links live on the `label` op only** (a short glyph run, never wrapped),
  which sidesteps OSC 8's re-open-per-visual-row requirement for wrapped text;
  a pane too narrow for chip + links (< ~34 cols) just drops the links.
- **Subagent & codex blocks tag too** — the substream and codex stream stamp `g`
  on their fg/bg/monitor command header/`code`/`gut` ops (the inner `tool_use_id`,
  threaded to nested `claude-stream.py` tailers via `CLAUDE_STREAM_GROUP`) and a
  fresh `O.new_group()` id on every message/prompt/mail/reasoning/review/result
  block (with `O.COPY_ALL`), so a subagent's or codex run's activity is copyable
  just like the lead's commands.
- **File-op one-liners don't copy — they EXPAND (click-to-view, below).** A
  Read/Update/Write line's real payload isn't its path, it's the content the op
  touched; clicking the line shows that content in place instead of putting
  anything on a clipboard.

**Click-to-view — file-op lines expand in place.** Every successful
Read/Update/Write one-liner — the main session's (`file_fmt.py`) and a
subagent's/teammate's (`substream_render.py` `render_file`) — is itself an OSC 8
hyperlink (`claude-copy:///<key>/<tool_use_id>/view`, baked into the op's text
by the producer; the renderer needs no geometry). Clicking it expands the op's
full content directly under the line; clicking again collapses it. What
expands: a **Read** shows the text it returned, syntax-highlighted
(`coderender.LANGS` — python/kotlin/java/bash + friends) with a dim
line-number gutter from its real start line — **except a `.md`/`.markdown`
file, which is instead pretty-rendered as markdown** (headings→amber banners,
bold/emphasis, lists, blockquotes, GFM tables, fenced code in its own CODE_BG
panel) by the same `core/mdrender.py` AST renderer the live streaming path uses
(`file_fmt._md_ops` → `mdrender.MarkdownStreamer`, gated by `tools.is_md`), with
no line-number gutter (prose isn't source); a **Write** its written body,
same treatment (markdown when `.md`, else syntax-highlighted code); an
**Update** (Edit/MultiEdit/NotebookEdit) a delta-style diff
— every row line-numbered (old numbers on removals, new on
additions/context; no +/- signs), the code syntax-highlighted with removals on
a soft red panel and additions on a soft green panel (the tint alone carries
the meaning), non-adjacent hunks separated by a dim `⋮`. Mechanism, in three
parts:
- **The stash** (hook time): the producer pre-renders the block into paint ops
  (`file_fmt.view_ops` — the ONE block builder, public API shared by the
  substream renderer, so a subagent's op expands identically)
  and writes them to the state DB kv table under `view:<tool_use_id>`
  (audited as a `view-stash` state_files row), because the payload
  (`tool_response` content, `old_string`/`new_string`) exists ONLY while the
  hook runs and the file on disk drifts afterwards — a click must work forever
  (the kv table parks/restores across resume with the rest of the DB). Diffs
  prefer the result's `structuredPatch` (real file line numbers) and fall back
  to a difflib unified diff over the input strings — which is also what a
  subagent's op uses, since its transcript result carries no patch (its Read
  body falls back to a disk re-read at stream time). Code **highlighting is
  deferred to the renderer** via `lex`/`num` fields on the gut op (plus the
  raw body), because hook producers may run a bare python3 without pygments —
  the renderer re-execs into one that has it (the same reason `code` ops ship
  raw text). Diff runs (contiguous same-signed rows share one op) ride the
  same fields, with the red/green `bg` panel stashed alongside; only a file
  with no known lexer falls back to producer-styled red/green foreground
  rows. A **markdown** Read/Write body is the exception to the deferral: it is
  rendered to already-styled gut ops AT hook time (no `lex`/`num`, `bg=CODE_BG`
  on code panels — exactly what `stream.py`'s `emit_md` emits), because
  `mdrender` degrades gracefully when `wenmode`/`pygments` are absent, just as
  the streaming path already relies on. The view body is deliberately UNCAPPED.
- **The toggle** (click time): the emitted one-liner op carries the id as
  `"v"`; `claude-copy.py`'s `view` verb flips that id in the session's
  `view-open` kv set (audited as a `view` state_files row with `open:
  true/false`), then SIGWINCH-nudges the renderer — whose pid is the
  `renderer-pid` kv row it registers at startup — for an instant reflow
  instead of the 200ms poll tick (the renderer idles in a
  `signal.set_wakeup_fd` + `select` wait precisely so a signal actually
  interrupts it; PEP 475 makes a plain `time.sleep` resume). A click on an id
  with no stash (pre-feature line) is a feedback no-op.
- **The expansion** (paint time): the renderer keeps `view-open` mirrored (one
  kv read per tick), and paints any `v`-tagged op followed by its stashed
  block whenever its id is open. Every toggle is a full reflow repaint (the
  resize path — a terminal can't insert lines mid-scrollback), which
  necessarily parks the viewport at the bottom; the renderer then makes the
  toggle read as **unfolding in place**, like an editor fold. Before flipping
  the set it captures the pane's visible text (`Frontend.get_text` → `kitten
  @ get-text --extent screen`, which returns the *scrolled-to viewport*,
  verified live — not the live screen) and matches it against the pre-toggle
  rendered rows to recover the viewport's top-line offset (`locate_viewport`
  — a GLOBAL text match, sped up by a distinctive-probe-row candidate index;
  an earlier version pinned the search to the screenful above the clicked
  line on the assumption "the clicked line must be visible", but a confident
  global match is stronger evidence than that assumption, and the windowed
  version missed real viewports — score 1/58 in-window vs 58/58 global,
  observed live — silently degrading to line-at-top jumps). After
  repainting, the restore scrolls back by the **top-line anchor rule**: the
  viewport's top line is put back exactly where it was — expand or collapse,
  any block size, the frame simply does not move; the toggle only changes
  what renders below the clicked line inside it. The SAME matcher then
  VERIFIES where the viewport actually landed, retrying the restore once on
  a miss (a DSR-handshake timeout means the scrolls raced kitty's parse; the
  verify read itself serializes the retry) — landed/dsr/retried all recorded
  on the `view-reflow` audit row. (A
  fold-style rule that raised the frame to reveal a tall block was tried and
  rejected — any frame movement at all reads as a jump; the user scrolls
  down themselves if they want the rest of the block.) If the buffer bottom
  is above that frame (a collapse shrank the content), no scroll is issued
  and the pane stays bottom-following. The intermediate viewport-at-bottom
  frame is closed off by LATENCY, not by freezing: the repaint write ends
  with a **DSR probe** (`\033[6n`; the pane tty is switched to
  no-echo/non-canonical at startup so the renderer can read the reply),
  whose answer proves kitty has parsed the frame — only then does the
  restore scroll fire, over a **raw unix-socket rc write**
  (`Frontend.scroll_window_fast`/`get_text`'s `_rc_raw` — the same
  `ESC P @kitty-cmd` DCS bytes the kitten client sends, ~1ms). The gap
  between frame and scroll is thus ~1ms — under one display frame. The
  restore itself is ABSOLUTE: scroll-to-END (`Frontend.scroll_window_end`, a
  deterministic base) then up by the computed amount — never relative to
  wherever the viewport happened to be, because a reflow that clears
  scrollback under a still-SCROLLED viewport (collapsing a block that was
  expanded-and-pinned, mid-parse states while output streams) leaves kitty's
  scroll state undefined, and relative math from there landed at random
  offsets ("hide jumps to random places", observed live; verified pinned
  under concurrent op streaming after the fix). **Follow-mode exception**: if
  the pre-toggle viewport was AT the bottom (anchor within a few rows of
  `total+1-h` — the tolerance absorbs the logical-vs-visual line bias of
  wrapped rows), the restore targets the NEW bottom instead of pinning —
  pinning an at-bottom viewport to an absolute offset silently detaches it
  from the live tail, and the user later finds the pane parked on stale
  content (observed live: an `anchor: 0` click minutes after an at-bottom
  toggle). Why not
  the obvious alternatives: a `kitten @` **subprocess** (~100ms) leaves the
  bottom frame visible — the original flicker; a **DEC 2026 freeze bracket**
  can't work because kitty *buffers* (does not parse) input while frozen, so
  the DSR handshake starves and the scroll races the unparsed frame (landed
  at the buffer start, observed live); *tty-injected* rc scroll commands
  misbehave in kitty 0.45 (any amount scrolls to start — also why replayed
  poisoned output was so violent, below). An unrecoverable anchor degrades to
  the clicked-line-at-top frame — and EVERY null path is audited with its
  reason (`no window` / `no capture` / `empty capture` / `no match`), with
  the capture itself retried 3× under load, because the no-capture path was
  once silent and four `anchor: null` jump-to-end clicks were undiagnosable
  until it wasn't. Three more hard-won rules live here. **The frame must fit
  the scrollback** (`ROW_BUDGET`, default 4800, tune via
  `CLAUDE_MIRROR_SCROLLBACK` to match kitty.conf's `scrollback_lines` minus a
  screenful): every reflow rewrites the whole buffer, so painted rows beyond
  the ceiling don't exist afterwards — a restore targeting them CLAMPS
  (observed live: `landed == total+1-h-5000`, "the expand jumped somewhere
  random"); trimming the oldest ops to the budget loses nothing that could
  ever be scrolled to. **Startup adopts, never toggles**: the persisted
  `view-open` kv set inherited at renderer start (or park/restore reset) is
  state, not a click — letting the kv poll see it as a delta planned a
  toggle restore toward some op's line, and a freshly toggled pane opened
  scrolled deep into history instead of at the bottom (observed live).
  **A WINCH at an unchanged size with no toggle plan repaints nothing**
  (audited as `paint` kind `skip`): a full repaint there isn't just wasted —
  its clear-scrollback clamps a scrolled-up viewport to the bottom with no
  restore (observed live via a bare SIGWINCH probe), so stray or duplicate
  click-nudges must not reach the repaint path. Every toggle leaves a
  `view-reflow` audit row (gid/idx/anchor/cap0/up/applied/dsr/landed/
  retried/follow) and every full reflow a `paint` row (width/rows/ops/open)
  — the pair that cracked three live regressions: the nudge SIGWINCH setting
  `_resized`, whose planning guard then skipped the anchor entirely (guard
  removed); an off-by-one in the restore amount (the repaint's trailing
  newline leaves the cursor on an extra row, so the parked frame top is
  `total+1-h`, verified against `get-text`); and the poisoned-output replay
  (next paragraph). Because a toggle can verify its landing and the pane
  still end up elsewhere MOMENTS later (reported live, zero audit rows in
  between), every toggle also arms an 8-second **drift watch**: the renderer
  re-locates the viewport each poll tick and records every movement as a
  `view-drift` row (from/to offsets + timing) — turning "it jumped and
  nobody saw it" into a stored trajectory. The trajectory data cracked the
  deepest bug in this feature: **twin content**. A session's mirror fills
  with near-identical blocks (many expanded views of the same file's
  regions, repeated command outputs), and a global text match then scores
  EQUALLY at multiple offsets — the anchor picked the wrong copy, the
  restore teleported there, and the verify CONFIRMED the same wrong copy: an
  audit-perfect row for a real user-visible jump ("hide jumps to a random
  location", repeatedly reported while every row looked healthy; the
  matcher's misread signature in drift rows is a physically impossible
  there-and-back bounce, 4808→1270→4880 in 400ms). `locate_viewport` now
  tie-breaks near-best matches toward the caller's prior (`near`): the
  clicked line for the anchor — the user clicked a VISIBLE line, so the true
  viewport is near it, as a prior, not the old windowed-search constraint —
  the restore target for the verify, the previous sample for the drift
  watch. The watch's first ~700ms is a **settle guard** (sampled at
  ~80ms): the position belongs to the toggle's INTENDED anchor — never the
  measured landing, which momentum in flight during the restore can corrupt
  (observed adopted 1176 rows off) — and any displacement >5 rows is
  snapped back by an ABSOLUTE restore (max 2 per toggle, `corrected:
  true`). Restores themselves CONVERGE: up to 3 delta passes until landed
  == target, because "in place" means zero rows off — a 17-row near-miss
  reads as a lost scroll position — and a first miss >400 rows (momentum
  raced the restore) redoes the absolute restore before delta-correcting.
  What it guards against — the last unexplained jump class — is the user's
  own **residual trackpad momentum**: they flick-scroll to reach the line,
  click while the flick's momentum is still alive, the reflow rebuilds the
  buffer and restores the anchor, and the leftover momentum then applies on
  top of the fresh restore ("I clicked hide and it jumped 1000 rows").
  Diagnosed by elimination: kitty's socket scroll measured EXACT (12/12 in a
  sterile window), batch writes while scrolled leave the viewport stable,
  reflow-free displacement bursts appear only with a human at the trackpad,
  and the drift trajectories show classic momentum decay (149→131→121→75
  rows/tick). Deliberate post-click navigation (observed starting at
  +1100ms) is outside the guard window and never fought; corrections are
  absolute because a relative fix against a still-moving target amplifies
  (observed: a chased phantom landed 1476 rows off). Restore retries are CORRECTIVE — scroll by the
  measured landing error rather than re-running the same absolute amount,
  which reproduces the same miss when wrapped rows make kitty's visual-line
  scroll units diverge from the renderer's logical row math (observed live:
  17 short, retried, still 17 short).

**Paint-time neutralization — replayed output must not execute.** The ops
stream carries RAW command output, and the renderer replays it on EVERY
reflow (resize, toggle, pane re-open) — so a stray escape sequence in some
command's output doesn't execute once, like in a normal terminal; it
re-executes forever. This was found live: a wire-capture experiment tee'd a
raw `ESC P @kitty-cmd scroll-window` DCS into the mirror, and every repaint
re-ran it, scrolling the pane to the very top (masquerading as a scroll-
restore bug). `render.neutralize()` — applied by the renderer to every op's
text — strips ALL control sequences except SGR styling (the mirror's own,
plus legitimate colours in tailed output) and OSC 8 hyperlinks (the copy/
view links). Neutralizing at paint time (not at ingestion) means already-
recorded poisoned history is defused too, with no data scrubbing. Why not the alternatives: *emit the block at the
  bottom* (append-only friendly, no repaint) reads as a teleport away from
  the line you clicked; *renderer-side mouse reporting* was already rejected
  for the copy links; *hiding via mutable op rows* breaks the append-only
  `ops` contract that resume/park/restore depends on. The OSC 8 sequences the
  producers now bake into `line`/`gut` text required teaching `render._ANSI`
  to match whole OSC sequences (before the 2-char branch that ate just
  `\x1b]`), so hyperlinks are zero-width to `wrap_gutter`/`dwidth` and vanish
  from `strip_ansi` copies.
