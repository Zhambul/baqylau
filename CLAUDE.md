# CLAUDE.md

Guidance for Claude Code working in this repository.

## What this is

**baqylau** (Kazakh *бақылау*, "observation") — observability for agent coding sessions.
It watches sessions from several harnesses (Claude Code, Codex), interprets what they do
into one canonical event model, and presents it in a kitty terminal pane and a localhost
web dashboard.

## Architecture

**A process either appends evidence or interprets it, never both** — see
`docs/recorder-interpreter.md` for the full flow and the why. Evidence flows one
way, and every stage is recorded:

```
wrapper ──register once──▶ session_harness
recorders (hooks, otel, wrappers) ──append──▶ raw_events ◀──append── interpreter's pulled sources
                                                  │
              interpreter: translate → translation_records → canonical_events + canonical_provenance
```

- **`session_harness`** — written ONCE per session and never updated: by the
  harness's wrapper (`plugins/*/command.py`) at launch, or by the interpreter
  from the session's own orphan evidence (`HarnessSessionEvidence`) — never by
  hooks. Everything that changes during a session is a canonical fact.
- **`raw_events`** — immutable evidence: the exact bytes a source produced. Reusing a
  `raw_event_id` with different bytes raises (corruption). A pulled source resumes from
  the `source_position` of its last recorded raw event (`source_identity` column) —
  there is no separate checkpoint table, so progress cannot drift from evidence.
- **`canonical_events`** — an *idempotent projection*. `event_id` names a **fact**, so
  several sources may converge on one event; re-observing it only appends provenance.
  First writer wins; bodies are **not** compared (the later rendering stays recoverable
  from its own raw event). `cursor` (autoincrement) is the ordering key, not `event_id`.
- **`watches`** — file-watch coordination rows, applied by the interpreter from
  `watch` raw events a hook records (never files).
- **`occurred_at` is nullable by design** — when the *source* said it happened. Sources
  without a clock leave it NULL. Readers must fall back to `accepted_at`; ordering on the
  bare column is forbidden (contract test).

### Layers

| layer | contains | may import |
|---|---|---|
| `domain/` | events, codec, ids, values — the vocabulary | stdlib |
| `contracts/` | harness + terminal protocols | domain |
| `runtime/` | recorder, sessions, watches, canonical store, projections | domain, contracts |
| `app/` | bootstrap, interpreter, services | all of the above, core |
| `plugins/<harness>/` | one harness adapter each (`plugin.py` is the entry) | core, frontends |
| `terminal/`, `dashboard/` | presenters | domain/runtime/app |
| `core/`, `frontends/` | audit, process helpers; one terminal each | core only / core |

**`domain`, `contracts`, `runtime`, `app`, `core`, `dashboard`, `frontends`, `terminal` are
shared code and must contain NO concrete harness vocabulary** — no "claude", "codex",
"transcript", "rollout" (enforced by `tests/test_canonical_architecture.py`). Harness
specifics live only in `plugins/<harness>/`.

### The interpreter

`app/interpreter.py Interpreter.tick()` is the ONE read-and-interpret loop: it pulls
every registered unfinished session's sources, translates the untranslated backlog
(hook evidence included — hooks never translate), and reacts to committed facts
(panes, plugin reactors). It runs as a thread in the dashboard server, every 0.25s.

Recorder processes (hooks, the otel receiver, the wrappers) only append raw events
and do not depend on it. That split is load-bearing: when the interpreter stops,
recorders keep flowing, so a session still *looks* alive while nothing is being
interpreted.

**The daemon builds the application graph exactly once** (`serve()` in
`dashboard/http/handler.py`); every other process is a recorder or a thin
HTTP/SSE client of the daemon (`app/daemon_client.py`) — the pane processes
stream rendered frames, the keybinding and click handlers POST gestures
(enforced by `test_the_application_graph_is_built_only_by_the_daemon`;
`bin/baqylau-audit.py` is the one sanctioned direct reader, so forensics work
when the daemon is the thing being debugged). Pane rendering itself lives in
`app/pane_streams.py`: one shared, width-independent block model per session,
rendered per client width. Hence the interpreter contains failures per-session, per-source and
per-raw-event and audits every swallow — never let an exception escape it, and
never leave a raw event without a translation verdict (an unverdicted row wedges
the ordered backlog).

### Data

| store | path | holds |
|---|---|---|
| event store | `<data>/events.db` | what the session did (primary evidence) |
| operational audit | `<data>/audit/audit.db` | `errors`, `state_files`, `spawns`, `streams` |

`<data>` = `~/.local/share/baqylau`, overridable with `$BAQYLAU_DATA_DIR`.
Audit: `$BAQYLAU_AUDIT_DIRECTORY`, `BAQYLAU_AUDIT=0` disables.

Hooks are wired (in `~/.claude/settings.json`, outside this repo) to
`plugins/claude_code/canonical_hook.py`. Harnesses are launched through their
wrappers (`plugins/claude_code/command.py`, `plugins/codex/command.py`) — the
wrapper registers the session at launch (and anchors its kitty panes); launches
that skip it are registered by the interpreter from their own evidence, one
tick after their first hook, without a pid or a deterministic pane anchor. `bin/baqylau-*.py` holds the
shared executable entries; put implementation in the packages.

## Commands

```sh
python3 bin/baqylau-audit.py session <sid>   # all raw evidence + its interpretations
python3 bin/baqylau-audit.py raw <raw_id>    # one observation, exact bytes

python3 bin/baqylau-dashboard.py serve|start|stop|status

make test        # full suite
make lint        # must stay clean (ruff, encodes docs/styleguide.md)
make lint-fix
```

To debug a session bug, use the **`audit-debug` skill**
(`.claude/skills/audit-debug/SKILL.md`) — it has the schema and the known bug shapes.

## Rules

- **Hooks must never block or fail.** Exit 0, swallow exceptions — but audit before every
  swallow (`core/audit.py`). This applies to any long-lived loop too: nothing that drives
  other work may die silently.
- **A hook parses stdin, records raw events, prints its reply, and exits.** No session
  registration, no translation, no terminal, no files, no application graph (enforced by
  `test_recorder_entries_never_build_the_application`). Anything a hook wants done later
  is a raw event the interpreter reacts to (watch directives, the plugin reactor).
- **Harnesses fire no hook on cancel/interrupt.** Every cancellation path needs its own
  evidence-based signal. Never use an idle timeout as a backstop — it false-positives on
  long thinking.
- **Replayed output must not execute.** Recorded output is re-rendered many times; route
  every paint path through the neutralizing renderer.
- **Never re-encode a shared fact.** It gets one owner; see `docs/styleguide.md`.
- **Read `docs/styleguide.md` before writing or reviewing code** — layering, naming,
  import-time purity (no I/O or DB work at import), registries over if/elif, SQL binding
  and `mode=ro` probes, named constants, test conventions.

### Audit coverage

A mechanism that leaves no rows is undebuggable after the fact. In the same commit as the
feature: audit new swallow sites (`errors`), new coordination writes and control gestures
(`state_files`), new detached processes (`spawns`/`streams`). Then update the
**`audit-debug` skill** — both its schema table *and* its bug shapes; they drift
independently, and a schema-only update makes the skill look current while it triages
blind.

## Docs

`docs/` (indexed by `docs/README.md`) is the design record — *why the alternatives
failed*. Update the relevant file in the same commit as a behaviour change.
`docs/recorder-interpreter.md` describes the current session/evidence flow and wins
over anything that disagrees; `docs/canonical-harness-architecture.md` describes the
canonical event model but predates the recorder/interpreter split. Files describing
the pre-rewrite system (`mirror-pane.md`, `tab-colors.md`, `scoreboard.md`,
`sessionapi.md`, `otel.md`, `streaming.md`, `subagents.md`) are historical and should
not be trusted as current.

## Working in this repo

Multiple agents often work here concurrently.

1. **Never work directly in the main checkout.** Branch off latest `main` in an isolated
   worktree (`EnterWorktree`, or `isolation: "worktree"`). After entering one, absolute
   paths still point at the main checkout — always target the worktree path; confirm with
   `git rev-parse --show-toplevel`.
2. Commit there. When complete and `make test` is green, rebase onto `main`, `git merge
   --ff-only`, and push. Keep history linear. No PRs.
3. Remove the worktree and delete the branch.
4. **The dashboard does NOT hot-reload.** After any change it imports, the running
   singleton keeps the old code: fast-forward the main checkout, then
   `python3 bin/baqylau-dashboard.py stop && ... start`, confirm with `status`, and tell
   the user to hard-reload the browser (a restart cannot evict their cached JS/CSS).

## Live sessions

Some behaviour is only checkable against a live TUI. Spawning one opens a **visible tab in
the user's kitty** — get their OK first, and close it (`kitten @ close-tab --match
id:<win>`) the moment you are done.

- Launch under an account (`c1`/`c2`), inline the prompt:
  `kitten @ launch --type=tab --cwd <trusted-dir> /bin/zsh -lic "c1 claude '<PROMPT>'"`.
  A bare `claude` from GUI kitty is logged out and runs nothing.
- Use a directory the tool already trusts, or a trust prompt stalls the launch.
- **Prefer "is the screen still changing" over any marker string.** Glyphs and wording are
  version-fragile; two successive marker guesses each shipped a broken verify. Diff two
  captures a beat apart instead.
- **`editorMode: vim` makes the input modal** — one Escape exits INSERT and does not
  interrupt. Check the mode before assuming Escape semantics.
- **Prefer the audit trail to spawning at all.** Have the user drive their real session and
  read the resulting rows; reach for a throwaway only when you must watch the screen.
