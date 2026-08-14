# CLAUDE.md

Guidance for Claude Code working in this repository.

## What this is

**baqylau** (Kazakh *бақылау*, "observation") — observability for agent coding sessions.
It watches sessions from several harnesses (Claude Code, Codex), interprets what they do
into one canonical event model, and presents it in a kitty terminal pane and a localhost
web dashboard.

## Architecture

Evidence flows one way, and every stage is recorded:

```
source (file / stream / hook) → raw_events → translation_records → canonical_events
                                     │                                    │
                               source_checkpoints                canonical_provenance
```

- **`raw_events`** — immutable evidence: the exact bytes a source produced. Reusing a
  `raw_event_id` with different bytes raises (corruption).
- **`canonical_events`** — an *idempotent projection*. `event_id` names a **fact**, so
  several sources may converge on one event; re-observing it only appends provenance.
  First writer wins; bodies are **not** compared (the later rendering stays recoverable
  from its own raw event). `cursor` (autoincrement) is the ordering key, not `event_id`.
- **`occurred_at` is nullable by design** — when the *source* said it happened. Sources
  without a clock leave it NULL. Readers must fall back to `accepted_at`; ordering on the
  bare column is forbidden (contract test).

### Layers

| layer | contains | may import |
|---|---|---|
| `domain/` | events, codec, ids, values — the vocabulary | stdlib |
| `contracts/` | harness + terminal protocols | domain |
| `runtime/` | event store, ingest, projections, registry, state | domain, contracts |
| `app/` | bootstrap, observation, delivery, services | all of the above, core |
| `plugins/<harness>/` | one harness adapter each (`plugin.py` is the entry) | core, frontends |
| `terminal/`, `dashboard/` | presenters | domain/runtime/app |
| `core/`, `frontends/` | audit, process helpers; one terminal each | core only / core |

**`domain`, `contracts`, `runtime`, `app`, `core`, `dashboard`, `frontends`, `terminal` are
shared code and must contain NO concrete harness vocabulary** — no "claude", "codex",
"transcript", "rollout" (enforced by `tests/test_canonical_architecture.py`). Harness
specifics live only in `plugins/<harness>/`.

### Observation

`app/observe.py ObservationRunner` is the ONE scheduler for **pulled** sources
(transcripts, rollouts, foreground output, liveness, process state). It runs as a thread
in the dashboard server, every 0.25s. **Pushed** sources (`hook`, `otel`, `account`) are
written by separate short-lived processes and do not depend on it.

That split is load-bearing: when the scheduler stops, pushed sources keep flowing, so a
session still *looks* alive while its conversation silently stops. Hence the scheduler
contains failures per-source and per-pass and audits every swallow — never let an
exception escape it.

### Data

| store | path | holds |
|---|---|---|
| event store | `<data>/events.db` | what the session did (primary evidence) |
| operational audit | `<data>/audit/audit.db` | `errors`, `state_files`, `spawns`, `streams` |

`<data>` = `~/.local/share/baqylau`, overridable with `$BAQYLAU_DATA_DIR`.
Audit: `$BAQYLAU_AUDIT_DIRECTORY`, `BAQYLAU_AUDIT=0` disables.

Hooks are wired (in `~/.claude/settings.json`, outside this repo) to
`plugins/claude_code/canonical_hook.py`. `bin/baqylau-*.py` holds every executable entry;
put implementation in the packages.

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
`docs/canonical-harness-architecture.md` and the `rewrite-design-*` docs describe the
current model; files describing the pre-rewrite system (`mirror-pane.md`, `tab-colors.md`,
`scoreboard.md`, `sessionapi.md`, `otel.md`, `streaming.md`, `subagents.md`) are historical
and should not be trusted as current.

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
