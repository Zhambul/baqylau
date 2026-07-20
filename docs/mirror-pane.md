# Command mirror pane (vertical split)

A single vertical split — the right **25%** of the tab (configurable via
`CLAUDE_MIRROR_BIAS`; resizable / toggleable on the fly, see below) — shows each
command Claude runs as a block:

```
 ▶ foreground          (blue chip — see kinds below)
slug="$(pwd -P …)"     ← bash, syntax-highlighted,
for f in *.output; do    real line breaks preserved,
  …                       word-wrapped to pane width
done
─────────────────────  (rule, pane-width)
output
─────────────────────
 ■ finished · 1.2s     (magenta chip)
```

Each block is **bracketed by dividers** (a full-width rule before the header and
after the finish chip) and opens with a **coloured chip naming its kind**, so
foreground, background, and monitor are unmistakable (the generic `command`
label is gone):

The header chip, the output gutter, and the finish chip of a block **all share
one colour**, so you can match every part of a stream — and tell parallel streams
apart at a glance:

| Chip | Kind | Block colour |
|------|------|--------------|
| **▶ foreground** | a normal blocking command — full output, `■ finished` | by status: slate ok / red failed / orange interrupted |
| **▷ background** | a `run_in_background` command — output streams live, `■ background finished` | the job's slot from a **5-colour palette** |
| **◉ monitor** | a Monitor tool stream — events stream live, `■ monitor ended` | the monitor's slot from a **separate 5-colour palette** |
| **▶ \<agent-type\>** | a **subagent** (Task/Agent tool) — its prompt, messages, commands, file ops & result stream in, framed by `▶ <type> · <description>` … `■ <type> ended` | the subagent's slot from a **third 5-colour palette** |
| **▶ \<name\> · teammate** | an **agent-team teammate** — same streaming as a subagent, plus its messages (`✉ from` / `✉ to`), framed by `▶ <name> · teammate · <desc>` … `■ <name> ended` | a **fourth, lighter (pastel) palette** so a teammate reads apart from a subagent |
| **codex ▶ \<label\>** | **any codex run** — a companion job (`Review` / `Adversarial Review` / `Task` / `Stop Gate Review`) or a raw `codex`/`codex exec` (`cli`) — its commands (`▶ cmd`), reasoning (`⋯ reasoning`), messages (`✎ message`), prompt (`⇢ prompt`) & review/result (`⇠ review` / `⇠ result`) stream in, framed by `codex ▶ <label>` … `■ codex <label> ended` | a **fifth palette** (jade · sky · orchid · gold) so a codex stream never reads as one of our own agents |
| **✚ / ✓ task #N** | an agent-team **shared-task-list** event — created (`✚`, amber) / completed (`✓`, green) | a fixed one-line accent (not a stream) |
| **⚠ audit** | the **audit warning light** (`core/errwatch.py`) — a one-liner per swallowed exception the audit `errors` table recorded: `⚠ audit: <script>: <exception summary>` (the traceback's last line, char-capped — except a deliberate no-exception degrade row, whose stored traceback is format_exc's `NoneType: None` sentinel: there the `func` string is shown instead, since it IS the message — `spawn nope.py (script missing)`), or — past `FLOOD_N` (3) new rows in one 5s poll — a single collapsed `⚠ audit: N new errors (bin/claude-audit.py errors <sid>)` pointing at the CLI. Emitted by the **scorebar's** slow audit poll (the one long-lived per-session process that already emits mirror events — the renderer's drain loop was rejected: it repaints on SIGWINCH/backfill with no once-only cadence, and per-hook checks would open the global audit DB from every short-lived process); each row is emitted **exactly once** — the last-seen rowid checkpoint lives in the state DB kv (`errseen`), parked/restored with the session, and the checkpoint advances BEFORE the emit (at-most-once beats a re-emitting loop for an ambient warning). The same poll feeds the scorebar's `⚠ N` chip ([scoreboard.md](scoreboard.md)) | a fixed AMBER accent (a degradation warning, not a failure) |

Output lines carry a **colour-coded `│ ` gutter** — a per-**stream** tag — so
several jobs running in parallel stay distinguishable when they interleave in the
shared log. Foreground uses one status colour; background, monitor, and subagents
each draw from their own 5-colour palette (so up to 5 concurrent jobs of each kind
— 15 streams plus foreground — get distinct colours; beyond 5 of a kind, colours
reuse). When a background/monitor job launches, its hook **claims a free palette
slot** (a row in the state DB's `live` table, claimed in one transaction,
liveness-checked by pid, released when the streamer exits), colours the header
chip with it, and hands the slot to the streamer for
the gutter + finish — so the whole block is one colour and concurrent jobs of the
same kind never collide. A subagent claims its slot by **`agent_id`** (so its
`SubagentStart` header, its streamer's body, and its footer all share one colour).
A subagent's nested background/monitor job carries **two** gutters — the subagent's
colour outside, the job's own palette colour inside. The colours across all five
palettes are chosen to be well-separated, so no two look alike. (An agent-team
**teammate** reuses the subagent slot machinery — same `agent_id`-keyed slot and
`sub.*` markers, so it keeps the tab blue while it runs — but draws its colour from
the lighter teammate palette instead.)

The **finish chip is the same colour as that stream's gutter**, so you can tell
which stream just finished even when several are interleaved. For foreground
that colour encodes status — so a **failed** command turns the header, gutter,
**and** finish chip **red** (`■ failed (exit 5)`, error output as the body), and
an interrupted one orange (`■ interrupted`). For background/monitor the finish
chip uses the job's palette-slot colour.

A background command is **printed once**: the `▷ background` chip, the command,
then its live output streams directly underneath (orange `│ ` gutter).

The command is **pretty-printed**: its real line structure is preserved (not
collapsed to one line), it's **syntax-highlighted** (via `pygments` — keywords
magenta, strings green, `$variables` yellow, numbers orange, comments grey), and
long lines are **word-wrapped to the pane's current width** with a hanging
indent, so even large commands stay readable in the narrow split.

**The command name itself is highlighted (blue), not just shell builtins.**
pygments' `BashLexer` only tags builtins it knows (`echo`/`cd`/… → cyan); every
external command (`python3`, `kitten`, `sed`, `git`, …) is plain text. So the
formatter runs a command-position pass — the first word at the start, after a
separator (`;` `|` `&&` `$(` newline …), or after a command-introducing keyword
(`do`/`then`/`if`/`while`/…) is retagged blue — leaving builtins cyan and
arguments their default colour.

**Embedded Python is highlighted as Python** — both heredocs fed to python
(`python3 … <<PY … PY`) and `python -c '…'` arguments. The command is split into
bash / python segments and each is lexed with its own lexer (so `import`/`def`
read as keywords, `print` as a builtin, etc.), then merged into one wrapped,
coloured block.

**Dense one-liners are pretty-printed before highlighting.** A compressed command
is reflowed into readable multi-line form — **bash** breaks after top-level `&&` /
`||` / `|` and turns `;` into a line break; **embedded Python** (`-c` args + heredoc
bodies) is reformatted via `ast`, so `python3 -c "import os;x=1;print(x)"` becomes
three real lines. It's width-INDEPENDENT (real newlines the renderer still wraps), so
it runs **once at op creation** (`ops.code` → `render.format_code`), not
in the paint loop. Best-effort and conservative — operators inside quotes (`git commit
-m "a && b"`), background `&`, redirections, bash heredocs, `case` bodies, and Python
that carries comments are all left exactly as written; anything it can't confidently
reformat passes through untouched. Set **`CLAUDE_MIRROR_FORMAT=0`** to show commands
verbatim.

**Section banners in output are emphasised.** Lines that scripts (and Claude Code
itself) print to delimit sections — `=== title ===`, `--- title ---`,
`### title ###` — are rendered **bold amber** so section boundaries pop out of a
wall of output. Detection (`render.emphasize`) runs on each line's *visible*
text and is deliberately conservative: the `=` family needs a run of `==`+ followed
by a space or end-of-line (so `x == y` and valgrind's `==123==` are left alone),
and the `-`/`#`/`*`/`~` forms must be **bracketed** on both ends (so a diff header
`--- a/file` and a bare `-----` rule stay plain). It's applied at every real
command-output site — foreground, background/monitor tail, and subagent output —
but *not* to a subagent's messages/prompts (which share the gutter helper).

**Markdown files are pretty-rendered.** When a command streams a markdown file's
raw contents — an allowlisted plain-text reader (`cat` / `head` / `tail`) with a
`.md`/`.markdown` argument, or a bare `< file.md` — the body is rendered as styled
ANSI instead of raw `#`/`**`/`` ` `` characters: headings become bold-amber
banners, `**bold**`/`*italic*`/`~~strike~~` become SGR, `` `code` `` is coloured,
fenced blocks are **syntax-highlighted by language** (pygments — `` ```java ``,
`` ```js ``, … not just bash/python) and rendered as a **full-width background
panel** (each code block is emitted as its own gut op with a `bg`, which
`wrap_gutter` fills to the pane edge at paint time so the panel reflows on
resize), bullets/ordered lists nest (a fenced block **inside** a list item keeps its own
lines — the streamer's block cut is indent-aware, so an indented continuation is
never orphaned), **GFM task-list** items render `☐`/`☑`, blockquotes get
a rail, **GFM tables** render row-per-logical-line with a dim `│` rail and a
bold header (no column alignment — that's width-dependent, so wrap_gutter still
reflows a wide row), **footnotes** (`[^id]` references + `[^id]: …` definitions,
handled at the text level since the streaming preset ships no footnote plugin)
are dimmed, blocks are blank-line separated, and `[links](url)` become OSC-8
hyperlinks. Two wiki conventions the CommonMark parser doesn't know are handled
too: **YAML frontmatter** (`--- … ---`) renders as a dim key/value header (not a
stray rule), and Obsidian **`[[wikilinks]]`** / `[[target|alias]]` are coloured
like links (brackets stripped). Detection (`tools.md_source`) is deliberately narrow —
**pipes, redirects, and chains disqualify** (the bytes would be filtered, not the
document), and `bat`/`glow`/`mdcat`/`less` are excluded (they already style their
own output — re-rendering would double-format). Set **`CLAUDE_MIRROR_MD=0`** to
stream markdown verbatim.

It's an **AST-driven** renderer (`core/mdrender.py`): the `wenmode` CommonMark
parser produces an mdast tree, and an `OpsRenderer(BaseRenderer)` subclass turns
each node into styled text reusing the same primitives as everything else
(`render.BANNER`, `render.COL`, `render.hyperlink`). Output carries only
zero-width SGR + **logical** newlines, so it stays width-INDEPENDENT and the
renderer reflows it on resize like any other gut op — it runs in the tailer
(`claude-stream.py`), block by block via `mdrender.MarkdownStreamer`, which holds
an incomplete trailing block (fence-aware) so a partially-arrived file never
splits mid-construct. *Why not `glow` (or `rich`, `mdcat`, …)?* Every terminal
markdown **renderer** hard-wraps its output to a fixed width — which the mirror's
reflow-on-SIGWINCH model can't consume (content wouldn't re-wrap when the pane
resizes). A **parser** we drive keeps the producer/renderer split intact. *Why
not the old `render.markdown()` regex subset?* It's line-oriented — no real
nesting, ordered lists, fenced blocks, or blockquotes. `mdrender` supersedes it
(and is the fallback if `wenmode` is absent). Tables render as lightly-styled
rows without column alignment (alignment is width-dependent — out of scope).
Block spacing, frontmatter, wikilinks, and code highlighting live in the
`OpsRenderer` handlers + `MarkdownStreamer` in `core/mdrender.py`.

**JSON and YAML files are colourised the same way.** `cat file.json`
(`tools.json_source`, gated by `CLAUDE_MIRROR_JSON`; excludes `jq`/`bat` which
self-colour, and `head`/`tail` since JSON needs the whole file) is re-indented
(`json.dumps` indent 2) and syntax-highlighted (keys blue, strings green, numbers
orange, `true`/`false`/`null` magenta). **JSON Lines / NDJSON** (`.jsonl`/`.ndjson`,
one JSON value per line) is rendered the same way — every non-blank line pretty-
printed and blank-line separated; a single non-JSON line taints the whole stream
back to verbatim (never a misleading partial view). `cat`/`head`/`tail` of a `.yml`/`.yaml`
(`tools.yaml_source`, `CLAUDE_MIRROR_YAML`) is syntax-highlighted **in place** —
NOT reparsed, because a YAML round-trip drops comments and reorders keys, which is
destructive for hand-written config; it's coloured raw via pygments' `YamlLexer`
(all plain scalars read green — YAML scalars are genuinely ambiguous). Neither
gets a background panel (that's reserved for markdown fenced code) — just colour on
the normal gutter. Both render **once at completion** (a partial JSON document is
invalid; YAML block scalars make partial colouring unreliable), so
`core/jsonrender.JsonStreamer` / `core/yamlrender.YamlStreamer` buffer the whole
stream and fall back to the raw text verbatim if it isn't valid (truncated JSON,
JSON Lines, a plain log; or pygments absent). The buffer-then-render-at-close
skeleton (feed buffers, close renders, and the single-sourced verbatim fallback
`emphasize(unescape(raw))`) is `render.BufferedStreamer`, shared with
`CodeStreamer` — subclasses supply only `render()`; only markdown renders
incrementally. Their token→colour ladders aren't forked either: both `_pick`s
delegate to `render.pick` via small `pre`/`post` override ladders (json: keys
blue, other names/comments default; yaml: same plus a *post*-ladder
`Token.Literal`→string for plain scalars — after the core rows so real
strings/numbers keep their own colours; order is load-bearing, the checks are
`startswith`), so a palette change propagates from one place — pinned
byte-identical by the `tests/golden/render-*.ansi` goldens. No new dependency:
stdlib `json` + the same optional pygments for colour.

**Source files are syntax-highlighted too.** `cat`/`head`/`tail` of a `.py`,
`.java`, `.kt`/`.kts`, or `.sh`/`.bash`/`.zsh` (`tools.code_source`, gated by
`CLAUDE_MIRROR_CODE`) is coloured in place via the matching pygments lexer,
reusing `render.pick` (keywords magenta, function names blue, strings green,
numbers orange, comments grey) — no reformat, no panel. `sed`/`grep`/`egrep`/`fgrep`
of a source file qualify too (`sed -n '80,130p' x.py`, `grep -n def app.py`) — but
because these put a SCRIPT/PATTERN arg *before* the file, the lexer is read from the
**trailing** arg only, so a pattern like `grep 'foo.py' x.txt` can't masquerade as
python and a recursive `grep -r pat src/` (a directory, no extension) correctly opts
out. One generic renderer, `core/coderender.CodeStreamer(lexer)`; adding a language
is one line in `coderender.LANGS` (extension → lexer name). `code_source` returns the
lexer name (not just a bool) so the tailer knows which lexer to load. Detection runs
on the command's **effective read** (`tools._effective`): a trailing **truncation
pipe** (`grep … x.py | head -40`, `| tail`) is stripped — it only shortens the same
output, so it still colours — a **line-continued** pipeline (a line ending in
`|`/`&&`/`||`/`\` that spills onto the next line, e.g. `grep … x.py |↵head`) is
rejoined before splitting so it isn't mis-cut at the newline, and a
**multi-statement** command (`;`/`&&`/newline-separated: `grep … a.py↵echo …↵sed …
b.py`) keys off its **last statement's** file
(earlier statements/banners inherit that lexer — imperfect but chosen). What still
disqualifies: a **transform pipe** (`| awk`, `| grep …` — the bytes are derived, not
the file), `python foo.py`, an output redirect, and command substitution.

**One registry, one skeleton.** The four filename-keyed detectors share a single
implementation: `tools.RENDER_KINDS` is a priority-ordered table of `RenderKind`
entries, each declaring its render-kind name, `CLAUDE_MIRROR_*` env gate, reader
allowlist, trailing-arg readers (code's sed/grep rule), word-matcher (extension
set, or the LANGS lexer lookup), and the `module:Class` of its core content
streamer; `tools._detect_source` is the one skeleton (`_effective` → tokenise →
plumbing guard → `< file` redirect → reader allowlist) they all run through.
`md_source`/`json_source`/`yaml_source`/`code_source` survive as thin wrappers.
`claude-stream.py::_detect_render` iterates the registry and instantiates the
winning entry's declared streamer — so **adding a render mode is one registry
entry** (plus its core streamer module), not a fifth copy of the skeleton and a
lockstep edit to the tailer's mode switch (the pre-registry design drifted
exactly that way).

**Where detection runs: in the tailer, not the launch hook.** All four
filename-keyed modes (md/JSON/YAML/code) are decided by `claude-stream.py`
itself (`_detect_render`), from the ORIGINAL pre-tee-wrap command every launch
site passes via `CLAUDE_STREAM_CMD`. The env contract has ONE builder,
`hookkit.stream_env` — `claude-cmd-pre.py` (main fg), `claude-cmd-fmt.py` (bg),
and `claude-substream.py` (a subagent's live fg, `spawn_fg_tailer` — the
transcript's `tool_use` command, which is the model-authored original;
`updatedInput` rewrites the *executed* input, not the assistant message) all go
through it. *Why not decide at the launch site and pass the decision (the
original design)?* Detection is a pure function of the command — presentation
logic, which belongs to the presenting process — and with per-launcher env
assembly the subagent fg launch site silently missed the render keys, so a
subagent's `cat Foo.kt` streamed uncoloured while the main session's coloured
(the general failure mode: any launcher-side feature must be re-plumbed once
per launch site, and the forgotten one fails silently). Rendering stays
**fg-only** inside the tailer (bg/monitor output is a job log, not a file's
contents), so bg launchers passing the command changes nothing today.

**Fenced output is markdown — the general mixed-content path.** All of the above
key off the *filename*, so they miss a command that *prints* markdown to stdout
(an agent, a report generator, a `write-then-cat` one-liner). The fallback keys
off the *content* instead: when no filename mode was picked, a fg command's output
is sniffed for a **fenced code block** (` ```lang `) — the one markdown token that's
unambiguous and essentially never appears by accident in logs or diffs (unlike a
bare `#`/`*`, which is why general markdown-sniffing is a false-positive swamp).
If the **first data-bearing read** contains a fence, the whole stream renders as
markdown — prose plus each fence highlighted by its language (json/python/yaml/…);
otherwise it streams verbatim, exactly as before. This is *the* answer to mixed
content ("partly prose, partly JSON, partly Python"): a fence is the boundary
declaration that makes the regions unambiguous — auto-segmenting an *undeclared*
stream is not attempted (there are no boundaries to honour, only guesses). The
decision is made on that first read **only** — never buffered across polls — so
live line-by-line streaming is untouched (a plain `make build` shows each line as
it lands). A fence that first appears in a *later* chunk than the first is missed
by design (that liveness guarantee is worth more than catching the rare late
fence); a `cat` of a file always delivers its fence in the first read. Gated
default-on by `CLAUDE_MIRROR_MD_SNIFF`; filename detection always wins when present.


Copy links (` ⧉cmd ⧉out`), click-to-view expansion of file-op lines, and
paint-time neutralization are documented in [click-to-view.md](click-to-view.md).
The streaming machinery behind foreground/background/monitor blocks is in
[streaming.md](streaming.md); subagent/teammate streams in [subagents.md](subagents.md);
codex streams in [codex.md](codex.md); the scoreboard window in [scoreboard.md](scoreboard.md).

## The renderer, file ops, and the pane lifecycle

- **`claude-mirror.py LOG`** — the renderer — runs inside the pane (launched
  directly by `claude-split.py`, replacing the old `tail -F`) on that session's
  log KEY (the key is the historical `/tmp/claude-mirror-<sid>.log` path, from
  which the state-DB path derives — no log file exists anymore). At startup it
  re-execs itself into a `pygments`-capable interpreter if the launching one
  lacks it (see *Pretty-print needs pygments* below). The
  renderer polls the structured paint-op rows (the state DB's `ops` table, see
  *Reflow* below), paints each op at the pane's **current** width, and re-renders
  everything on resize (`SIGWINCH`) so content **reflows**. It reads the table
  from id 0 and **never deletes** — so toggling the pane off/on re-shows the whole
  session history (the DB is created fresh only for a genuinely new session and
  parked as `*.keep` at SessionEnd — a `--resume`/`--continue` restores it, so the
  mirror replays the prior session; see `claude-split.py` below), and while off
  there is no process at all. It keeps at most `MAX_OPS` (8000) ops in memory so a
  long session can't grow unbounded. The click-to-view expansion cache
(`_VIEW_OPS` — a view id → its pre-rendered body, highlighted content / ±
diff) is likewise bounded: an entry is dropped the moment its view **collapses**
(the `view-open` poll), not just on a DB-inode reset, so a session that expanded
many large Reads no longer pins all their content for its life (the durable
`view:<id>` kv re-feeds a re-expand). One process — no file-switching,
  byte-offsets, `lsof`, or orphaned tails. An **idle** tick (0.2s) costs one
  `ops` SELECT and nothing else: the empty-path `MAX(id)` recreated-DB probe
  is skipped (`ops_after(..., check_reset=False)` — the per-iteration inode
  stat already catches every recreation, since a park/restore or fresh
  session always swaps the file's inode), and the `view-open` kv read is
  nudge-gated ([click-to-view.md](click-to-view.md) › *The expansion*).
- **`claude-file-fmt.py`** (a `PostToolUse` hook for `Read`/`Edit`/`Write`/
  `MultiEdit`/`NotebookEdit`) logs file operations as compact one-liners showing
  just the verb + basename — `Read(README.md)`, `Update(README.md)`,
  `Write(new.py)` — interleaved with the command blocks so the pane reads as a
  running log of what Claude did. The name is **location-aware**
  (`streamfmt.file_display`, 2026-07-16 — a bare basename hid WHERE an op
  landed; scratchpad, wiki, and repo files all looked alike): a file under the
  session cwd stays a bare basename (the quiet default), a file in a **session
  scratchpad** (`/tmp/claude-<uid>/…/scratchpad/…`, matched by path shape —
  no env var names it) shows a `✎` icon — `Write(✎ repro.py)` — and any other
  out-of-project file shows a dim abbreviated directory prefix,
  `Update(~/wiki/…/zenith/concepts/x.md)` (home → `~`, long chains
  middle-elided to first + last two components). The audit decision carries a
  matching `[scratch]`/`[out]` tag. The subagent streamer's file ops
  (`substream_render.render_file`) go through the same builder, baselined on
  the tailer's inherited cwd = the session directory. Verbs mirror Claude Code's own UI (Edit/
  MultiEdit → **Update**, colour-coded: read blue, update yellow, write green);
  formatting lives in **`claude-file-fmt.py`**. A mutation also shows its
  added/removed line counts — green `+N` / red `-M`, e.g. `Update(task-manager.md)
  +18 -1` — from a real line-level diff of the tool input (`old_string` vs
  `new_string`, summed over a MultiEdit; the whole body for a Write), the same
  additions/removals Claude Code reports. Reads and failures show none. The shared
  counter is `tools.diff_counts()`, used by both this path and the subagent
  streamer. A mutation also shows the **line range(s) it touched** — a dim
  `start-end` after the counts, e.g. `Update(README.md) +18 -1 445-462`, comma-joined
  for a multi-hunk MultiEdit (capped at 3, `+k` for the rest) — read from the result's
  `structuredPatch` hunks via `tools.edit_range()`; a brand-new Write shows no
  range (its `+N` already says the size). A **Read** instead shows how much of the file it took: a bare
  `Read(name)` means the **whole file**, while a dim `start-end/total` (e.g.
  `Read(big.py) 1-2000/5000`) flags a **partial** read — either an explicit
  `offset`/`limit` slice or a bare read that hit Claude Code's **2000-line cap** on a
  larger file. The extent comes from the result's `startLine`/`numLines`/`totalLines`
  via `tools.read_extent()`.
- **`claude-split.py open|close|toggle|grow|shrink|reset|setpct`** manages the pane,
  **per Claude session**. Everything is keyed by `session_id` so PARALLEL sessions
  never collide: each mirror pane carries `var:claude_mirror=<sid>`, each Claude pane
  carries `var:claude_session=<sid>`, and each session's content is its own state
  DB at `/tmp/claude-mirror-<sid>.log.state.db` (the `.log` path is the KEY the
  scripts pass around; no log file exists anymore). The key format itself —
  sanitizing a session id, the cwd-slug fallback, deriving/parsing the path — is
  owned by ONE stdlib-only module, **`core/paths.py`**; it used to be encoded in
  four independently-maintained regexes (ops/audit/split/tab-status) that had
  already drifted (audit captured the full sid where `team_dir` captured 8 hex
  chars), and any two of them disagreeing silently breaks the audit join or the
  fallback DB paths. `open` (SessionStart) reads
  the `session_id` from its hook payload, sets up that session's state DB (see
  *history across resume* below), tags the Claude pane, switches the
  tab to the `splits` layout, and launches the split at `${CLAUDE_MIRROR_BIAS:-25}`
  percent, plus the **scoreboard bar** — a ~4-row `claude-scorebar.py` window hsplit
  under the mirror (`--next-to` the mirror window, then resized to exactly
  `BAR_ROWS` since kitty's `--bias` is approximate; excluded from the width math,
  which would otherwise double-count the column it shares with the mirror). It also
  fires **`claude-codex-launch.py`** (see [codex.md](codex.md)),
  which detaches this session's codex watcher and returns immediately. `close`
  (SessionEnd) closes that session's mirror + bar and **parks** its state DB
  (`<log>.state.db*` — ops history, scoreboard, coordination state) by MOVING it
  into the **durable** park dir `~/.claude/baqylau-mirror-history/` (`core/paths.py`
  `HISTORY_DIR`/`parked_db`), and sweeps stale debris (parked/orphaned session
  files older than 7
  days, pre-migration leftovers). The `close` path runs **even when the frontend
  is unusable** (`FE.usable()` false — no kitty / no `kitten` binary, e.g. a
  headless CI host): parking the state DB is core session-lifecycle, not pane
  work (a `--resume` replays that history; the codex watcher / scorebar poll for
  the DB path vanishing as their exit signal), and the pane-close calls inside
  self-no-op. Only `open` (which has nothing to set up without a terminal) and the
  keybindings stay gated behind `FE.usable()`.

  **History across resume.** `--resume`/`--continue` keeps the same `session_id`,
  so `open` decides the DB's fate purely from **file existence**, never from the
  payload's `source` field (which would miss resume-after-crash):
  - a durable park (`HISTORY_DIR/<sid>.state.db*`) exists → a prior SessionEnd
    parked this sid; move the DB back to the live `/tmp` path and the renderer
    replays the entire prior session, scoreboard included (**restore-history**).
    A legacy in-place `<db>.keep` (parked by an older build) is still honoured, so
    a resume across the upgrade restores too.
  - the DB itself exists → SessionStart fired mid-session (`compact`) or the prior
    run crashed without a SessionEnd; leave it alone (**reuse-live-db**).
    (Truncating unconditionally here — the pre-DB design — wiped the live mirror
    on auto-compact.)
  - neither → a genuinely new session: nothing to do, the first writer creates the
    DB (**fresh-db**). The sid-less cwd-slug fallback removes any leftover DB
    instead — it may belong to another session.
  - the park exists but the MAIN file can't be moved back (ENOSPC, EPERM) →
    **restore-failed (park kept)**: the failure is audited (`errors` row, func
    `decide_log_fate (restore move main)`), the park stays intact for a later
    resume, and `ensure_db` starts the session fresh. A restore also removes any
    stale LIVE `-wal`/`-shm` that has no parked counterpart — SQLite would
    replay a foreign WAL into the freshly restored main file.

  Why *park-and-move* rather than simply not deleting: the DB **path** vanishing
  is the exit signal the codex watcher and the bar's renderer poll for — leaving
  it in place at SessionEnd would leak both. Why the park lives under `~/.claude`
  rather than next to the live DB in `/tmp` (the original `*.keep`-in-place design):
  **macOS wipes `/tmp` on reboot**, so an in-place park was silently dropped when
  the machine restarted between SessionEnd and a `--resume`, and the resume started
  `fresh-db` — an empty mirror + zeroed scoreboard. The live DB stays in `/tmp` (its
  existence is the session-alive signal, and stale runtime state *should* clear on
  reboot); only the parked history is durable. Each fate is audited as a
  `state_files` row (action = the fate, content = the payload's `source`), so a
  resume that came back empty is a `fresh-db` row on a `source=resume` start — a
  canned `anomalies` query.

  **The park itself can fail — it must never LIE about it.** `park_db` used to
  do three independent `shutil.move`s (db, `-wal`, `-shm`) with every `OSError`
  swallowed bare, then return `keep-history` unconditionally. Two failure
  shapes fell out: (a) the MAIN move failing (ENOSPC, EPERM, a blocked
  destination) left the live path in place while everything reported success —
  `parked()` never fired, so the scorebar (a `while True` with no backstop) and
  the codex watcher polled forever as orphans, and a same-sid resume took
  `reuse-live-db` instead of restoring; (b) a partial failure TORE the WAL —
  a parked main file without its uncheckpointed frames, or a stale sidecar left
  at the live path for the next session to replay. Current `park_db`
  (core/hostpane.py): first a best-effort `wal_checkpoint(TRUNCATE)` through a
  short-lived connection (`CHECKPOINT_TIMEOUT_S`) — connecting is fine here,
  the DB still exists and this IS the park path; a busy-failed checkpoint
  (writers can still be live at SessionEnd) degrades gracefully because the
  `-wal` is then moved alongside, frames intact — so the main-file move is the
  only one that matters. Then the MAIN file moves FIRST: if it fails, the park
  stops before touching the sidecars (live DB stays whole, not torn), audits
  (`errors` func `park_db (main move — DB kept live)`), and returns the
  distinct fate **`park-failed (kept live)`**, which SessionEnd audits as a
  `state_files` row — the orphaned pollers are now at least VISIBLE (the
  `errors` row also lights the errwatch `⚠` chip); a poller backstop for that
  state is deliberately not added. A sidecar-only move failure still returns
  `keep-history` (the checkpoint already folded the frames into the parked main
  file) but is audited and the stale live sidecar removed so the next restore
  can't replay it.

  **Resume (and backgrounding) can FORK the sid (adoption).** "keeps the same
  `session_id`" above has observed exceptions (both 2026-07-11). On a
  daemon-origin resume from the agents view, Claude Code fired the
  `source=resume` SessionStart with the **old** sid
  — so the mirror, scorebar, state DB and pane tags all keyed to it — while
  **every subsequent hook event and OTEL datapoint carried a new sid** that never
  got a SessionStart of its own (its first `InstructionsLoaded` even arrived a
  second *before* the old-sid SessionStart). Result: the old sid received nothing
  but ConfigChange, the new sid accrued 1,100+ events into a state DB nothing
  rendered, the scorebar cost froze at the pre-resume total, and the tab never
  repainted. **Backgrounding a session forks the sid the same way** (observed
  12e32815 → 0ed3231c: the conversation continues under the background-job id
  with no SessionStart at all), so the recovery is trigger-agnostic. The fix is
  **adoption** (`plugins/claude_code/adopt.py`, run by `dispatch.py` before
  anything else touches the payload): every SessionStart registers its sid in
  the global tab DB (`sids` table — "this sid really started") and every
  **hosted** start (split.py `cmd_open`, once the pane + state DB really exist —
  a skipped daemon/headless start must never shadow the real predecessor)
  additionally leaves a **take-once note** keyed by cwd (`adopt_pending`). An
  event whose sid has
  **no state DB, no parked `*.keep`, and no prior SessionStart** consumes a
  matching note and adopts the predecessor: its state DB (+`-wal`/`-shm`) moves
  to the new sid's path via **hardlink-then-atomic-symlink-swap** — `os.link`
  gives the inode the new name (so the running renderer/scorebar/OTLP-receiver
  connections keep working) *while the old name stays resolvable*, then a
  symlink created under a tmp name (`.adopt-tmp`, removed and audited on
  failure) is `os.rename`d over the old path, so **the old path exists at
  every instant**. Why not the earlier `os.replace`-then-`os.symlink` pair:
  between those two syscalls the old path was ABSENT — an old-key poller
  sampling `parked()` (a bare `exists`) in that window read it as SessionEnd
  and exited permanently (frozen scoreboard for the continuing session), and a
  straggler old-key writer's `_connect` created a fresh orphan DB there
  (writes lost from the adopted DB, the symlink then failing EEXIST). If the
  hardlink itself fails while the original still exists, the symlink swap is
  skipped — renaming over it would destroy the un-adopted data — and the
  old-path miss is audited as before. The result leaves **symlinks
  at the old paths** (old-key pollers and any old-path reopen resolve to the
  adopted DB; SQLite derives `-wal`/`-shm` names from the path a connection was
  opened with, hence all three), then retags `claude_session`/`claude_mirror`/
  `claude_scorebar` to the new sid and writes the `sessions` audit row the fork
  never got. Guards, each closing a mis-adoption path: an existing DB or `*.keep`
  = a known session (also the one-`stat` fast path every normal event takes); a
  sid with its **own** start (headless `claude -p`, agents-view agent
  sessions — both skip the pane lifecycle so they have no DB either) is a genuine
  new session, never a fork; the note only captures while the predecessor's DB is
  still **live**; and the take-once delete makes concurrent hook processes elect
  exactly one adopter. That "own start" mark is set on **both** `SessionStart`
  **and the earlier-firing `InstructionsLoaded`** — because `InstructionsLoaded`
  precedes `SessionStart` for a real new session but is *never* emitted by a fork
  (a resumed/backgrounded continuation already has its instructions). Marking only
  on SessionStart left a TOCTOU: a new session's pre-SessionStart
  `InstructionsLoaded` reached the adopter with `sid_seen` still false and, if a
  *concurrent* independent session shared the cwd, consumed **its** note and stole
  its panes (live 2026-07-13: `507fc4c8`'s InstructionsLoaded adopted the unrelated
  live `db081e65` — toggling 507's mirror then toggled db081e65's, because 507's
  `claude_mirror` tag had been moved to db081e65's tab). Flagged by the canned
  anomaly *"adopted a predecessor despite having its OWN SessionStart
  (mis-adoption — pane theft)"*. Audited as a `state_files` `adopt` row (`from`/`moved`/
  `retagged`) plus a `hook_events` decision row (handler `claude-hook.py`,
  `adopt: resume forked sid — adopted <old>`); the un-adopted regression is the
  canned anomaly *"hook traffic under a sid with no sessions row"*. The same
  forked events also arrive with the **scrubbed daemon env** (no
  `KITTY_WINDOW_ID`/`KITTY_LISTEN_ON`), which used to skip every tab paint
  ("not inside kitty") — `tabstatus` now resolves like `split.py` does
  (`frontends.get(resolve=True)`: ppid walk / lone-socket, only when the env var
  is absent) and falls back to the `claude_session=<sid>`-tagged window for the
  window id (`_ensure_win`, run before the dispatch handlers because d_stop/
  d_notify and the watchers consult `WIN` themselves); the detached bg-watch/
  interrupt-watch children get the resolved `KITTY_WINDOW_ID`/`KITTY_LISTEN_ON`
  stamped into their env at spawn, since re-parented processes can't repeat the
  ppid walk.

  **Anchoring, and daemon-origin SessionStarts (the agents view).** `open` must
  decide *where* the pane belongs. The normal interactive case is trivial — the
  hook process runs with the Claude pane's env, so `KITTY_WINDOW_ID` **is** the
  host pane. But Claude Code's **agents view** (left arrow from the chat) spawns
  a `claude daemon run --origin transient` process whose hook children carry a
  **scrubbed env** — no `KITTY_WINDOW_ID`, no `KITTY_LISTEN_ON` (the socket still
  resolves via the ppid walk / lone-socket fallback, so the pane calls *work*,
  they just don't know where they are). Two distinct SessionStarts arrive that
  way: the view's own **agent sessions** (`source=startup`, payload carries
  `agent_type` — sessions with **no terminal pane anywhere**), and a
  **`source=resume` for the real chat** when the user re-enters it. The old
  fallback — "no `KITTY_WINDOW_ID` → use the focused tab" (designed for
  keybindings) — made the first kind **hijack whatever tab the user was looking
  at**: `close_stale_mirrors` swept the focused session's mirror as "stale"
  (different sid, same tab) and vsplit an **empty mirror keyed to a session that
  lives nowhere**; the resume 3s later then shuffled panes again wherever focus
  happened to be. So `open` now resolves an **anchor** in order:
  `KITTY_WINDOW_ID` (own pane) → the window already tagged
  `var:claude_session=<sid>` (a daemon-origin resume of a real session — the tag
  survives from the original SessionStart) → **neither = no host pane at all →
  skip the entire lifecycle** (no pane, no state DB, no codex watcher; audited as
  a `pane_events` `open` row, detail `skipped: no host pane (daemon/headless
  session)`). This also covers a headless `claude -p` that reaches the socket
  via the lone-socket fallback. The anchor then makes every pane op
  focus-independent: `goto-layout --match window_id:<anchor>` (not the active
  tab), the mirror launches `--match window_id:<anchor> --next-to id:<anchor>`
  — BOTH flags are load-bearing: `--next-to` alone is resolved only within
  the ACTIVE tab, so an anchored open while the user looked at a different
  tab silently vsplit THAT tab (observed live 2026-07-11: two mirrors in one
  tab, none in the session's own); `--match` picks the anchor's TAB first,
  `--next-to` the window inside it — and `close_stale_mirrors` sweeps the
  *anchor's* tab — each sweep now
  audited (`pane_events` action `close-stale` naming the closed sid; sweeping a
  still-open session's mirror is the canned `pane hijack` anomaly). The
  keybinding `toggle` anchors the same way (env, else the focused tab's
  `claude_session` window — `sid_from_focus` already proved it's there).
  `toggle` closes the pane if present **without** touching the DB, so reopening re-shows
  the whole session history — and while closed there is **no process at all** (no
  resources, nothing to leak). `grow`/`shrink [N]` resize by N cells
  (default `${CLAUDE_MIRROR_STEP:-4}`); `setpct N` sets an absolute width of N%
  of the tab (the size presets) and `reset` is `setpct ${CLAUDE_MIRROR_BIAS}` —
  both computed from live tab geometry and iterated to the exact target, since
  kitty's splits layout only resizes by an inexact relative increment. `open`/`close`
  get the sid from their payload (stdin); the **keybindings have no payload, so they
  recover the sid from the currently focused kitty tab** (`os_window`+`tab` `is_focused`
  → the tab's `claude_session`/`claude_mirror` var). Wired to
  `SessionStart` (open) and `SessionEnd` (close); `toggle`/`grow`/`shrink`/
  `reset`/`setpct` are bound to keys (below). When invoked from a keybinding (a
  background `launch` that doesn't inherit `KITTY_LISTEN_ON`, runs in `$HOME`,
  and has no Claude env), the script makes itself self-sufficient: it resolves
  the kitty socket by walking its ancestor pids to the controlling `kitty`
  (whose pid names `/tmp/kitty-<pid>`, falling back to the lone socket); it runs
  with `--cwd current` so `$PWD` is the project; and it reads `CLAUDE_MIRROR_BIAS`
  / `CLAUDE_MIRROR_STEP` by merging the global `~/.claude/settings.json` with the
  project `.claude/settings*.json` (project wins) — the single source of truth,
  read in one place (`read_setting`), no value hardcoded in the script.

Behaviour & limits:
- **Foreground commands** stream live, same as background: the `▶ foreground`
  header appears immediately and output lines arrive as the command produces
  them, closing with an accurate `■ finished · Ns` (or `■ failed`/`■ interrupted`)
  once the real outcome is known — see [streaming.md](streaming.md). Even
  instant commands show correctly (the block still renders in one shot when
  there's nothing to stream). Ctrl+B-backgrounding or cancelling one mid-run is
  also handled (same section).
- **Background commands** stream live: a single `▷ background` chip + the
  command, then `claude-stream.py` appends each output line (`│ ` gutter in the
  job's palette colour) as it arrives, and a matching-colour `■ background
  finished · Ns` line when the job ends — all one block, command printed once.
- **Monitor streams** show too — a `◉ monitor · <description>` header, the events
  as they fire (`│ ` gutter in the monitor's palette colour), and a matching
  `■ monitor ended · Ns` when the monitor's command process exits — exact at any
  tick cadence (seconds or hours apart), no grace. A *persistent* monitor's
  process lives until it's stopped / the session ends, so its block stays open
  until then.
- **Subagents** stream live with full visibility — the `▶ <type> · <desc>` header,
  then the subagent's **prompt** (`⇢ prompt`), its **text messages** (`✎ message`),
  its **commands** (`<type> ▶ foreground` / `▷ background`), **file ops**
  (`Read(name)` …, mutations carrying the same green `+N` / red `-M` line counts and
  touched `start-end` range, reads the same `start-end/total` extent as the main
  session, before the model tag — every file op is deferred to its result so the
  result-only extent/range is known), and its **final message / result**, then
  `■ <type> ended · Ns`
  — all in the subagent's colour, every op header tagged `<model>·<effort>` and each
  turn carrying a colour-coded `ctx N% · used/max` fill line. Several subagents in
  parallel interleave in the
  shared log but stay readable by colour. A subagent's **background command** (or
  monitor) streams with a **double gutter** (`│ │ …`): outer = the subagent's
  colour, inner = that job's own palette colour, so multiple background jobs from
  one subagent (or from different subagents) stay distinct.
- **Gutters as per-stream tags:** output lines are prefixed with a colour-coded
  `│ ` so parallel streams interleaved in the shared log stay distinguishable.
  Foreground uses one status colour (slate ok / red failed / orange interrupted);
  background, monitor, and subagents each draw from a separate 5-colour palette,
  with each running job claiming a free slot — so up to 5 concurrent of each kind
  get distinct colours. All the palette/status colours are chosen to be
  well-separated (min pairwise distance is large), so no two look alike. The finish
  chip reuses the stream's gutter colour so you can tell which one finished.
  Lines wider than the pane are **hard-wrapped** so the gutter repeats on every
  visual row (ANSI-aware — colour is re-asserted across the wrap), rather than
  soft-wrapping and losing the gutter on the continuation. **Tabs are expanded to
  spaces (8-col stops) before any width math**: the terminal advances a raw `\t`
  to the next tab stop but the wrap counters saw 1 cell, so tab-containing output
  (git diff, Makefiles, TSV) overran the pane and broke gutter alignment — and
  exact terminal tab stops are unknowable once the gutter shifts every column
  anyway, so deterministic spaces are the point.
- **Failures** show in red: a non-zero exit / tool error fires `PostToolUseFailure`
  (not `PostToolUse`), whose payload carries the combined output in an `error`
  field prefixed `Exit code N`. The block's header + finish chip turn red and the
  chip shows the code — `■ failed (exit N)`. So, unlike successes, **failed
  commands do show their exit code**.
- **Command output** is shown verbatim (real ANSI passes through and renders).
  Output that prints escape sequences as *text* — `^[`, `\033`, `\x1b`, `\e`, `<ESC>`,
  `` (e.g. from `cat -v` or `sed 's/\x1b/^[/g'`) — is **unescaped back to
  real ESC bytes** so the pane interprets them instead of showing `^[[…m`
  gibberish (`claude-cmd-fmt.py` / `claude-stream.py` `unescape()`). This covers
  **all** sequences, not just colour: a command that emits an escaped cursor-move
  or clear-screen (e.g. `^[[2J`) will have it execute in the pane.
- **Reflow on resize.** Producers write width-INDEPENDENT **paint ops** (rows in
  the state DB's `ops` table via `core/ops.py`; one transaction per block, so
  concurrent producers' blocks never interleave — the atomicity the old JSONL
  log's single O_APPEND write gave) — `rule` / `label` / `code` / `gut` / `line`, each carrying its
  colours + pre-highlighted text but no baked width. The renderer
  (`claude-mirror.py`, running in the pane) paints them at the pane's **live** width
  (`os.get_terminal_size`, no `kitten @ ls` round-trip), and on resize the pane's
  pty delivers `SIGWINCH` → it clears and **re-renders every op** at the new width,
  so dividers, gutters, and wrapped code/output all re-fit. (Earlier the width was
  baked at write time, so resizing left old blocks frozen.) Cost: a resize
  re-renders the whole history (re-highlighting code) — fine for interactive use.
  All column accounting counts **terminal cells, not code points**
  (`render.dwidth`/`dsplit`, wcwidth-style: CJK/emoji are 2 cells, combining
  marks/ZWJ/VS16 are 0) — with `len()`, any op containing wide text overran the
  pane and knocked the `│ ` gutter out of alignment on wrapped rows.
- **The `_c` render cache carries the frame math, not just the text.** Each op
  caches its render per width as `_c = [w, text, rows, stripped]`
  (`claude-mirror.py rendered()`): `rows` is the painted row count, so the
  row-accounting walks (`trim_to_budget` — which runs on *every* append batch
  and used to re-`count("\n")` the whole session's rendered text, quadratic in
  session length — plus `painted_rows`/`measure`) read a cached int; `stripped`
  is the lazily-built ANSI-stripped line list `locate_viewport` matches pane
  captures against (the post-toggle drift watch runs ~40-50 such full-frame
  searches per toggle — one strip pass per op per width instead). All four
  fields invalidate together on width change, and nothing else can invalidate
  them: ops are immutable once emitted and `viewbody`'s `_hl` highlight is
  write-once, which is also why a fancier *incremental* frame-row total was
  rejected — per-op cached counts already delete the rescan cost with no
  invalidation surface beyond the width key. `width()` itself is memoized per
  loop tick (refreshed each iteration and on SIGWINCH), so a walk costs zero
  ioctls and sees one consistent width.
- **Tailers read exactly the bytes they measured.** Every poll-loop FILE reader
  (`claude-stream.py`, `claude-substream.py`, `claude-codex-stream.py` — the
  renderer reads ops by rowid, which has no such race)
  reads `size - pos` bytes, never an unbounded `read()`: a producer appending
  *during* the read would otherwise hand the tailer bytes past the measured
  `size`, which `pos = size` then fails to account for — so the next poll
  re-read and **duplicated** them (repeated blocks in the pane).
- **Divider** spans the pane's current width and reflows with everything else.
- **Pretty-print needs `pygments`**, and highlighting happens **in the renderer
  process** — so the interpreter running `claude-mirror.py` must be one that can
  `import pygments`. kitty often launches the pane with a `python3` that resolves
  to the bare macOS/Xcode build (no pygments), which would silently drop *all*
  highlighting (bash and embedded python, foreground and background alike — they
  all go through the renderer's `R.render`). So at startup `claude-mirror.py`
  **probes** for a pygments-capable interpreter — `$CLAUDE_MIRROR_PYTHON`, then
  `python3`, then a pyenv shim / newest `~/.pyenv/versions/*`, then
  Homebrew/local — and re-execs itself into it (`os.execv`); if none has it, it
  keeps running uncoloured (this replaced the `claude-mirror.sh` wrapper, whose
  only job was that probe). Without pygments
  the command still shows with its line structure intact, just uncoloured. (A
  change here only takes effect on a **fresh** pane — toggle the mirror off/on, as
  the running renderer keeps its interpreter.)
- **Markdown rendering needs `wenmode`** (optional, pure-Python — `pip install
  wenmode`). Unlike pygments, this runs in the **tailer** (`claude-stream.py`),
  not the renderer pane, so it needs `wenmode` importable by whatever `python3`
  the hooks spawn — no re-exec probe. If it's absent, `mdrender` degrades to the
  `render.markdown()` regex subset (headings/bold/italic/inline-code only), so a
  `.md` still renders, just with less structure. Disable the whole feature with
  `CLAUDE_MIRROR_MD=0`.
- **Cost**: the tab uses the `splits` layout, leaving the Claude pane at ~75%
  (or `100 − CLAUDE_MIRROR_BIAS`%).
- **Sizing & on/off — settings + keys.** The default width is `CLAUDE_MIRROR_BIAS`
  (percent, default `25`) and the resize step is `CLAUDE_MIRROR_STEP` (cells,
  default `4`). Set either in the `env` block of Claude's `settings.json` —
  **both the global `~/.claude/settings.json` and the project `.claude/`
  settings are read, with the project overriding the global** (Claude's own
  layering: `settings.local.json` > `settings.json` > global). `claude-split.py`
  resolves this in one place: it uses the value already in its environment (the
  hook path inherits Claude's merged `env`) or, when absent (the keybinding
  path), reads + merges the same files itself — that's why the keybindings pass
  `--cwd current`, so the script sees the project dir. Live controls use a
  `kitty_mod+m` leader (added to `~/.config/kitty/kitty.conf`):
  | Keys | Action |
  | --- | --- |
  | `kitty_mod+m` then `t` | toggle the mirror on/off |
  | `kitty_mod+m` then `=` / `+` | widen by `CLAUDE_MIRROR_STEP` cells |
  | `kitty_mod+m` then `-` | narrow by `CLAUDE_MIRROR_STEP` cells |
  | `kitty_mod+m` then `0` | reset to `CLAUDE_MIRROR_BIAS`% |
  | `kitty_mod+m` then `1` / `2` / `3` | size preset: 75% / 50% / 25% of the tab |

  Presets + reset use `claude-split.py setpct <N>`, which sets an absolute width:
  kitty's splits layout only resizes by a relative increment (and one unit isn't
  exactly one column), so it reads the live geometry and **iterates** toward the
  target until within a cell. The tab's total width comes from walking the
  mirror's `neighbors` chain (whose entries are **group ids** — resolved through
  the tab's `groups` map; confirmed live), summing one window per horizontal
  segment — *not* from summing every window's columns: hsplit-stacked windows
  each report the full column width, so the plain sum double-counted shared
  columns, under-reported the mirror's %, and drove `reset`/`setpct` (and the
  remembered size) far off whenever the shell side was split. (Plain sum remains
  the fallback for a kitty too old to report `neighbors`.)
- **Remembered per project.** Any resize (grow/shrink/preset/reset) records the
  resulting width %, keyed by the project's cwd, in
  `~/.claude/kitty-mirror.db` (`sizes` table — was a directory of one-number
  files, imported once and removed). On the next `SessionStart` the mirror for
  that project opens at the remembered width instead of `CLAUDE_MIRROR_BIAS` (which
  is just the fallback when a project has no saved size). So sizing is sticky across
  restarts, independently per project.
- Opened on `SessionStart`; toggle it off/on any time with the key above (or
  `./bin/claude-split.py toggle`) — reopening re-shows the session's full history, and
  while off nothing runs. **Per session:** each Claude session has its own mirror
  (own content, own size, independent toggle), so running several sessions in
  parallel no longer makes one session's toggle close another's pane.
